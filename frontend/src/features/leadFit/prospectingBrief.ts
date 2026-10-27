// View-model puro de la ficha de prospección (contrato prospecting-brief-1.0).
// Une el contexto server-owned, la evaluación ya emitida y las fuentes descargadas.
// No calcula scores, no clasifica, no llama al servidor y no muta sus entradas.
// La autoridad comercial sigue siendo el backend: aquí sólo se presenta.

import type { FitEvidenceSource } from "./api";
import type {
  AssessmentStatus,
  CriterionKind,
  CriterionSpec,
  FitClassification,
  FitEvaluationContext,
  FitEvaluationRead,
} from "./types";

export type BriefState = "loading" | "unavailable" | "pending" | "failed" | "stale" | "ready";
export type CriterionGroup = "profile" | "signals" | "exclusions" | "other";

export interface ProspectingBriefInput {
  leadId: string;
  authorityState: "loading" | "ready" | "unavailable";
  context: FitEvaluationContext | null;
  evaluation: FitEvaluationRead | null;
  sources: Array<FitEvidenceSource & { safe_url: string | null }>;
}

export interface BriefCriterion {
  id: string;
  group: CriterionGroup;
  kind: CriterionKind;
  description: string;
  result: AssessmentStatus;
  confidence: number;
  evidenceIds: string[];
}

export interface ProspectingBriefQuestion {
  criterionId: string;
  text: string;
}

export interface ProspectingBriefView {
  contractVersion: "prospecting-brief-1.0";
  state: BriefState;
  classification: FitClassification | null;
  coverage: number | null;
  fitScore: number | null;
  criteria: BriefCriterion[];
  questions: ProspectingBriefQuestion[];
  buyingIntent: "not_assessed";
  policyVersion: number | null;
  policyApprovedAt: string | null;
}

const PROFILE_POINTERS = [
  "commercial_context.icp.industries",
  "commercial_context.icp.geographies",
  "commercial_context.icp.buyer_roles",
  "commercial_context.icp.desired_traits",
] as const;

const SIGNAL_POINTER_PATTERN = /^commercial_context\.signals\[(\d+)\](?:\..+)?$/;
const INDEXED_POINTER_PATTERN = /^([A-Za-z0-9_.-]+)\[(\d+)\]$/;

const ASSESSMENT_STATUSES: AssessmentStatus[] = [
  "matches",
  "does_not_match",
  "not_established",
  "conflicting",
];
const CLASSIFICATIONS: FitClassification[] = ["qualified", "needs_more_research", "not_qualified"];

const MAX_QUESTIONS = 3;

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isIndexedPointer(pointer: string, base: string): boolean {
  const match = INDEXED_POINTER_PATTERN.exec(pointer);
  if (!match) return false;
  if (match[1] !== base) return false;
  // Índice entero no negativo y sin ceros a la izquierda.
  return /^(0|[1-9]\d*)$/.test(match[2]);
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasOnlyKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return Object.keys(value).every((key) => keys.includes(key));
}

function isExactRule(value: unknown): boolean {
  if (value === null) return true;
  if (!isPlainObject(value) || typeof value.kind !== "string") return false;
  if (value.kind === "field_in") {
    return hasOnlyKeys(value, ["kind", "field", "values"])
      && typeof value.field === "string"
      && Array.isArray(value.values)
      && value.values.every((item) => typeof item === "string");
  }
  if (value.kind === "numeric_range") {
    return hasOnlyKeys(value, ["kind", "field", "min", "max", "unit"])
      && typeof value.field === "string"
      && (value.min === null || isFiniteNumber(value.min))
      && (value.max === null || isFiniteNumber(value.max))
      && (value.unit === null || typeof value.unit === "string");
  }
  if (value.kind === "geo_within") {
    return hasOnlyKeys(value, ["kind", "center_lat", "center_lon", "radius_km", "polygon"])
      && [value.center_lat, value.center_lon, value.radius_km].every(
        (item) => item === null || isFiniteNumber(item),
      )
      && (value.polygon === null || (Array.isArray(value.polygon)
        && value.polygon.every((point) => Array.isArray(point)
          && point.length === 2 && point.every(isFiniteNumber))));
  }
  return false;
}

function isCriterion(value: unknown): value is CriterionSpec {
  if (!isPlainObject(value) || !hasOnlyKeys(value, [
    "id", "kind", "description", "source_pointer", "weight", "evaluator", "exact_rule", "must_resolve",
  ])) return false;
  return typeof value.id === "string" && value.id.length > 0
    && (value.kind === "required" || value.kind === "preference" || value.kind === "exclusion")
    && typeof value.description === "string" && value.description.trim().length > 0
    && typeof value.source_pointer === "string" && value.source_pointer.length > 0
    && isFiniteNumber(value.weight) && value.weight >= 0
    && (value.evaluator === "semantic" || value.evaluator === "exact")
    && isExactRule(value.exact_rule)
    && typeof value.must_resolve === "boolean";
}

/**
 * Agrupación cerrada por contratos: kind=exclusion manda; después pointers
 * exactos de perfil; después pointers de señales; cualquier otro pointer válido
 * queda en "other". Nunca se infiere el grupo por texto, id, nombre o dominio.
 */
export function groupCriterion(criterion: CriterionSpec): CriterionGroup {
  if (criterion.kind === "exclusion") return "exclusions";
  const pointer = typeof criterion.source_pointer === "string" ? criterion.source_pointer : "";
  if (PROFILE_POINTERS.some((base) => pointer === base || isIndexedPointer(pointer, base))) {
    return "profile";
  }
  if (SIGNAL_POINTER_PATTERN.test(pointer)) return "signals";
  return "other";
}

function questionPriority(criterion: CriterionSpec): number {
  if (criterion.kind === "required" || criterion.must_resolve) return 0;
  if (criterion.kind === "exclusion") return 1;
  return 2;
}

function emptyView(state: BriefState): ProspectingBriefView {
  return {
    contractVersion: "prospecting-brief-1.0",
    state,
    classification: null,
    coverage: null,
    fitScore: null,
    criteria: [],
    questions: [],
    buyingIntent: "not_assessed",
    policyVersion: null,
    policyApprovedAt: null,
  };
}

interface RawAssessment {
  criterion_id: unknown;
  result: unknown;
  confidence: unknown;
  evidence_ids: unknown;
}

function readAssessments(result: Record<string, unknown>): RawAssessment[] | null {
  const assessments = result.assessments;
  if (!Array.isArray(assessments)) return null;
  return assessments.map((item) => {
    const record = isPlainObject(item) ? item : {};
    return {
      criterion_id: record.criterion_id,
      result: record.result,
      confidence: record.confidence,
      evidence_ids: record.evidence_ids,
    };
  });
}

/**
 * Construye la ficha de prospección a partir de lecturas ya obtenidas.
 * Función pura: no escribe, no llama al servidor y no altera sus entradas.
 */
export function buildProspectingBrief(input: ProspectingBriefInput): ProspectingBriefView {
  if (input.authorityState === "loading") return emptyView("loading");
  if (input.authorityState === "unavailable") return emptyView("unavailable");

  const evaluation = input.evaluation;
  if (!evaluation) return emptyView("unavailable");
  if (evaluation.status === "pending" || evaluation.status === "running") return emptyView("pending");
  if (evaluation.status === "failed") return emptyView("failed");
  if (evaluation.status !== "completed") return emptyView("unavailable");

  const context = input.context;
  if (!context) return emptyView("unavailable");
  const policy = context.policy ?? null;
  // Un servidor sin snapshot no autoriza a reconstruir la política por nombres.
  if (!policy) return emptyView("unavailable");

  // Vigencia: cualquier diferencia de lead, política o fuentes invalida la ficha.
  if (evaluation.lead_id !== input.leadId) return emptyView("stale");
  if (context.policy_id !== policy.id) return emptyView("stale");
  if (evaluation.policy_id !== context.policy_id) return emptyView("stale");
  if (!context.policy_digest || context.policy_digest !== policy.policy_digest) {
    return emptyView("stale");
  }
  if (evaluation.policy_digest !== context.policy_digest) return emptyView("stale");
  if (evaluation.source_digest !== context.expected_source_digest) return emptyView("stale");

  const result = evaluation.result;
  if (!isPlainObject(result)) return emptyView("unavailable");

  // Un digest ausente o mal formado es un contrato inválido, no un cambio de
  // política: un digest distinto (presente y verificable) sí es deriva (stale).
  if (typeof result.policy_digest !== "string" || typeof result.source_digest !== "string") {
    return emptyView("unavailable");
  }
  if (result.policy_digest !== context.policy_digest) return emptyView("stale");
  if (result.source_digest !== context.expected_source_digest) return emptyView("stale");

  // Coherencia entre el snapshot de resultado y las columnas de la evaluación.
  if (result.classification !== evaluation.classification) return emptyView("unavailable");
  if (!Object.is(result.coverage, evaluation.coverage)) return emptyView("unavailable");
  if (!Object.is(result.fit_score, evaluation.fit_score)) return emptyView("unavailable");

  const classification = evaluation.classification;
  if (classification === null || !CLASSIFICATIONS.includes(classification)) {
    return emptyView("unavailable");
  }
  const coverage = evaluation.coverage;
  if (coverage !== null && (!isFiniteNumber(coverage) || coverage < 0 || coverage > 100)) {
    return emptyView("unavailable");
  }
  const fitScore = evaluation.fit_score;
  if (fitScore !== null && (!isFiniteNumber(fitScore) || fitScore < 0 || fitScore > 100)) {
    return emptyView("unavailable");
  }

  const policyCriteria = policy.content?.criteria;
  if (!Array.isArray(policyCriteria) || policyCriteria.length === 0
    || !policyCriteria.every(isCriterion)
    || new Set(policyCriteria.map((criterion) => criterion.id)).size !== policyCriteria.length) {
    return emptyView("unavailable");
  }

  const assessments = readAssessments(result);
  if (!assessments) return emptyView("unavailable");

  const assessmentByCriterion = new Map<string, RawAssessment>();
  for (const assessment of assessments) {
    if (typeof assessment.criterion_id !== "string" || !assessment.criterion_id) {
      return emptyView("unavailable");
    }
    if (assessmentByCriterion.has(assessment.criterion_id)) return emptyView("unavailable");
    if (!ASSESSMENT_STATUSES.includes(assessment.result as AssessmentStatus)) {
      return emptyView("unavailable");
    }
    if (!isFiniteNumber(assessment.confidence) || assessment.confidence < 0 || assessment.confidence > 1) {
      return emptyView("unavailable");
    }
    if (!Array.isArray(assessment.evidence_ids)
      || assessment.evidence_ids.some((id) => typeof id !== "string")) {
      return emptyView("unavailable");
    }
    assessmentByCriterion.set(assessment.criterion_id, assessment);
  }

  const criteria: BriefCriterion[] = [];
  const pending: Array<{ criterion: CriterionSpec; brief: BriefCriterion }> = [];
  for (const criterion of policyCriteria) {
    const assessment = assessmentByCriterion.get(criterion.id);
    if (!assessment) return emptyView("unavailable");
    const brief: BriefCriterion = {
      id: criterion.id,
      group: groupCriterion(criterion),
      kind: criterion.kind,
      description: criterion.description,
      result: assessment.result as AssessmentStatus,
      confidence: assessment.confidence as number,
      evidenceIds: assessment.evidence_ids as string[],
    };
    criteria.push(brief);
    if (brief.result === "not_established" || brief.result === "conflicting") {
      pending.push({ criterion, brief });
    }
  }
  // Exactamente un juicio por criterio aprobado: ni de más, ni de menos.
  if (assessmentByCriterion.size !== policyCriteria.length) return emptyView("unavailable");

  const ordered = pending
    .map((item, index) => ({ ...item, index, priority: questionPriority(item.criterion) }))
    .sort((left, right) => (left.priority - right.priority) || (left.index - right.index));

  const questions: ProspectingBriefQuestion[] = ordered.slice(0, MAX_QUESTIONS).map((item) => ({
    criterionId: item.brief.id,
    text: item.brief.result === "conflicting"
      ? `¿Qué evidencia permite aclarar el conflicto sobre esta condición: «${item.brief.description}»?`
      : `¿Qué evidencia permite verificar esta condición: «${item.brief.description}»?`,
  }));

  return {
    contractVersion: "prospecting-brief-1.0",
    state: "ready",
    classification,
    coverage,
    fitScore,
    criteria,
    questions,
    buyingIntent: "not_assessed",
    policyVersion: isFiniteNumber(policy.version) ? policy.version : null,
    policyApprovedAt: typeof policy.approved_at === "string" ? policy.approved_at : null,
  };
}
