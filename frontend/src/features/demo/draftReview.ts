import { apiFetch } from "../../lib/api";

export interface DemoDraftReview {
  execution_id: string;
  draft_digest: string;
  status: "pending_human_review" | "approved_for_manual_use" | "contact_recorded" | "blocked";
  reason: string | null;
  approved_at: string | null;
}

export function getDraftReview(leadId: string, executionId: string) {
  return apiFetch<DemoDraftReview>(`/demo/leads/${encodeURIComponent(leadId)}/draft-review?execution_id=${encodeURIComponent(executionId)}`);
}

export function approveDraft(leadId: string, review: DemoDraftReview) {
  return apiFetch<DemoDraftReview>(`/demo/leads/${encodeURIComponent(leadId)}/draft-review`, {
    method: "POST",
    body: JSON.stringify({ execution_id: review.execution_id, draft_digest: review.draft_digest, warnings_acknowledged: true }),
  });
}
