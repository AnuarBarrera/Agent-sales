import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch } from "../../lib/api";
import { getSetterReadiness } from "../leadFit/api";
import type { SetterDraftReadinessRead } from "../leadFit/types";
import { getDraftReview, type DemoDraftReview } from "./draftReview";

interface DraftExecution { id: string; touch1_draft: { channel: string } | null }

export function DemoNextStep({ leadId, contacted = false, nextFollowUpAt = null }: { leadId: string; contacted?: boolean; nextFollowUpAt?: string | null }) {
  const [readiness, setReadiness] = useState<SetterDraftReadinessRead | null>(null);
  const [draft, setDraft] = useState<DraftExecution | null>(null);
  const [review, setReview] = useState<DemoDraftReview | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [channel, setChannel] = useState<"email" | "phone">("email");
  const requestKey = useRef<string | null>(null);
  const inFlight = useRef(false);

  useEffect(() => {
    if (contacted) { setLoading(false); return; }
    let active = true;
    setLoading(true); setError(""); setDraft(null); setReview(null); setReadiness(null);
    void (async () => {
      try {
        const existing = await apiFetch<DraftExecution | null>(`/leads/${leadId}/setter/latest`);
        if (!active) return;
        if (existing?.touch1_draft) {
          setDraft(existing);
          const nextReview = await getDraftReview(leadId, existing.id);
          if (active) setReview(nextReview);
        } else {
          const nextReadiness = await getSetterReadiness(leadId);
          if (active) { setReadiness(nextReadiness); setChannel(nextReadiness.contact_channels[0] ?? "email"); }
        }
      } catch (reason) { if (active) setError(reason instanceof Error ? reason.message : "No se pudo consultar el siguiente paso."); }
      finally { if (active) setLoading(false); }
    })();
    return () => { active = false; };
  }, [leadId, contacted]);

  const prepare = async () => {
    if (!readiness?.draft_ready || !readiness.contact_channels.includes(channel) || inFlight.current) return;
    inFlight.current = true; setBusy(true); setError("");
    try {
      requestKey.current ??= globalThis.crypto.randomUUID();
      const next = await apiFetch<DraftExecution>(`/demo/leads/${leadId}/draft`, {
        method: "POST", headers: { "Idempotency-Key": requestKey.current }, body: JSON.stringify({ channel }),
      });
      setDraft(next);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "No se pudo preparar el borrador."); }
    finally { inFlight.current = false; setBusy(false); }
  };

  if (contacted) return <div className="min-w-44 space-y-2 text-left">
    <p className="text-xs font-semibold text-teal-800">Contacto registrado · en seguimiento</p>
    <p className="text-xs text-slate-600">{nextFollowUpAt ? `Próximo seguimiento: ${new Date(nextFollowUpAt).toLocaleString("es-MX")}` : "Sin seguimiento programado"}</p>
    <Link className="btn-secondary" to={`/leads/${leadId}#lead-follow-up`}>Ver seguimiento</Link>
  </div>;

  return <div className="min-w-44 space-y-2 text-left">
    {error && <p role="alert" className="text-xs text-red-700">{error}</p>}
    {nextFollowUpAt && <p className="text-xs text-slate-600">Próximo seguimiento: {new Date(nextFollowUpAt).toLocaleString("es-MX")}</p>}
    {loading ? <p className="text-xs text-slate-500">Consultando siguiente paso…</p> : draft?.touch1_draft ? <>
      <p className="text-xs font-semibold text-teal-800">{review?.status === "contact_recorded" ? "Contacto registrado · en seguimiento" : review?.status === "approved_for_manual_use" ? "Aprobado · listo para contacto manual" : review?.status === "blocked" ? "Revisión bloqueada" : "Borrador pendiente de aprobación"}</p>
      {review?.reason && <p className="text-xs text-amber-800">{review.reason}</p>}
      <Link className="btn-secondary" to={`/leads/${leadId}`}>{review?.status === "contact_recorded" ? "Ver seguimiento" : review?.status === "approved_for_manual_use" ? "Ver siguiente paso" : "Revisar y aprobar borrador"}</Link>
    </> : readiness?.draft_ready ? <>
      <p className="text-xs text-slate-600">Prepara el borrador para poder revisarlo y aprobarlo.</p>
      <label className="block text-xs text-slate-600">Canal<select className="input mt-1 w-full" value={channel} disabled={busy} onChange={(event) => { setChannel(event.target.value as "email" | "phone"); requestKey.current = null; }}>
        {readiness.contact_channels.map((value) => <option key={value} value={value}>{value === "email" ? "Correo" : "Pitch de llamada"}</option>)}
      </select></label>
      <button className="btn-primary" type="button" disabled={busy} onClick={() => void prepare()}>{busy ? "Preparando…" : "Preparar borrador"}</button>
    </> : readiness ? <p className="text-xs text-amber-800">{readiness.reason_codes.map(readinessReasonLabel).join(". ") || "Revisa los requisitos del prospecto."}</p> : null}
    <Link to={`/leads/${leadId}`} className="inline-block text-sm text-blue-700 underline">Abrir prospecto</Link>
  </div>;
}

function readinessReasonLabel(reason: string): string {
  const labels: Record<string, string> = {
    authority_unavailable: "Falta un contexto comercial aprobado y vigente",
    evaluation_unavailable: "La evaluación todavía no está disponible",
    evaluation_stale: "La evaluación necesita actualizarse",
    research_active: "La investigación sigue en curso",
    identity_unresolved: "Falta confirmar la identidad del negocio",
    excluded: "Las condiciones revisadas excluyen este negocio",
    required_unresolved: "Falta evidencia para una condición indispensable",
    required_failed: "No cumple una condición indispensable",
    contact_unavailable: "Falta un canal de contacto verificable y autorizado",
  };
  return labels[reason] ?? "Revisa los requisitos del prospecto";
}
