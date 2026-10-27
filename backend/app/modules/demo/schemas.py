"""Typed public request and response contracts for the hackathon demo."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.modules.activities.schemas import ActivityRead, FollowUpTaskRead
from app.modules.demo.otp import email_key
from app.modules.identity.schemas import TokenResponse


class DemoErrorDetail(BaseModel):
    code: str
    message: str


class DemoPublicConfigRead(BaseModel):
    enabled: bool
    otp_length: Literal[6] = 6
    expires_in: Literal[600] = 600
    resend_after: Literal[60] = 60


class DemoOtpRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return email_key(value)


class DemoOtpChallengeRead(BaseModel):
    challenge_id: UUID
    expires_in: Literal[600] = 600
    resend_after: Literal[60] = 60


class DemoLoginContext(BaseModel):
    enabled: Literal[True] = True
    workspace_id: UUID


class DemoOtpVerifyRead(TokenResponse):
    demo: DemoLoginContext


class DemoOtpVerify(BaseModel):
    model_config = ConfigDict(extra="forbid")

    challenge_id: UUID
    code: str = Field(pattern=r"^[0-9]{6}$")


class DemoDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: Literal["email", "phone"]


class DemoSearchQuota(BaseModel):
    limit: Literal[2] = 2
    used: int = Field(ge=0, le=2)
    remaining: int = Field(ge=0, le=2)


class DemoDraftApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: UUID
    draft_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    warnings_acknowledged: Literal[True]


class DemoDraftReviewRead(BaseModel):
    execution_id: UUID
    draft_digest: str
    status: Literal["pending_human_review", "approved_for_manual_use", "contact_recorded", "blocked"]
    reason: str | None = None
    approved_at: datetime | None = None


class DemoCapabilities(BaseModel):
    review_business: bool = True
    maps_search: bool = True
    prepare_draft: bool = True
    paid_research: bool = False
    manual_evaluation: bool = False
    playbook_generation: bool = False
    outbound_sending: bool = False
    administration: bool = False
    manual_follow_up: bool = True


class DemoFollowUpNoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID
    content: str = Field(min_length=1, max_length=4000)


class DemoFollowUpContactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID
    channel: Literal["email", "phone"]
    outcome: Literal["no_response", "conversation", "appointment", "not_interested"]
    note: str = Field(default="", max_length=4000)


class DemoFollowUpTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: UUID
    title: str = Field(min_length=1, max_length=255)
    due_at: AwareDatetime


class DemoFollowUpCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID


class DemoFollowUpRead(BaseModel):
    lead_id: UUID
    contacted: bool
    first_contacted_at: datetime | None
    next_follow_up_at: datetime | None
    activities: list[ActivityRead]
    tasks: list[FollowUpTaskRead]


class DemoSessionRead(BaseModel):
    enabled: bool
    workspace_id: UUID
    template_version: str
    review_required: bool
    searches: DemoSearchQuota
    global_search_available: bool
    capabilities: DemoCapabilities
