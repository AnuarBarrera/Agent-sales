import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { apiFetch } from "../../lib/api";
import { getCapabilities, getLatestEvaluation } from "../leadFit/api";
import type { FitEvaluationRead } from "../leadFit/types";
import { useOptionalAuth } from "../auth/useAuth";
import { useOptionalDemoSession } from "../demo/DemoProvider";
import { isDemoRunSettled } from "../demo/demo";
import { DemoNextStep } from "../demo/DemoNextStep";

interface Run {
  id: string; giro: string; location_label: string; status: string;
  found_count: number; new_count: number; duplicate_count: number; error_count: number;
  error_summary: string | null; fit_policy_id?: string | null;
}
interface RunEvents {
  status: string; found_count: number; new_count: number; duplicate_count: number; error_count: number;
  events: Array<{ id: string; event_type: string; created_at: string }>;
}
interface FitResults {
  found_count: number; new_count: number; duplicate_count: number; discovery_failed_count: number;
  evaluation_unavailable_count: number; total: number; qualified: number; needs_more_research: number;
  not_qualified: number; evaluation_failed: number; pending: number;
  items: Array<{ id: string; lead_id: string; status: string; classification: string | null; fit_score: number | null; coverage: number | null }>;
}
interface DemoLead { contacted?: boolean; next_follow_up_at?: string | null; id: string; name: string; address: string | null; phone: string | null; website: string | null }
type DemoEvaluationSummary =
  | { status: "loading" | "none" | "pending" | "unavailable" | "error" }
  | { status: "completed"; classification: string; reasons: string[]; coverage: number | null; nextAction: string };
type Capability = "loading" | "enabled" | "disabled" | "unavailable";

const terminalStatuses = new Set(["ready", "partial", "failed"]);

// Etiquetas normativas de la ficha de prospección. Los valores de la API
// (qualified / needs_more_research / not_qualified) no cambian.
const CLASSIFICATION_LABELS: Record<string, string> = {
  qualified: "Cumple condiciones revisadas",
  needs_more_research: "Datos por confirmar",
  not_qualified: "No cumple condiciones revisadas",
};

function summarizeLatestEvaluation(evaluation: FitEvaluationRead | null, fitItemStatus: string): DemoEvaluationSummary {
  if (!evaluation) return fitItemStatus === "pending" || fitItemStatus === "running" ? { status: "pending" } : { status: "none" };
  if (evaluation.status === "pending" || evaluation.status === "running") return { status: "pending" };
  if (evaluation.status !== "completed" || !evaluation.classification) return { status: "unavailable" };
  const reasons = Array.isArray(evaluation.result?.reasons)
    ? evaluation.result.reasons.filter((reason): reason is string => typeof reason === "string" && !!reason.trim())
    : [];
  const nextAction = evaluation.classification === "qualified"
    ? "Revisar la evidencia y el canal de contacto antes de decidir."
    : evaluation.classification === "not_qualified"
      ? "Revisar las condiciones evaluadas antes de decidir."
      : "Revisar la información pendiente antes de decidir.";
  return {
    status: "completed",
    classification: CLASSIFICATION_LABELS[evaluation.classification] ?? "Evaluación completada",
    reasons,
    coverage: typeof evaluation.coverage === "number" && Number.isFinite(evaluation.coverage) ? evaluation.coverage : null,
    nextAction,
  };
}

function DemoEvaluationBrief({ summary }: { summary: DemoEvaluationSummary }) {
  if (summary.status === "loading") return <p className="mt-1 text-sm text-slate-500">Consultando evaluación…</p>;
  if (summary.status === "none") return <p className="mt-1 text-sm text-slate-500">Sin evaluación registrada.</p>;
  if (summary.status === "pending") return <p className="mt-1 text-sm text-slate-600">Evaluación pendiente o en curso.</p>;
  if (summary.status === "unavailable") return <p className="mt-1 text-sm text-amber-800">Evaluación no disponible.</p>;
  if (summary.status === "error") return <p className="mt-1 text-sm text-amber-800">No se pudo consultar la evaluación.</p>;
  if (summary.status !== "completed") return null;
  return <div className="mt-1 space-y-1 text-sm text-slate-700">
    <p><strong>Evaluación:</strong> {summary.classification}</p>
    <p><strong>Razón:</strong> {summary.reasons.length ? summary.reasons.join(" ") : "La evaluación no reportó una razón."}</p>
    {summary.coverage !== null && <p>Cobertura de evidencia: {summary.coverage}%</p>}
    <p><strong>Siguiente paso:</strong> {summary.nextAction}</p>
  </div>;
}

function isDisplayScore(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 100;
}

export function ProspectionRunPage() {
  const { demo } = useOptionalAuth();
  const demoEnabled = !!demo?.enabled;
  const { session, loading: demoSessionLoading, error: demoSessionError, refresh: refreshDemoSession } = useOptionalDemoSession();
  const { runId } = useParams<{ runId: string }>();
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<RunEvents | null>(null);
  const [fit, setFit] = useState<FitResults | null>(null);
  const [demoLeads, setDemoLeads] = useState<Record<string, DemoLead>>({});
  const [demoLeadError, setDemoLeadError] = useState("");
  const [demoEvaluationSummaries, setDemoEvaluationSummaries] = useState<Record<string, DemoEvaluationSummary>>({});
  const [pendingEvaluationCount, setPendingEvaluationCount] = useState<number | null>(null);
  const [globalFitProgress, setGlobalFitProgress] = useState<Pick<FitResults, "pending" | "total" | "evaluation_unavailable_count"> | null>(null);
  const [capability, setCapability] = useState<Capability>("loading");
  const [classification, setClassification] = useState("");
  const [evaluationStatus, setEvaluationStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    void getCapabilities().then((result: unknown) => {
      if (cancelled) return;
      if (!result || typeof result !== "object" || typeof (result as { enabled?: unknown }).enabled !== "boolean") setCapability("unavailable");
      else setCapability((result as { enabled: boolean }).enabled ? "enabled" : "disabled");
    }).catch(() => { if (!cancelled) setCapability("unavailable"); });
    return () => { cancelled = true; };
  }, []);

  const loadRun = useCallback(async () => {
    if (!runId) return;
    try {
      const [runData, eventData] = await Promise.all([
        apiFetch<Run>(`/prospection-runs/${runId}`),
        apiFetch<RunEvents>(`/prospection-runs/${runId}/events`),
      ]);
      setRun(runData); setEvents(eventData); setError("");
    } catch (err) { setError(err instanceof Error ? err.message : "No se pudo cargar la búsqueda."); }
  }, [runId]);

  const loadFit = useCallback(async () => {
    if (!runId || capability !== "enabled" || !run?.fit_policy_id) return;
    const params = new URLSearchParams({ limit: "25", offset: String(offset) });
    if (classification) params.set("classification", classification);
    if (evaluationStatus) params.set("evaluation_status", evaluationStatus);
    try {
      const data = await apiFetch<FitResults>(`/prospection-runs/${runId}/fit-results?${params.toString()}`);
      setFit(data);
    } catch (err) { setError(err instanceof Error ? err.message : "No se pudieron cargar los resultados de encaje."); }
  }, [runId, capability, run?.fit_policy_id, offset, classification, evaluationStatus]);

  const loadProgress = useCallback(async () => {
    if (!runId || capability !== "enabled" || !run?.fit_policy_id) return;
    try {
      const progress = await apiFetch<Pick<FitResults, "pending" | "total" | "evaluation_unavailable_count">>(`/prospection-runs/${runId}/fit-results?limit=1&offset=0`);
      setPendingEvaluationCount(progress.pending);
      setGlobalFitProgress(progress);
    } catch (err) { setPendingEvaluationCount(null); setGlobalFitProgress(null); setError(err instanceof Error ? err.message : "No se pudo consultar el progreso de evaluaciones."); }
  }, [runId, capability, run?.fit_policy_id]);

  useEffect(() => { void loadRun(); }, [loadRun]);
  useEffect(() => { void loadFit(); }, [loadFit]);
  useEffect(() => { void loadProgress(); }, [loadProgress]);
  useEffect(() => { if (demoEnabled) void refreshDemoSession(); }, [demoEnabled, refreshDemoSession]);
  useEffect(() => {
    if (!demoEnabled || !run?.id) { setDemoLeads({}); setDemoLeadError(""); return; }
    let active = true;
    setDemoLeads({}); setDemoLeadError("");
    void apiFetch<{ items: DemoLead[] }>(`/leads?run_id=${encodeURIComponent(run.id)}`)
      .then((result) => { if (active) setDemoLeads(Object.fromEntries(result.items.map((lead) => [lead.id, lead]))); })
      .catch((reason: unknown) => { if (active) setDemoLeadError(reason instanceof Error ? reason.message : "No se pudieron cargar los contactos del run."); });
    return () => { active = false; };
  }, [demoEnabled, run?.id, run?.found_count, run?.status]);
  useEffect(() => {
    if (!demoEnabled || !fit) { setDemoEvaluationSummaries({}); return; }
    let active = true;
    const visible = fit.items.slice(0, 3);
    setDemoEvaluationSummaries({});
    void Promise.all(visible.map(async (item) => {
      try { return [item.lead_id, summarizeLatestEvaluation(await getLatestEvaluation(item.lead_id), item.status)] as const; }
      catch { return [item.lead_id, { status: "error" } as const] as const; }
    })).then((results) => { if (active) setDemoEvaluationSummaries(Object.fromEntries(results)); });
    return () => { active = false; };
  }, [demoEnabled, fit]);

  useEffect(() => {
    if (!runId) return;
    const discoveryDone = !!run && terminalStatuses.has(run.status);
    const evaluationsDone = capability !== "enabled" || !run?.fit_policy_id || pendingEvaluationCount === 0;
    const demoSettled = !!run && isDemoRunSettled(run, globalFitProgress);
    if (demoEnabled ? demoSettled : discoveryDone && evaluationsDone) return;
    const timer = window.setInterval(() => { void loadRun(); void loadFit(); void loadProgress(); }, 2000);
    return () => window.clearInterval(timer);
  }, [runId, run, capability, pendingEvaluationCount, loadRun, loadFit, loadProgress, demoEnabled, globalFitProgress]);

  const status = run?.status || events?.status || "queued";
  const discoveryDone = !!run && terminalStatuses.has(run.status);
  const pageSize = 25;
  return <div className="mx-auto max-w-4xl space-y-6">
    <header><p className="text-sm text-gray-500">Prospección</p><h2 className="text-2xl font-bold text-gray-900">{run?.giro || "Cargando búsqueda…"}</h2><p className="text-gray-600">{run?.location_label}</p></header>
    {demoEnabled && <div className="quota-pill" aria-live="polite">{demoSessionError ? "Cuota no disponible" : demoSessionLoading || !session ? "Actualizando cuota…" : `${session.searches.remaining} de 2 búsquedas restantes`}</div>}
    {demoEnabled && <p className="text-sm text-slate-600">Maps muestra candidatos a revisar; no garantiza tres oportunidades.</p>}
    {error && <p className="text-red-700" role="alert">{error}</p>}
    <section className="card">
      <div className="flex flex-wrap items-center justify-between gap-3"><span className={`status status-${status}`}>{status}</span>{run && <Link className="btn-secondary" to={`/leads?run_id=${run.id}`}>Ver leads</Link>}</div>
      <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-4"><Metric label="Encontrados" value={run?.found_count ?? events?.found_count ?? 0} /><Metric label="Nuevos" value={run?.new_count ?? events?.new_count ?? 0} /><Metric label="Duplicados" value={run?.duplicate_count ?? events?.duplicate_count ?? 0} /><Metric label="Errores de descubrimiento" value={run?.error_count ?? events?.error_count ?? 0} /></div>
      {run?.error_summary && <p className="mt-4 text-sm text-red-700">{run.error_summary}</p>}
    </section>

    {capability === "loading" && <section className="card text-sm text-slate-600">Consultando resultados de encaje…</section>}
    {capability === "unavailable" && <section className="card text-sm text-slate-700">Los resultados de encaje no están disponibles porque no se pudo confirmar la capacidad del servidor.</section>}
    {capability === "enabled" && run?.fit_policy_id && <section className="card space-y-5" aria-labelledby="run-fit-heading">
      <div><h3 id="run-fit-heading" className="font-semibold text-gray-900">Resultados de encaje</h3><p className="text-sm text-slate-600">El estado de descubrimiento y el de evaluación avanzan por separado. Esta pantalla sólo consulta estados; nunca inicia reintentos pagados.</p></div>
      {fit && <>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><Metric label="Cumple condiciones revisadas (filtro actual)" value={fit.qualified} /><Metric label="Datos por confirmar (filtro actual)" value={fit.needs_more_research} /><Metric label="No cumple condiciones revisadas (filtro actual)" value={fit.not_qualified} /><Metric label="Pendientes en todo el run" value={pendingEvaluationCount ?? fit.pending} /></div>
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">
          <p><strong>Evaluaciones no disponibles (incidencia operativa):</strong> {fit.evaluation_unavailable_count}</p>
          <p><strong>Evaluaciones con fallo técnico:</strong> {fit.evaluation_failed}</p>
          <p className="mt-1">Un rechazo comercial es distinto de una evaluación fallida o ausente. Revisa los prospectos del run para resolver incidencias.</p>
          <Link className="mt-2 inline-block underline" to={`/leads?run_id=${run.id}`}>Revisar prospectos del run</Link>
        </div>
        {!demoEnabled && <div className="flex flex-wrap gap-3">
          <label className="flex flex-col gap-1 text-sm">Clasificación<select className="input" value={classification} onChange={(event) => { setOffset(0); setClassification(event.target.value); }}><option value="">Todas</option><option value="qualified">Cumple condiciones revisadas</option><option value="needs_more_research">Datos por confirmar</option><option value="not_qualified">No cumple condiciones revisadas</option></select></label>
          <label className="flex flex-col gap-1 text-sm">Estado técnico<select className="input" value={evaluationStatus} onChange={(event) => { setOffset(0); setEvaluationStatus(event.target.value); }}><option value="">Todos</option><option value="pending">Pendiente</option><option value="running">En curso</option><option value="completed">Completado</option><option value="failed">Falló</option></select></label>
        </div>}
        {demoEnabled ? <p className="text-xs text-slate-600">Se muestran hasta tres prospectos. Los pendientes y las incidencias operativas se cuentan en todo el run. La cobertura representa condiciones revisadas con evidencia, no el encaje del negocio.</p> : <p className="text-xs text-slate-600">Evaluaciones mostradas con los filtros actuales: {fit.total}. Los conteos de clasificación y fallos mostrados siguen estos filtros; pendientes en todo el run e incidencias no disponibles muestran el total operativo. La cobertura de criterios es el porcentaje de condiciones revisadas con evidencia, no el encaje del negocio.</p>}
        {demoEnabled && demoLeadError && <p role="alert" className="text-sm text-amber-800">No se pudieron cargar los datos de contacto de algunos prospectos: {demoLeadError}</p>}
        <ul className="divide-y divide-slate-100">{fit.items.slice(0, demoEnabled ? 3 : undefined).map((item) => {
          if (!demoEnabled) return <li key={item.id} className="flex flex-wrap items-center justify-between gap-3 py-3 text-sm"><Link className="text-teal-800 underline" to={`/leads/${item.lead_id}`}>Abrir prospecto</Link><span>{item.status === "failed" ? "Fallo técnico" : item.classification ? (CLASSIFICATION_LABELS[item.classification] ?? item.classification) : item.status}</span><span>Puntuación según reglas: {isDisplayScore(item.fit_score) ? item.fit_score : "No calculada"} · Cobertura de criterios: {isDisplayScore(item.coverage) ? `${item.coverage.toFixed(1)}%` : "no disponible"}</span></li>;
          const lead = demoLeads[item.lead_id];
          return <li key={item.id} className="grid gap-3 py-4 text-sm sm:grid-cols-[1fr_1.4fr]">
            <div><p className="font-semibold text-slate-900">{lead?.name || "Prospecto"}</p><p className="text-slate-600">{lead?.address || "Dirección no disponible"}</p>{lead?.phone && <p className="text-slate-700">{lead.phone}</p>}{lead?.website && <p className="break-all text-slate-600">Sitio observado: {lead.website}</p>}</div>
            <div className="space-y-3"><DemoEvaluationBrief summary={demoEvaluationSummaries[item.lead_id] ?? { status: "loading" }} /><DemoNextStep leadId={item.lead_id} contacted={lead?.contacted} nextFollowUpAt={lead?.next_follow_up_at} /></div>
          </li>;
        })}{fit.items.length === 0 && <li className="py-3 text-sm text-slate-500">{discoveryDone ? "La búsqueda terminó sin evaluaciones de prospectos." : "Buscando negocios en Maps…"}</li>}</ul>
        {!demoEnabled && <div className="flex items-center justify-between"><button className="btn-secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - pageSize))}>Anterior</button><span className="text-sm text-slate-600">Página {Math.floor(offset / pageSize) + 1}</span><button className="btn-secondary" disabled={offset + pageSize >= fit.total} onClick={() => setOffset(offset + pageSize)}>Siguiente</button></div>}
      </>}
    </section>}
    {capability === "disabled" && run?.fit_policy_id && <section className="card text-sm text-slate-600">La evaluación de encaje está desactivada para esta búsqueda.</section>}
    {!demoEnabled && <section className="card"><h3 className="font-semibold text-gray-900">Actividad de la búsqueda</h3><ol className="mt-4 space-y-3">{events?.events.map((event) => <li key={event.id} className="border-l-2 border-blue-200 pl-3 text-sm"><span className="font-medium">{event.event_type}</span><time className="ml-2 text-gray-500">{new Date(event.created_at).toLocaleString("es-MX")}</time></li>)}{!events?.events.length && <li className="text-sm text-gray-500">Esperando eventos…</li>}</ol></section>}
  </div>;
}

function Metric({ label, value }: { label: string; value: number }) { return <div><p className="text-sm text-gray-600">{label}</p><p className="text-2xl font-semibold text-gray-900">{value}</p></div>; }
