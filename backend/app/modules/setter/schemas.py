from datetime import datetime
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.modules.sales_playbook.schemas import SalesChannel

SetterReference = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$",
    ),
]

PendingReviewStatus = Literal["pending_human_review"]
PENDING_HUMAN_REVIEW: PendingReviewStatus = "pending_human_review"


class SetterDecisionAction(str, Enum):
    DRAFT_TOUCH_1 = "draft_touch_1"
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    DO_NOT_CONTACT = "do_not_contact"


class SetterContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PersonalizationClaim(SetterContract):
    claim: str = Field(..., min_length=1, max_length=1000)
    evidence_ids: list[SetterReference] = Field(..., min_length=1, max_length=20)

    @field_validator("evidence_ids")
    @classmethod
    def evidence_ids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("evidence_ids must not contain duplicates")
        return value


def _unique_references(value: list[str], field_name: str) -> list[str]:
    if len(value) != len(set(value)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return value


class SetterModelOutput(SetterContract):
    """The only provider-authored Setter payload; persistence authority is excluded."""

    action: SetterDecisionAction
    rationale: str = Field(..., min_length=1, max_length=2000)
    stage: Literal["touch_1"]
    channel: SalesChannel | Literal["email"]
    evidence_ids: list[SetterReference] = Field(..., min_length=1, max_length=20)
    guardrail_ids: list[SetterReference] = Field(..., max_length=20)
    assertion_ids: list[SetterReference] = Field(..., max_length=20)
    message: str | None = Field(..., min_length=1, max_length=5000)
    cta: str | None = Field(..., min_length=1, max_length=1000)
    personalization_claims: list[PersonalizationClaim] | None = Field(..., max_length=10)

    @field_validator("evidence_ids")
    @classmethod
    def evidence_ids_are_unique(cls, value: list[str]) -> list[str]:
        return _unique_references(value, "evidence_ids")

    @field_validator("guardrail_ids")
    @classmethod
    def guardrail_ids_are_unique(cls, value: list[str]) -> list[str]:
        return _unique_references(value, "guardrail_ids")

    @field_validator("assertion_ids")
    @classmethod
    def assertion_ids_are_unique(cls, value: list[str]) -> list[str]:
        return _unique_references(value, "assertion_ids")

    @model_validator(mode="after")
    def message_matches_action(self) -> "SetterModelOutput":
        has_draft = self.action == SetterDecisionAction.DRAFT_TOUCH_1
        if has_draft and (self.message is None or self.personalization_claims is None):
            raise ValueError("message and personalization_claims are required for draft_touch_1")
        if not has_draft and (
            self.message is not None or self.cta is not None or self.personalization_claims is not None
        ):
            raise ValueError(
                "message, cta, and personalization_claims are allowed only for draft_touch_1"
            )
        return self

    @staticmethod
    def _structured_output_schema(schema: dict[str, object]) -> None:
        schema["allOf"] = [
            {
                "if": {
                    "properties": {"action": {"const": "draft_touch_1"}},
                    "required": ["action"],
                },
                "then": {
                    "properties": {
                        "message": {"type": "string", "minLength": 1, "maxLength": 5000},
                        "cta": {
                            "anyOf": [
                                {"type": "string", "minLength": 1, "maxLength": 1000},
                                {"type": "null"},
                            ]
                        },
                        "personalization_claims": {
                            "type": "array",
                            "maxItems": 10,
                        },
                    }
                },
                "else": {
                    "properties": {
                        "message": {"type": "null"},
                        "cta": {"type": "null"},
                        "personalization_claims": {"type": "null"},
                    }
                },
            }
        ]

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra=_structured_output_schema,
    )


class Touch1Draft(SetterModelOutput):
    action: Literal[SetterDecisionAction.DRAFT_TOUCH_1] = SetterDecisionAction.DRAFT_TOUCH_1
    review_status: PendingReviewStatus = PENDING_HUMAN_REVIEW


class SetterDecision(SetterContract):
    """Persisted decision envelope; these fields are populated only by the server."""

    id: UUID
    workspace_id: UUID
    lead_id: UUID
    analysis_id: UUID
    research_id: UUID
    playbook_id: UUID
    playbook_version: str = Field(..., min_length=1, max_length=100)
    playbook_digest: str = Field(..., min_length=1, max_length=256)
    workflow_stage: Literal["touch_1"]
    action: SetterDecisionAction
    draft_purpose: Literal["discovery", "opportunity_grounded"] | None = None
    output: SetterModelOutput
    evidence_ids: list[SetterReference] = Field(..., min_length=1, max_length=20)
    approved_guardrail_ids: list[SetterReference] = Field(..., max_length=20)
    approved_assertion_ids: list[SetterReference] = Field(..., max_length=20)
    touch1_draft: Touch1Draft | None = None
    provider: str | None = Field(..., min_length=1, max_length=100)
    model: str | None = Field(..., min_length=1, max_length=200)
    context_digest: str = Field(..., min_length=1, max_length=256)
    created_at: datetime

    @field_validator("evidence_ids")
    @classmethod
    def evidence_ids_are_unique(cls, value: list[str]) -> list[str]:
        return _unique_references(value, "evidence_ids")

    @field_validator("approved_guardrail_ids")
    @classmethod
    def guardrail_ids_are_unique(cls, value: list[str]) -> list[str]:
        return _unique_references(value, "approved_guardrail_ids")

    @field_validator("approved_assertion_ids")
    @classmethod
    def assertion_ids_are_unique(cls, value: list[str]) -> list[str]:
        return _unique_references(value, "approved_assertion_ids")

    @model_validator(mode="after")
    def traceability_matches_output(self) -> "SetterDecision":
        if (self.provider is None) != (self.model is None):
            raise ValueError("provider and model metadata must both be set or both be null")
        if self.action != self.output.action:
            raise ValueError("persisted action must match the model output")
        if self.output.stage != self.workflow_stage:
            raise ValueError("workflow_stage must match the model output stage")
        if self.evidence_ids != self.output.evidence_ids:
            raise ValueError("persisted evidence_ids must match the model output")
        if self.approved_guardrail_ids != self.output.guardrail_ids:
            raise ValueError("approved guardrail IDs must match the model output references")
        if self.approved_assertion_ids != self.output.assertion_ids:
            raise ValueError("approved assertion IDs must match the model output references")

        is_draft = self.action == SetterDecisionAction.DRAFT_TOUCH_1
        if is_draft and self.touch1_draft is None:
            self.touch1_draft = Touch1Draft.model_validate(self.output.model_dump())
        elif not is_draft and self.touch1_draft is not None:
            raise ValueError("non-draft decisions must not contain a Touch1Draft")
        elif self.touch1_draft is not None:
            draft_output = self.touch1_draft.model_dump(exclude={"review_status"})
            if draft_output != self.output.model_dump():
                raise ValueError("Touch1Draft must match the provider output")
        return self


SetterValidationStatus = Literal["passed", "review_required", "blocked"]


class SetterValidationFinding(SetterContract):
    code: SetterReference
    message: str = Field(..., min_length=1, max_length=1000)
    severity: Literal["warning", "error"]


class SetterExecutionRead(SetterContract):
    id: UUID
    workspace_id: UUID
    lead_id: UUID
    decision: SetterDecision
    touch1_draft: Touch1Draft | None
    validation_status: SetterValidationStatus
    validation_findings: list[SetterValidationFinding] = Field(..., max_length=50)
    provider: str | None = Field(..., min_length=1, max_length=100)
    model: str | None = Field(..., min_length=1, max_length=200)
    review_status: PendingReviewStatus
    created_at: datetime

    @model_validator(mode="after")
    def read_matches_decision(self) -> "SetterExecutionRead":
        if (self.provider is None) != (self.model is None):
            raise ValueError("provider and model metadata must both be set or both be null")
        if self.workspace_id != self.decision.workspace_id or self.lead_id != self.decision.lead_id:
            raise ValueError("execution scope must match its persisted decision")
        if self.touch1_draft != self.decision.touch1_draft:
            raise ValueError("execution draft must match its persisted decision")
        if self.provider != self.decision.provider or self.model != self.decision.model:
            raise ValueError("execution provider metadata must match its persisted decision")
        return self


class SetterDraftReadinessRead(SetterContract):
    contract_version: Literal["setter-readiness-1.0"] = "setter-readiness-1.0"
    draft_ready: bool
    draft_purpose: Literal["discovery", "opportunity_grounded"] | None = None
    reason_codes: list[Literal[
        "authority_unavailable", "evaluation_unavailable", "evaluation_stale", "research_active",
        "identity_unresolved", "excluded", "required_unresolved", "required_failed",
        "contact_unavailable", "opportunity_unresolved", "qualified_evidence",
    ]] = Field(default_factory=list)
    identity_status: Literal["matched", "unresolved"]
    contact_channels: list[Literal["email", "phone"]] = Field(default_factory=list)
    lead_id: UUID
    evaluation_id: UUID | None = None
    policy_id: UUID | None = None
    playbook_id: UUID | None = None
    research_id: UUID | None = None
    policy_digest: str | None = None
    source_digest: str | None = None
    result_digest: str | None = None

    @model_validator(mode="after")
    def purpose_matches_readiness(self):
        if self.draft_ready != (self.draft_purpose is not None):
            raise ValueError("draft purpose must be present exactly when the draft is ready")
        if self.draft_ready and any(value is None for value in (
            self.evaluation_id, self.policy_id, self.playbook_id, self.research_id,
            self.policy_digest, self.source_digest, self.result_digest,
        )):
            raise ValueError("ready drafts require complete verified authority bindings")
        return self
