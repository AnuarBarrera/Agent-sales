import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../../lib/api";
import { approvePolicy, getCapabilities, getCurrentPolicy, previewPolicy } from "./api";
import type { CriterionKind, CriterionSpec, FitPolicyContent } from "./types";

interface PolicyReviewPanelProps {
  playbookId: string;
  compact?: boolean;
  opportunitySourcePointers?: string[];
  onPolicyApproved?: (policy: Record<string, unknown>) => void;
}

type Capability = "loading" | "enabled" | "unavailable";

export function PolicyReviewPanel({ playbookId, compact = false, opportunitySourcePointers = [], onPolicyApproved }: PolicyReviewPanelProps) {
  const [capability, setCapability] = useState<Capability>("loading");
  const [policy, setPolicy] = useState<FitPolicyContent | null>(null);
  const [commercialDigest, setCommercialDigest] = useState("");
  const [approved, setApproved] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setCapability("loading");
    try {
      const result: unknown = await getCapabilities();
      if (!result || typeof result !== "object" || (result as { enabled?: unknown }).enabled !== true) {
        setCapability("unavailable");
        return;
      }
      setCapability("enabled");
      const current = await getCurrentPolicy(playbookId);
      if (current && typeof current.commercial_digest === "string" && current.content) {
        setPolicy(current.content);
        setCommercialDigest(current.commercial_digest);
        setApproved(true);
        setConfirmed(false);
      } else {
        setPolicy(null);
        setCommercialDigest("");
        setApproved(false);
        setConfirmed(false);
      }
      setError("");
    } catch {
      setCapability("unavailable");
      setError("");
    }
  }, [playbookId]);

  useEffect(() => { void load(); }, [load]);

  const propose = async () => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const preview = await previewPolicy({ playbook_id: playbookId });
      if (!preview.expected_commercial_digest || !preview.content) {
        throw new Error("La propuesta no incluyó una autoridad comercial vigente.");
      }
      setPolicy(preview.content);
      setCommercialDigest(preview.expected_commercial_digest);
      setApproved(false);
      setConfirmed(false);
    } catch (err) {
      setError(err instanceof ApiError && err.status === 409
        ? "El contexto comercial cambió. Actualiza la propuesta antes de aprobar."
        : "No se pudo cargar la propuesta de condiciones.");
    } finally { setBusy(false); }
  };

  const changeCriterion = (index: number, update: Partial<CriterionSpec>) => {
    setPolicy((current) => current ? {
      ...current,
      criteria: current.criteria.map((criterion, row) => row === index ? { ...criterion, ...update } : criterion),
    } : current);
    setApproved(false);
    setConfirmed(false);
  };

  const changePolicy = (next: FitPolicyContent) => {
    setPolicy(next);
    setApproved(false);
    setConfirmed(false);
  };

  const save = async () => {
    if (!policy || !commercialDigest) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await approvePolicy({
        playbook_id: playbookId,
        expected_commercial_digest: commercialDigest,
        content: policy,
        confirmed: true,
      });
      setApproved(true);
      setConfirmed(false);
      setNotice("Condiciones aprobadas para el digest comercial vigente.");
      onPolicyApproved?.(result);
    } catch (err) {
      setCommercialDigest("");
      setApproved(false);
      setError(err instanceof ApiError && err.status === 409
        ? "El digest comercial cambió. Actualiza la propuesta y revísala de nuevo."
        : err instanceof ApiError && err.status === 403
          ? "Tu rol no puede aprobar estas condiciones."
          : err instanceof ApiError && err.status === 400
            ? err.message
            : "No se pudieron aprobar las condiciones. Revisa el estado antes de repetir la acción.");
    } finally { setBusy(false); }
  };

  if (capability === "loading") return <section className="card p-4 text-sm text-slate-500" aria-live="polite">Consultando disponibilidad de condiciones de prospección…</section>;
  if (capability !== "enabled") return <section className="card p-4 text-sm text-slate-600">Condiciones de prospección no disponibles en este momento.</section>;

  return <section className="card space-y-5" aria-labelledby="policy-review-heading">
    <div>
      <p className="eyebrow">Condiciones de prospección</p>
      <h3 id="policy-review-heading" className="mt-1 text-lg font-bold text-slate-900">Revisar condiciones de encaje</h3>
      <p className="mt-1 text-sm text-slate-600">Ajusta qué es indispensable y qué sólo suma prioridad. La aprobación crea una versión nueva y no cambia el contexto 4.0.</p>
      <p className="mt-1 text-sm text-slate-600">Industria y zona orientan la búsqueda en Maps; no se convierten automáticamente en requisitos de encaje. Si el contexto define una oportunidad observable, la nueva propuesta se centra en ella y en las exclusiones. Las condiciones ya aprobadas no cambian: usa Actualizar propuesta para revisar y aprobar una nueva versión.</p>
      <p className="mt-1 text-sm text-slate-600">Las oportunidades son opcionales para prospectar. Puedes dejarlas como preferencia o elegir Indispensable; si falta evidencia, se investiga. Esto no exige intención de compra.</p>
      <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">
        <p className="font-semibold">Hipótesis de prospección</p>
        <p className="mt-1">
          Estas condiciones son hipótesis comerciales iniciales que revisa una persona. No son reglas calibradas
          con ventas históricas y no predicen quién comprará: describen qué se busca comprobar y con qué evidencia.
        </p>
        <p className="mt-1">
          Abrir o editar esta pantalla no aprueba nada. La aprobación exige tu confirmación explícita del digest
          comercial vigente.
        </p>
      </div>
    </div>

    {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-800">{error}</p>}
    {notice && <p role="status" className="rounded-lg bg-emerald-50 p-3 text-sm text-emerald-800">{notice}</p>}

    {!policy && <div className="space-y-3">
      <p className="text-sm text-slate-600">Aún no hay condiciones aprobadas. Carga una propuesta desde el contexto comercial aprobado para revisarla.</p>
      <button type="button" className="btn-primary" disabled={busy} onClick={() => void propose()}>{busy ? "Cargando propuesta…" : "Proponer condiciones"}</button>
    </div>}

    {policy && <>
      {approved && <p className="text-sm text-emerald-800">Hay una versión aprobada para este contexto comercial.</p>}
      <fieldset className="space-y-3">
        <legend className="font-semibold text-slate-900">Criterios revisables</legend>
        {policy.criteria.map((criterion, index) => <div key={`${criterion.id}-${index}`} className={`grid gap-3 rounded-lg border border-slate-200 p-4 ${compact ? "sm:grid-cols-[minmax(0,1fr)_10rem]" : "sm:grid-cols-[minmax(0,1fr)_10rem_8rem]"}`}>
          <label className="flex flex-col gap-1 text-sm">Condición
            {opportunitySourcePointers.includes(criterion.source_pointer) && <span className="text-xs text-teal-700">Oportunidad observable</span>}
            <input className="input" value={criterion.description} readOnly={opportunitySourcePointers.includes(criterion.source_pointer)} onChange={(event) => changeCriterion(index, { description: event.target.value })} />
            {opportunitySourcePointers.includes(criterion.source_pointer) && <span className="text-xs text-slate-500">Para cambiar esta oportunidad o su evidencia, crea y aprueba una nueva versión del contexto comercial.</span>}
          </label>
          <label className="flex flex-col gap-1 text-sm">Tipo
            <select className="input" value={criterion.kind} onChange={(event) => {
              const kind = event.target.value as CriterionKind;
              changeCriterion(index, { kind, weight: kind === "exclusion" ? 0 : Math.max(1, criterion.weight), must_resolve: kind === "required" });
            }}>
              <option value="required">Indispensable</option><option value="preference">Preferencia</option>
              {!opportunitySourcePointers.includes(criterion.source_pointer) && <option value="exclusion">Exclusión</option>}
            </select>
          </label>
          {!compact && <label className="flex flex-col gap-1 text-sm">{`Peso: ${criterion.description}`}
            <input className="input" aria-label={`Peso: ${criterion.description}`} type="number" min={criterion.kind === "exclusion" ? 0 : 1} max={100} value={criterion.weight} onChange={(event) => changeCriterion(index, { weight: Number(event.target.value) })} disabled={criterion.kind === "exclusion"} />
          </label>}
        </div>)}
      </fieldset>

      {!compact && <fieldset>
        <legend className="mb-2 font-semibold text-slate-900">Umbrales de aceptación</legend>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <NumberField label="Puntuación mínima" value={policy.accept_score} min={0} max={100} onChange={(value) => changePolicy({ ...policy, accept_score: value })} />
          <NumberField label="Cobertura mínima (%)" value={policy.min_coverage} min={0} max={100} onChange={(value) => changePolicy({ ...policy, min_coverage: value })} />
          <NumberField label="Confianza mínima (%)" value={Math.round(policy.choice_confidence_floor * 100)} min={0} max={100} onChange={(value) => changePolicy({ ...policy, choice_confidence_floor: value / 100 })} />
          <NumberField label="Confianza de identidad (%)" value={Math.round(policy.entity_confidence_floor * 100)} min={0} max={100} onChange={(value) => changePolicy({ ...policy, entity_confidence_floor: value / 100 })} />
          <NumberField label="Antigüedad máxima de evidencia (horas)" value={policy.max_evidence_age_hours} min={1} max={8760} onChange={(value) => changePolicy({ ...policy, max_evidence_age_hours: value })} />
        </div>
        <p className="mt-2 text-xs text-slate-500">Puntuación, cobertura, confianza e información de contacto son medidas distintas. La puntuación no predice compra.</p>
      </fieldset>}

      <div className="flex flex-wrap gap-3">
        {approved && <button type="button" className="btn-secondary" disabled={busy} onClick={() => void propose()}>Actualizar propuesta</button>}
        {!approved && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={confirmed} disabled={busy || !commercialDigest} onChange={(event) => setConfirmed(event.target.checked)} />Revisé los criterios y el digest comercial mostrado.</label>}
        <button type="button" className="btn-primary" disabled={busy || !commercialDigest || approved || !confirmed} onClick={() => void save()}>{busy ? "Guardando…" : "Aprobar condiciones"}</button>
      </div>
      <p className="break-all text-xs text-slate-500">Digest comercial revisado: {commercialDigest || "Propuesta vencida; actualiza la vista previa."}</p>
    </>}
  </section>;
}

function NumberField({ label, value, min, max, onChange }: { label: string; value: number; min: number; max: number; onChange: (value: number) => void }) {
  return <label className="flex flex-col gap-1 text-sm">{label}<input className="input" type="number" min={min} max={max} value={value} onChange={(event) => onChange(Number(event.target.value))} /></label>;
}
