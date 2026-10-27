import { useEffect, useRef, useState } from "react";
import { ApiError } from "../../lib/api";
import { authorizeResearch } from "./api";
import type { FitResearchPlan } from "./types";

interface ResearchPreviewDialogProps {
  open: boolean;
  onClose: () => void;
  onAuthorized: (grant: Record<string, unknown>) => void;
  onRefreshPreview: () => void;
  onBusyChange: (busy: boolean) => void;
  onAuthorizationError: () => void;
  leadId: string;
  plan?: FitResearchPlan | null;
}

export function ResearchPreviewDialog({ open, onClose, onAuthorized, onRefreshPreview, onBusyChange, onAuthorizationError, leadId, plan }: ResearchPreviewDialogProps) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const key = useRef<{ planHash: string; value: string } | null>(null);

  useEffect(() => {
    if (!plan?.plan_hash) { key.current = null; return; }
    if (key.current?.planHash !== plan.plan_hash) {
      key.current = { planHash: plan.plan_hash, value: globalThis.crypto.randomUUID() };
      setError("");
    }
  }, [plan?.plan_hash]);

  const authorize = async () => {
    if (!plan?.plan_hash || !key.current || key.current.planHash !== plan.plan_hash) return;
    setBusy(true);
    onBusyChange(true);
    setError("");
    try {
      const grant = await authorizeResearch({
        lead_id: leadId,
        evaluation_id: plan.evaluation_id,
        purpose: plan.purpose,
        expected_plan_hash: plan.plan_hash,
        confirmed: true,
        idempotency_key: key.current.value,
        monetary_cap_usd: plan.monetary_cap_usd,
      });
      onAuthorized(grant as Record<string, unknown>);
      onClose();
    } catch (err) {
      onAuthorizationError();
      if (err instanceof ApiError && err.status === 409) {
        key.current = null;
        setError("La vista previa cambió. Actualízala y confirma de nuevo para continuar.");
      } else {
        setError("No se pudo confirmar la investigación. Revisa el estado antes de repetir la acción.");
      }
  } finally { setBusy(false); onBusyChange(false); }
  };

  if (!open) return null;
  return <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-labelledby="research-dialog-title">
    <button type="button" aria-label="Cerrar vista previa" className="fixed inset-0 bg-black/40" onClick={onClose} />
    <section className="relative z-10 w-full max-w-lg space-y-5 rounded-xl bg-white p-6 shadow-xl">
      <div><p className="eyebrow">Investigación incremental</p><h3 id="research-dialog-title" className="mt-1 text-lg font-bold text-slate-900">Revisar investigación adicional</h3>
        <p className="mt-1 text-sm text-slate-600">Abrir, cerrar o actualizar esta vista previa no autoriza llamadas.</p></div>
      {plan ? <>
        <div className="rounded-lg border border-slate-200 p-4"><p className="text-xs text-slate-500">Propósito</p><p className="font-medium text-slate-900">{plan.purpose === "fit" ? "Resolver criterios de encaje" : "Comprobar disponibilidad de contacto"}</p><p className="mt-2 text-sm text-slate-700">{plan.query}</p>
          <p className="mt-2 text-xs text-slate-500">Criterios: {plan.missing_criterion_ids.join(", ") || "Disponibilidad de contacto"}</p></div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">{[["Búsquedas", plan.max_search], ["Extracciones", plan.max_extract], ["Preguntas Jev", plan.max_jev], ["Rondas", plan.max_rounds]].map(([label, value]) => <div className="rounded-lg border border-slate-200 p-3" key={String(label)}><p className="text-xs text-slate-500">{label}</p><p className="font-semibold">{value}</p></div>)}</div>
        <p className="break-all text-xs text-slate-500">Hash de la propuesta: {plan.plan_hash}</p>
        {plan.monetary_cap_usd !== null && <p className="text-sm text-slate-700">Cap de gasto propuesto: USD {plan.monetary_cap_usd}</p>}
      </> : <p className="text-sm text-slate-600">No hay una vista previa vigente. Cárgala antes de confirmar.</p>}
      {error && <p role="alert" className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900">{error}</p>}
      <div className="flex flex-wrap justify-end gap-3">
        {error && <button type="button" className="btn-secondary" disabled={busy} onClick={onRefreshPreview}>Actualizar vista previa</button>}
        <button type="button" className="btn-secondary" disabled={busy} onClick={onClose}>Cancelar</button>
        <button type="button" className="btn-primary" disabled={busy || !plan?.plan_hash} onClick={() => void authorize()}>{busy ? "Autorizando…" : "Confirmar investigación"}</button>
      </div>
      <p className="text-xs text-slate-500">Un error no reintenta automáticamente ni crea otro permiso. La confirmación queda vinculada al hash mostrado.</p>
    </section>
  </div>;
}
