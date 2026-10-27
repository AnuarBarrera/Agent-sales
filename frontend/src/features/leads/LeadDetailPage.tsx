import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { apiFetch } from "../../lib/api";
import { LeadFitPanel, type LeadFitCapability } from "../leadFit";
import type { SetterDraftReadinessRead } from "../leadFit/types";
import { useOptionalAuth } from "../auth/useAuth";
import { getSetterReadiness } from "../leadFit/api";
import { FitResultPanel } from "../leadFit/FitResultPanel";
import { getEvaluationContext, getFitEvidence, getLatestEvaluation } from "../leadFit/api";
import type { FitEvidenceSource } from "../leadFit/api";
import type { FitEvaluationContext, FitEvaluationRead } from "../leadFit/types";
import { DemoDraftReviewPanel } from "../demo/DemoDraftReviewPanel";
import { DemoFollowUpPanel } from "../demo/DemoFollowUpPanel";

interface Lead {
  id: string; profile: string; validation_status: string; score: number | null; score_reason: string | null;
  priority: number; next_follow_up_at: string | null; setter_name: string | null; closer_name: string | null;
  pipeline_stage_name: string | null; giro: string | null; zona: string | null; web_status: string | null;
  web_check_detail: string | null; google_presence: string | null; meta_ads_active: boolean | null;
  contacted: boolean; source_searched_at: string | null; outreach_message: string | null;
  business: { name: string; address: string | null; phone: string | null; website: string | null; rating: number | null; reviews_total: number | null };
}
interface Activity { id: string; type: string; content: string; created_at: string }
interface ResearchState { id: string; status: "pending" | "running" | "completed" | "failed"; error: string | null; result_count: number; extracted_count: number; requested_at: string; completed_at: string | null; total_credits: number | null }
interface Evidence { evidence_id: string; source_url: string; source_title: string | null; source_type: string; excerpt: string; retrieved_at: string; relevance_score: number | null }
interface Observation { statement: string; evidence_ids: string[] }
interface Hypothesis { statement: string; confidence: number; supporting_evidence_ids: string[] }
interface Intelligence { summary: string; observations: Observation[]; hypotheses: Hypothesis[]; missing_information: string[] }
interface Qualification { decision: "qualified" | "not_qualified" | "needs_more_research"; score: number | null; confidence: number; positive_signals: string[]; negative_signals: string[]; recommended_angle: string | null; rationale: string; evidence_ids: string[] }
interface AnalysisState { id: string; status: "completed" | "failed"; error: string | null; intelligence: Intelligence | null; qualification: Qualification | null; intelligence_model: string | null; qualification_model: string | null; intelligence_prompt_version: string | null; qualification_prompt_version: string | null; intelligence_tokens: number | null; qualification_tokens: number | null; intelligence_latency_ms: number | null; qualification_latency_ms: number | null }
type SetterAction = "draft_touch_1" | "do_not_contact" | "needs_human_review";
interface SetterClaim { claim: string; evidence_ids: string[] }
interface SetterOutput { action: SetterAction; rationale: string; stage: "touch_1"; channel: string; evidence_ids: string[]; guardrail_ids: string[]; assertion_ids: string[]; message: string | null; cta: string | null; personalization_claims: SetterClaim[] | null }
interface SetterDraft extends SetterOutput { review_status: "pending_human_review" }
interface SetterExecutionRead {
  id: string; workspace_id: string; lead_id: string; created_at: string;
  decision: { action: SetterAction; draft_purpose?: "discovery" | "opportunity_grounded" | null; evidence_ids?: string[]; rationale: string; research_id: string; playbook_version: string; playbook_digest: string; output: SetterOutput };
  touch1_draft: SetterDraft | null; validation_status: "passed" | "review_required" | "blocked";
  validation_findings: { code: string; message: string; severity: "warning" | "error" }[];
  provider: string | null; model: string | null; review_status: "pending_human_review";
}

export function LeadDetailPage() {
  const { demo } = useOptionalAuth();
  const demoEnabled = !!demo?.enabled;
  const { leadId } = useParams<{ leadId: string }>();
  const [lead, setLead] = useState<Lead | null>(null);
  const [timeline, setTimeline] = useState<Activity[]>([]);
  const [research, setResearch] = useState<ResearchState | null>(null);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [analysis, setAnalysis] = useState<AnalysisState | null>(null);
  const [setterExecution, setSetterExecution] = useState<SetterExecutionRead | null>(null);
  const [setterEvidence, setSetterEvidence] = useState<Evidence[] | null>(null);
  const [setterLoaded, setSetterLoaded] = useState(false);
  const [setterLoadError, setSetterLoadError] = useState("");
  const [setterError, setSetterError] = useState("");
  const [setterBusy, setSetterBusy] = useState(false);
  const [draftChannel, setDraftChannel] = useState<"email" | "phone">("email");
  const [approvedExecutionId, setApprovedExecutionId] = useState<string | null>(null);
  const onApprovalChange = useCallback((executionId: string, approved: boolean) => {
    setApprovedExecutionId(approved ? executionId : null);
  }, []);
  const draftKey = useRef<string | null>(null);
  const [comment, setComment] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [fitCapability, setFitCapability] = useState<LeadFitCapability>("loading");
  const [fitSetterState, setFitSetterState] = useState<{ leadId: string; readiness: SetterDraftReadinessRead | null }>({ leadId: "", readiness: null });
  const [demoFit, setDemoFit] = useState<{ status: "loading" | "ready" | "unavailable"; context: FitEvaluationContext | null; evaluation: FitEvaluationRead | null; sources: Array<FitEvidenceSource & { safe_url: string | null }> }>({ status: "loading", context: null, evaluation: null, sources: [] });
  const updateFitSetterReadiness = useCallback((readiness: SetterDraftReadinessRead | null) => {
    setFitSetterState({ leadId: leadId || "", readiness });
  }, [leadId]);

  const load = useCallback(async () => {
    if (!leadId) return;
    try {
      if (demoEnabled) {
        const [leadResult, contextResult, evaluationResult] = await Promise.allSettled([
          apiFetch<Lead>(`/leads/${leadId}`), getEvaluationContext(leadId), getLatestEvaluation(leadId),
        ]);
        if (leadResult.status === "rejected") throw leadResult.reason;
        setLead(leadResult.value);
        const context = contextResult.status === "fulfilled" ? contextResult.value : null;
        const evaluation = evaluationResult.status === "fulfilled" ? evaluationResult.value : null;
        const sources = context?.research_id ? await getFitEvidence(leadId, context.research_id).catch(() => []) : [];
        setDemoFit({ status: contextResult.status === "fulfilled" ? "ready" : "unavailable", context, evaluation, sources });
        setEvidence(sources.map((item) => ({ evidence_id: item.evidence_id, source_url: item.safe_url || "", source_title: item.source_title, source_type: item.source_type, excerpt: item.excerpt, retrieved_at: "", relevance_score: null })));
        setTimeline([]); setResearch(null); setAnalysis(null); setError(""); return;
      }
      const [leadData, timelineData, researchData, evidenceData, analysisData] = await Promise.all([
        apiFetch<Lead>(`/leads/${leadId}`),
        apiFetch<{ items: Activity[] }>(`/activities/leads/${leadId}/timeline`),
        apiFetch<ResearchState | null>(`/leads/${leadId}/research`),
        apiFetch<Evidence[]>(`/leads/${leadId}/research/evidence`),
        apiFetch<AnalysisState | null>(`/leads/${leadId}/analysis`),
      ]);
      setLead(leadData); setTimeline(timelineData.items); setResearch(researchData); setEvidence(evidenceData); setAnalysis(analysisData);
    } catch (err) { setError(err instanceof Error ? err.message : "No se pudo cargar el lead"); }
  }, [leadId, demoEnabled]);

  const loadSetterEvidence = useCallback(async (execution: SetterExecutionRead | null) => {
    if (!leadId || !execution) { setSetterEvidence(null); return; }
    try {
      const rows = await apiFetch<Evidence[]>(`/leads/${leadId}/setter/executions/${encodeURIComponent(execution.id)}/evidence`);
      setSetterEvidence(rows); setSetterLoadError("");
    } catch (err) {
      setSetterEvidence([]);
      setSetterLoadError(err instanceof Error ? err.message : "No se pudo cargar la evidencia usada por Setter");
    }
  }, [leadId]);

  const loadLatestSetter = useCallback(async () => {
    if (!leadId) return;
    try {
      const latest = await apiFetch<SetterExecutionRead | null>(`/leads/${leadId}/setter/latest`);
      setSetterExecution(latest); setSetterLoadError("");
      await loadSetterEvidence(latest);
    } catch (err) {
      setSetterExecution(null);
      setSetterLoadError(err instanceof Error ? err.message : "No se pudo consultar Setter");
    } finally { setSetterLoaded(true); }
  }, [leadId, loadSetterEvidence]);

  useEffect(() => { void load(); void loadLatestSetter(); }, [load, loadLatestSetter]);
  useEffect(() => {
    if (!demoEnabled || !leadId) return;
    let active = true;
    void getSetterReadiness(leadId).then((readiness) => {
      if (!active) return;
      setFitCapability("enabled"); setFitSetterState({ leadId, readiness });
      if (readiness.contact_channels.length === 1) setDraftChannel(readiness.contact_channels[0]);
      else setDraftChannel("email");
    }).catch((reason: unknown) => {
      if (active) { setFitCapability("unavailable"); setSetterLoadError(reason instanceof Error ? reason.message : "No se pudo consultar la preparación del borrador."); }
    });
    return () => { active = false; };
  }, [demoEnabled, leadId]);
  useEffect(() => {
    if (!research || (research.status !== "pending" && research.status !== "running")) return;
    const timer = window.setInterval(() => { void load(); }, 2500);
    return () => window.clearInterval(timer);
  }, [research, load]);

  const runAnalysis = async (reanalyze = false) => {
    if (!leadId) return;
    setBusy(true); setError("");
    try {
      const next = await apiFetch<AnalysisState>(`/leads/${leadId}/analysis${reanalyze ? "/reanalyze" : ""}`, { method: "POST" });
      setAnalysis(next);
      await load();
    } catch (err) { setError(err instanceof Error ? err.message : "No se pudo analizar el prospecto"); }
    finally { setBusy(false); }
  };

  const startResearch = async (refresh = false) => {
    if (!leadId) return;
    setBusy(true); setError("");
    try {
      const next = await apiFetch<ResearchState>(`/leads/${leadId}/research${refresh ? "/refresh" : ""}`, { method: "POST" });
      setResearch(next); await load();
    } catch (err) { setError(err instanceof Error ? err.message : "No se pudo iniciar la investigación"); }
    finally { setBusy(false); }
  };

  const addComment = async (event: React.FormEvent) => {
    event.preventDefault(); if (!leadId || !comment.trim()) return;
    try { await apiFetch("/activities", { method: "POST", body: JSON.stringify({ lead_id: leadId, type: "note", content: comment.trim() }) }); setComment(""); await load(); }
    catch (err) { setError(err instanceof Error ? err.message : "No se pudo guardar el comentario"); }
  };

  const executeSetter = async () => {
    if (!leadId) return;
    setSetterBusy(true); setSetterError("");
    try {
      draftKey.current ??= globalThis.crypto.randomUUID();
      const result = await apiFetch<SetterExecutionRead>(demoEnabled ? `/demo/leads/${leadId}/draft` : `/leads/${leadId}/setter/execute`, {
        method: "POST",
        ...(demoEnabled ? { headers: { "Idempotency-Key": draftKey.current }, body: JSON.stringify({ channel: draftChannel }) } : {}),
      });
      draftKey.current = null;
      setSetterExecution(result); setSetterLoadError(""); setSetterLoaded(true);
      await loadSetterEvidence(result);
    } catch (err) { setSetterError(err instanceof Error ? err.message : "No se pudo generar el borrador Setter"); }
    finally { setSetterBusy(false); }
  };

  if (!lead) return <div className="card">{error || "Cargando lead…"}</div>;
  const researchLabel = research?.status === "completed" ? "Completada" : research?.status === "running" ? "En curso" : research?.status === "pending" ? "En cola" : research?.status === "failed" ? "Falló" : "Sin investigar";
  const analysisLabel = analysis?.status === "completed" ? "Completado" : analysis?.status === "failed" ? "Falló" : "Sin analizar";
  return <div className="mx-auto max-w-5xl space-y-6"><div><Link className="text-sm text-blue-700" to="/leads">← Volver a leads</Link><h2 className="mt-2 text-2xl font-bold text-gray-900">{lead.business.name}</h2><p className="text-gray-600">{lead.business.address || "Sin dirección"}</p></div>{error && <p role="alert" className="text-red-600">{error}</p>}<div className="grid gap-6 lg:grid-cols-[1fr_1.4fr]"><section className="card space-y-4">{!demoEnabled && <div className="flex flex-wrap gap-2"><span className="badge">{lead.profile}</span><span className="badge">{lead.pipeline_stage_name || "Nuevo"}</span></div>}<Info label="Teléfono" value={lead.business.phone || "No disponible"} /><Info label="Sitio web" value={lead.business.website || "No disponible"} />{!demoEnabled && <><Info label="Validación" value={lead.validation_status} /><Info label="Setter" value={lead.setter_name || "Sin asignar"} /><Info label="Closer" value={lead.closer_name || "Sin asignar"} /><Info label="Puntuación" value={lead.score === null ? "Pendiente" : `${lead.score}/100`} />{lead.score_reason && <p className="text-sm text-gray-600">{lead.score_reason}</p>}<div className="border-t border-slate-100 pt-4"><p className="eyebrow">Datos importados</p><div className="mt-3 grid gap-3 sm:grid-cols-2"><Info label="Giro" value={lead.giro || "No disponible"} /><Info label="Zona" value={lead.zona || "No disponible"} /><Info label="Estado web" value={lead.web_status || "No validado"} /><Info label="Presencia Google" value={lead.google_presence || "No validado"} /><Info label="Meta Ads" value={lead.meta_ads_active === null ? "Sin dato" : lead.meta_ads_active ? "Activos" : "No detectados"} /><Info label="Contacto" value={lead.contacted ? "Contactado" : "Pendiente"} /></div></div>{lead.outreach_message && <div className="border-t border-slate-100 pt-4"><p className="eyebrow">Mensaje frío</p><p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">{lead.outreach_message}</p></div>}</>}</section><div className="space-y-6">
    {demoEnabled ? <FitResultPanel leadId={leadId || ""} authorityState={demoFit.status} evaluation={demoFit.evaluation} context={demoFit.context} sources={demoFit.sources} /> : <LeadFitPanel leadId={leadId || ""} onCapabilityChange={setFitCapability} onSetterReadinessChange={updateFitSetterReadiness} />}
    {demoEnabled && fitSetterState.leadId === leadId && fitSetterState.readiness?.contact_channels.length === 2 && !setterExecution?.touch1_draft && <fieldset className="card"><legend className="font-semibold">Elige el canal para este único borrador</legend><div className="mt-3 flex gap-5"><label className="flex items-center gap-2 text-sm"><input type="radio" name="draft-channel" value="email" checked={draftChannel === "email"} onChange={() => setDraftChannel("email")} />Email (recomendado)</label><label className="flex items-center gap-2 text-sm"><input type="radio" name="draft-channel" value="phone" checked={draftChannel === "phone"} onChange={() => setDraftChannel("phone")} />Pitch de llamada</label></div></fieldset>}
    {demoEnabled && <SetterPanel contactRecorded={lead.contacted} hideExecution={!!setterExecution?.touch1_draft || lead.contacted} approvedForManualUse={!!setterExecution && approvedExecutionId === setterExecution.id} execution={setterExecution} loaded={setterLoaded} loadError={setterLoadError} error={setterError} busy={setterBusy} readiness={fitSetterState.leadId === leadId ? fitSetterState.readiness : null} disabled={fitSetterState.leadId !== leadId || !fitSetterState.readiness?.draft_ready || !!setterExecution?.touch1_draft} evidence={setterExecution ? (setterEvidence ?? []) : evidence} onExecute={() => void executeSetter()} />}
    {demoEnabled && setterExecution?.touch1_draft?.message && <DemoDraftReviewPanel leadId={leadId || ""} executionId={setterExecution.id} message={setterExecution.touch1_draft.message} cta={setterExecution.touch1_draft.cta} onApprovalChange={onApprovalChange} />}
    {demoEnabled && <DemoFollowUpPanel key={leadId} leadId={leadId || ""} onContactRecorded={() => void load()} />}
    {!demoEnabled && fitCapability === "disabled" && <><ResearchPanel research={research} evidence={evidence} statusLabel={researchLabel} busy={busy} onResearch={() => void startResearch(false)} onRefresh={() => void startResearch(true)} /><AnalysisPanel analysis={analysis} evidence={evidence} statusLabel={analysisLabel} busy={busy} onAnalyze={() => void runAnalysis(false)} onReanalyze={() => void runAnalysis(true)} /><SetterPanel execution={setterExecution} loaded={setterLoaded} loadError={setterLoadError} error={setterError} busy={setterBusy} evidence={setterExecution ? (setterEvidence ?? []) : evidence} onExecute={() => void executeSetter()} /> </>}
    {!demoEnabled && fitCapability === "enabled" && <SetterPanel execution={setterExecution} loaded={setterLoaded} loadError={setterLoadError} error={setterError} busy={setterBusy} readiness={fitSetterState.leadId === leadId ? fitSetterState.readiness : null} disabled={fitSetterState.leadId !== leadId || !fitSetterState.readiness?.draft_ready} evidence={setterExecution ? (setterEvidence ?? []) : evidence} onExecute={() => void executeSetter()} />}
    {!demoEnabled && <section className="card"><h3 className="font-semibold text-gray-900">Línea de tiempo</h3><form onSubmit={addComment} className="mt-4 flex gap-2"><input aria-label="Nuevo comentario" className="input flex-1" value={comment} onChange={(event) => setComment(event.target.value)} placeholder="Agregar comentario o nota" /><button className="btn-primary" type="submit">Guardar</button></form><ol className="mt-6 space-y-4">{timeline.map((item) => <li key={item.id} className="border-l-2 border-gray-200 pl-3"><p className="text-xs uppercase tracking-wide text-gray-500">{item.type}</p><p className="text-sm text-gray-800">{item.content}</p><time className="text-xs text-gray-500">{new Date(item.created_at).toLocaleString("es-MX")}</time></li>)}{timeline.length === 0 && <li className="text-sm text-gray-500">Todavía no hay actividad registrada.</li>}</ol></section>}</div></div></div>;
}

function ResearchPanel({ research, evidence, statusLabel, busy, onResearch, onRefresh }: { research: ResearchState | null; evidence: Evidence[]; statusLabel: string; busy: boolean; onResearch: () => void; onRefresh: () => void }) { return <section className="card overflow-hidden p-0"><div className="border-b border-slate-100 bg-slate-50/80 p-5"><div className="flex items-start justify-between gap-4"><div><p className="eyebrow">Investigación pública</p><h3 className="mt-1 text-lg font-bold text-slate-900">Fuentes verificables del prospecto</h3><p className="mt-1 text-sm text-slate-500">Tavily aporta fuentes; no son conclusiones de IA.</p></div><span className={`status status-${research?.status || "idle"}`}>{statusLabel}</span></div><div className="mt-4 flex flex-wrap items-center gap-3"><button className="btn-primary" disabled={busy || research?.status === "pending" || research?.status === "running"} onClick={research ? onRefresh : onResearch}>{busy ? "Iniciando…" : research ? "Actualizar investigación" : "Investigar con Tavily"}</button>{research?.total_credits !== null && research?.total_credits !== undefined && <span className="text-xs text-slate-500">Créditos: {research.total_credits}</span>}</div>{research?.status === "failed" && <p role="alert" className="mt-3 rounded-xl bg-red-50 px-3 py-2 text-sm text-red-700">{research.error || "La investigación no pudo completarse."}</p>}</div><div className="divide-y divide-slate-100">{evidence.map((item) => <EvidenceCard item={item} key={item.evidence_id} />)}{evidence.length === 0 && <div className="p-5 text-sm text-slate-500">{research?.status === "completed" ? "No se encontraron fuentes seleccionadas." : "Inicia una investigación para consultar fuentes públicas trazables."}</div>}</div></section>; }

function AnalysisPanel({ analysis, evidence, statusLabel, busy, onAnalyze, onReanalyze }: { analysis: AnalysisState | null; evidence: Evidence[]; statusLabel: string; busy: boolean; onAnalyze: () => void; onReanalyze: () => void }) { const lookup = new Map(evidence.map((item) => [item.evidence_id, item])); return <section className="card overflow-hidden p-0"><div className="border-b border-slate-100 bg-slate-50/80 p-5"><div className="flex items-start justify-between gap-4"><div><p className="eyebrow">Inteligencia explicable</p><h3 className="mt-1 text-lg font-bold text-slate-900">Interpretación y calificación</h3><p className="mt-1 text-sm text-slate-500">Nemotron interpreta las fuentes; no las reemplaza.</p></div><span className={`status status-${analysis?.status || "idle"}`}>{statusLabel}</span></div><div className="mt-4"><button className="btn-primary" disabled={busy} onClick={analysis ? onReanalyze : onAnalyze}>{busy ? "Analizando…" : analysis ? "Reanalizar prospecto" : "Analizar prospecto"}</button></div>{analysis?.status === "failed" && <p role="alert" className="mt-3 rounded-xl bg-red-50 px-3 py-2 text-sm text-red-700">{analysis.error || "El análisis no pudo completarse."}</p>}</div>{analysis?.intelligence && <div className="space-y-5 p-5"><div><p className="eyebrow">Resumen</p><p className="mt-2 text-sm leading-6 text-slate-700">{analysis.intelligence.summary}</p></div><section><h4 className="font-semibold text-slate-900">Observaciones con fuentes</h4><p className="mt-1 text-xs text-slate-500">Las citas permiten revisar el análisis; no garantizan que cada afirmación sea correcta.</p><div className="mt-3 space-y-3">{analysis.intelligence.observations.map((item, index) => <Insight text={item.statement} evidenceIds={item.evidence_ids} lookup={lookup} key={`observation-${index}`} />)}{analysis.intelligence.observations.length === 0 && <p className="text-sm text-slate-500">No hay observaciones con evidencia.</p>}</div></section><section><div className="flex items-center gap-2"><h4 className="font-semibold text-slate-900">Hipótesis</h4><span className="rounded-full bg-amber-100 px-2 py-1 text-[11px] font-bold text-amber-800">No verificadas</span></div><div className="mt-3 space-y-3">{analysis.intelligence.hypotheses.map((item, index) => <div className="rounded-xl border border-amber-200 bg-amber-50/60 p-3" key={`hypothesis-${index}`}><p className="text-sm leading-6 text-slate-700">{item.statement}</p><p className="mt-2 text-xs font-semibold text-amber-800">Confianza: {Math.round(item.confidence * 100)}%</p><EvidenceLinks ids={item.supporting_evidence_ids} lookup={lookup} /></div>)}{analysis.intelligence.hypotheses.length === 0 && <p className="text-sm text-slate-500">No hay hipótesis registradas.</p>}</div></section><section><h4 className="font-semibold text-slate-900">Información faltante</h4><ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-600">{analysis.intelligence.missing_information.map((item) => <li key={item}>{item}</li>)}{analysis.intelligence.missing_information.length === 0 && <li className="list-none pl-0">No se identificaron vacíos críticos.</li>}</ul></section>{analysis.qualification && <QualificationPanel qualification={analysis.qualification} lookup={lookup} />}</div>}{!analysis?.intelligence && !analysis?.error && <div className="p-5 text-sm text-slate-500">Ejecuta el análisis manualmente cuando exista un playbook aprobado y evidencia exitosa.</div>}</section>; }

function SetterPanel({ execution, loaded, loadError, error, busy, disabled = false, readiness = null, evidence, onExecute, approvedForManualUse = false, hideExecution = false, contactRecorded = false }: {
  execution: SetterExecutionRead | null; loaded: boolean; loadError: string; error: string;
  approvedForManualUse?: boolean; hideExecution?: boolean; contactRecorded?: boolean;
  busy: boolean; disabled?: boolean; readiness?: SetterDraftReadinessRead | null; evidence: Evidence[]; onExecute: () => void;
}) {
  const lookup = new Map(evidence.map((item) => [item.evidence_id, item]));
  const output = execution?.decision.output;
  const draft = execution?.touch1_draft;
  const callPitchReady = readiness?.draft_ready && readiness.contact_channels[0] === "phone";
  const actionLabel = contactRecorded ? "Contacto registrado" : !execution
    ? loaded ? "Sin propuesta" : "Consultando"
    : draft ? approvedForManualUse ? "Listo para contacto manual" : "Pendiente de revisión"
      : execution.decision.action === "do_not_contact" ? "No contactar" : "Requiere revisión humana";

  return <section className="card overflow-hidden p-0" aria-labelledby="setter-heading">
    <div className="border-b border-slate-100 bg-slate-50/80 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="eyebrow">Primer contacto</p>
          <h3 id="setter-heading" className="mt-1 text-lg font-bold text-slate-900">Setter · primer contacto</h3>
          <p className="mt-1 text-sm text-slate-500">El playbook aprobado guía la propuesta; una persona decide qué hacer después.</p>
        </div>
        <span className={`status ${execution?.validation_status === "review_required" ? "bg-amber-100 text-amber-800" : "status-idle"}`}>
          {actionLabel}
        </span>
      </div>
      {!hideExecution && <button className="btn-primary mt-4" type="button" disabled={busy || disabled} onClick={onExecute}>
        {busy ? "Preparando borrador…" : callPitchReady ? "Preparar pitch de llamada" : readiness?.draft_purpose === "discovery" ? "Preparar pregunta de discovery" : "Generar borrador Touch 1"}
      </button>}
      {readiness?.draft_purpose === "discovery" && <p className="mt-2 text-sm text-slate-600">La oportunidad aún no está confirmada. Este borrador pregunta, no afirma una necesidad.</p>}
      {!hideExecution && disabled && readiness?.reason_codes.includes("contact_unavailable") && <p className="mt-2 text-sm text-slate-600">Falta un correo o teléfono atribuible y permitido por el playbook.</p>}
      {!hideExecution && disabled && readiness && !readiness.reason_codes.includes("contact_unavailable") && <p className="mt-2 text-sm text-slate-600">La preparación está bloqueada: {readiness.reason_codes.join(", ") || "autoridad o identidad no disponible"}.</p>}
      <p className="mt-2 text-xs text-slate-500">No se enviará ni programará ningún mensaje desde aquí.</p>
      {loadError && <p role="alert" className="mt-3 rounded-xl bg-amber-50 px-3 py-2 text-sm text-amber-800">{loadError}</p>}
      {error && <p role="alert" className="mt-3 rounded-xl bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
    </div>
    {!execution && !loadError && <p className="p-5 text-sm text-slate-500">{loaded ? "Todavía no hay una propuesta de Setter para este prospecto." : "Consultando el último resultado Setter…"}</p>}
    {execution && output && <div className="space-y-4 p-5">
      {draft ? <article className="rounded-r-xl border-l-4 border-teal-700 bg-teal-50/70 px-4 py-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-sm font-bold text-teal-800">{contactRecorded ? "Borrador histórico · contacto registrado" : approvedForManualUse ? "Borrador aprobado · listo para contacto manual" : draft.channel === "phone" ? "Pitch de llamada · pendiente de revisión" : "Borrador · pendiente de revisión"}</span>
          <span className="badge">{draft.channel === "phone" ? "Llamada" : draft.channel === "email" ? "Email" : draft.channel}</span>
        </div>
        {execution.decision.draft_purpose === "discovery" && <p className="mt-3 text-sm font-semibold text-amber-800">La oportunidad aún no está confirmada. Este borrador pregunta, no afirma una necesidad.</p>}
        <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-slate-800">{draft.message}</p>
        {draft.cta && <p className="mt-3 text-sm font-semibold text-slate-800">{draft.cta}</p>}
        <p className="mt-3 text-xs text-slate-500">Decisión: {output.rationale}</p>
      </article> : <div className="rounded-xl border border-slate-200 bg-white p-4">
        <p className="font-semibold text-slate-900">{actionLabel}</p>
        <p className="mt-1 text-sm leading-6 text-slate-600">{output.rationale}</p>
      </div>}

      {draft?.personalization_claims && draft.personalization_claims.length > 0 && <section>
        <h4 className="text-sm font-semibold text-slate-900">Base de personalización</h4>
        <div className="mt-2 space-y-2">{draft.personalization_claims.map((claim, index) => <div className="rounded-lg bg-slate-50 p-3" key={`${index}-${claim.claim}`}>
          <p className="text-sm leading-6 text-slate-700">{claim.claim}</p>
          <EvidenceLinks ids={claim.evidence_ids} lookup={lookup} />
        </div>)}</div>
      </section>}

      {execution.decision.evidence_ids && execution.decision.evidence_ids.length > 0 && <section>
        <h4 className="text-sm font-semibold text-slate-900">Fuentes de identidad y contacto</h4>
        <p className="mt-1 text-xs text-slate-500">Fuentes citadas por esta ejecución; no confirman una oportunidad comercial.</p>
        <EvidenceLinks ids={execution.decision.evidence_ids} lookup={lookup} />
      </section>}

      <section className="border-t border-slate-100 pt-4">
        <h4 className="text-sm font-semibold text-slate-900">Autoridad del playbook</h4>
        <p className="mt-1 text-xs text-slate-500" title={execution.decision.playbook_digest}>
          Versión {execution.decision.playbook_version} · digest {execution.decision.playbook_digest.slice(0, 10)}
        </p>
        {output.guardrail_ids.length > 0 && <div className="mt-3">
          <p className="text-xs font-semibold text-slate-600">Guardrails referenciados</p>
          <div className="mt-1 flex flex-wrap gap-2">{output.guardrail_ids.map((id) => <span className="badge" key={id}>{id.replace(/_/g, " ")}</span>)}</div>
        </div>}
        {output.assertion_ids.length > 0 && <div className="mt-3">
          <p className="text-xs font-semibold text-slate-600">Políticas revisadas</p>
          <div className="mt-1 flex flex-wrap gap-2">{output.assertion_ids.map((id) => <span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-700" key={id}>{id}</span>)}</div>
        </div>}
      </section>

      {execution.validation_findings.length > 0 && <section className="rounded-lg border border-amber-200 bg-amber-50/70 p-3">
        <h4 className="text-sm font-semibold text-amber-900">{approvedForManualUse ? "Advertencias revisadas" : "Revisión necesaria"}</h4>
        <ul className="mt-2 space-y-1 text-sm text-amber-900">{execution.validation_findings.map((finding, index) => <li key={`${finding.code}-${index}`}>{finding.message}</li>)}</ul>
      </section>}
      <p className="text-xs text-slate-500">Validación: {setterValidationLabel(execution.validation_status)} · {contactRecorded ? "contacto registrado" : approvedForManualUse ? "aprobado para contacto manual" : setterReviewLabel(execution.review_status)}</p>
    </div>}
  </section>;
}

function setterValidationLabel(status: SetterExecutionRead["validation_status"]): string {
  if (status === "review_required") return "requiere revisión humana";
  if (status === "blocked") return "bloqueado";
  return "validación completada";
}

function setterReviewLabel(status: SetterExecutionRead["review_status"]): string {
  return status === "pending_human_review" ? "pendiente de revisión" : status;
}

function QualificationPanel({ qualification, lookup }: { qualification: Qualification; lookup: Map<string, Evidence> }) { const label = qualification.decision === "qualified" ? "Calificado" : qualification.decision === "not_qualified" ? "No calificado" : "Requiere más investigación"; return <section className="border-t border-slate-100 pt-5"><div className="flex flex-wrap items-center gap-2"><h4 className="font-semibold text-slate-900">Calificación</h4><span className="badge">{label}</span><span className="text-xs text-slate-500">Confianza: {Math.round(qualification.confidence * 100)}%</span></div>{qualification.score !== null && <p className="mt-2 text-sm text-slate-600">Score estructurado: {qualification.score}/100</p>}<div className="mt-3 grid gap-4 sm:grid-cols-2"><Signal title="Señales positivas" items={qualification.positive_signals} tone="positive" /><Signal title="Señales negativas" items={qualification.negative_signals} tone="negative" /></div>{qualification.recommended_angle && <div className="mt-4 rounded-xl bg-teal-50 p-3"><p className="text-xs font-bold uppercase tracking-wide text-teal-700">Ángulo comercial recomendado</p><p className="mt-1 text-sm text-teal-950">{qualification.recommended_angle}</p></div>}<p className="mt-4 text-sm leading-6 text-slate-700">{qualification.rationale}</p><EvidenceLinks ids={qualification.evidence_ids} lookup={lookup} /></section>; }
function Signal({ title, items, tone }: { title: string; items: string[]; tone: "positive" | "negative" }) { return <div><p className={`text-xs font-bold uppercase tracking-wide ${tone === "positive" ? "text-emerald-700" : "text-rose-700"}`}>{title}</p><ul className="mt-2 list-disc space-y-1 pl-4 text-sm text-slate-600">{items.map((item) => <li key={item}>{item}</li>)}{items.length === 0 && <li className="list-none pl-0">Ninguna registrada.</li>}</ul></div>; }
function Insight({ text, evidenceIds, lookup }: { text: string; evidenceIds: string[]; lookup: Map<string, Evidence> }) { return <div className="rounded-xl border border-emerald-200 bg-emerald-50/60 p-3"><p className="text-sm leading-6 text-slate-700">{text}</p><EvidenceLinks ids={evidenceIds} lookup={lookup} /></div>; }
function EvidenceLinks({ ids, lookup }: { ids: string[]; lookup: Map<string, Evidence> }) { return <div className="mt-2 flex flex-wrap gap-2">{ids.map((id) => { const item = lookup.get(id); return item ? <a className="rounded-full bg-white px-2 py-1 text-[11px] font-semibold text-teal-700 ring-1 ring-teal-200 hover:bg-teal-50" href={item.source_url} target="_blank" rel="noreferrer" key={id}>Fuente: {item.source_title || item.source_url}</a> : <span className="rounded-full bg-red-50 px-2 py-1 text-[11px] font-semibold text-red-700" key={id}>Fuente no disponible</span>; })}</div>; }
function EvidenceCard({ item }: { item: Evidence }) { return <article className="p-5"><div className="flex items-start justify-between gap-3"><div><p className="font-semibold text-slate-900">{item.source_title || item.source_url}</p><a className="mt-1 block break-all text-xs text-teal-700 hover:underline" href={item.source_url} target="_blank" rel="noreferrer">{item.source_url}</a></div><span className="badge">{item.source_type.replace(/_/g, " ")}</span></div><p className="mt-3 line-clamp-4 text-sm leading-6 text-slate-600">{item.excerpt}</p><p className="mt-3 text-xs text-slate-400">Recuperado {new Date(item.retrieved_at).toLocaleString("es-MX")}</p></article>; }
function Info({ label, value }: { label: string; value: string }) { return <div><p className="text-xs uppercase tracking-wide text-gray-500">{label}</p><p className="text-gray-900">{value}</p></div>; }
