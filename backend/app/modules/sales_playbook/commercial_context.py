"""Commercial Context v4.0 — compact approved authority for analysis and Setter."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

COMMERCIAL_CONTEXT_VERSION = "4.0"
COMMERCIAL_CONTEXT_KIND = "commercial_context"


def _string_sequence(value: object, field_name: str) -> list[str] | tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise ValueError(f"{field_name} must be a list or tuple of strings")
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must contain only strings")
    return value


class OfferBlock(BaseModel):
    """Business offer summary for commercial context."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, str_min_length=1, frozen=True)

    summary: str | None = Field(default=None, max_length=2000)
    problem_solved: str | None = Field(default=None, max_length=2000)
    differentiators: tuple[str, ...] = Field(default_factory=tuple, max_length=5)
    price_context: str | None = Field(default=None, max_length=1000)

    @field_validator("differentiators", mode="before")
    @classmethod
    def _validate_differentiators(cls, v: object) -> tuple[str, ...]:
        v = _string_sequence(v, "differentiators")
        seen = set()
        result = []
        for item in v:
            if not item or not item.strip():
                raise ValueError("differentiators cannot contain blank values")
            item = item.strip()
            if len(item) > 500:
                raise ValueError("differentiator exceeds 500 characters")
            if item in seen:
                raise ValueError("differentiators must be unique")
            seen.add(item)
            result.append(item)
        return tuple(result)


class ICPBlock(BaseModel):
    """Ideal Customer Profile for commercial context."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, str_min_length=1, frozen=True)

    industries: tuple[str, ...] = Field(default_factory=tuple, max_length=10)
    geographies: tuple[str, ...] = Field(default_factory=tuple, max_length=10)
    buyer_roles: tuple[str, ...] = Field(default_factory=tuple, max_length=10)
    desired_traits: tuple[str, ...] = Field(default_factory=tuple, max_length=10)
    exclusions: tuple[str, ...] = Field(default_factory=tuple, max_length=10)

    @field_validator("industries", "geographies", "buyer_roles", "desired_traits", "exclusions", mode="before")
    @classmethod
    def _validate_string_list(cls, v: object, info) -> tuple[str, ...]:
        v = _string_sequence(v, info.field_name)
        seen = set()
        result = []
        max_len = 255 if info.field_name in ("industries", "geographies") else 500
        for item in v:
            if not item or not item.strip():
                raise ValueError(f"{info.field_name} cannot contain blank values")
            item = item.strip()
            if len(item) > max_len:
                raise ValueError(f"{info.field_name} item exceeds {max_len} characters")
            if item in seen:
                raise ValueError(f"{info.field_name} values must be unique")
            seen.add(item)
            result.append(item)
        return tuple(result)


class SignalBlock(BaseModel):
    """Opportunity or fit signal with evidence hint."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, str_min_length=1, frozen=True)

    id: str = Field(..., pattern=r"^[a-z][a-z0-9_]{0,63}$")
    kind: Literal["fit", "opportunity"]
    direction: Literal["positive", "negative"]
    description: str = Field(..., min_length=1, max_length=1000)
    evidence_hint: str = Field(..., min_length=1, max_length=1000)


class CommunicationBlock(BaseModel):
    """Communication policy for commercial context."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, str_min_length=1, frozen=True)

    tone: str | None = Field(default=None, max_length=500)
    call_to_action: str | None = Field(default=None, max_length=500)
    allowed_channels: tuple[Literal["email", "phone", "whatsapp"], ...] = Field(default_factory=tuple)
    forbidden_claims: tuple[str, ...] = Field(default_factory=tuple, max_length=10)
    contact_restrictions: tuple[str, ...] = Field(default_factory=tuple, max_length=10)
    pricing_policy: Literal["omit"] = "omit"

    @field_validator("forbidden_claims", "contact_restrictions", mode="before")
    @classmethod
    def _validate_claim_lists(cls, v: object, info) -> tuple[str, ...]:
        v = _string_sequence(v, info.field_name)
        seen = set()
        result = []
        for item in v:
            if not item or not item.strip():
                raise ValueError("communication lists cannot contain blank values")
            item = item.strip()
            if len(item) > 500:
                raise ValueError("claim item exceeds 500 characters")
            if item in seen:
                raise ValueError("communication list values must be unique")
            seen.add(item)
            result.append(item)
        return tuple(result)

    @field_validator("allowed_channels", mode="before")
    @classmethod
    def _validate_allowed_channels(cls, v: object) -> tuple[str, ...]:
        v = _string_sequence(v, "allowed_channels")
        allowed = {"email", "phone", "whatsapp"}
        seen = set()
        result = []
        for item in v:
            if item not in allowed:
                raise ValueError(f"channel {item!r} not in allowed set {allowed}")
            if item in seen:
                raise ValueError("allowed_channels must be unique")
            seen.add(item)
            result.append(item)
        return tuple(result)


class CommercialContextContent(BaseModel):
    """
    Complete commercial context content v4.0.

    Drafts may have unknown fields (null or empty lists); approval requires full readiness.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        str_min_length=1,
        validate_assignment=True,
        frozen=True,
    )

    contract_version: Literal[COMMERCIAL_CONTEXT_VERSION] = COMMERCIAL_CONTEXT_VERSION
    kind: Literal[COMMERCIAL_CONTEXT_KIND] = COMMERCIAL_CONTEXT_KIND

    business_name: str | None = Field(default=None, max_length=255)

    offer: OfferBlock | None = None
    icp: ICPBlock | None = None
    signals: tuple[SignalBlock, ...] = Field(default_factory=tuple, max_length=10)
    communication: CommunicationBlock | None = None

    @field_validator("signals", mode="before")
    @classmethod
    def _validate_signals(cls, v: object) -> tuple[SignalBlock, ...]:
        if v is None:
            return ()
        if not isinstance(v, list | tuple):
            raise ValueError("signals must be a list or tuple of objects")
        seen_ids = set()
        result = []
        for item in v:
            if isinstance(item, SignalBlock):
                signal = item
            elif isinstance(item, dict):
                signal = SignalBlock(**item)
            else:
                raise ValueError("signal must be an object")
            if signal.id in seen_ids:
                raise ValueError(f"duplicate signal id: {signal.id}")
            seen_ids.add(signal.id)
            result.append(signal)
        return tuple(result)

class CommercialReadiness(BaseModel):
    """Readiness assessment for commercial context approval."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ready_for_approval: bool
    missing_fields: list[str]
    blockers: list[str]


class CommercialDocumentSnapshot(BaseModel):
    """Server-owned identity and normalized content fingerprint for one document."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: UUID
    normalized_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")


class CommercialSourceSnapshot(BaseModel):
    """Canonical snapshot of source identity for digest and drift detection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    seller_profile_id: UUID
    seller_profile_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    documents: tuple[CommercialDocumentSnapshot, ...] = Field(default_factory=tuple, max_length=1)
    pasted_text_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    answers_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def sources_are_exclusive(self):
        if self.documents and self.pasted_text_sha256 is not None:
            raise ValueError("documents and pasted text cannot coexist in a source snapshot")
        return self


@dataclass(frozen=True)
class ApprovedCommercialContext:
    """
    Detached, validated projection of approved commercial context.

    Immutable; no shared mutable lists/dicts with ORM.
    """

    workspace_id: UUID
    playbook_id: UUID
    version: int
    content_contract_version: str  # "4.0"
    content_digest: str  # digest of content at approval
    source_snapshot: CommercialSourceSnapshot
    business_name: str
    offer: OfferBlock
    icp: ICPBlock
    signals: tuple[SignalBlock, ...]
    communication: CommunicationBlock


def commercial_readiness(content: CommercialContextContent) -> CommercialReadiness:
    """
    Evaluate whether a commercial context draft is ready for approval.

    Returns missing required fields and structural blockers.
    """
    missing: list[str] = []
    blockers: list[str] = []

    if not content.business_name:
        missing.append("business_name")

    if not content.offer:
        missing.append("offer.summary")
        missing.append("offer.problem_solved")
    else:
        if not content.offer.summary:
            missing.append("offer.summary")
        if not content.offer.problem_solved:
            missing.append("offer.problem_solved")

    if not content.icp:
        missing.append("icp.industries")
        missing.append("icp.geographies")
    else:
        if not content.icp.industries:
            missing.append("icp.industries")
        if not content.icp.geographies:
            missing.append("icp.geographies")

    if not content.signals:
        blockers.append("At least one complete signal is required")
    else:
        for i, signal in enumerate(content.signals):
            if not signal.description:
                blockers.append(f"signal[{i}].description is required")
            if not signal.evidence_hint:
                blockers.append(f"signal[{i}].evidence_hint is required")

    if not content.communication:
        missing.append("communication.tone")
        missing.append("communication.call_to_action")
    else:
        if not content.communication.tone:
            missing.append("communication.tone")
        if not content.communication.call_to_action:
            missing.append("communication.call_to_action")
        if not content.communication.allowed_channels:
            blockers.append("communication.allowed_channels must have at least one channel")

    # Deduplicate missing fields
    missing = list(dict.fromkeys(missing))
    blockers = list(dict.fromkeys(blockers))

    return CommercialReadiness(
        ready_for_approval=len(missing) == 0 and len(blockers) == 0,
        missing_fields=missing,
        blockers=blockers,
    )


def _canonical_json(value: dict) -> bytes:
    """Produce canonical JSON for digest computation."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def commercial_review_digest(
    workspace_id: UUID,
    content: CommercialContextContent,
    source_snapshot: CommercialSourceSnapshot,
) -> str:
    """
    Compute SHA-256 digest of the approval envelope.

    Envelope: {version, workspace_id, content, source_snapshot}
    """
    envelope = {
        "version": COMMERCIAL_CONTEXT_VERSION,
        "workspace_id": str(workspace_id),
        "content": content.model_dump(mode="json"),
        "source_snapshot": source_snapshot.model_dump(mode="json"),
    }
    return hashlib.sha256(_canonical_json(envelope)).hexdigest()


def verified_approved_commercial_context(playbook) -> ApprovedCommercialContext:
    """
    Extract and validate approved commercial context from a SalesPlaybook.

    Raises ValueError if playbook is not approved, not v4, or validation fails.
    """
    from app.modules.sales_playbook.models import SalesPlaybookStatus

    if playbook.status != SalesPlaybookStatus.APPROVED:
        raise ValueError("Only approved playbooks can provide commercial context")
    if playbook.content_contract_version != COMMERCIAL_CONTEXT_VERSION:
        raise ValueError(f"Unsupported approved playbook contract: {playbook.content_contract_version}")

    content = CommercialContextContent.model_validate(playbook.structured_content)
    readiness = commercial_readiness(content)
    if not readiness.ready_for_approval:
        raise ValueError("Approved commercial context fails readiness: " + "; ".join(readiness.missing_fields + readiness.blockers))

    # Verify source snapshot and digest
    audit = playbook.generation_audit or {}
    snapshot = audit.get("approval_snapshot")
    if not isinstance(snapshot, dict):
        raise ValueError("Missing approval snapshot in generation_audit")
    if snapshot.get("version") != COMMERCIAL_CONTEXT_VERSION:
        raise ValueError("Approval snapshot version mismatch")
    source_snapshot = CommercialSourceSnapshot.model_validate(snapshot.get("source_snapshot"))
    if playbook.seller_profile_id != source_snapshot.seller_profile_id:
        raise ValueError("Approval snapshot seller profile does not match the playbook binding")
    content_digest = snapshot.get("digest")
    expected_digest = commercial_review_digest(playbook.workspace_id, content, source_snapshot)
    if content_digest != expected_digest:
        raise ValueError("Approved commercial context digest does not match its content and source snapshot")

    return ApprovedCommercialContext(
        workspace_id=playbook.workspace_id,
        playbook_id=playbook.id,
        version=playbook.version,
        content_contract_version=COMMERCIAL_CONTEXT_VERSION,
        content_digest=content_digest,
        source_snapshot=source_snapshot,
        business_name=content.business_name or "",
        offer=content.offer,
        icp=content.icp,
        signals=tuple(content.signals),
        communication=content.communication,
    )
