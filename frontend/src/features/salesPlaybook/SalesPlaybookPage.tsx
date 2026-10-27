import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { apiFetch } from "../../lib/api";
import { CommercialContextPanel } from "./CommercialContextPanel";
import { useOptionalAuth } from "../auth/useAuth";

type SourceMode = "questionnaire" | "document";
interface UploadedSource {
  id: string;
  original_filename: string;
  mime_type?: string;
  original_size_bytes: number;
  original_sha256?: string;
  normalized_sha256: string;
  normalization_status?: string;
  normalization_warnings: string[];
  created_at?: string;
  sections: { id: string; heading: string; heading_path: string[]; start_line: number; end_line: number; text: string }[];
}
interface SourceDocumentSelection {
  id: string;
  original_filename: string;
  mime_type: string;
  created_at: string;
  original_sha256: string;
  normalized_sha256: string;
  section_count: number;
  eligibility_status: "eligible" | "unavailable";
}
interface SourceDocumentReview extends UploadedSource { original_sha256: string; converter: string; converter_version: string }
type PlaybookStatus = "draft" | "approved" | "archived" | "rejected";
type RuleEffect = "must" | "must_not" | "should" | "may";
type GenerationJobStatus = "queued" | "running" | "succeeded" | "failed";
interface GenerationJob {
  id: string;
  status: GenerationJobStatus;
  phase: string;
  current_step: number;
  total_steps: number;
  progress_message: string;
  result_playbook_id: string | null;
  failure_code: string | null;
  failure_message: string | null;
}
const ACTIVE_GENERATION_STORAGE_KEY = "sales-playbook-active-generation-job";
const SOURCE_DOCUMENT_SELECTION_STORAGE_KEY = "sales-playbook-source-document-selection";

interface LegacyPlaybookContent {
  target_customer: { summary: string; market: string | null };
  offer: { summary: string; price_context: string | null };
  value_proposition: string[];
  pain_points: string[];
  qualification_positive_signals: string[];
  qualification_negative_signals: string[];
  setter_guidance: { opening_angle: string; discovery_questions: string[]; qualification_focus: string[] };
  closer_guidance: { conversation_goal: string; proof_points: string[]; recommended_next_step: string };
  avoid_rules: string[];
  handoff_signals: string[];
}

interface RuleProvenance {
  origin?: string | null;
  source_ref?: string | null;
}

interface Rule {
  code?: string | null;
  title: string;
  description: string;
  effect?: RuleEffect | null;
  condition?: string | null;
  maturity?: string | null;
  provenance?: RuleProvenance;
}

interface ExecutablePlaybookContent {
  contract_version: "2.0";
  schema_version: "2.0";
  strategy: {
    target_segments: { name: string; description: string; maturity: string }[];
    offer: { summary: string; price_context?: string | null };
    positioning: string[];
    commercial_goal: string;
    qualification_principles: Rule[];
  };
  prospecting: {
    sourcing_rules: Rule[];
    classification_rules: { profile: string; priority: number; angle: string; ads_state: string; web_state: string; marketplace_state: string; requires_active_investment_signal: boolean }[];
    prioritization_rules: Rule[];
  };
  workflow: {
    stages: { code: string; step_number: number; name: string; objective: string; actions: string[]; entry_conditions: string[]; exit_conditions: string[]; timing?: string | null; responsible_role?: string | null; maturity?: string | null; rules?: Rule[] }[];
    transitions: { from_stage: string; to_stage: string; condition: string; outcome: string; wait_hours?: number | null; description?: string | null; provenance?: RuleProvenance }[];
  };
  setter_policy: { objectives: string[]; allowed_actions: string[]; forbidden_rules: Rule[]; language_rules: Rule[]; handoff_conditions: { code: string; description: string }[]; responsibilities: string[] };
  closer_policy: { objectives: string[]; discovery_questions: { question: string; purpose: string }[]; fit_signals: Rule[]; no_fit_signals: Rule[]; objection_rules: Rule[]; closing_rules: Rule[]; responsibilities: string[] };
  channel_policies: { channel: string; max_new_messages_per_day?: number | null; sending_windows: { start: string; end: string }[]; minimum_delay_seconds?: number | null; max_followups_per_lead?: number | null; personalization_required: boolean; rules: Rule[] }[];
  templates: { id: string; purpose: string; stage: string; channel: string; scenario?: string | null; text: string; required_variables: string[]; maturity: string }[];
  experiments: { id: string; name: string; variable: string; variants: { id: string; description: string }[]; success_metric: string; maturity: string }[];
  pending_decisions: { code: string; question: string; options: string[]; maturity: string }[];
}

type PlaybookContent = LegacyPlaybookContent | ExecutablePlaybookContent | SemanticPlaybookContent;

interface Playbook {
  id: string;
  version: number;
  status: PlaybookStatus;
  source_type: SourceMode | "manual";
  structured_content: PlaybookContent;
  content_contract_version?: string | null;
  rejection_reason?: string | null;
  created_at: string;
  updated_at: string;
  approval_readiness?: ApprovalReadiness | null;
  content_digest?: string | null;
  source_payload?: { pasted_text?: string | null; source_document_manifest?: { id: string; filename: string; original_sha256: string; normalized_sha256: string }[] };
}

interface ApprovalReadiness {
  blockers: string[];
  required_guardrail_assertion_ids: string[];
  missing_required_transitions?: { condition: string }[];
  blocker_details?: { kind: string; identifier: string; message?: string | null }[];
  ready_for_approval?: boolean;
}

interface SemanticPolicyAssertion {
  assertion_id: string;
  assertion_text: string;
  modality: "required" | "prohibited" | "recommended" | "permitted";
  concept: string | null;
  scope_text: string | null;
  condition_text: string | null;
  maturity: string | null;
  maturity_origin: string | null;
  maturity_source_section_ids: string[];
  source_section_ids: string[];
}

interface CompiledPolicyRule {
  rule: Rule;
  source_assertion_ids: string[];
  source_section_ids: string[];
  maturity_source_section_ids: string[];
}

interface UnresolvedSemanticPolicy {
  assertions: SemanticPolicyAssertion[];
  status: string;
  reason: string;
  source_section_ids: string[];
}

interface SemanticCompilation {
  input_assertion_ids: string[];
  compiled_rules: CompiledPolicyRule[];
  unresolved: UnresolvedSemanticPolicy[];
}

interface CompiledRulePlacement {
  stage_code: string;
  source_assertion_ids: string[];
}

interface SemanticPlaybookPolicy {
  assertions: SemanticPolicyAssertion[];
  compilation: SemanticCompilation;
  placements: CompiledRulePlacement[];
  source_section_ids: string[];
  source_review_required: true;
  extraction_complete: boolean;
  extraction_truncated: boolean;
  reviewed_guardrail_assertion_ids: string[];
  rejected_assertion_ids: string[];
  safety_findings: string[];
  workflow_reviews?: { finding_id: string; action: string; reviewer_id: string; reviewed_at: string }[];
}

interface SemanticPlaybookContent {
  contract_version: "3.0";
  schema_version: "3.0";
  strategy: {
    target_segments: { name: string; description: string; maturity: string }[];
    offer: { summary: string; price_context?: string | null };
    positioning: string[];
    commercial_goal: string;
    qualification_principles: Rule[];
  };
  prospecting: {
    sourcing_rules: Rule[];
    classification_rules: { profile: string; priority: number; angle: string; ads_state: string; web_state: string; marketplace_state: string; requires_active_investment_signal: boolean }[];
    prioritization_rules: Rule[];
  };
  workflow: {
    stages: { code: string; step_number: number; name: string; objective: string; actions: string[]; entry_conditions: string[]; exit_conditions: string[]; timing?: string | null; responsible_role?: string | null; maturity?: string | null; rules?: Rule[] }[];
    transitions: { from_stage: string; to_stage: string; condition: string; outcome: string; wait_hours?: number | null; description?: string | null; maturity?: string; provenance?: RuleProvenance }[];
  };
  workflow_conflicts?: WorkflowStageConflict[];
  workflow_missing_required_conditions?: string[];
  setter_policy: { objectives: string[]; allowed_actions: string[]; forbidden_rules: Rule[]; language_rules: Rule[]; handoff_conditions: { code: string; description: string }[]; responsibilities: string[] };
  closer_policy: { objectives: string[]; discovery_questions: { question: string; purpose: string }[]; fit_signals: Rule[]; no_fit_signals: Rule[]; objection_rules: Rule[]; closing_rules: Rule[]; responsibilities: string[] };
  channel_policies: { channel: string; max_new_messages_per_day?: number | null; sending_windows: { start: string; end: string }[]; minimum_delay_seconds?: number | null; max_followups_per_lead?: number | null; personalization_required: boolean; rules: Rule[] }[];
  templates: { id: string; purpose: string; stage: string; channel: string; scenario?: string | null; text: string; required_variables: string[]; maturity: string }[];
  experiments: { id: string; name: string; variable: string; variants: { id: string; description: string }[]; success_metric: string; maturity: string }[];
  pending_decisions: { code: string; question: string; options: string[]; maturity: string }[];
  semantic_policy: SemanticPlaybookPolicy;
  source_documents?: { document_id: string; filename: string; original_sha256: string; normalized_sha256: string }[];
  source_section_map?: { extraction_section_id: string; document_id: string | null; original_section_id: string; filename: string; heading_path: string[]; start_line: number; end_line: number; fragment_id?: string | null; start_byte_offset?: number | null; end_byte_offset?: number | null }[];
  source_conflicts?: { conflict_id: string; field: string; claims: { origin: string; label: string; value: string; source_section_ids: string[] }[]; status: string }[];
  generic_workflow_graph?: {
    contract_version: string;
    nodes: { node_id: string; kind: string; label: string; source_section_ids: string[]; review_status: string }[];
    relations: {
      relation_id: string; primitive: string; from_node_id: string | null; to_node_id: string | null;
      assertion_ids: string[]; source_section_ids: string[]; guard_polarity: string; route_role: string;
      optionality: string; timing: { kind: string; minimum?: number | null; maximum?: number | null; unit?: string | null } | null;
      evidence_status: string; compilation_status: string; review_reason?: string | null; conflict_id?: string | null;
    }[];
    conflicts: { conflict_id: string; relation_ids: string[]; source_section_ids: string[]; summary: string; status: string }[];
    findings: { finding_id: string; kind: string; message: string; relation_ids: string[]; node_ids: string[]; source_section_ids: string[] }[];
    authority_status: string;
  } | null;
}

interface WorkflowStageConflict {
  conflict_id: string;
  stage_code: string;
  conflict_type: "stage" | "transition";
  conflicting_fields: string[];
  status: "needs_review" | "resolved";
  resolved_candidate_id?: string | null;
  candidates: {
    candidate_id: string;
    owner: string;
    source_section_ids: string[];
    stage?: {
      name: string;
      objective: string;
      actions: string[];
      entry_conditions: string[];
      exit_conditions: string[];
      timing?: string | null;
      responsible_role?: string | null;
      maturity?: string | null;
    } | null;
    transitions: { transition: { from_stage: string; to_stage: string; condition: string; outcome: string } }[];
  }[];
}

const questions = [
  ["offer_summary", "Qué vendes", "Describe tu producto o servicio principal"],
  ["target_customer", "Cliente ideal", "Qué tipo de cliente quieres atraer"],
  ["problem_solved", "Qué problema resuelves", "Qué situación mejoras o resuelves"],
  ["market_location", "Dónde vendes", "Zona, ciudad, país o mercado"],
  ["price_context", "Contexto de precio", "Rango, desde qué precio o cómo cotizas"],
  ["differentiators", "Qué te diferencia", "Razones para elegirte frente a otras opciones"],
  ["acquisition_method", "Cómo consigues clientes", "Canales y métodos actuales"],
  ["first_contact_outcome", "Resultado del primer contacto", "Qué quieres conseguir en la primera conversación"],
  ["prohibited_behavior", "Qué debes evitar", "Promesas, mensajes o comportamientos prohibidos"],
] as const;

type Questionnaire = Record<(typeof questions)[number][0], string>;

const emptyQuestionnaire = questions.reduce((answers, [key]) => ({ ...answers, [key]: "" }), {}) as Questionnaire;

function newIdempotencyKey(): string {
  return globalThis.crypto?.randomUUID?.()
    ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export function SalesPlaybookPage() {
  const { demo } = useOptionalAuth();
  const [advanced, setAdvanced] = useState(false);
  return <div className="space-y-5">
    {!demo?.enabled && <div className="flex justify-end"><button className="btn-secondary" onClick={() => setAdvanced((current) => !current)}>{advanced ? "Volver al contexto comercial" : "Abrir playbook avanzado"}</button></div>}
    {advanced && !demo?.enabled ? <AdvancedSalesPlaybookPage /> : <CommercialContextPanel demoMode={!!demo?.enabled} allowAssisted={!demo?.enabled} />}
  </div>;
}

export function AdvancedSalesPlaybookPage() {
  const [playbooks, setPlaybooks] = useState<Playbook[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [sourceMode, setSourceMode] = useState<SourceMode>("questionnaire");
  const [questionnaire, setQuestionnaire] = useState<Questionnaire>(emptyQuestionnaire);
  const [pastedText, setPastedText] = useState("");
  const [uploadedSources, setUploadedSources] = useState<UploadedSource[]>([]);
  const [sourceDocuments, setSourceDocuments] = useState<SourceDocumentSelection[]>([]);
  const [primarySourceId, setPrimarySourceId] = useState<string | null>(null);
  const [isLoadingSourceDocuments, setIsLoadingSourceDocuments] = useState(true);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [generationJob, setGenerationJob] = useState<GenerationJob | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [idempotencyKey, setIdempotencyKey] = useState<string>(newIdempotencyKey);
  const [reviewState, setReviewState] = useState<{ snapshotKey: string | null; assertionIds: string[] }>({
    snapshotKey: null,
    assertionIds: [],
  });
  const activeGenerationJobId = generationJob && ["queued", "running"].includes(generationJob.status)
    ? generationJob.id
    : null;

  useEffect(() => {
    void loadPlaybooks();
    void recoverActiveGeneration();
    void loadSourceDocuments();
  }, []);

  useEffect(() => {
    if (!activeGenerationJobId) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const current = await apiFetch<GenerationJob>(`/sales-playbooks/generation-jobs/${activeGenerationJobId}`);
        if (!active) return;
        if (current.status === "succeeded" && current.result_playbook_id) {
          const draft = await apiFetch<Playbook>(`/sales-playbooks/${current.result_playbook_id}`);
          if (!active) return;
          setPlaybooks((items) => [draft, ...items.filter((item) => item.id !== draft.id)]);
          setSelectedId(draft.id);
          setIdempotencyKey(newIdempotencyKey());
          window.sessionStorage.removeItem(ACTIVE_GENERATION_STORAGE_KEY);
          setGenerationJob(null);
          setMessage("Borrador generado. Revísalo antes de aprobarlo.");
          return;
        }
        setGenerationJob(current);
        if (current.status === "failed") {
          setError(current.failure_message || "No pudimos terminar el análisis de tu documentación.");
          return;
        }
        timer = setTimeout(() => { void poll(); }, 2000);
      } catch {
        if (!active) return;
        setError("No pudimos consultar el avance. Seguiremos intentando actualizar el estado.");
        timer = setTimeout(() => { void poll(); }, 5000);
      }
    };
    timer = setTimeout(() => { void poll(); }, 500);
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [activeGenerationJobId]);

  const selected = useMemo(
    () => playbooks.find((playbook) => playbook.id === selectedId) || playbooks[0] || null,
    [playbooks, selectedId],
  );
  const selectedSourceDocument = sourceDocuments.find((item) => item.id === primarySourceId) ?? null;
  const duplicateNormalizedHashes = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of sourceDocuments) counts.set(item.normalized_sha256, (counts.get(item.normalized_sha256) ?? 0) + 1);
    return new Set([...counts].filter(([, count]) => count > 1).map(([hash]) => hash));
  }, [sourceDocuments]);

  const reviewSnapshotKey = selected ? `${selected.id}:${selected.content_digest ?? ""}` : null;
  const reviewedGuardrailIds = reviewState.snapshotKey === reviewSnapshotKey ? reviewState.assertionIds : [];
  const isGenerating = generationJob?.status === "queued" || generationJob?.status === "running";

  async function loadPlaybooks() {
    setIsLoading(true);
    try {
      const result = (await apiFetch<Playbook[]>("/sales-playbooks")).filter((item) => item.content_contract_version !== "4.0");
      setPlaybooks(result);
      setSelectedId((current) => current && result.some((item) => item.id === current) ? current : result[0]?.id || null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudieron cargar los playbooks");
    } finally {
      setIsLoading(false);
    }
  }

  async function loadSourceDocuments() {
    setIsLoadingSourceDocuments(true);
    try {
      const documents = await apiFetch<SourceDocumentSelection[]>("/sales-playbooks/source-documents/selection");
      setSourceDocuments(documents);
      const savedId = window.sessionStorage.getItem(SOURCE_DOCUMENT_SELECTION_STORAGE_KEY);
      const savedDocument = documents.find((item) => item.id === savedId && item.eligibility_status === "eligible");
      setPrimarySourceId(savedDocument?.id ?? null);
      if (savedDocument) {
        setMessage("Restauramos el documento que habías seleccionado.");
      } else if (savedId) {
        window.sessionStorage.removeItem(SOURCE_DOCUMENT_SELECTION_STORAGE_KEY);
      }
    } catch (reason) {
      setSourceDocuments([]);
      setPrimarySourceId(null);
      window.sessionStorage.removeItem(SOURCE_DOCUMENT_SELECTION_STORAGE_KEY);
      setError(reason instanceof Error ? reason.message : "No se pudieron cargar los documentos guardados.");
    } finally {
      setIsLoadingSourceDocuments(false);
    }
  }

  async function recoverActiveGeneration() {
    try {
      const recovered = await apiFetch<GenerationJob | null>("/sales-playbooks/generation-jobs/relevant");
      if (!recovered) return;
      window.sessionStorage.setItem(ACTIVE_GENERATION_STORAGE_KEY, recovered.id);
      if (["queued", "running", "failed"].includes(recovered.status)) {
        setGenerationJob(recovered);
        if (recovered.status === "failed") {
          setError(recovered.failure_message || "No pudimos terminar el análisis de tu documentación.");
        } else {
          setMessage("Tu análisis sigue en curso; recuperamos el avance.");
        }
      } else if (recovered.status === "succeeded" && recovered.result_playbook_id) {
        const draft = await apiFetch<Playbook>(`/sales-playbooks/${recovered.result_playbook_id}`);
        setPlaybooks((items) => [draft, ...items.filter((item) => item.id !== draft.id)]);
        setSelectedId(draft.id);
        window.sessionStorage.removeItem(ACTIVE_GENERATION_STORAGE_KEY);
        setMessage("Borrador generado. Revísalo antes de aprobarlo.");
      } else {
        window.sessionStorage.removeItem(ACTIVE_GENERATION_STORAGE_KEY);
      }
    } catch {
      // The playbook page remains usable if generation-history recovery is unavailable.
    }
  }

  function updateQuestionnaire(key: keyof Questionnaire, value: string) {
    setMessage("");
    setQuestionnaire((current) => ({ ...current, [key]: value }));
  }

  function handleSourceInputChange() {
    setIdempotencyKey(newIdempotencyKey());
  }

  function handleReviewToggle(assertionId: string) {
    setReviewState((current) => {
      const assertionIds = current.snapshotKey === reviewSnapshotKey ? current.assertionIds : [];
      return {
        snapshotKey: reviewSnapshotKey,
        assertionIds: assertionIds.includes(assertionId)
          ? assertionIds.filter((id) => id !== assertionId)
          : [...assertionIds, assertionId],
      };
    });
  }

  async function generateDraft(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setMessage("");
    setIsSaving(true);
    try {
      const uploadedDocumentIds = uploadedSources.length
        ? [primarySourceId, ...uploadedSources.filter((item) => item.id !== primarySourceId).map((item) => item.id)]
          .filter((id): id is string => Boolean(id))
        : [];
      const selectedDocumentIds = uploadedDocumentIds.length
        ? uploadedDocumentIds
        : selectedSourceDocument?.eligibility_status === "eligible" && primarySourceId
          ? [primarySourceId]
          : [];
      if (primarySourceId && !uploadedSources.some((item) => item.id === primarySourceId)
          && selectedSourceDocument?.eligibility_status !== "eligible") {
        throw new Error("El documento seleccionado ya no está disponible. Selecciona otro documento.");
      }
      if (sourceMode === "document" && selectedDocumentIds.length === 0 && !pastedText.trim()) {
        throw new Error("Selecciona un documento disponible o pega el texto de tu estrategia.");
      }
      const job = await apiFetch<GenerationJob>("/sales-playbooks/generation-jobs", {
        method: "POST",
        body: JSON.stringify(sourceMode === "questionnaire"
          ? { source_type: "questionnaire", questionnaire, idempotency_key: idempotencyKey }
          : { source_type: "document", ...(selectedDocumentIds.length ? { source_document_ids: selectedDocumentIds } : { pasted_text: pastedText }), idempotency_key: idempotencyKey }),
      });
      setGenerationJob(job);
      window.sessionStorage.setItem(ACTIVE_GENERATION_STORAGE_KEY, job.id);
      setError("");
      setMessage("Iniciamos el análisis de tu documentación.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo generar el borrador");
    } finally {
      setIsSaving(false);
    }
  }

  async function retryGeneration() {
    if (!generationJob) return;
    setError("");
    setIsSaving(true);
    try {
      const retry = await apiFetch<GenerationJob>(`/sales-playbooks/generation-jobs/${generationJob.id}/retry`, {
        method: "POST",
      });
      setGenerationJob(retry);
      window.sessionStorage.setItem(ACTIVE_GENERATION_STORAGE_KEY, retry.id);
      setMessage("Reiniciamos el análisis de tu documentación.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo iniciar un nuevo análisis.");
    } finally {
      setIsSaving(false);
    }
  }

  async function changeStatus(playbook: Playbook, action: "approve" | "archive" | "reject", reason?: string, reviewedGuardrailIds?: string[]) {
    setError("");
    setMessage("");
    try {
      let body: Record<string, unknown> = {};
      if (action === "reject") {
        body = { reason: reason?.trim() || null };
      } else if (action === "approve" && playbook.content_contract_version === "3.0") {
        body = {
          reviewed_guardrail_assertion_ids: reviewedGuardrailIds || [],
          content_digest: playbook.content_digest
        };
      }

      const updated = await apiFetch<Playbook>(`/sales-playbooks/${playbook.id}/${action}`, {
        method: "POST",
        ...(Object.keys(body).length > 0 ? { body: JSON.stringify(body) } : {}),
      });
      setPlaybooks((current) => current.map((item) => {
        if (item.id === updated.id) return updated;
        if (action === "approve" && item.status === "approved") return { ...item, status: "archived" };
        return item;
      }));
      setMessage(action === "approve" ? "Playbook aprobado y activo." : action === "reject" ? "Playbook rechazado. El motivo quedó registrado." : "Playbook archivado.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo actualizar el playbook");
    }
  }

  async function resolveWorkflowConflict(playbook: Playbook, conflictId: string, candidateId: string) {
    setError("");
    setMessage("");
    try {
      const updated = await apiFetch<Playbook>("/sales-playbooks/" + playbook.id + "/workflow-conflicts/" + encodeURIComponent(conflictId) + "/resolve", {
        method: "POST",
        body: JSON.stringify({ candidate_id: candidateId }),
      });
      setPlaybooks((current) => current.map((item) => item.id === updated.id ? updated : item));
      setMessage("Interpretación seleccionada. Revisa de nuevo el contenido actualizado antes de aprobar.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo resolver el conflicto del workflow");
    }
  }

  async function completeWorkflowTransition(playbook: Playbook, transition: Record<string, unknown>) {
    setError("");
    setMessage("");
    try {
      const updated = await apiFetch<Playbook>("/sales-playbooks/" + playbook.id + "/workflow-transitions/complete", {
        method: "POST",
        body: JSON.stringify(transition),
      });
      setPlaybooks((current) => current.map((item) => item.id === updated.id ? updated : item));
      setMessage("Transición completada manualmente. Revisa de nuevo el contenido y los bloqueadores restantes.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo completar la transición requerida");
    }
  }

  async function reviewSemanticWorkflowFinding(playbook: Playbook, findingId: string) {
    setError("");
    setMessage("");
    try {
      const updated = await apiFetch<Playbook>(`/sales-playbooks/${playbook.id}/semantic-workflow-findings/${encodeURIComponent(findingId)}/review`, { method: "POST" });
      setPlaybooks((current) => current.map((item) => item.id === updated.id ? updated : item));
      setMessage("La ruta quedó aceptada mediante revisión humana; vuelve a revisar readiness y digest antes de aprobar.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo registrar la revisión semántica");
    }
  }

  return (
    <div className="mx-auto max-w-7xl space-y-8">
      <header>
        <p className="eyebrow">Estrategia comercial</p>
        <h1 className="page-title">Sales Playbook</h1>
        <p className="page-subtitle">
          Convierte el contexto de Mi negocio y tu conocimiento de ventas en una estrategia revisable para el equipo.
        </p>
      </header>

      {(error || message) && (
        <p className={error ? "rounded-xl bg-red-50 px-4 py-3 text-sm text-red-700" : "rounded-xl bg-emerald-50 px-4 py-3 text-sm text-emerald-700"} role={error ? "alert" : "status"}>
          {error || message}
        </p>
      )}

      {generationJob && (isGenerating || generationJob.status === "failed") && (
        <section className="card space-y-3" aria-live="polite" aria-label="Avance del análisis">
          <div>
            <p className="eyebrow">Generación de borrador</p>
            <h2 className="section-title mt-1">
              {generationJob.status === "failed" ? "El análisis no pudo completarse" : "Analizando tu documentación"}
            </h2>
          </div>
          <p className="text-sm text-slate-600">
            {generationJob.status === "failed"
              ? generationJob.failure_message || "No pudimos terminar el análisis de tu documentación."
              : generationJob.progress_message}
          </p>
          {isGenerating && generationJob.total_steps > 0 && (
            <div className="space-y-1">
              <div className="flex justify-between text-xs text-slate-500">
                <span>Pasos completados</span>
                <span>{generationJob.current_step} / {generationJob.total_steps}</span>
              </div>
              <progress className="h-2 w-full accent-teal-600" max={generationJob.total_steps} value={generationJob.current_step} aria-label="Pasos completados" />
            </div>
          )}
          {generationJob.status === "failed" && (
            <button className="btn-secondary" type="button" onClick={() => void retryGeneration()} disabled={isSaving}>
              {isSaving ? "Iniciando…" : "Intentar de nuevo"}
            </button>
          )}
        </section>
      )}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(22rem,0.8fr)]">
        <form onSubmit={generateDraft} className="card space-y-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <h2 className="section-title">Crea un borrador</h2>
              <p className="mt-1 max-w-xl text-sm leading-6 text-slate-500">Nueve preguntas de alto valor o una estrategia que ya tienes escrita. El borrador nunca queda activo automáticamente.</p>
            </div>
            <span className="badge">Siempre requiere revisión</span>
          </div>

          <div className="flex gap-2 rounded-xl bg-slate-100 p-1" role="tablist" aria-label="Fuente de estrategia">
            <button type="button" role="tab" aria-selected={sourceMode === "questionnaire"} onClick={() => { setSourceMode("questionnaire"); handleSourceInputChange(); }} disabled={isGenerating} className={`flex-1 rounded-lg px-3 py-2 text-sm font-semibold ${sourceMode === "questionnaire" ? "bg-white text-teal-700 shadow-sm" : "text-slate-500"}`}>
              Cuestionario
            </button>
            <button type="button" role="tab" aria-selected={sourceMode === "document"} onClick={() => { setSourceMode("document"); handleSourceInputChange(); }} disabled={isGenerating} className={`flex-1 rounded-lg px-3 py-2 text-sm font-semibold ${sourceMode === "document" ? "bg-white text-teal-700 shadow-sm" : "text-slate-500"}`}>
              Subir documentación
            </button>
          </div>

          {sourceMode === "questionnaire" ? (
            <div className="space-y-4">
              {questions.map(([key, label, placeholder]) => (
                <label key={key} htmlFor={`playbook-${key}`} className="block space-y-2">
                  <span className="text-sm font-semibold text-slate-700">{label} <span className="text-teal-600">*</span></span>
                  <textarea id={`playbook-${key}`} required className="input min-h-20 w-full resize-y" placeholder={placeholder} value={questionnaire[key]} onChange={(event) => { updateQuestionnaire(key, event.target.value); handleSourceInputChange(); }} disabled={isSaving || isGenerating} />
                </label>
              ))}
            </div>
          ) : (
            <div className="space-y-4">
              <label htmlFor="sales-source-files" className="block space-y-2">
                <span className="text-sm font-semibold text-slate-700">¿Ya tienes documentado tu proceso de ventas? Sube tus archivos</span>
                <input id="sales-source-files" type="file" multiple accept=".md,.txt,.docx,.odt,.html,.htm" className="input w-full" disabled={isSaving || isGenerating || isLoadingSourceDocuments} onChange={async (event) => {
                  const input = event.currentTarget;
                  const files = Array.from(event.currentTarget.files ?? []);
                  if (!files.length) return;
                  if (files.length + uploadedSources.length > 10) { setError("Puedes cargar hasta 10 documentos por borrador."); input.value = ""; return; }
                  setError(""); setMessage(""); setIsSaving(true);
                  try {
                    const uploaded: UploadedSource[] = [];
                    for (const file of files) {
                      const body = new FormData(); body.append("file", file);
                      uploaded.push(await apiFetch<UploadedSource>("/sales-playbooks/source-documents", { method: "POST", body }));
                    }
                    setUploadedSources((current) => [...current, ...uploaded]);
                    const catalogEntries: SourceDocumentSelection[] = uploaded.map((source) => ({
                      id: source.id,
                      original_filename: source.original_filename,
                      mime_type: source.mime_type ?? "application/octet-stream",
                      created_at: source.created_at ?? new Date().toISOString(),
                      original_sha256: source.original_sha256 ?? "",
                      normalized_sha256: source.normalized_sha256,
                      section_count: source.sections.length,
                      eligibility_status: source.normalization_status === "complete"
                        && source.sections.length > 0
                        && Boolean(source.original_sha256)
                        && Boolean(source.normalized_sha256)
                        ? "eligible"
                        : "unavailable",
                    }));
                    setSourceDocuments((current) => [...catalogEntries, ...current.filter((item) => !catalogEntries.some((entry) => entry.id === item.id))]);
                    setPrimarySourceId((current) => {
                      const next = current ?? uploaded[0]?.id ?? null;
                      if (next) window.sessionStorage.setItem(SOURCE_DOCUMENT_SELECTION_STORAGE_KEY, next);
                      return next;
                    });
                    setIdempotencyKey(newIdempotencyKey());
                  } catch (reason) { setError(reason instanceof Error ? reason.message : "No se pudo normalizar el documento"); }
                  finally { setIsSaving(false); input.value = ""; }
                }} />
                <span className="text-xs text-slate-500">Markdown, texto, Word, OpenDocument y HTML · máximo 20 MiB por archivo. Los originales se conservan sin modificar.</span>
              </label>
              <section className="space-y-2 rounded-lg border border-slate-200 p-3" aria-label="Documentos persistidos">
                <label className="block space-y-1 text-sm" htmlFor="persisted-source-document">
                  <span className="font-semibold">Usar un documento guardado</span>
                  <select
                    id="persisted-source-document"
                    className="input w-full"
                    value={primarySourceId ?? ""}
                    onChange={(event) => {
                      const id = event.target.value || null;
                      setPrimarySourceId(id);
                      setUploadedSources([]);
                      if (id) window.sessionStorage.setItem(SOURCE_DOCUMENT_SELECTION_STORAGE_KEY, id);
                      else window.sessionStorage.removeItem(SOURCE_DOCUMENT_SELECTION_STORAGE_KEY);
                      handleSourceInputChange();
                    }}
                    disabled={isSaving || isGenerating || isLoadingSourceDocuments}
                  >
                    <option value="">Selecciona explícitamente un documento</option>
                    {sourceDocuments.map((document) => (
                      <option key={document.id} value={document.id} disabled={document.eligibility_status !== "eligible"}>
                        {document.original_filename} · {new Date(document.created_at).toLocaleDateString("es-MX")} · ID {document.id.slice(0, 8)} · hash {document.normalized_sha256.slice(0, 8)} · {document.section_count} secciones{document.eligibility_status !== "eligible" ? " · no disponible" : ""}
                      </option>
                    ))}
                  </select>
                </label>
                {isLoadingSourceDocuments && <p className="text-xs text-slate-500">Cargando documentos guardados…</p>}
                {!isLoadingSourceDocuments && sourceDocuments.length === 0 && <p className="text-xs text-slate-500">Aún no hay documentos guardados disponibles.</p>}
                {selectedSourceDocument?.eligibility_status === "eligible" && (
                  <div className="rounded bg-emerald-50 p-3 text-xs text-slate-700" aria-live="polite">
                    <p className="font-semibold">Documento seleccionado: {selectedSourceDocument.original_filename}</p>
                    <p>ID completo: {selectedSourceDocument.id}</p>
                    <p>Hash normalizado: {selectedSourceDocument.normalized_sha256}</p>
                    <p>{selectedSourceDocument.section_count} secciones · {new Date(selectedSourceDocument.created_at).toLocaleString("es-MX")}</p>
                  </div>
                )}
                {selectedSourceDocument && selectedSourceDocument.eligibility_status !== "eligible" && (
                  <p role="alert" className="text-xs text-red-700">El documento seleccionado no está disponible para generación.</p>
                )}
                {sourceDocuments.some((document) => duplicateNormalizedHashes.has(document.normalized_sha256)) && (
                  <p className="text-xs text-amber-800">Hay documentos con contenido idéntico. Cada registro conserva su propio ID; confirma el ID completo antes de generar.</p>
                )}
              </section>
              {uploadedSources.length > 0 && <div className="space-y-2" aria-label="Documentos normalizados">
                <p className="text-sm font-semibold">Documento(s) cargado(s) en esta sesión</p>
                {uploadedSources.map((source) => <article key={source.id} className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm">
                  <p className="font-semibold">{source.original_filename} · {(source.original_size_bytes / 1024).toFixed(0)} KB</p>
                  <p className="text-xs text-slate-600">{source.sections.length} secciones · normalizado para revisión</p>
                  <details className="mt-2 text-xs"><summary className="cursor-pointer font-semibold">Vista previa normalizada</summary><div className="mt-2 max-h-48 space-y-2 overflow-auto">{source.sections.map((section) => <div key={section.id} className="rounded bg-white p-2"><strong>{section.heading}</strong><span className="ml-2 text-slate-400">líneas {section.start_line}–{section.end_line}</span><p className="mt-1 whitespace-pre-wrap text-slate-600">{section.text.slice(0, 1200)}{section.text.length > 1200 ? "…" : ""}</p></div>)}</div></details>
                  {source.normalization_warnings.map((warning) => <p key={warning} className="text-xs text-amber-800">Aviso: {warning}</p>)}
                  <button type="button" className="text-xs text-red-700 underline" disabled={isGenerating} onClick={() => { const next = uploadedSources.filter((item) => item.id !== source.id); setUploadedSources(next); if (primarySourceId === source.id) { setPrimarySourceId(null); window.sessionStorage.removeItem(SOURCE_DOCUMENT_SELECTION_STORAGE_KEY); } handleSourceInputChange(); }}>Quitar de esta generación</button>
                </article>)}
              </div>}
              <details className="rounded-lg border border-slate-200 p-3">
                <summary className="cursor-pointer text-sm font-semibold text-slate-700">O pegar texto como alternativa</summary>
                <label htmlFor="pasted-strategy" className="mt-3 block text-sm font-semibold text-slate-700">Pega tu estrategia actual</label>
                <textarea id="pasted-strategy" maxLength={100000} className="input mt-3 min-h-48 w-full resize-y" placeholder="Pega aquí tus notas, argumentos, precios y reglas comerciales…" value={pastedText} onChange={(event) => { setPastedText(event.target.value); handleSourceInputChange(); }} disabled={isSaving || isGenerating} />
                <span className="block text-right text-xs tabular-nums text-slate-400">{pastedText.length.toLocaleString("es-MX")} / 100,000 caracteres</span>
              </details>
            </div>
          )}

          <button className="btn-primary w-full" type="submit" disabled={isSaving || isGenerating || isLoading || isLoadingSourceDocuments || (sourceMode === "document" && !pastedText.trim() && !primarySourceId)}>
            {isSaving ? "Iniciando análisis…" : isGenerating ? "Análisis en curso…" : "Generar borrador"}
          </button>
        </form>

        <aside className="space-y-6">
          <section className="card space-y-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="eyebrow">Historial</p>
                <h2 className="section-title mt-1">Versiones</h2>
              </div>
              <span className="text-xs font-semibold text-slate-400">{playbooks.length} total</span>
            </div>
            {isLoading ? <p className="text-sm text-slate-500">Cargando versiones…</p> : playbooks.length === 0 ? <p className="text-sm leading-6 text-slate-500">Todavía no hay una estrategia. Crea el primer borrador para comenzar.</p> : (
              <div className="space-y-2">
                {playbooks.map((playbook) => (
                  <button key={playbook.id} type="button" onClick={() => setSelectedId(playbook.id)} className={`flex w-full items-center justify-between gap-3 rounded-xl border px-3 py-3 text-left transition ${selected?.id === playbook.id ? "border-teal-300 bg-teal-50" : "border-slate-200 hover:border-teal-200"}`}>
                    <span><span className="block text-sm font-bold text-slate-800">Versión {playbook.version}</span><span className="mt-1 block text-xs text-slate-500">{sourceLabel(playbook.source_type)}</span></span>
                    <Status status={playbook.status} />
                  </button>
                ))}
              </div>
            )}
          </section>

      {selected && <PlaybookReview playbook={selected} onApprove={() => void changeStatus(selected, "approve", undefined, reviewedGuardrailIds)} onArchive={() => void changeStatus(selected, "archive")} onReject={(reason) => void changeStatus(selected, "reject", reason)} onResolveConflict={(conflictId, candidateId) => void resolveWorkflowConflict(selected, conflictId, candidateId)} onCompleteTransition={(playbook, transition) => void completeWorkflowTransition(playbook, transition)} onReviewSemanticFinding={(playbook, findingId) => void reviewSemanticWorkflowFinding(playbook, findingId)} reviewedGuardrailIds={reviewedGuardrailIds} onReviewToggle={handleReviewToggle} />}
        </aside>
      </div>
    </div>
  );
}

function PlaybookReview({ playbook, onApprove, onArchive, onReject, onResolveConflict, onCompleteTransition, onReviewSemanticFinding, reviewedGuardrailIds, onReviewToggle }: { playbook: Playbook; onApprove: () => void; onArchive: () => void; onReject: (reason: string) => void; onResolveConflict: (conflictId: string, candidateId: string) => void; onCompleteTransition: (playbook: Playbook, transition: Record<string, unknown>) => void; onReviewSemanticFinding: (playbook: Playbook, findingId: string) => void; reviewedGuardrailIds: string[]; onReviewToggle: (id: string) => void }) {
  const [isRejecting, setIsRejecting] = useState(false);
  const [rejectionReason, setRejectionReason] = useState("");
  const content = playbook.structured_content;
  const readiness = playbook.approval_readiness;

  const isV3Playbook = content && "semantic_policy" in content && content.contract_version === "3.0";
  const hasValidDigest = typeof playbook.content_digest === "string" && /^[0-9a-f]{64}$/.test(playbook.content_digest);
  const canApprove = isV3Playbook
    ? (readiness
        && hasValidDigest
        && readiness.blockers.length === 0
        && readiness.required_guardrail_assertion_ids.every((id) => reviewedGuardrailIds.includes(id)))
    : true;

  return (
    <section className="card space-y-5" aria-labelledby="review-heading">
      <div className="flex items-start justify-between gap-3">
        <div><p className="eyebrow">Revisión humana</p><h2 id="review-heading" className="section-title mt-1">{statusLabel(playbook.status)} · v{playbook.version}</h2></div>
        <Status status={playbook.status} />
      </div>
      {isExecutable(content) ? <ExecutableReview content={content} /> : isSemanticPlaybook(content) ? <SemanticPlaybookReview playbook={playbook} content={content} reviewedGuardrailIds={reviewedGuardrailIds} onReviewToggle={onReviewToggle} onResolveConflict={onResolveConflict} onCompleteTransition={(transition) => onCompleteTransition(playbook, transition)} onReviewSemanticFinding={(findingId) => onReviewSemanticFinding(playbook, findingId)} readiness={readiness} /> : <LegacyReview content={content} />}
      {playbook.status === "draft" && !isRejecting && <div className="grid gap-3 sm:grid-cols-2"><button className="btn-primary w-full" type="button" onClick={onApprove} disabled={!canApprove}>Aprobar playbook</button><button className="btn-secondary w-full border-red-200 text-red-700 hover:border-red-300 hover:bg-red-50" type="button" onClick={() => setIsRejecting(true)}>Rechazar playbook</button></div>}
      {playbook.status === "draft" && isRejecting && <div className="space-y-3 rounded-xl border border-red-100 bg-red-50/50 p-4"><label htmlFor="rejection-reason" className="block space-y-2"><span className="text-sm font-semibold text-slate-700">Motivo del rechazo <span className="font-normal text-slate-500">(opcional)</span></span><textarea id="rejection-reason" className="input min-h-24 w-full resize-y" placeholder="Qué debe revisarse en este análisis…" value={rejectionReason} onChange={(event) => setRejectionReason(event.target.value)} /></label><div className="grid gap-3 sm:grid-cols-2"><button className="btn-secondary w-full" type="button" onClick={() => { setIsRejecting(false); setRejectionReason(""); }}>Cancelar</button><button className="w-full rounded-xl bg-red-600 px-4 py-3 text-sm font-bold text-white transition hover:bg-red-700" type="button" onClick={() => onReject(rejectionReason)}>Confirmar rechazo</button></div></div>}
      {playbook.status === "approved" && <button className="btn-secondary w-full" type="button" onClick={onArchive}>Archivar playbook</button>}
      {playbook.status === "archived" && <p className="rounded-xl bg-slate-100 px-3 py-2 text-xs leading-5 text-slate-500">Esta versión se conserva para auditoría, pero no puede ser consumida por agentes.</p>}
      {playbook.status === "rejected" && <div className="rounded-xl border border-red-100 bg-red-50 px-3 py-2 text-xs leading-5 text-red-700"><p className="font-bold">Este borrador fue rechazado.</p>{playbook.rejection_reason && <p className="mt-1">Motivo: {playbook.rejection_reason}</p>}</div>}
    </section>
  );
}

function SemanticPlaybookReview({
  playbook,
  content,
  reviewedGuardrailIds,
  onReviewToggle,
  onResolveConflict,
  onCompleteTransition,
  onReviewSemanticFinding,
  readiness,
}: {
  playbook: Playbook;
  content: SemanticPlaybookContent;
  reviewedGuardrailIds: string[];
  onReviewToggle: (id: string) => void;
  onResolveConflict: (conflictId: string, candidateId: string) => void;
  onCompleteTransition: (transition: Record<string, unknown>) => void;
  onReviewSemanticFinding: (findingId: string) => void;
  readiness: ApprovalReadiness | null | undefined;
}) {
  const { semantic_policy } = content;
  const blockers = readiness?.blockers || [];
  const requiredGuardrails = readiness?.required_guardrail_assertion_ids || [];
  const assertionsById = new Map(semantic_policy.assertions.map((assertion) => [assertion.assertion_id, assertion]));
  const reviewedSnapshot = new Set(semantic_policy.reviewed_guardrail_assertion_ids);
  const [transitionForms, setTransitionForms] = useState<Record<string, {
    from_stage: string; to_stage: string; outcome: string; maturity: string; source_section_id: string;
  }>>({});
  const [sourceDocuments, setSourceDocuments] = useState<SourceDocumentReview[]>([]);
  useEffect(() => {
    let active = true;
    const references = content.source_documents ?? [];
    if (!references.length) { setSourceDocuments([]); return () => { active = false; }; }
    void Promise.all(references.map((reference) => apiFetch<SourceDocumentReview>(`/sales-playbooks/source-documents/${reference.document_id}`)))
      .then((documents) => { if (active) setSourceDocuments(documents); })
      .catch(() => { if (active) setSourceDocuments([]); });
    return () => { active = false; };
  }, [content.source_documents]);
  const setTransitionField = (condition: string, key: keyof (typeof transitionForms)[string], value: string) => {
    setTransitionForms((current) => ({
      ...current,
      [condition]: { ...{ from_stage: "", to_stage: "", outcome: "", maturity: "", source_section_id: "" }, ...(current[condition] ?? {}), [key]: value },
    }));
  };
  const stagesByAssertionId = new Map<string, string[]>();
  for (const placement of semantic_policy.placements) {
    for (const assertionId of placement.source_assertion_ids) {
      const stages = stagesByAssertionId.get(assertionId) ?? [];
      if (!stages.includes(placement.stage_code)) stages.push(placement.stage_code);
      stagesByAssertionId.set(assertionId, stages);
    }
  }

  return <div className="space-y-5 text-sm">
    {blockers.length > 0 && (
      <ReviewSection title="Bloqueadores de aprobación">
        <p className="mb-3 text-slate-600">El proceso contiene {content.workflow.stages.length} etapas y {content.workflow.transitions.length} rutas detectadas. Aún faltan decisiones necesarias para aprobar:</p>
        <ul className="list-disc space-y-1 pl-5 text-red-700">
          {blockers.map((blocker, index) => <li key={index}>{businessBlockerLabel(blocker)}</li>)}
        </ul>
        {(readiness?.blocker_details ?? []).filter((item) => item.kind === "semantic_workflow").map((finding) => (
          <article key={finding.identifier} className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3">
            <p className="text-sm text-slate-700">{finding.message ?? businessBlockerLabel(finding.identifier)}</p>
            <details className="mt-2 text-xs text-slate-500"><summary className="cursor-pointer">Ver identificador técnico</summary><p>{finding.identifier}</p></details>
            {playbook.status === "draft" && <button className="btn-secondary mt-3" type="button" onClick={() => onReviewSemanticFinding(finding.identifier)}>
              Confirmo que esta ruta refleja el proceso vigente
            </button>}
            <p className="mt-2 text-xs text-slate-600">Esta acción registra una decisión humana; no cambia la ruta extraída ni afirma que la documentación la respalde.</p>
          </article>
        ))}
      </ReviewSection>
    )}
    {(semantic_policy.workflow_reviews ?? []).length > 0 && <ReviewSection title="Rutas confirmadas por una persona">
      <ul className="space-y-2">{semantic_policy.workflow_reviews!.map((review) => <li key={review.finding_id} className="rounded-lg bg-slate-50 p-3 text-xs text-slate-600">
        Confirmación de proceso registrada por {review.reviewer_id} el {new Date(review.reviewed_at).toLocaleString("es-MX")}.
        <details className="mt-1"><summary className="cursor-pointer">Ver identificador y acción</summary><p>{review.finding_id} · {review.action}</p></details>
      </li>)}</ul>
    </ReviewSection>}
    {playbook.status === "draft" && (!readiness || !hasValidPlaybookDigest(playbook.content_digest)) && (
      <p className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700" role="alert">
        No se puede habilitar la aprobación: falta la validación del borrador o la huella del contenido revisado.
      </p>
    )}
    {content.generic_workflow_graph && <GenericWorkflowGraphReview
      graph={content.generic_workflow_graph}
      sourceDocuments={sourceDocuments}
      sourceSectionMap={content.source_section_map ?? []}
    />}
    {(sourceDocuments.length > 0 || playbook.source_payload?.pasted_text) && <ReviewSection title="Documentación usada como fuente">
      <p className="mb-3 text-sm text-slate-600">Esta es la versión normalizada que se usó para preparar el borrador. Las citas de secciones apuntan a este contenido.</p>
      {sourceDocuments.map((document) => <details key={document.id} className="mb-3 rounded-lg border border-slate-200 bg-white p-3">
        <summary className="cursor-pointer font-semibold">{document.original_filename} · {document.sections.length} secciones</summary>
        <div className="mt-3 space-y-3">{document.sections.map((section) => <article key={section.id} id={`source-${section.id}`} className="rounded bg-slate-50 p-3">
          <h4 className="font-semibold">{section.heading}<span className="ml-2 text-xs font-normal text-slate-500">líneas {section.start_line}–{section.end_line}</span></h4>
          <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-slate-700">{section.text}</p>
          <p className="mt-1 text-xs text-slate-400">Sección {section.id}</p>
        </article>)}</div>
        <details className="mt-2 text-xs text-slate-500"><summary className="cursor-pointer">Integridad y conversión</summary><p>SHA-256 original: {document.original_sha256}</p><p>SHA-256 normalizado: {document.normalized_sha256}</p><p>Conversor: {document.converter} {document.converter_version}</p></details>
      </details>)}
      {playbook.source_payload?.pasted_text && <details className="rounded-lg border border-slate-200 p-3"><summary className="cursor-pointer font-semibold">Texto pegado original</summary><pre className="mt-3 max-h-[32rem] overflow-auto whitespace-pre-wrap text-sm leading-6">{playbook.source_payload.pasted_text}</pre></details>}
    </ReviewSection>}
    {playbook.status === "draft" && readiness && readiness.blockers.length === 0 && requiredGuardrails.some((id) => !reviewedGuardrailIds.includes(id)) && (
      <p className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-800">
        Revisa cada aserción fuente vinculada a las reglas ejecutables para habilitar la aprobación.
      </p>
    )}

    {(content.workflow_conflicts ?? []).length > 0 && (
      <ReviewSection title="Decisiones de negocio pendientes">
        <p className="text-sm text-slate-600">Encontramos instrucciones diferentes en la documentación. Elige cuál describe el proceso actual; no hay una recomendación automática.</p>
        <div className="space-y-3">
          {(content.workflow_conflicts ?? []).map((conflict) => (
            <article key={conflict.conflict_id} className="rounded-lg border border-amber-200 bg-amber-50/60 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <strong>{stageBusinessName(conflict.stage_code)}</strong>
                <span className="badge bg-amber-100 text-amber-800">{conflict.status === "needs_review" ? "Necesita revisión" : "Resuelto"}</span>
              </div>
              <p className="mt-1 text-sm text-slate-700">{conflictQuestion(conflict)}</p>
              {conflict.status === "resolved" && (
                <p className="mt-2 text-sm font-semibold text-emerald-700">
                  Conflicto resuelto: {conflict.candidates.find((candidate) => candidate.candidate_id === conflict.resolved_candidate_id)?.stage?.name ?? conflict.resolved_candidate_id}
                </p>
              )}
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                {conflict.candidates.map((candidate) => (
                  <div key={candidate.candidate_id} className="rounded-lg border border-slate-200 bg-white p-3">
                    <p className="font-semibold text-slate-800">{candidate.stage?.name ?? ("Transición de " + candidate.owner)}</p>
                    <p className="mt-1 text-xs text-slate-600">Interpretación encontrada en: {candidate.owner}</p>
                    {candidate.stage && <div className="mt-2 space-y-2">
                      <ReviewItem label="Objetivo" value={candidate.stage.objective} />
                      <ReviewItem label="Acciones" value={candidate.stage.actions.join("; ") || "No especificadas"} />
                      <details className="mt-2 rounded bg-slate-50 p-2 text-xs">
                        <summary className="cursor-pointer font-semibold">Ver fuente y detalles técnicos</summary>
                        <p className="mt-2">Fuentes: {candidate.source_section_ids.map((id) => sourceEvidenceLabel(id, sourceDocuments, content.source_section_map ?? [])).join("; ")}</p>
                        <p>Entrada: {candidate.stage.entry_conditions.map(conditionBusinessName).join(", ") || "No especificada"}</p>
                        <p>Salida: {candidate.stage.exit_conditions.map(conditionBusinessName).join(", ") || "No especificada"}</p>
                        {candidate.stage.timing && <p>Temporalidad: {candidate.stage.timing}</p>}
                        {candidate.stage.responsible_role && <p>Responsable: {candidate.stage.responsible_role}</p>}
                        {candidate.stage.maturity && <p>Madurez de extracción: {candidate.stage.maturity}</p>}
                        <p>Campos diferentes: {conflict.conflicting_fields.join(", ")}</p>
                      </details>
                    </div>}
                    {candidate.transitions.map(({ transition }, index) => <p key={index} className="mt-2 text-sm text-slate-700">{conditionBusinessName(transition.condition)} → {stageBusinessName(transition.to_stage)}</p>)}
                    {playbook.status === "draft" && conflict.status === "needs_review" && (
                      <button className="btn-secondary mt-3 w-full" type="button" onClick={() => onResolveConflict(conflict.conflict_id, candidate.candidate_id)}>
                        Confirmar que esta interpretación refleja el proceso actual
                      </button>
                    )}
                    {conflict.resolved_candidate_id === candidate.candidate_id && <p className="mt-3 text-sm font-semibold text-emerald-700">Interpretación seleccionada</p>}
                  </div>
                ))}
              </div>
            </article>
          ))}
        </div>
      </ReviewSection>
    )}
    {(content.source_conflicts ?? []).length > 0 && <ReviewSection title="Datos de negocio que requieren confirmación">
      {(content.source_conflicts ?? []).map((conflict) => <article key={conflict.conflict_id} className="space-y-2 rounded-lg border border-amber-200 bg-amber-50 p-3">
        <h3 className="font-semibold">La información sobre {businessField(conflict.field)} difiere entre tus fuentes.</h3>
        {conflict.claims.map((claim, index) => <div key={`${claim.origin}:${index}`} className="rounded bg-white p-2">
          <p className="text-sm"><strong>{businessClaimOrigin(claim.origin)} · {businessField(conflict.field)}:</strong> {claim.value}</p>
          {claim.source_section_ids.length > 0 && <details className="mt-1 text-xs"><summary className="cursor-pointer">Ver sección de origen</summary><p>{claim.source_section_ids.join(", ")}</p></details>}
        </div>)}
        <p className="text-xs text-slate-600">Confirma con la persona responsable qué información está vigente. No se combinaron ni eligieron valores automáticamente. Para reflejar una corrección, actualiza el perfil o carga una versión corregida del documento y genera un nuevo borrador.</p>
      </article>)}
    </ReviewSection>}
    {(content.workflow_missing_required_conditions ?? []).length > 0 && (
      <ReviewSection title="Transiciones obligatorias sin resolver">
        <p className="mb-3 text-amber-800">La extracción no contiene estas condiciones requeridas. Cada transición debe completarse con valores explícitos; no se elige una ruta automáticamente.</p>
        <div className="space-y-4">
          {(content.workflow_missing_required_conditions ?? []).map((condition) => {
            const form = transitionForms[condition] ?? { from_stage: "", to_stage: "", outcome: "", maturity: "", source_section_id: "" };
            const unresolvedConflict = (content.workflow_conflicts ?? []).some((item) => item.status === "needs_review");
            const complete = Boolean(form.from_stage && form.to_stage && form.outcome && form.maturity);
            return <article key={condition} className="rounded-lg border border-amber-200 bg-white p-3">
              <h3 className="font-semibold text-slate-900">Falta transición requerida: {condition}</h3>
              <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                <label className="space-y-1"><span className="block text-xs font-semibold">Etapa de origen</span><select className="input w-full" value={form.from_stage} onChange={(event) => setTransitionField(condition, "from_stage", event.target.value)}><option value="">Selecciona una etapa</option>{content.workflow.stages.map((stage) => <option key={stage.code} value={stage.code}>{stage.name} ({stage.code})</option>)}</select></label>
                <label className="space-y-1"><span className="block text-xs font-semibold">Etapa destino</span><select className="input w-full" value={form.to_stage} onChange={(event) => setTransitionField(condition, "to_stage", event.target.value)}><option value="">Selecciona una etapa</option>{content.workflow.stages.map((stage) => <option key={stage.code} value={stage.code}>{stage.name} ({stage.code})</option>)}</select></label>
                <label className="space-y-1"><span className="block text-xs font-semibold">Resultado</span><select className="input w-full" value={form.outcome} onChange={(event) => setTransitionField(condition, "outcome", event.target.value)}><option value="">Selecciona resultado</option>{["advance", "branch", "wait", "stop", "shorten"].map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
                <label className="space-y-1"><span className="block text-xs font-semibold">Madurez</span><select className="input w-full" value={form.maturity} onChange={(event) => setTransitionField(condition, "maturity", event.target.value)}><option value="">Selecciona madurez</option>{["established", "experimental", "pending_validation"].map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
                <label className="space-y-1"><span className="block text-xs font-semibold">Sección fuente confirmada (opcional)</span><select className="input w-full" value={form.source_section_id} onChange={(event) => setTransitionField(condition, "source_section_id", event.target.value)}><option value="">Sin atribución directa a sección</option>{semantic_policy.source_section_ids.map((id) => <option key={id} value={id}>{id}</option>)}</select></label>
              </div>
              <p className="mt-2 text-xs text-slate-600">La transición quedará marcada como completada por una persona. Sin sección seleccionada, no se afirmará respaldo directo del manual.</p>
              <button className="btn-secondary mt-3" type="button" disabled={!complete || unresolvedConflict} onClick={() => onCompleteTransition({
                from_stage: form.from_stage, condition, to_stage: form.to_stage, outcome: form.outcome,
                maturity: form.maturity, source_section_ids: form.source_section_id ? [form.source_section_id] : [],
              })}>Guardar transición humana</button>
              {unresolvedConflict && <p className="mt-2 text-xs text-amber-700">Resuelve primero los conflictos del workflow para poder completar transiciones.</p>}
            </article>;
          })}
        </div>
      </ReviewSection>
    )}
    {(content.workflow_missing_required_conditions ?? []).length === 0 && content.workflow.transitions.some((item) => item.provenance?.origin === "human_reviewed") && (
      <ReviewSection title="Transiciones completadas por revisión humana">
        <p className="mb-2 text-emerald-800">No hay transiciones obligatorias pendientes.</p>
        {content.workflow.transitions.filter((item) => item.provenance?.origin === "human_reviewed").map((item, index) => (
          <p key={`${item.from_stage}:${item.condition}:${index}`} className="text-xs text-slate-700">
            {item.from_stage} → {item.to_stage} · {item.condition} · {item.outcome} · madurez {item.maturity}
            {item.provenance?.source_ref ? ` · sección confirmada: ${item.provenance.source_ref}` : " · completada por una persona, sin atribución directa a sección"}
          </p>
        ))}
      </ReviewSection>
    )}

    <ReviewSection title="Aserciones de política semántica (fuente)">
      {semantic_policy.assertions.map((assertion) => (
        <div key={assertion.assertion_id} className="rounded-lg border border-slate-200 bg-white p-3">
          <div className="flex flex-wrap items-center gap-2">
            <strong className="text-slate-800">{assertion.assertion_text}</strong>
            <span className={`badge ${assertion.modality === "required" ? "bg-emerald-100 text-emerald-700" : assertion.modality === "prohibited" ? "bg-red-100 text-red-700" : assertion.modality === "recommended" ? "bg-amber-100 text-amber-700" : "bg-blue-100 text-blue-700"}`}>
              {assertion.modality}
            </span>
          </div>
          <div className="mt-2 space-y-2">
            {assertion.concept && <ReviewItem label="Concepto normalizado" value={assertion.concept} />}
            <ReviewItem label="Ámbito" value={assertion.scope_text ?? "No especificado"} />
            <ReviewItem label="Condición" value={assertion.condition_text ?? "No especificada"} />
            {assertion.maturity ? (
              <ReviewItem label="Madurez" value={`${assertion.maturity} (${assertion.maturity_origin})`} />
            ) : (
              <ReviewItem label="Madurez" value="Sin resolver" />
            )}
            {assertion.maturity_source_section_ids.length > 0 && (
              <ReviewItem label="Fuente de madurez" value={assertion.maturity_source_section_ids.join(", ")} />
            )}
            {stagesByAssertionId.has(assertion.assertion_id) && (
              <ReviewItem label="Etapa asociada" value={stagesByAssertionId.get(assertion.assertion_id)!.join(", ")} />
            )}
            <ReviewItem label="Documentos y líneas citados" value={assertion.source_section_ids.map((id) => sourceEvidenceLabel(id, sourceDocuments, content.source_section_map ?? [])).join("; ")} />
          </div>
        </div>
      ))}
    </ReviewSection>

    <ReviewSection title="Reglas compiladas (ejecutables)">
      {semantic_policy.compilation.compiled_rules.length > 0 ? (
        <div className="space-y-3">
          {semantic_policy.compilation.compiled_rules.map((trace, index) => {
            const requiredSourceIds = trace.source_assertion_ids.filter((id) => requiredGuardrails.includes(id));

  return (
              <div key={trace.source_assertion_ids.join("-") || index} className="rounded-lg border border-slate-200 bg-white p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <strong className="text-slate-800">{trace.rule.title}</strong>
                  {trace.rule.code && <span className="badge">{trace.rule.code}</span>}
                  {requiredSourceIds.length > 0 && <span className="badge bg-blue-100 text-blue-700">Revisión fuente obligatoria</span>}
                </div>
                <div className="mt-2 space-y-2">
                  <ReviewItem label={trace.rule.effect === "must_not" ? "Qué no hacer" : "Qué hacer"} value={trace.rule.description} />
                  {trace.rule.condition && <ReviewItem label="Condición" value={trace.rule.condition} />}
                  <ReviewItem label="Efecto" value={trace.rule.effect ?? "No especificado"} />
                  <ReviewItem label="Madurez" value={trace.rule.maturity ?? "Sin resolver"} />
                  <ReviewItem label="Aserciones fuente" value={trace.source_assertion_ids.join(", ")} />
                  <ReviewItem label="Documentos y líneas citados" value={trace.source_section_ids.map((id) => sourceEvidenceLabel(id, sourceDocuments, content.source_section_map ?? [])).join("; ")} />
                  {trace.maturity_source_section_ids.length > 0 && <ReviewItem label="Fuentes gobernanza" value={trace.maturity_source_section_ids.join(", ")} />}
                </div>
                {requiredSourceIds.length > 0 && (
                  <fieldset className="mt-3 space-y-2 rounded-lg bg-slate-50 p-3">
                    <legend className="px-1 text-xs font-semibold text-slate-600">Confirma las fuentes de esta regla</legend>
                    {requiredSourceIds.map((assertionId) => {
                      const assertion = assertionsById.get(assertionId);
                      const isReviewed = reviewedGuardrailIds.includes(assertionId)
                        || (playbook.status === "approved" && reviewedSnapshot.has(assertionId));
                      return (
                        <label key={assertionId} className="flex cursor-pointer items-start gap-2">
                          <input
                            type="checkbox"
                            name={`review-${assertionId}`}
                            checked={isReviewed}
                            onChange={() => onReviewToggle(assertionId)}
                            disabled={playbook.status !== "draft"}
                            aria-label={`Confirmo la fuente para la aserción ${assertionId}`}
                          />
                          <span className="text-sm text-slate-700">
                            {assertion ? `${assertion.assertion_text} (${assertion.source_section_ids.join(", ")})` : assertionId}
                          </span>
                        </label>
                      );
                    })}
                  </fieldset>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <p className="text-slate-400">Sin reglas compiladas</p>
      )}
    </ReviewSection>

    <ReviewSection title="Aserciones sin resolver">
      {semantic_policy.compilation.unresolved.length > 0 ? (
        <div className="space-y-3">
          {semantic_policy.compilation.unresolved.map((unresolved, index) => (
            <div key={index} className="rounded-lg border border-amber-200 bg-amber-50 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="badge bg-amber-100 text-amber-700">{unresolved.status}</span>
                <span className="text-xs font-bold uppercase tracking-wide text-amber-700">{unresolved.reason}</span>
              </div>
              <div className="mt-2 space-y-2">
                {unresolved.assertions.map((assertion, aIndex) => (
                  <div key={aIndex} className="rounded bg-white p-2">
                    <ReviewItem label="Aserción" value={assertion.assertion_text} />
                    {assertion.concept && <ReviewItem label="Concepto" value={assertion.concept} />}
                    {assertion.scope_text && <ReviewItem label="Ámbito" value={assertion.scope_text} />}
                    {assertion.condition_text && <ReviewItem label="Condición" value={assertion.condition_text} />}
                    {!assertion.concept && <ReviewItem label="Estado" value="Concepto no normalizado" />}
                    {!assertion.maturity && <ReviewItem label="Madurez" value="Sin resolver" />}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-slate-400">Sin aserciones sin resolver</p>
      )}
    </ReviewSection>

    <ReviewSection title="Estado de extracción">
      <div className="flex flex-wrap gap-4 text-sm">
        <span className={semantic_policy.extraction_complete ? "text-emerald-600" : "text-amber-600"}>
          {semantic_policy.extraction_complete ? "Extracción completa" : "Extracción incompleta"}
        </span>
        <span className={semantic_policy.extraction_truncated ? "text-red-600" : "text-emerald-600"}>
          {semantic_policy.extraction_truncated ? "Extracción truncada" : "Sin truncamiento"}
        </span>
      </div>
    </ReviewSection>

    {semantic_policy.safety_findings.length > 0 && (
      <ReviewSection title="Hallazgos de seguridad">
        <ul className="list-disc space-y-1 pl-5 text-red-700">
          {semantic_policy.safety_findings.map((finding, index) => (
            <li key={index}>{finding}</li>
          ))}
        </ul>
      </ReviewSection>
    )}

    <ReviewSection title="Estructura v2 compatible (workflow, setter, closer, etc.)">
      <div className="space-y-5 text-sm">
        <ReviewSection title="Strategy"><ReviewItem label="Objetivo comercial" value={content.strategy.commercial_goal} /><ReviewItem label="Oferta" value={`${content.strategy.offer.summary}${content.strategy.offer.price_context ? ` · ${content.strategy.offer.price_context}` : ""}`} /><ReviewList label="Posicionamiento" items={content.strategy.positioning} /></ReviewSection>
        <ReviewSection title="Classification"><div className="grid gap-2 sm:grid-cols-2">{content.prospecting.classification_rules.map((item) => <div key={item.profile} className="rounded-xl border border-slate-200 p-3"><div className="flex items-center justify-between gap-2"><strong className="capitalize text-slate-800">{item.profile.replace(/_/g, " ")}</strong><span className="badge">Prioridad {item.priority}</span></div><p className="mt-2 text-slate-600">{item.angle}</p><p className="mt-2 text-xs text-slate-400">Ads: {item.ads_state} · Web: {item.web_state} · Inversión activa: {item.requires_active_investment_signal ? "sí" : "no"}</p></div>)}</div></ReviewSection>
        <ReviewSection title="Workflow"><ol className="space-y-2">{[...content.workflow.stages].sort((a, b) => a.step_number - b.step_number).map((stage) => <li key={stage.code} className="flex gap-3 rounded-xl bg-slate-50 p-3"><span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-teal-100 text-xs font-bold text-teal-700">{stage.step_number}</span><div className="min-w-0 flex-1"><strong className="text-slate-800">{stage.name}</strong><p className="mt-1 text-slate-600">{stage.objective}</p>{stage.rules?.length ? <div className="mt-3 space-y-2">{stage.rules.map((rule, index) => <WorkflowRuleReview key={`${stage.code}-${rule.code ?? rule.title}-${index}`} rule={rule} />)}</div> : null}</div></li>)}</ol><div className="mt-3 space-y-2"><p className="text-xs font-bold uppercase tracking-wide text-slate-400">Transiciones condicionales</p>{content.workflow.transitions.map((transition) => <p key={`${transition.from_stage}-${transition.condition}`} className="rounded-lg border border-slate-200 px-3 py-2 text-xs text-slate-600"><strong>{transition.from_stage}</strong> → <strong>{transition.to_stage}</strong> si {transition.condition} ({transition.outcome})</p>)}</div></ReviewSection>
        <ReviewSection title="Setter"><ReviewList label="Acciones permitidas" items={content.setter_policy.allowed_actions} /><ReviewRules label="Reglas" rules={[...content.setter_policy.forbidden_rules, ...content.setter_policy.language_rules]} /></ReviewSection>
        <ReviewSection title="Closer"><ReviewList label="Responsabilidades" items={content.closer_policy.responsibilities} /><ReviewList label="Preguntas de discovery" items={content.closer_policy.discovery_questions.map((item) => item.question)} /><ReviewRules label="Señales y objeciones" rules={[...content.closer_policy.fit_signals, ...content.closer_policy.no_fit_signals, ...content.closer_policy.objection_rules, ...content.closer_policy.closing_rules]} /></ReviewSection>
        <ReviewSection title="Channels"><div className="space-y-2">{content.channel_policies.map((channel) => <div key={channel.channel} className="rounded-xl border border-slate-200 p-3"><strong className="capitalize text-slate-800">{channel.channel}</strong><p className="mt-1 text-xs text-slate-600">{channel.max_new_messages_per_day ?? "Sin límite definido"} mensajes/día · {channel.minimum_delay_seconds ?? 0}s de pausa · máximo {channel.max_followups_per_lead ?? "—"} seguimientos · personalización {channel.personalization_required ? "obligatoria" : "opcional"}</p></div>)}</div></ReviewSection>
        <ReviewSection title="Templates"><div className="space-y-3">{content.templates.map((template) => <div key={template.id} className="rounded-xl border border-slate-200 p-3"><p className="text-xs font-bold uppercase tracking-wide text-slate-400">{template.purpose} · {template.channel}</p><p className="mt-2 whitespace-pre-wrap leading-6 text-slate-700">{template.text}</p></div>)}</div></ReviewSection>
        <ReviewSection title="Experiments / pending"><ReviewList label="Experimentos" items={content.experiments.map((item) => `${item.name}: ${item.variants.map((variant) => variant.description).join(" / ")}`)} /><ReviewList label="Pendiente de validar" items={content.pending_decisions.map((item) => `${item.question} (${item.options.join(", ")})`)} /></ReviewSection>
      </div>
    </ReviewSection>
  </div>;
}

function isExecutable(content: PlaybookContent): content is ExecutablePlaybookContent {
  return "strategy" in content && content.contract_version === "2.0";
}

function isSemanticPlaybook(content: PlaybookContent): content is SemanticPlaybookContent {
  return "semantic_policy" in content && content.contract_version === "3.0";
}

function hasValidPlaybookDigest(digest: string | null | undefined): boolean {
  return typeof digest === "string" && /^[0-9a-f]{64}$/.test(digest);
}

function LegacyReview({ content }: { content: LegacyPlaybookContent }) {
  return <div className="space-y-4 text-sm"><ReviewItem label="Cliente ideal" value={content.target_customer.summary} /><ReviewItem label="Oferta" value={content.offer.summary} /><ReviewItem label="Ángulo del setter" value={content.setter_guidance.opening_angle} /><ReviewItem label="Objetivo del closer" value={content.closer_guidance.conversation_goal} /><ReviewItem label="Evitar" value={content.avoid_rules.join("; ")} /><p className="rounded-xl bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-700">Esta versión es histórica (contrato v1). Se conserva para auditoría y no puede alimentar análisis nuevos.</p></div>;
}

function ExecutableReview({ content }: { content: ExecutablePlaybookContent }) {
  return <div className="space-y-5 text-sm">
    <ReviewSection title="Strategy"><ReviewItem label="Objetivo comercial" value={content.strategy.commercial_goal} /><ReviewItem label="Oferta" value={`${content.strategy.offer.summary}${content.strategy.offer.price_context ? ` · ${content.strategy.offer.price_context}` : ""}`} /><ReviewList label="Posicionamiento" items={content.strategy.positioning} /></ReviewSection>
    <ReviewSection title="Classification"><div className="grid gap-2 sm:grid-cols-2">{content.prospecting.classification_rules.map((item) => <div key={item.profile} className="rounded-xl border border-slate-200 p-3"><div className="flex items-center justify-between gap-2"><strong className="capitalize text-slate-800">{item.profile.replace(/_/g, " ")}</strong><span className="badge">Prioridad {item.priority}</span></div><p className="mt-2 text-slate-600">{item.angle}</p><p className="mt-2 text-xs text-slate-400">Ads: {item.ads_state} · Web: {item.web_state} · Inversión activa: {item.requires_active_investment_signal ? "sí" : "no"}</p></div>)}</div></ReviewSection>
    <ReviewSection title="Cómo avanza el proceso"><ol className="space-y-2">{[...content.workflow.stages].sort((a, b) => a.step_number - b.step_number).map((stage) => <li key={stage.code} className="flex gap-3 rounded-xl bg-slate-50 p-3"><span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-teal-100 text-xs font-bold text-teal-700">{stage.step_number}</span><div className="min-w-0 flex-1"><strong className="text-slate-800">{stage.name}</strong><p className="mt-1 text-slate-600">{stage.objective}</p>{stage.rules?.length ? <div className="mt-3 space-y-2">{stage.rules.map((rule, index) => <WorkflowRuleReview key={`${stage.code}-${rule.code ?? rule.title}-${index}`} rule={rule} />)}</div> : null}</div></li>)}</ol><div className="mt-3 space-y-2"><p className="text-xs font-bold uppercase tracking-wide text-slate-400">Qué hacer según la respuesta</p>{content.workflow.transitions.map((transition) => <article key={`${transition.from_stage}-${transition.condition}`} className="rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700"><p>{transitionBusinessQuestion(transition.from_stage, transition.condition)}</p><p className="mt-1 font-semibold">{conditionBusinessName(transition.condition)} → {stageBusinessName(transition.to_stage)}</p><details className="mt-1 text-xs"><summary className="cursor-pointer">Ver detalle técnico y procedencia</summary><p className="mt-1">{transition.from_stage} → {transition.to_stage} · {transition.condition} · {transition.outcome} · {transition.provenance?.source_ref || transition.provenance?.origin || "sin procedencia directa"}</p></details></article>)}</div></ReviewSection>
    <ReviewSection title="Setter"><ReviewList label="Acciones permitidas" items={content.setter_policy.allowed_actions} /><ReviewRules label="Reglas" rules={[...content.setter_policy.forbidden_rules, ...content.setter_policy.language_rules]} /></ReviewSection>
    <ReviewSection title="Closer"><ReviewList label="Responsabilidades" items={content.closer_policy.responsibilities} /><ReviewList label="Preguntas de discovery" items={content.closer_policy.discovery_questions.map((item) => item.question)} /><ReviewRules label="Señales y objeciones" rules={[...content.closer_policy.fit_signals, ...content.closer_policy.no_fit_signals, ...content.closer_policy.objection_rules, ...content.closer_policy.closing_rules]} /></ReviewSection>
    <ReviewSection title="Channels"><div className="space-y-2">{content.channel_policies.map((channel) => <div key={channel.channel} className="rounded-xl border border-slate-200 p-3"><strong className="capitalize text-slate-800">{channel.channel}</strong><p className="mt-1 text-xs text-slate-600">{channel.max_new_messages_per_day ?? "Sin límite definido"} mensajes/día · {channel.minimum_delay_seconds ?? 0}s de pausa · máximo {channel.max_followups_per_lead ?? "—"} seguimientos · personalización {channel.personalization_required ? "obligatoria" : "opcional"}</p></div>)}</div></ReviewSection>
    <ReviewSection title="Templates"><div className="space-y-3">{content.templates.map((template) => <div key={template.id} className="rounded-xl border border-slate-200 p-3"><p className="text-xs font-bold uppercase tracking-wide text-slate-400">{template.purpose} · {template.channel}</p><p className="mt-2 whitespace-pre-wrap leading-6 text-slate-700">{template.text}</p></div>)}</div></ReviewSection>
    <ReviewSection title="Experiments / pending"><ReviewList label="Experimentos" items={content.experiments.map((item) => `${item.name}: ${item.variants.map((variant) => variant.description).join(" / ")}`)} /><ReviewList label="Pendiente de validar" items={content.pending_decisions.map((item) => `${item.question} (${item.options.join(", ")})`)} /></ReviewSection>
  </div>;
}

function GenericWorkflowGraphReview({
  graph, sourceDocuments, sourceSectionMap,
}: {
  graph: NonNullable<SemanticPlaybookContent["generic_workflow_graph"]>;
  sourceDocuments: SourceDocumentReview[];
  sourceSectionMap: NonNullable<SemanticPlaybookContent["source_section_map"]>;
}) {
  const nodeById = new Map(graph.nodes.map((node) => [node.node_id, node]));
  const primitiveNames: Record<string, string> = {
    PRECEDES: "Continúa después",
    ROUTES_IF: "Toma una ruta según la condición",
    HANDOFF_IF: "Transfiere la atención según la condición",
    STOPS_IF: "Detiene el proceso si se cumple la condición",
    WAIT_THEN: "Espera antes de continuar",
    ELIGIBLE_IF: "Es elegible cuando se cumple la condición",
  };
  const statusNames: Record<string, string> = {
    compiled_candidate: "Candidato estructurado · requiere revisión",
    review_only: "Solo para revisión humana",
    supported: "respaldado por la fuente",
    ambiguous: "ambiguo",
    source_conflict: "conflicto en la fuente",
    unsupported: "sin respaldo suficiente",
    contradicted: "contradice una instrucción explícita",
    insufficient_evidence: "evidencia insuficiente",
  };
  return <ReviewSection title="Proceso detectado en la documentación">
    <p className="mb-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">
      Este mapa conserva las relaciones propuestas y sus dudas. Todavía no autoriza acciones del Setter.
    </p>
    {graph.nodes.length > 0 && <ul className="mb-4 space-y-2">
      {graph.nodes.map((node) => <li key={node.node_id} className="rounded-lg border border-slate-200 p-3">
        <strong>{node.label}</strong><span className="ml-2 text-xs text-slate-500">{node.kind === "process_phase" ? "etapa" : node.kind === "action" ? "acción" : "resultado"}</span>
        {node.review_status !== "supported_candidate" && <p className="mt-1 text-xs text-amber-700">Requiere confirmación humana.</p>}
        <p className="mt-1 text-xs text-slate-500">Fuente: {node.source_section_ids.map((id) => sourceEvidenceLabel(id, sourceDocuments, sourceSectionMap)).join("; ") || "sin referencia disponible"}</p>
      </li>)}
    </ul>}
    {graph.relations.length > 0 ? <ul className="space-y-2">
      {graph.relations.map((relation) => {
        const from = relation.from_node_id ? nodeById.get(relation.from_node_id)?.label : null;
        const to = relation.to_node_id ? nodeById.get(relation.to_node_id)?.label : null;
        const timing = relation.timing
          ? relation.timing.kind === "immediate" ? "de inmediato" : `después de ${relation.timing.minimum}${relation.timing.maximum != null && relation.timing.maximum !== relation.timing.minimum ? `–${relation.timing.maximum}` : ""} ${relation.timing.unit ?? ""}`
          : null;
        return <li key={relation.relation_id} className="rounded-lg border border-slate-200 p-3">
          <p className="font-medium text-slate-800">{from ?? "Inicio / condición"} · {primitiveNames[relation.primitive] ?? "Relación detectada"}{to ? ` · ${to}` : ""}</p>
          <p className="mt-1 text-xs text-slate-600">Estado: {statusNames[relation.evidence_status] ?? relation.evidence_status}; {statusNames[relation.compilation_status] ?? relation.compilation_status}{timing ? `; ${timing}` : ""}{relation.route_role === "fallback" ? "; ruta alternativa de respaldo" : ""}{relation.optionality === "optional" ? "; opcional" : ""}</p>
          {relation.review_reason && <p className="mt-1 text-xs text-amber-700">{relation.review_reason}</p>}
          <p className="mt-1 text-xs text-slate-500">Fuente: {relation.source_section_ids.map((id) => sourceEvidenceLabel(id, sourceDocuments, sourceSectionMap)).join("; ") || "sin referencia disponible"}</p>
        </li>;
      })}
    </ul> : <p className="text-slate-500">No se identificaron relaciones de proceso suficientemente sustentadas.</p>}
    {graph.conflicts.length > 0 && <div className="mt-4 space-y-2"><h4 className="text-xs font-bold uppercase tracking-wide text-amber-700">Rutas en conflicto</h4>
      {graph.conflicts.map((conflict) => <article key={conflict.conflict_id} className="rounded-lg border border-amber-200 bg-amber-50 p-3">
        <p>{conflict.summary}</p><p className="mt-1 text-xs text-slate-600">Pendiente de decisión humana. Fuente: {conflict.source_section_ids.map((id) => sourceEvidenceLabel(id, sourceDocuments, sourceSectionMap)).join("; ")}</p>
      </article>)}
    </div>}
  </ReviewSection>;
}

function ReviewSection({ title, children }: { title: string; children: ReactNode }) { return <section className="border-t border-slate-100 pt-4 first:border-t-0 first:pt-0"><h3 className="mb-3 text-sm font-bold text-slate-800">{title}</h3>{children}</section>; }
function ReviewList({ label, items }: { label: string; items: string[] }) { return <div className="mb-3 last:mb-0"><p className="text-xs font-bold uppercase tracking-wide text-slate-400">{label}</p>{items.length ? <ul className="mt-1 list-disc space-y-1 pl-5 text-slate-600">{items.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul> : <p className="mt-1 text-slate-400">Sin información</p>}</div>; }
function ReviewRules({ label, rules }: { label: string; rules: Rule[] }) { return <ReviewList label={label} items={rules.map((rule) => `${rule.title}: ${rule.description}`)} />; }

function WorkflowRuleReview({ rule }: { rule: Rule }) {
  const actionLabel = rule.effect === "must_not" ? "Qué no hacer" : rule.effect ? "Qué hacer" : null;
  const source = rule.provenance?.source_ref || rule.provenance?.origin;

  return <div className="rounded-lg border border-slate-200 bg-white p-3">
    <div className="flex flex-wrap items-center gap-2">
      <strong className="text-slate-800">{rule.title}</strong>
      {rule.code && <span className="badge">{rule.code}</span>}
    </div>
    <div className="mt-2 space-y-2">
      <ReviewItem label={actionLabel || "Regla"} value={rule.description} />
      {rule.condition && <ReviewItem label="Condición" value={rule.condition} />}
      {rule.maturity && <ReviewItem label="Madurez" value={rule.maturity} />}
      {source && <ReviewItem label="Fuente" value={source} />}
    </div>
  </div>;
}

function ReviewItem({ label, value }: { label: string; value: string }) {
  return <div><p className="text-xs font-bold uppercase tracking-wide text-slate-400">{label}</p><p className="mt-1 leading-6 text-slate-700">{value || "Sin información"}</p></div>;
}

function Status({ status }: { status: PlaybookStatus }) {
  const styles = { draft: "bg-amber-100 text-amber-700", approved: "bg-emerald-100 text-emerald-700", archived: "bg-slate-100 text-slate-500", rejected: "bg-red-100 text-red-700" };
  return <span className={`status ${styles[status]}`}>{statusLabel(status)}</span>;
}

function statusLabel(status: PlaybookStatus) {
  return { draft: "Borrador", approved: "Aprobado", archived: "Archivado", rejected: "Rechazado" }[status];
}

function sourceLabel(source: Playbook["source_type"]) {
  return { questionnaire: "Cuestionario", document: "Texto pegado", manual: "Manual" }[source];
}

function stageBusinessName(stage: string): string {
  const names: Record<string, string> = {
    sourcing: "Encontrar prospectos", enrichment: "Completar información", classification: "Priorizar prospectos",
    touch_1: "Primer contacto", touch_1_5: "Continuación inmediata", branch_identification: "Identificar quién responde",
    touch_2: "Segundo mensaje", touch_3: "Tercer mensaje", pre_close_call: "Llamada de rescate",
    exploration_call: "Llamada de exploración", complete_analysis: "Análisis completo", proposal_closing: "Propuesta y cierre",
    follow_up_graceful_close: "Seguimiento o cierre respetuoso",
  };
  return names[stage] ?? stage.replace(/_/g, " ");
}

function conditionBusinessName(condition: string): string {
  const names: Record<string, string> = {
    bot_response: "responde un bot o asistente", human_first: "responde directamente una persona",
    whatsapp_not_scheduled: "no se logra agendar por WhatsApp", negative_response: "la respuesta es negativa",
    exploration_completed: "termina la llamada de exploración", clear_interest: "muestra interés claro",
    no_response: "no hay respuesta", fit_confirmed: "se confirma que hay encaje", no_fit: "se confirma que no hay encaje",
    call_scheduled: "queda agendada una llamada",
  };
  return names[condition] ?? condition.replace(/_/g, " ");
}

function transitionBusinessQuestion(from: string, condition: string): string {
  return `¿Qué debe hacer el vendedor durante “${stageBusinessName(from)}” si ${conditionBusinessName(condition)}?`;
}

function conflictQuestion(conflict: WorkflowStageConflict): string {
  const transition = conflict.candidates.flatMap((candidate) => candidate.transitions)[0]?.transition;
  if (transition) return transitionBusinessQuestion(transition.from_stage, transition.condition);
  return `¿Qué debe ocurrir durante “${stageBusinessName(conflict.stage_code)}” según el proceso vigente?`;
}

function businessField(field: string): string {
  return ({ price_context: "el precio", target_customer: "el cliente ideal", offer_summary: "la oferta", location_or_market: "el mercado" } as Record<string, string>)[field] ?? field.replace(/_/g, " ");
}

function businessClaimOrigin(origin: string): string {
  return ({ seller_profile: "Perfil del negocio", source_document: "Documento cargado", pasted_document: "Texto proporcionado" } as Record<string, string>)[origin] ?? origin;
}

function sourceEvidenceLabel(
  id: string,
  documents: SourceDocumentReview[],
  mappedSections: NonNullable<SemanticPlaybookContent["source_section_map"]>,
): string {
  const mapped = mappedSections.find((section) => section.extraction_section_id === id);
  if (mapped) {
    const splitSection = mappedSections.filter((section) =>
      section.document_id === mapped.document_id && section.original_section_id === mapped.original_section_id,
    ).length > 1;
    const fragment = splitSection && mapped.start_byte_offset != null && mapped.end_byte_offset != null
      ? ` · fragmento ${mapped.start_byte_offset}–${mapped.end_byte_offset} dentro del apartado`
      : "";
    return `${mapped.filename} · ${mapped.heading_path.join(" / ")} · líneas ${mapped.start_line}–${mapped.end_line}${fragment}`;
  }
  for (const document of documents) {
    const section = document.sections.find((item) => item.id === id);
    if (section) return `${document.original_filename} · ${section.heading_path?.join(" / ") || section.heading} · líneas ${section.start_line}–${section.end_line}`;
  }
  return id;
}

function businessBlockerLabel(blocker: string): string {
  const conflict = blocker.match(/^Unresolved workflow conflict: (.+)$/i);
  if (conflict) return `Hay más de una instrucción para “${stageBusinessName(conflict[1])}”; revisa las interpretaciones antes de aprobar.`;
  const missing = blocker.match(/^Workflow is missing required transitions: (.+)$/i);
  if (missing) return `Falta definir qué debe pasar cuando: ${missing[1].split(",").map((item) => conditionBusinessName(item.trim())).join("; ")}.`;
  if (blocker.startsWith("Source disagreement needs human review:")) { const parts = blocker.split(":"); return `Confirma qué dato de negocio está vigente: ${businessField(parts[parts.length - 1]?.trim() ?? "")}.`; }
  return blocker;
}
