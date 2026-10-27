// Capa de API de Lead Fit / Jev (T08).
// Todas las llamadas van al backend y usan apiFetch (que ya incluye CSRF y headers).

import { apiFetch } from "../../lib/api";
import type {
  FitEvaluationContext,
  FitEvaluationRead,
  FitPolicyContent,
  FitPolicyPreview,
  FitPolicyRead,
  FitResearchGrantRead,
  FitResearchPlan,
  SetterDraftReadinessRead,
} from "./types";

export interface PreviewPolicyInput { playbook_id: string }

export interface ApprovePolicyInput {
  playbook_id: string;
  expected_commercial_digest: string;
  content: FitPolicyContent;
  confirmed: boolean;
}

export interface CreateEvaluationInput {
  lead_id: string;
  expected_policy_digest: string;
  expected_source_digest: string;
  idempotency_key: string;
  retry_of?: string | null;
  reenqueue?: boolean;
}

export interface AuthorizeResearchInput {
  lead_id: string;
  evaluation_id: string;
  purpose: "fit" | "contact";
  expected_plan_hash: string;
  confirmed: boolean;
  idempotency_key: string;
  monetary_cap_usd?: number | null;
}

export async function previewPolicy(input: PreviewPolicyInput) {
  return apiFetch<FitPolicyPreview>("/lead-fit/policies/preview", {
    method: "POST", body: JSON.stringify(input),
  });
}

export async function approvePolicy(input: ApprovePolicyInput) {
  return apiFetch<Record<string, unknown>>(`/lead-fit/policies`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function getCurrentPolicy(playbookId: string) {
  return apiFetch<(FitPolicyRead & { content: FitPolicyContent }) | null>(
    `/lead-fit/policies/current?playbook_id=${encodeURIComponent(playbookId)}`
  );
}

export async function getCapabilities(): Promise<unknown> {
  return apiFetch<unknown>("/lead-fit/capabilities");
}

export async function createEvaluation(input: CreateEvaluationInput) {
  const { lead_id: leadId, idempotency_key: idempotencyKey, ...body } = input;
  return apiFetch<FitEvaluationRead>(
    `/leads/${encodeURIComponent(leadId)}/fit-evaluations`,
    {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ retry_of: null, reenqueue: false, ...body }),
    },
  );
}

export async function getLatestEvaluation(leadId: string) {
  return apiFetch<FitEvaluationRead | null>(
    `/leads/${encodeURIComponent(leadId)}/fit-evaluations/latest`
  );
}

export async function getEvaluationContext(leadId: string) {
  return apiFetch<FitEvaluationContext>(
    `/leads/${encodeURIComponent(leadId)}/fit-evaluations/context`
  );
}

export async function getSetterReadiness(leadId: string) {
  return apiFetch<SetterDraftReadinessRead>(
    `/leads/${encodeURIComponent(leadId)}/setter/readiness`,
  );
}

export async function previewResearch(input: {
  lead_id: string;
  purpose?: "fit" | "contact";
}) {
  return apiFetch<FitResearchPlan>(
    `/leads/${encodeURIComponent(input.lead_id)}/fit-research/preview`,
    {
      method: "POST",
      body: JSON.stringify({ purpose: input.purpose ?? "fit" }),
    },
  );
}

export async function authorizeResearch(input: AuthorizeResearchInput) {
  const { lead_id: leadId, evaluation_id: evaluationId, purpose, expected_plan_hash, confirmed, idempotency_key: idempotencyKey, monetary_cap_usd } = input;
  return apiFetch<Record<string, unknown>>(
    `/leads/${encodeURIComponent(leadId)}/fit-research?evaluation_id=${encodeURIComponent(evaluationId)}`,
    {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
      body: JSON.stringify({ purpose, expected_plan_hash, confirmed, ...(monetary_cap_usd !== undefined ? { monetary_cap_usd } : {}) }),
    },
  );
}

export async function getResearchStatus(leadId: string, grantId: string) {
  return apiFetch<FitResearchGrantRead | null>(
    `/leads/${encodeURIComponent(leadId)}/fit-research/${encodeURIComponent(grantId)}`,
  );
}

export interface FitEvidenceSource {
  evidence_id: string;
  source_url: string | null;
  source_title: string | null;
  source_type: string;
  excerpt: string;
}

export async function getFitEvidence(leadId: string, researchId: string) {
  const rows = await apiFetch<FitEvidenceSource[]>(
    `/leads/${encodeURIComponent(leadId)}/research/evidence?research_id=${encodeURIComponent(researchId)}`,
  );
  return rows.map((row) => ({ ...row, safe_url: safeExternalUrl(row.source_url) }));
}

function safeExternalUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const parsed = new URL(value);
    if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) return null;
    return parsed.href;
  } catch { return null; }
}
