import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../../lib/api";
import { FitResultPanel } from "./FitResultPanel";
import { ResearchPreviewDialog } from "./ResearchPreviewDialog";
import { createEvaluation, getCapabilities, getEvaluationContext, getFitEvidence, getLatestEvaluation, getResearchStatus, getSetterReadiness, previewResearch, type FitEvidenceSource } from "./api";
import type { FitEvaluationContext, FitEvaluationRead, FitResearchGrantRead, FitResearchPlan, SetterDraftReadinessRead } from "./types";

export type LeadFitCapability = "loading" | "enabled" | "disabled" | "unavailable";

interface LeadFitPanelProps {
  leadId: string;
  onCapabilityChange?: (capability: LeadFitCapability) => void;
  onSetterEligibilityChange?: (eligible: boolean) => void;
  onSetterReadinessChange?: (readiness: SetterDraftReadinessRead | null) => void;
}

const isTerminalEvaluation = (value: FitEvaluationRead | null) => !value || ["completed", "failed"].includes(value.status);
const isTerminalGrant = (value: FitResearchGrantRead | null) => !value || ["completed", "failed", "exhausted"].includes(value.status);
const evaluationStorageKey = (leadId: string, digest: string) => `lead-fit:intent:${leadId}:${digest}`;
const readIntentKey = (leadId: string, digest: string) => {
  try { return window.sessionStorage.getItem(evaluationStorageKey(leadId, digest)); } catch { return null; }
};
const saveIntentKey = (leadId: string, digest: string, key: string) => {
  try { window.sessionStorage.setItem(evaluationStorageKey(leadId, digest), key); } catch { /* In-memory key remains stable for this page session. */ }
};

function isFitAwareSetterEligible({
  leadId, capability, context, evaluation, activeGrant, busy, authorityRefreshing, researchAuthorizing, error,
}: {
  leadId: string;
  capability: LeadFitCapability;
  context: FitEvaluationContext | null;
  evaluation: FitEvaluationRead | null;
  activeGrant: FitResearchGrantRead | null;
  busy: boolean;
  authorityRefreshing: boolean;
  researchAuthorizing: boolean;
  error: string;
}): boolean {
  if (capability !== "enabled" || busy || authorityRefreshing || researchAuthorizing || error || !context
    || !evaluation || evaluation.status !== "completed" || evaluation.classification !== "qualified"
    || evaluation.lead_id !== leadId || evaluation.policy_id !== context.policy_id
    || evaluation.policy_digest !== context.policy_digest
    || evaluation.source_digest !== context.expected_source_digest
    || activeGrant?.status === "failed"
    || (activeGrant && ["pending", "authorized", "running"].includes(activeGrant.status))) return false;
  const result = evaluation.result;
  if (!result || result.classification !== "qualified" || result.contact_ready !== true
    || result.policy_digest !== context.policy_digest
    || result.source_digest !== context.expected_source_digest) return false;
  const readiness = result.contact_readiness;
  if (!readiness || typeof readiness !== "object") return false;
  const channels = readiness as Record<string, unknown>;
  return channels.email === true || channels.phone === true;
}

export function LeadFitPanel({ leadId, onCapabilityChange, onSetterEligibilityChange, onSetterReadinessChange }: LeadFitPanelProps) {
  const [capability, setCapability] = useState<LeadFitCapability>("loading");
  const [context, setContext] = useState<FitEvaluationContext | null>(null);
  const [authorityLeadId, setAuthorityLeadId] = useState<string | null>(null);
  const [evaluation, setEvaluation] = useState<FitEvaluationRead | null>(null);
  const [setterReadiness, setSetterReadiness] = useState<SetterDraftReadinessRead | null>(null);
  const [sources, setSources] = useState<Array<FitEvidenceSource & { safe_url: string | null }>>([]);
  const [activeGrant, setActiveGrant] = useState<FitResearchGrantRead | null>(null);
  const [plan, setPlan] = useState<FitResearchPlan | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [authorityRefreshing, setAuthorityRefreshing] = useState(false);
  const [researchAuthorizing, setResearchAuthorizing] = useState(false);
  const [error, setError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const evaluationKey = useRef<{ digest: string; key: string } | null>(null);
  const retryKey = useRef<string | null>(null);
  const readRequestId = useRef(0);
  const currentLeadId = useRef(leadId);
  const mounted = useRef(false);
  currentLeadId.current = leadId;
  const setterEligible = isFitAwareSetterEligible({
    leadId, capability, context, evaluation, activeGrant, busy, authorityRefreshing, researchAuthorizing, error,
  });
  const contextDrift = !!context && !!evaluation && (
    evaluation.policy_id !== context.policy_id
    || evaluation.policy_digest !== context.policy_digest
    || evaluation.source_digest !== context.expected_source_digest
  );
  const authorityState: "loading" | "ready" | "unavailable" =
    authorityRefreshing || capability === "loading" || authorityLeadId !== leadId ? "loading"
      : capability === "enabled" ? "ready" : "unavailable";

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      readRequestId.current += 1;
    };
  }, []);

  useEffect(() => {
    onSetterEligibilityChange?.(setterEligible);
  }, [onSetterEligibilityChange, setterEligible]);

  useEffect(() => {
    const current = setterReadiness && setterReadiness.lead_id === leadId
      && !error && !authorityRefreshing && capability === "enabled"
      && setterReadiness.evaluation_id === evaluation?.id
      && setterReadiness.policy_digest === context?.policy_digest
      && setterReadiness.source_digest === context?.expected_source_digest
      && (!activeGrant || !["pending", "authorized", "running"].includes(activeGrant.status))
      ? setterReadiness : null;
    onSetterReadinessChange?.(current);
  }, [activeGrant, authorityRefreshing, capability, context, error, evaluation, leadId, onSetterReadinessChange, setterReadiness]);

  const announce = useCallback((value: LeadFitCapability) => {
    setCapability(value);
    onCapabilityChange?.(value);
  }, [onCapabilityChange]);

  const load = useCallback(async () => {
    if (!mounted.current || currentLeadId.current !== leadId) return;
    const requestId = ++readRequestId.current;
    const requestLeadId = leadId;
    const isCurrent = () => mounted.current
      && currentLeadId.current === requestLeadId
      && readRequestId.current === requestId;
    onSetterEligibilityChange?.(false);
    setAuthorityRefreshing(true);
    try {
      const capabilityResult: unknown = await getCapabilities();
      if (!isCurrent()) return;
      if (!capabilityResult || typeof capabilityResult !== "object" || typeof (capabilityResult as { enabled?: unknown }).enabled !== "boolean") {
        announce("unavailable");
        return;
      }
      if ((capabilityResult as { enabled: boolean }).enabled === false) {
        announce("disabled");
        return;
      }
      announce("enabled");
      const [nextContext, latest, readiness] = await Promise.all([
        getEvaluationContext(leadId), getLatestEvaluation(leadId),
        (typeof getSetterReadiness === "function" ? getSetterReadiness(leadId) : Promise.resolve(null))
          .catch(() => null),
      ]);
      if (!isCurrent()) return;
      if (!nextContext.policy_id || !nextContext.policy_digest || !nextContext.expected_source_digest) {
        throw new Error("No hay una política aprobada vigente para este prospecto.");
      }
      const nextSources = nextContext.research_id
        ? await getFitEvidence(leadId, nextContext.research_id).catch(() => [])
        : [];
      if (!isCurrent()) return;
      const digest = `${nextContext.policy_digest}:${nextContext.expected_source_digest}`;
      if (evaluationKey.current && evaluationKey.current.digest !== digest) retryKey.current = null;
      const savedKey = readIntentKey(leadId, digest);
      evaluationKey.current = savedKey ? { digest, key: savedKey } : null;
      setAuthorityLeadId(requestLeadId);
      setContext(nextContext);
      setEvaluation(latest);
      setSetterReadiness(readiness && readiness.lead_id === leadId
        && readiness.evaluation_id === latest?.id
        && readiness.policy_digest === nextContext.policy_digest
        && readiness.source_digest === nextContext.expected_source_digest
        ? readiness : null);
      setSources(nextSources);
      setError("");
    } catch (err) {
      if (!isCurrent()) return;
      setAuthorityLeadId(null);
      setContext(null);
      setError(err instanceof ApiError && err.status === 409
        ? "No hay una política aprobada vigente. Revisa las condiciones comerciales antes de evaluar."
        : "No se pudo consultar la autoridad de encaje. Actualiza la página e inténtalo de nuevo.");
      announce("unavailable");
    } finally {
      if (isCurrent()) setAuthorityRefreshing(false);
    }
  }, [announce, leadId, onSetterEligibilityChange]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (busy || error || capability !== "enabled") setSetterReadiness(null);
  }, [busy, capability, error]);

  const refreshEvaluation = useCallback(async () => {
    if (!mounted.current || currentLeadId.current !== leadId) return;
    const requestId = ++readRequestId.current;
    const requestLeadId = leadId;
    const isCurrent = () => mounted.current
      && currentLeadId.current === requestLeadId
      && readRequestId.current === requestId;
    setAuthorityRefreshing(true);
    onSetterEligibilityChange?.(false);
    try {
      const [latest, currentContext, readiness] = await Promise.all([
        getLatestEvaluation(leadId), getEvaluationContext(leadId),
        getSetterReadiness(leadId).catch(() => null),
      ]);
      if (!isCurrent()) return;
      setAuthorityLeadId(requestLeadId);
      setEvaluation(latest);
      setContext(currentContext.policy_id && currentContext.policy_digest && currentContext.expected_source_digest ? currentContext : null);
      setSetterReadiness(readiness && readiness.evaluation_id === latest?.id
        && readiness.policy_digest === currentContext.policy_digest
        && readiness.source_digest === currentContext.expected_source_digest ? readiness : null);
      setError("");
      if (isTerminalEvaluation(latest)) setRefreshKey((value) => value + 1);
    } catch {
      if (!isCurrent()) return;
      setContext(null);
      setError("No se pudo confirmar la vigencia de la evaluación. Actualiza el resultado antes de usar Setter.");
    } finally {
      if (isCurrent()) setAuthorityRefreshing(false);
    }
  }, [leadId, onSetterEligibilityChange]);

  useEffect(() => {
    if (!evaluation || evaluation.lead_id !== leadId || isTerminalEvaluation(evaluation)) return;
    const timer = window.setInterval(() => { void refreshEvaluation(); }, 2000);
    return () => window.clearInterval(timer);
  }, [evaluation, leadId, refreshEvaluation]);

  const refreshCurrentAuthority = useCallback(async () => {
    if (!mounted.current || currentLeadId.current !== leadId) return;
    const requestId = ++readRequestId.current;
    const requestLeadId = leadId;
    const isCurrent = () => mounted.current
      && currentLeadId.current === requestLeadId
      && readRequestId.current === requestId;
    setAuthorityRefreshing(true);
    onSetterEligibilityChange?.(false);
    try {
      const [currentContext, latest, readiness] = await Promise.all([
        getEvaluationContext(leadId), getLatestEvaluation(leadId),
        getSetterReadiness(leadId).catch(() => null),
      ]);
      if (!isCurrent()) return;
      setAuthorityLeadId(requestLeadId);
      setContext(currentContext.policy_id && currentContext.policy_digest && currentContext.expected_source_digest ? currentContext : null);
      setEvaluation(latest);
      setSetterReadiness(readiness && readiness.evaluation_id === latest?.id
        && readiness.policy_digest === currentContext.policy_digest
        && readiness.source_digest === currentContext.expected_source_digest ? readiness : null);
      setError("");
    } catch {
      if (!isCurrent()) return;
      setContext(null);
      setError("No se pudo confirmar la vigencia de la evaluación. Actualiza el resultado antes de usar Setter.");
    } finally {
      if (isCurrent()) setAuthorityRefreshing(false);
    }
  }, [leadId, onSetterEligibilityChange]);

  useEffect(() => {
    if (!setterEligible) return;
    const timer = window.setInterval(() => { void refreshCurrentAuthority(); }, 15000);
    return () => window.clearInterval(timer);
  }, [setterEligible, refreshCurrentAuthority]);

  const requestEvaluation = async (explicitRetry = false) => {
    if (authorityLeadId !== leadId || authorityRefreshing || !context?.policy_digest || !context.expected_source_digest) return;
    onSetterEligibilityChange?.(false);
    setSetterReadiness(null);
    setBusy(true);
    setError("");
    const digest = `${context.policy_digest}:${context.expected_source_digest}`;
    const enqueueRetry = explicitRetry && evaluation?.error_code === "enqueue_failed";
    const providerRetry = explicitRetry && evaluation?.status === "failed" && !enqueueRetry;
    if (providerRetry && !retryKey.current) {
      retryKey.current = globalThis.crypto.randomUUID();
      saveIntentKey(leadId, digest, retryKey.current);
    }
    if (!enqueueRetry && (providerRetry || evaluation?.status === "completed" || evaluation?.status === "failed"
      || !evaluationKey.current || evaluationKey.current.digest !== digest)) {
      evaluationKey.current = { digest, key: retryKey.current ?? globalThis.crypto.randomUUID() };
      saveIntentKey(leadId, digest, evaluationKey.current.key);
    }
    if (enqueueRetry && (!evaluationKey.current || evaluationKey.current.digest !== digest)) {
      setError("No se conserva la clave original de este intento. Actualiza el contexto para iniciar una solicitud nueva.");
      setBusy(false);
      return;
    }
    if (!evaluationKey.current) {
      setError("No se pudo preparar la clave de esta evaluación. Actualiza el contexto antes de continuar.");
      setBusy(false);
      return;
    }
    const actionKey = retryKey.current ?? evaluationKey.current.key;
    try {
      const result = await createEvaluation({
        lead_id: leadId,
        expected_policy_digest: context.policy_digest,
        expected_source_digest: context.expected_source_digest,
        idempotency_key: actionKey,
        retry_of: explicitRetry && evaluation?.status === "failed" && !enqueueRetry ? evaluation.id : null,
        reenqueue: enqueueRetry,
      });
      setEvaluation(result);
      const readiness = await getSetterReadiness(leadId).catch(() => null);
      if (mounted.current && currentLeadId.current === leadId) {
        setSetterReadiness(readiness && readiness.evaluation_id === result.id
          && readiness.policy_digest === context.policy_digest
          && readiness.source_digest === context.expected_source_digest ? readiness : null);
      }
      retryKey.current = null;
      evaluationKey.current = { digest, key: actionKey };
      setRefreshKey((value) => value + 1);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        evaluationKey.current = null;
        retryKey.current = null;
        setContext(null);
        setError("La política o los datos del prospecto cambiaron. Actualiza el contexto antes de evaluar.");
      } else {
        setError("No se pudo iniciar la evaluación. Revisa su estado antes de volver a intentarlo.");
      }
    } finally { setBusy(false); }
  };

  const requestResearchPreview = async () => {
    onSetterEligibilityChange?.(false);
    setSetterReadiness(null);
    setBusy(true);
    setError("");
    try {
      const next = await previewResearch({ lead_id: leadId, purpose: "fit" });
      setPlan(next);
      setDialogOpen(true);
    } catch (err) {
      setError(err instanceof ApiError && err.status === 409
        ? "La evaluación ya no permite esta investigación. Actualiza el resultado y vuelve a revisar."
        : "No se pudo crear la vista previa. No se autorizó ninguna llamada; puedes volver a intentarlo.");
    } finally { setBusy(false); }
  };

  useEffect(() => {
    if (!activeGrant || isTerminalGrant(activeGrant)) return;
    const poll = async () => {
      try {
        const next = await getResearchStatus(leadId, activeGrant.id);
        if (next) {
          if (isTerminalGrant(next)) {
            onSetterEligibilityChange?.(false);
            setAuthorityRefreshing(true);
          }
          setActiveGrant(next);
          if (isTerminalGrant(next)) {
            try {
              const [latest, currentContext, readiness] = await Promise.all([
                getLatestEvaluation(leadId), getEvaluationContext(leadId),
                getSetterReadiness(leadId).catch(() => null),
              ]);
              setEvaluation(latest);
              setContext(currentContext.policy_id && currentContext.policy_digest && currentContext.expected_source_digest ? currentContext : null);
              setSetterReadiness(readiness && readiness.evaluation_id === latest?.id
                && readiness.policy_digest === currentContext.policy_digest
                && readiness.source_digest === currentContext.expected_source_digest ? readiness : null);
              if (latest?.research_id) setSources(await getFitEvidence(leadId, latest.research_id).catch(() => []));
              setRefreshKey((value) => value + 1);
            } finally {
              setAuthorityRefreshing(false);
            }
          }
        }
      } catch {
        setContext(null);
        setError("No se pudo consultar el estado de investigación. Actualiza el resultado antes de tomar otra acción.");
      }
    };
    const timer = window.setInterval(() => { void poll(); }, 2000);
    return () => window.clearInterval(timer);
  }, [activeGrant, leadId, onSetterEligibilityChange]);

  if (capability === "loading") return <section className="card p-4 text-sm text-slate-500">Consultando disponibilidad de evaluación de encaje…</section>;
  if (capability === "disabled") return null;
  if (capability !== "enabled") return <section className="card p-4 text-sm text-slate-700">La evaluación de encaje no está disponible. Las acciones de prospección y Setter permanecen deshabilitadas hasta confirmar la capacidad del servidor.</section>;

  return <section className="space-y-4" aria-labelledby="lead-fit-title">
    <div className="card space-y-3">
      <div><p className="eyebrow">Prospección revisada</p><h3 id="lead-fit-title" className="mt-1 text-lg font-bold text-slate-900">Evaluación de encaje</h3><p className="text-sm text-slate-600">Se evalúa con las condiciones aprobadas y la evidencia actual. Encaje no significa intención de compra.</p></div>
      {error && <p role="alert" className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900">{error}</p>}
      {(!context || contextDrift) && <button type="button" className="btn-secondary" onClick={() => void load()}>Actualizar autoridad</button>}
      {context && authorityLeadId === leadId && <button type="button" className="btn-primary" disabled={busy || authorityRefreshing || (!!evaluation && !isTerminalEvaluation(evaluation))} onClick={() => void requestEvaluation(!!evaluation)}>
        {busy ? "Enviando evaluación…" : evaluation?.status === "failed" ? "Reintentar evaluación" : evaluation ? "Evaluar de nuevo" : "Evaluar encaje"}
      </button>}
      {activeGrant && <div role="status" className="rounded-lg border border-slate-200 p-3 text-sm">
        <p className="font-medium">Investigación: {activeGrant.status === "authorized" || activeGrant.status === "running" ? "en curso" : activeGrant.status === "failed" ? "falló" : activeGrant.status === "exhausted" ? "llegó al límite aprobado" : "completada"}</p>
        {(activeGrant.status === "authorized" || activeGrant.status === "running") && <p>El resultado mostrado corresponde a la evaluación previa. Se actualizará cuando termine la investigación.</p>}
        {activeGrant.status === "failed" && <p role="alert">La investigación falló. Revisa la propuesta antes de autorizar otra acción.</p>}
      </div>}
    </div>
    <FitResultPanel
      key={refreshKey}
      leadId={leadId}
      authorityState={authorityState}
      evaluation={evaluation}
      context={context}
      sources={sources}
      onResearch={requestResearchPreview}
    />
    {evaluation?.status === "failed" && evaluation.error_code === "enqueue_failed" && <p className="text-xs text-slate-600">La tarea no llegó a la cola. Usa «Reintentar evaluación» para volver a encolar este mismo intento.</p>}
    <ResearchPreviewDialog open={dialogOpen} leadId={leadId} plan={plan} onClose={() => setDialogOpen(false)} onRefreshPreview={() => void requestResearchPreview()} onBusyChange={setResearchAuthorizing} onAuthorizationError={() => setError("La autorización de investigación falló. Revisa su estado antes de usar Setter.")} onAuthorized={(grant) => {
      setActiveGrant(grant as unknown as FitResearchGrantRead);
      setDialogOpen(false);
    }} />
  </section>;
}
