import { useEffect, useRef, useState } from "react";
import { approveDraft, getDraftReview, type DemoDraftReview } from "./draftReview";

export function DemoDraftReviewPanel({ leadId, executionId, message, cta, onApprovalChange }: {
  leadId: string; executionId: string; message: string; cta: string | null;
  onApprovalChange: (executionId: string, approved: boolean) => void;
}) {
  const [review, setReview] = useState<DemoDraftReview | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const inFlight = useRef(false);
  const activeExecution = useRef(executionId);
  activeExecution.current = executionId;

  useEffect(() => {
    let active = true;
    setReview(null); setAcknowledged(false); setError(""); setCopied(false);
    onApprovalChange(executionId, false);
    void getDraftReview(leadId, executionId).then((next) => {
      if (!active) return;
      setReview(next); onApprovalChange(executionId, (next.status === "approved_for_manual_use" || next.status === "contact_recorded"));
    }).catch((reason: unknown) => { if (active) setError(reason instanceof Error ? reason.message : "No se pudo consultar la revisión."); });
    return () => { active = false; };
  }, [leadId, executionId, onApprovalChange]);

  const approve = async () => {
    if (!review || !acknowledged || inFlight.current || review.status !== "pending_human_review") return;
    inFlight.current = true; setBusy(true); setError("");
    try {
      const next = await approveDraft(leadId, review);
      if (activeExecution.current !== executionId) return;
      setReview(next); onApprovalChange(executionId, (next.status === "approved_for_manual_use" || next.status === "contact_recorded"));
    } catch (reason) {
      if (activeExecution.current === executionId) {
        setError(reason instanceof Error ? reason.message : "No se pudo aprobar el borrador.");
        setAcknowledged(false);
      }
    } finally { inFlight.current = false; setBusy(false); }
  };

  const copy = async () => {
    setError("");
    try { await navigator.clipboard.writeText([message, cta].filter(Boolean).join("\n\n")); setCopied(true); }
    catch { setError("No se pudo copiar. Puedes seleccionar y copiar el texto del borrador."); }
  };

  return <section className="card space-y-3" aria-labelledby="draft-review-heading">
    <h3 id="draft-review-heading" className="font-semibold text-slate-900">Revisión y siguiente paso</h3>
    {error && <div className="space-y-2"><p role="alert" className="text-sm text-red-700">{error}</p><button type="button" className="btn-secondary" onClick={() => window.location.reload()}>Actualizar ficha</button></div>}
    {!review && !error && <p role="status" className="text-sm text-slate-600">Consultando revisión del borrador…</p>}
    {review?.status === "blocked" && <p className="text-sm text-amber-800">{review.reason}</p>}
    {review?.status === "pending_human_review" && <>
      <p className="text-sm text-slate-600">Revisa el texto, sus fuentes y las advertencias antes de aprobarlo para el contacto manual.</p>
      <label className="flex items-start gap-2 text-sm text-slate-700"><input type="checkbox" checked={acknowledged} disabled={busy} onChange={(event) => setAcknowledged(event.target.checked)} />He revisado el borrador, sus fuentes y las advertencias.</label>
      <button type="button" className="btn-primary" disabled={!acknowledged || busy} onClick={() => void approve()}>{busy ? "Aprobando…" : "Aprobar borrador y continuar"}</button>
    </>}
    {review?.status === "approved_for_manual_use" && <>
      <p className="font-semibold text-teal-800" role="status">Aprobado · listo para contacto manual</p>
      {review.approved_at && <p className="text-xs text-slate-500">Aprobado el {new Date(review.approved_at).toLocaleString("es-MX")}</p>}
      <p className="text-sm text-slate-600">El siguiente paso es usar este texto al contactar personalmente al negocio.</p>
      <button type="button" className="btn-secondary" onClick={() => void copy()}>{copied ? "Texto copiado" : "Copiar borrador aprobado"}</button>
    </>}
    {review?.status === "contact_recorded" && <>
      <p className="font-semibold text-teal-800">Contacto registrado · aprobación conservada en el historial</p>
      <a href="#lead-follow-up" className="text-sm text-blue-700 underline">Ver seguimiento del lead</a>
    </>}
    <p className="text-xs text-slate-500">La demo registra la aprobación. El envío o la llamada se realizan manualmente.</p>
  </section>;
}
