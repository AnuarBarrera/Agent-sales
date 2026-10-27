from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.modules.sales_playbook.schemas import SalesPlaybookContent

EvidenceId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$",
    ),
]


class QualificationStatus(str, Enum):
    QUALIFIED = "qualified"
    NOT_QUALIFIED = "not_qualified"
    NEEDS_MORE_RESEARCH = "needs_more_research"


class SetterAction(str, Enum):
    INITIAL_OUTREACH = "initial_outreach"
    CONTINUE_CONVERSATION = "continue_conversation"
    FOLLOW_UP = "follow_up"
    HANDOFF_TO_CLOSER = "handoff_to_closer"
    DO_NOT_CONTACT = "do_not_contact"


class IntentLabel(str, Enum):
    UNKNOWN = "unknown"
    NEUTRAL = "neutral"
    OBJECTION = "objection"
    INTEREST = "interest"
    BUYING_INTENT = "buying_intent"
    NOT_INTERESTED = "not_interested"


class CloserAction(str, Enum):
    REVIEW_MANUALLY = "review_manually"
    ANSWER_OBJECTION = "answer_objection"
    SCHEDULE_CALL = "schedule_call"
    SEND_PROPOSAL = "send_proposal"
    FOLLOW_UP = "follow_up"
    DO_NOT_CONTACT = "do_not_contact"


class AIContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: str = Field(default="1.0", pattern=r"^\d+\.\d+$")


class EvidenceBackedContract(AIContract):
    evidence_ids: list[EvidenceId] = Field(default_factory=list, max_length=20)

    @field_validator("evidence_ids")
    @classmethod
    def evidence_ids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("evidence_ids must not contain duplicates")
        return value


class ProspectObservation(AIContract):
    statement: str = Field(..., min_length=1, max_length=2000)
    evidence_ids: list[EvidenceId] = Field(..., min_length=1, max_length=20)

    @field_validator("evidence_ids")
    @classmethod
    def evidence_ids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("evidence_ids must not contain duplicates")
        return value


class ProspectHypothesis(AIContract):
    statement: str = Field(..., min_length=1, max_length=2000)
    confidence: float = Field(..., ge=0.0, le=1.0)
    supporting_evidence_ids: list[EvidenceId] = Field(..., min_length=1, max_length=20)

    @field_validator("supporting_evidence_ids")
    @classmethod
    def evidence_ids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("supporting_evidence_ids must not contain duplicates")
        return value


class SalesPlaybookGeneration(EvidenceBackedContract):
    contract_version: Literal["2.0"] = "2.0"
    playbook: SalesPlaybookContent
    rationale: str = Field(..., min_length=1, max_length=2000)


class ProspectIntelligence(AIContract):
    summary: str = Field(..., min_length=1, max_length=3000)
    observations: list[ProspectObservation] = Field(default_factory=list, max_length=50)
    hypotheses: list[ProspectHypothesis] = Field(default_factory=list, max_length=30)
    missing_information: list[str] = Field(default_factory=list, max_length=20)


class QualificationDecision(EvidenceBackedContract):
    decision: QualificationStatus
    score: int | None = Field(default=None, ge=0, le=100)
    confidence: float = Field(..., ge=0.0, le=1.0)
    positive_signals: list[str] = Field(default_factory=list, max_length=10)
    negative_signals: list[str] = Field(default_factory=list, max_length=10)
    recommended_angle: str | None = Field(default=None, max_length=1000)
    rationale: str = Field(..., min_length=1, max_length=2000)


class SetterDecision(EvidenceBackedContract):
    next_action: SetterAction
    intent: IntentLabel | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    rationale: str = Field(..., min_length=1, max_length=2000)
    message: str | None = Field(default=None, max_length=5000)


class IntentDecision(EvidenceBackedContract):
    intent: IntentLabel
    confidence: float = Field(..., ge=0.0, le=1.0)
    objections: list[str] = Field(default_factory=list, max_length=10)
    signals: list[str] = Field(default_factory=list, max_length=10)
    next_action: SetterAction
    should_handoff: bool


class BusinessContext(AIContract):
    business_name: str = Field(..., min_length=1, max_length=500)
    offer_summary: str = Field(..., min_length=1, max_length=3000)
    playbook_version: int | None = Field(default=None, ge=1)


class ProspectContext(AIContract):
    lead_id: UUID | None = None
    business_name: str = Field(..., min_length=1, max_length=500)
    contact_name: str | None = Field(default=None, max_length=500)
    location: str | None = Field(default=None, max_length=500)
    summary: str = Field(..., min_length=1, max_length=3000)


class HandoffPackage(EvidenceBackedContract):
    business_context: BusinessContext
    prospect_context: ProspectContext
    qualification: QualificationDecision
    qualification_summary: str = Field(..., min_length=1, max_length=3000)
    conversation_summary: str = Field(..., min_length=1, max_length=5000)
    intent: IntentDecision
    objections: list[str] = Field(default_factory=list, max_length=10)
    buying_signals: list[str] = Field(default_factory=list, max_length=10)
    recommended_next_action: CloserAction


class CloserDecision(EvidenceBackedContract):
    next_action: CloserAction
    confidence: float = Field(..., ge=0.0, le=1.0)
    rationale: str = Field(..., min_length=1, max_length=2000)
    message: str | None = Field(default=None, max_length=5000)
