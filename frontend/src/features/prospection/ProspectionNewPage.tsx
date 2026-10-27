import { useCallback, useEffect, useRef, useState } from "react";
import { MapSelector } from "./MapSelector";
import { apiFetch } from "../../lib/api";
import { getCapabilities, getCurrentPolicy } from "../leadFit/api";
import { useOptionalAuth } from "../auth/useAuth";
import { useOptionalDemoSession } from "../demo/DemoProvider";
import { ApiError } from "../../lib/api";
import { clearDemoSearchIntent, readDemoSearchIntent, writeDemoSearchIntent, type DemoSearchPayload } from "../demo/demo";

interface LocationData { center: [number, number]; radiusKm: number; polygon?: GeoJSON.Polygon }
interface CommercialVersion { id: string; status: string; version: number }
interface ApprovedPolicy { id: string; playbook_id: string; policy_digest: string; version: number; conditions: { kind: string; description: string }[] }
type Capability = "loading" | "enabled" | "disabled" | "unavailable";

export function ProspectionNewPage() {
  const { demo } = useOptionalAuth();
  const demoEnabled = !!demo?.enabled;
  const { session, loading: demoSessionLoading, error: demoSessionError, refresh: refreshDemoSession } = useOptionalDemoSession();
  const [restoredIntent] = useState(() => demoEnabled && session?.workspace_id ? readDemoSearchIntent(window.sessionStorage, session.workspace_id) : null);
  const [capability, setCapability] = useState<Capability>("loading");
  const [policies, setPolicies] = useState<ApprovedPolicy[]>([]);
  const [policyId, setPolicyId] = useState(restoredIntent?.body.fit_policy_id ?? "");
  const strategy = "maps" as const;
  const [query, setQuery] = useState(restoredIntent?.body.search_query ?? "");
  const [candidateLimit, setCandidateLimit] = useState(restoredIntent?.body.candidate_limit ?? (demoEnabled ? 3 : 10));
  const [giro, setGiro] = useState(restoredIntent?.body.giro ?? "");
  const [isLoading, setIsLoading] = useState(false);
  const [runId, setRunId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [locationData, setLocationData] = useState<LocationData | null>(() => restoredIntent ? {
    center: [restoredIntent.body.center_longitude, restoredIntent.body.center_latitude], radiusKm: restoredIntent.body.radius_km ?? 5,
    ...(restoredIntent.body.polygon_geometry ? { polygon: restoredIntent.body.polygon_geometry as GeoJSON.Polygon } : {}),
  } : null);
  const [locationLabel, setLocationLabel] = useState(restoredIntent?.body.location_label ?? "Área seleccionada en el mapa");
  const idempotencyKey = useRef<string | null>(restoredIntent?.key ?? null);
  const pendingDemoBody = useRef<DemoSearchPayload | null>(restoredIntent?.body ?? null);
  const demoSearchUnavailable = demoEnabled && (demoSessionLoading || !!demoSessionError || !session?.global_search_available || (session.searches.remaining ?? 0) < 1);

  const load = useCallback(async () => {
    if (demoEnabled) {
      setCapability("enabled");
      try {
        const versions = await apiFetch<CommercialVersion[]>("/sales-playbooks");
        const approvedVersions = versions.filter((item) => item.status === "approved");
        const currentPolicies = await Promise.all(approvedVersions.map((item) => getCurrentPolicy(item.id).catch(() => null)));
        setPolicies(currentPolicies.filter((item): item is NonNullable<typeof item> => !!item && typeof item.id === "string" && typeof item.policy_digest === "string").map((item) => ({
          id: item.id, playbook_id: item.playbook_id, policy_digest: item.policy_digest, version: item.version,
          conditions: (item.content?.criteria ?? []).filter((criterion) => criterion.kind === "required" || criterion.kind === "exclusion"),
        })));
        if (restoredIntent && approvedVersions.some((item) => item.id === restoredIntent.body.fit_policy_id)) setPolicyId(restoredIntent.body.fit_policy_id);
        setError("");
      } catch { setCapability("unavailable"); }
      return;
    }
    try {
      const result: unknown = await getCapabilities();
      if (!result || typeof result !== "object" || typeof (result as { enabled?: unknown }).enabled !== "boolean") {
        setCapability("unavailable"); return;
      }
      if ((result as { enabled: boolean }).enabled === false) { setCapability("disabled"); return; }
      setCapability("enabled");
      const versions = await apiFetch<CommercialVersion[]>("/sales-playbooks");
      const approvedVersions = versions.filter((item) => item.status === "approved");
      const currentPolicies = await Promise.all(approvedVersions.map((item) => getCurrentPolicy(item.id).catch(() => null)));
      const valid = currentPolicies.filter((item): item is NonNullable<typeof item> => !!item && typeof item.id === "string" && typeof item.policy_digest === "string").map((item) => ({
        id: item.id, playbook_id: item.playbook_id, policy_digest: item.policy_digest, version: item.version,
        conditions: (item.content?.criteria ?? []).filter((criterion) => criterion.kind === "required" || criterion.kind === "exclusion"),
      }));
      setPolicies(valid);
      setError("");
    } catch {
      setCapability("unavailable");
    }
  }, [demoEnabled, restoredIntent]);

  useEffect(() => { void load(); }, [load]);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    setIsLoading(true);
    try {
      if (demoEnabled) {
        const selected = policies.find((item) => item.id === policyId);
        if (!selected) throw new Error("Aprueba el contexto y las condiciones antes de buscar.");
        if (!query.trim() || !giro.trim()) throw new Error("Escribe y revisa el tipo de negocio y la consulta.");
        if (!locationData || !locationLabel.trim()) throw new Error("Selecciona el área de búsqueda en el mapa.");
        if (demoSessionLoading || demoSessionError) throw new Error("No se pudo confirmar tu cuota demo. Actualiza el estado y vuelve a intentar.");
        if (!session?.global_search_available) throw new Error("Las búsquedas demo están pausadas por capacidad global del servicio.");
        if ((session?.searches.remaining ?? 0) < 1) throw new Error("Ya usaste las dos búsquedas disponibles para esta cuenta.");
        if (!idempotencyKey.current) {
          idempotencyKey.current = globalThis.crypto.randomUUID();
          pendingDemoBody.current = {
          giro: giro.trim(), discovery_strategy: "maps", search_query: query.trim(), fit_policy_id: selected.id, candidate_limit: Math.min(3, candidateLimit), enrich_candidates: true,
          location_label: locationLabel.trim(), center_latitude: locationData.center[1], center_longitude: locationData.center[0],
          radius_km: locationData.polygon ? null : locationData.radiusKm, polygon_geometry: locationData.polygon || null,
          };
          if (session?.workspace_id) writeDemoSearchIntent(window.sessionStorage, { key: idempotencyKey.current, workspace_id: session.workspace_id, body: pendingDemoBody.current });
        }
        const body = pendingDemoBody.current!;
        try {
          const created = await apiFetch<{ id: string }>("/demo/searches", { method: "POST", headers: { "Idempotency-Key": idempotencyKey.current }, body: JSON.stringify(body) });
          setRunId(created.id); idempotencyKey.current = null; pendingDemoBody.current = null; clearDemoSearchIntent(window.sessionStorage); await refreshDemoSession();
        } catch (reason) {
          await refreshDemoSession();
          if (reason instanceof ApiError && reason.runId) { setRunId(reason.runId); idempotencyKey.current = null; pendingDemoBody.current = null; clearDemoSearchIntent(window.sessionStorage); }
          throw reason;
        }
        return;
      }
      if (capability === "disabled") {
        if (!locationData) throw new Error("Selecciona un área en el mapa");
        const legacy = await apiFetch<{ id: string }>("/prospection-runs", {
          method: "POST",
          body: JSON.stringify({
            giro: giro.trim(), location_label: locationLabel.trim() || "Área seleccionada en el mapa",
            center_latitude: locationData.center[1], center_longitude: locationData.center[0],
            radius_km: locationData.polygon ? null : locationData.radiusKm,
            polygon_geometry: locationData.polygon || null,
          }),
        });
        setRunId(legacy.id);
        return;
      }
      const selected = policies.find((item) => item.id === policyId);
      if (!selected) throw new Error("Selecciona una política de encaje aprobada.");
      if (!query.trim()) throw new Error("Escribe y revisa la consulta de búsqueda.");
      if (!giro.trim()) throw new Error("Indica el giro de los negocios que se buscarán.");
      if (strategy === "maps" && (!locationData || !locationLabel.trim())) throw new Error("Selecciona el área de búsqueda en el mapa.");
      const body: Record<string, unknown> = {
        giro: giro.trim(), discovery_strategy: strategy, search_query: query.trim(),
        fit_policy_id: selected.id, candidate_limit: candidateLimit,
      };
      if (strategy === "maps" && locationData) Object.assign(body, {
        location_label: locationLabel.trim(), center_latitude: locationData.center[1],
        center_longitude: locationData.center[0], radius_km: locationData.polygon ? null : locationData.radiusKm,
        polygon_geometry: locationData.polygon || null,
      });
      const created = await apiFetch<{ id: string }>("/prospection-runs", { method: "POST", body: JSON.stringify(body) });
      setRunId(created.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo crear la búsqueda.");
    } finally { setIsLoading(false); }
  }

  if (capability === "loading") return <div className="card" role="status">Consultando disponibilidad de prospección…</div>;
  if (capability === "unavailable") return <div className="card" role="alert">Las búsquedas no están disponibles hasta confirmar la capacidad del servidor.</div>;
  if (runId) return <div className="card space-y-4"><h2 className="text-xl font-bold text-slate-900">Búsqueda creada</h2>{!demoEnabled && <p className="text-sm text-slate-600">Run ID: <code>{runId}</code></p>}<a href={`/prospection/${runId}`} className="btn-primary inline-block">Ver resultados y progreso</a></div>;

  return <main className="mx-auto max-w-4xl space-y-5">
    <header><h2 className="text-2xl font-bold text-gray-900">Nueva búsqueda de prospectos</h2><p className="mt-1 text-sm text-slate-600">La consulta define qué empresas se buscarán. Revísala antes de iniciar.</p></header>
    <form onSubmit={(event) => void handleSubmit(event)} className="space-y-5 rounded-lg border border-gray-200 bg-white p-6">
      {error && <div className="text-sm text-red-700" role="alert">{error}</div>}
      {demoEnabled && idempotencyKey.current && <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900" role="status">La solicitud sigue pendiente de confirmación. Al reintentar se resolverá la misma búsqueda, sin crear otra.</p>}
      {demoEnabled && <div className="quota-pill" aria-live="polite">{session?.searches.remaining ?? 0} de 2 búsquedas restantes</div>}
      {demoEnabled && !session?.global_search_available && <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900" role="status">Las búsquedas están pausadas por capacidad global. Tus resultados y borradores siguen disponibles.</p>}
      {demoEnabled && demoSessionLoading && <p className="rounded-lg bg-slate-50 p-3 text-sm text-slate-700" role="status">Actualizando la cuota en el servidor…</p>}
      {demoEnabled && demoSessionError && <p className="rounded-lg bg-red-50 p-3 text-sm text-red-800" role="alert">No se pudo confirmar la cuota en el servidor. Actualiza la página antes de iniciar una búsqueda.</p>}
      <label className="flex flex-col gap-1 text-sm">Giro / Tipo de negocio<input aria-label="Giro / Tipo de negocio" className="input" required value={giro} onChange={(event) => setGiro(event.target.value)} placeholder="Describe el tipo de empresa" disabled={isLoading} /></label>
      {capability === "enabled" && <>
        <p className="text-sm text-slate-600">Maps descubre candidatos por ubicación; Tavily se usa únicamente para enriquecer candidatos ya descubiertos.</p>
        <label className="flex flex-col gap-1 text-sm">Política aprobada<select aria-label="Política aprobada" className="input" required value={policyId} onChange={(event) => setPolicyId(event.target.value)}><option value="">Selecciona una política vigente</option>{policies.map((item) => <option key={item.id} value={item.id}>Versión {item.version} · {item.playbook_id}</option>)}</select></label>
        {!!policies.find((item) => item.id === policyId)?.conditions.length && <section aria-label="Requisitos y exclusiones aprobados" className="rounded-lg border border-slate-200 p-4 text-sm">
          <h3 className="font-semibold">Condiciones que se comprobarán al evaluar</h3>
          <ul className="mt-2 list-disc space-y-1 pl-5">{policies.find((item) => item.id === policyId)!.conditions.map((condition, index) => <li key={index}><span>{condition.description}</span> <small>({condition.kind === "required" ? "Indispensable" : "Exclusión"})</small></li>)}</ul>
        </section>}
        {policies.length === 0 && <p className="text-sm text-amber-800">No hay una política vigente. Aprueba las condiciones de prospección desde el contexto comercial.</p>}
        <label className="flex flex-col gap-1 text-sm">Consulta revisada<textarea aria-label="Consulta revisada" className="input min-h-20" required maxLength={500} value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Escribe qué empresas buscar; no se completa automáticamente con tu propio giro." /></label>
        <p className="text-sm text-slate-500">La búsqueda descubre candidatos; no exige sitio web propio ni confirma necesidad o intención de compra. Usa los criterios aprobados para orientar la consulta. La falta de un dato queda pendiente de investigación, no se interpreta como ausencia confirmada.</p>
        <label className="flex max-w-xs flex-col gap-1 text-sm">Máximo de candidatos<input className="input" type="number" min={1} max={demoEnabled ? 3 : 10} value={candidateLimit} onChange={(event) => setCandidateLimit(Math.max(1, Math.min(demoEnabled ? 3 : 10, Number(event.target.value) || 1)))} /></label>
      </>}
      {(capability === "disabled" || strategy === "maps") && <>
        <label className="flex flex-col gap-1 text-sm">Nombre de la zona<input className="input" value={locationLabel} onChange={(event) => setLocationLabel(event.target.value)} placeholder="Ej. Monterrey" disabled={isLoading} /></label>
        <div><p className="mb-2 text-sm font-medium">Área de búsqueda</p><MapSelector onLocationSelect={(location) => setLocationData(location)} /></div>
      </>}
      {demoEnabled && !idempotencyKey.current && <p className="text-sm text-slate-600">Al iniciar autorizas la revisión web de hasta tres candidatos: máximo una búsqueda y una lectura de página por negocio. Después verás la evaluación y su motivo; no se envían mensajes.</p>}
      <button type="submit" disabled={isLoading || (capability === "enabled" && policies.length === 0) || demoSearchUnavailable} className="btn-primary">{isLoading ? "Creando búsqueda…" : "Iniciar búsqueda"}</button>
    </form>
  </main>;
}
