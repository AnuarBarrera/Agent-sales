// Tipos de Lead Fit / Jev para el frontend (T08).
// Sincronizados con los contratos del backend.

export type FitClassification = "qualified" | "needs_more_research" | "not_qualified";
export type FitEvaluationStatus = "pending" | "running" | "completed" | "failed";
export type CriterionKind = "required" | "preference" | "exclusion";
export type AssessmentStatus = "matches" | "does_not_match" | "not_established" | "conflicting";

export interface ExactRuleFieldIn {
  kind: "field_in";
  field: string;
  values: string[];
}

export interface ExactRuleNumericRange {
  kind: "numeric_range";
  field: string;
  min: number | null;
  max: number | null;
  unit: string | null;
}

export interface ExactRuleGeoWithin {
  kind: "geo_within";
  center_lat: number | null;
  center_lon: number | null;
  radius_km: number | null;
  polygon: number[][] | null;
}

export type ExactRule = ExactRuleFieldIn | ExactRuleNumericRange | ExactRuleGeoWithin;

export interface CriterionSpec {
  id: string;
  kind: CriterionKind;
  description: string;
  source_pointer: string;
  weight: number;
  evaluator: "semantic" | "exact";
  exact_rule: ExactRule | null;
  must_resolve: boolean;
}

export interface FitPolicyContent {
  contract_version: string;
  criteria: CriterionSpec[];
  accept_score: number;
  min_coverage: number;
  choice_confidence_floor: number;
  entity_confidence_floor: number;
  max_evidence_age_hours: number;
}

export interface FitPolicyPreview {
  content: FitPolicyContent;
  expected_policy_digest: string;
  expected_commercial_digest: string;
}

export interface FitPolicyCreate {
  playbook_id: string;
  expected_commercial_digest: string;
  content: FitPolicyContent;
  confirmed: boolean;
}

export interface FitPolicyRead {
  id: string;
  workspace_id: string;
  playbook_id: string;
  version: number;
  commercial_digest: string;
  content: FitPolicyContent;
  policy_digest: string;
  actor_id: string;
  approved_at: string;
  status: string;
}

export interface CriterionAssessment {
  criterion_id: string;
  result: AssessmentStatus;
  evidence_ids: string[];
  method: "code" | "jev";
  confidence: number;
  probabilities: Record<string, number>;
  reason_code: string;
}

export interface FitDecision {
  classification: FitClassification;
  fit_score: number | null;
  coverage: number;
  reasons: string[];
  missing_criterion_ids: string[];
  contact_ready: boolean;
}

export interface FitEvaluationRead {
  id: string;
  workspace_id: string;
  lead_id: string;
  policy_id: string;
  research_id: string | null;
  previous_id: string | null;
  policy_digest: string;
  source_digest: string;
  question_digest: string | null;
  engine_version: string;
  requested_model: string;
  response_model: string | null;
  result_digest: string | null;
  status: FitEvaluationStatus;
  classification: FitClassification | null;
  fit_score: number | null;
  coverage: number | null;
  result: Record<string, unknown> | null;
  audit: Record<string, unknown> | null;
  call_count: number;
  error_code: string | null;
  requested_at: string;
  completed_at: string | null;
}

export interface FitEvaluationResultSnapshot {
  classification: FitClassification | null;
  fit_score: number | null;
  coverage: number | null;
  reasons: string[];
  missing_criterion_ids: string[];
  contact_ready: boolean;
  contact_readiness: Record<string, boolean>;
  policy_digest: string;
  source_digest: string;
  assessments: CriterionAssessment[];
}

export interface FitEvaluationContext {
  policy_id: string | null;
  policy_digest: string | null;
  expected_source_digest: string;
  research_id?: string | null;
  // Snapshot aditivo y opcional de la misma política ya verificada en servidor.
  // Su ausencia significa "detalle no disponible"; nunca se reconstruye aquí.
  policy?: FitPolicyRead | null;
}

export interface SetterDraftReadinessRead {
  contract_version: "setter-readiness-1.0";
  draft_ready: boolean;
  draft_purpose: "discovery" | "opportunity_grounded" | null;
  reason_codes: string[];
  identity_status: "matched" | "unresolved";
  contact_channels: Array<"email" | "phone">;
  lead_id: string;
  evaluation_id: string | null;
  policy_id: string | null;
  playbook_id: string | null;
  research_id: string | null;
  policy_digest: string | null;
  source_digest: string | null;
  result_digest: string | null;
}

export interface FitResearchPlan {
  lead_id: string;
  evaluation_id: string;
  policy_id: string;
  source_digest: string;
  plan_hash: string;
  purpose: "fit" | "contact";
  missing_criterion_ids: string[];
  query: string;
  source_strategy: "maps" | "web" | "mixed";
  max_search: number;
  max_extract: number;
  max_jev: number;
  max_rounds: number;
  monetary_cap_usd: number | null;
  expires_at: string | null;
}

export interface FitResearchGrantRead {
  id: string;
  workspace_id: string;
  lead_id: string;
  evaluation_id: string;
  policy_id: string;
  status: string;
  error_code?: string | null;
  result_evaluation_id?: string | null;
  consumed_search: number;
  consumed_extract: number;
  consumed_jev: number;
  consumed_usd: number | null;
  max_search: number;
  max_extract: number;
  max_jev: number;
  max_rounds: number;
  monetary_cap_usd: number | null;
}

export interface ContactReadiness {
  email: boolean;
  whatsapp: boolean;
  phone: boolean;
  web_form: boolean;
}

export interface LeadFitResult {
  evaluation: FitEvaluationRead;
  decision: FitDecision;
  contact_readiness: ContactReadiness;
  assessments: CriterionAssessment[];
}
