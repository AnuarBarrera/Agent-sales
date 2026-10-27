"""Commercial Context v4.0 service — creation, editing, and approval."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.validation import AIContractValidationError
from app.modules.identity.models import Workspace
from app.modules.sales_playbook.commercial_context import (
    COMMERCIAL_CONTEXT_VERSION,
    CommercialContextContent,
    CommercialSourceSnapshot,
    CommunicationBlock,
    ICPBlock,
    OfferBlock,
    commercial_readiness,
    commercial_review_digest,
)
from app.modules.sales_playbook.models import (
    SalesPlaybook,
    SalesPlaybookSourceType,
    SalesPlaybookStatus,
)
from app.modules.seller_profile.models import SellerProfile

logger = logging.getLogger(__name__)


def _merge_assisted_content(
    request: CommercialContextContent | None,
    suggested: CommercialContextContent,
    profile_snapshot: dict[str, Any],
) -> CommercialContextContent:
    """Keep supplied facts and profile restrictions while filling gaps from the suggestion."""
    request_data = request.model_dump(mode="python") if request is not None else {}
    suggested_data = suggested.model_dump(mode="python")
    merged = suggested.model_dump(mode="python")

    def scalar(request_value: Any, profile_value: Any, suggested_value: Any) -> Any:
        if request_value is not None:
            return request_value
        return profile_value if profile_value is not None else suggested_value

    merged["business_name"] = scalar(
        request_data.get("business_name"), profile_snapshot.get("business_name"),
        suggested_data.get("business_name"),
    )

    request_offer = request_data.get("offer") or {}
    suggested_offer = suggested_data.get("offer") or {}
    offer = dict(suggested_offer)
    for field, profile_field in (("summary", "offer_summary"), ("price_context", "price_context")):
        offer[field] = scalar(
            request_offer.get(field), profile_snapshot.get(profile_field),
            suggested_offer.get(field),
        )
    request_differentiators = request_offer.get("differentiators", ())
    profile_differentiators = profile_snapshot.get("differentiators")
    if request_differentiators:
        offer["differentiators"] = request_differentiators
    elif isinstance(profile_differentiators, str) and profile_differentiators.strip():
        offer["differentiators"] = (profile_differentiators.strip(),)
    else:
        offer["differentiators"] = suggested_offer.get("differentiators", ())
    if request_offer:
        for field in ("problem_solved",):
            if request_offer.get(field) is not None:
                offer[field] = request_offer[field]
    has_profile_offer = any(
        profile_snapshot.get(field) is not None
        for field in ("offer_summary", "price_context", "differentiators")
    )
    merged["offer"] = offer if (
        request_offer or suggested_data.get("offer") is not None or has_profile_offer
    ) else None

    request_icp = request_data.get("icp") or {}
    suggested_icp = suggested_data.get("icp") or {}
    if request_icp or suggested_icp:
        icp = dict(suggested_icp)
        for field, request_value in request_icp.items():
            if request_value:
                icp[field] = request_value
        merged["icp"] = icp

    request_signals = request_data.get("signals", ())
    request_ids = {signal["id"] for signal in request_signals}
    merged["signals"] = [
        *request_signals,
        *(signal for signal in suggested_data.get("signals", ()) if signal["id"] not in request_ids),
    ]

    request_communication = request_data.get("communication")
    suggested_communication = suggested_data.get("communication") or {}
    profile_constraint = profile_snapshot.get("sales_constraints")
    has_profile_constraint = isinstance(profile_constraint, str) and bool(profile_constraint.strip())
    if (
        request_communication is not None
        or suggested_data.get("communication") is not None
        or has_profile_constraint
    ):
        communication = dict(suggested_communication)
        if request_communication is not None:
            for field in ("tone", "call_to_action"):
                if request_communication.get(field) is not None:
                    communication[field] = request_communication[field]
            for field in ("allowed_channels", "forbidden_claims"):
                if request_communication.get(field):
                    communication[field] = request_communication[field]
            # An explicitly supplied channel policy, including an empty one, is authoritative.
            communication["allowed_channels"] = request_communication["allowed_channels"]

        restrictions = list(request_communication.get("contact_restrictions", ())) \
            if request_communication is not None else []
        restrictions.extend(
            item for item in suggested_communication.get("contact_restrictions", ())
            if item not in restrictions
        )
        if has_profile_constraint and profile_constraint.strip() not in restrictions:
            restrictions.append(profile_constraint.strip())
        communication["contact_restrictions"] = restrictions
        merged["communication"] = communication

    return CommercialContextContent.model_validate(merged)


class IdempotencyConflict(ValueError):
    """A client key was already used with different creation input."""


class CommercialContextDraftCreate(BaseModel):
    """Request to create a commercial context draft."""

    model_config = ConfigDict(extra="forbid")

    content: CommercialContextContent | None = None
    pasted_text: str | None = Field(default=None, min_length=1, max_length=100000)
    source_document_ids: list[UUID] = Field(default_factory=list, max_length=1)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    assisted: bool = False

    @model_validator(mode="after")
    def sources_are_exclusive(self) -> CommercialContextDraftCreate:
        if self.pasted_text is not None and self.source_document_ids:
            raise ValueError("Choose uploaded documents or pasted text, not both")
        return self


class CommercialContextDraftUpdate(BaseModel):
    """Request to update a commercial context draft."""

    model_config = ConfigDict(extra="forbid")

    expected_digest: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    content: CommercialContextContent


class CommercialContextService:
    """Service for commercial context v4.0 lifecycle."""

    def __init__(self, db: Session, *, generator: Any = None, storage: Any = None) -> None:
        self.db = db
        self.generator = generator
        self.storage = storage

    def _storage_provider(self):
        if self.storage is None:
            from app.integrations.storage import StorageProvider

            self.storage = StorageProvider()
        return self.storage

    def _get_profile(self, workspace_id: UUID, seller_profile_id: UUID | None = None) -> SellerProfile | None:
        """Get seller profile for workspace."""
        query = select(SellerProfile).where(SellerProfile.workspace_id == workspace_id)
        if seller_profile_id is not None:
            query = query.where(SellerProfile.id == seller_profile_id)
        return self.db.execute(query).scalar_one_or_none()

    def _compute_profile_fingerprint(self, profile: SellerProfile | None) -> str:
        """Compute canonical fingerprint of seller profile commercial fields."""
        if profile is None:
            return hashlib.sha256(b"").hexdigest()
        data = {
            "workspace_id": str(profile.workspace_id),
            "seller_profile_id": str(profile.id),
            "business_name": profile.business_name,
            "description": profile.description,
            "offer_summary": profile.offer_summary,
            "target_customer_summary": profile.target_customer_summary,
            "location_or_market": profile.location_or_market,
            "price_context": profile.price_context,
            "primary_goal": profile.primary_goal,
            "differentiators": profile.differentiators,
            "sales_constraints": profile.sales_constraints,
        }
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _compute_answers_hash(self, content: CommercialContextContent | None, pasted_text: str | None) -> str:
        """Compute hash of user-provided content."""
        if content is not None:
            data = content.model_dump(mode="json", exclude_none=True)
        elif pasted_text is not None:
            data = {"pasted_text": pasted_text}
        else:
            data = {}
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _load_source_documents(self, workspace_id: UUID, document_ids: list[UUID]) -> list[Any]:
        """Load and verify source documents."""
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("Duplicate source document ID")
        if not document_ids:
            return []
        from app.modules.sales_playbook.document_models import SourceDocument

        records = list(self.db.execute(select(SourceDocument).where(
            SourceDocument.workspace_id == workspace_id,
            SourceDocument.id.in_(document_ids),
        )).scalars().all())
        by_id = {item.id: item for item in records}
        if len(by_id) != len(document_ids):
            raise ValueError("Source document not found in this workspace")

        from app.modules.sales_playbook.service import SalesPlaybookService

        verifier = SalesPlaybookService(self.db, generator=None)
        storage = self._storage_provider()
        result = []
        for document_id in document_ids:
            record = by_id[document_id]
            reference = SimpleNamespace(
                document_id=str(record.id), filename=record.original_filename,
                original_sha256=record.original_sha256,
                normalized_sha256=record.normalized_sha256,
            )
            verifier._verify_source_documents(workspace_id, [reference], storage=storage)
            result.append(record)
        return result

    def _assisted_input_snapshot(
        self,
        profile: SellerProfile,
        content: CommercialContextContent | None,
        pasted_text: str | None,
        source_documents: list[Any],
    ) -> dict[str, Any]:
        documents = []
        if source_documents:
            storage = self._storage_provider()
            for record in source_documents:
                normalized = storage.download(record.normalized_object_key)
                try:
                    normalized_text = normalized.decode("utf-8") if isinstance(normalized, bytes) else str(normalized)
                except UnicodeDecodeError as exc:
                    raise ValueError("Normalized source document is not valid UTF-8") from exc
                documents.append({
                    "filename": record.original_filename,
                    "normalized_content": normalized_text,
                })
        profile_fields = (
            "business_name", "description", "offer_summary", "target_customer_summary",
            "location_or_market", "price_context", "primary_goal", "differentiators",
            "sales_constraints",
        )
        return {
            "profile": {name: getattr(profile, name) for name in profile_fields},
            "answers": content.model_dump(mode="json", exclude_none=True) if content else {},
            "pasted_text": pasted_text,
            "documents": documents,
        }

    def _get_by_key(self, workspace_id: UUID, key: str) -> SalesPlaybook | None:
        return self.db.execute(select(SalesPlaybook).where(
            SalesPlaybook.workspace_id == workspace_id, SalesPlaybook.idempotency_key == key,
        )).scalar_one_or_none()

    def _replay(self, existing: SalesPlaybook, fingerprint: str) -> SalesPlaybook:
        if existing.request_fingerprint != fingerprint:
            raise IdempotencyConflict("Idempotency key already used with different input")
        return existing

    def _determine_source_type(self, content: CommercialContextContent | None, pasted_text: str | None,
                                source_document_ids: list[UUID]) -> SalesPlaybookSourceType:
        if source_document_ids:
            return SalesPlaybookSourceType.DOCUMENT
        if pasted_text:
            return SalesPlaybookSourceType.MANUAL
        return SalesPlaybookSourceType.QUESTIONNAIRE

    def _build_source_payload(self, content: CommercialContextContent | None, pasted_text: str | None,
                               source_documents: list[Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if content is not None:
            payload["questionnaire"] = content.model_dump(mode="json", exclude_none=True)
        if pasted_text is not None:
            payload["pasted_text"] = pasted_text
        if source_documents:
            payload["source_document_manifest"] = [
                {"id": str(item.id), "filename": item.original_filename,
                 "original_sha256": item.original_sha256, "normalized_sha256": item.normalized_sha256}
                for item in source_documents
            ]
        return payload

    def _build_source_snapshot(self, workspace_id: UUID, profile: SellerProfile | None,
                                source_documents: list[Any], pasted_text: str | None,
                                content: CommercialContextContent | None) -> CommercialSourceSnapshot:
        if profile is None:
            raise ValueError("A seller profile is required to bind a commercial context")
        profile_id = profile.id
        profile_sha256 = self._compute_profile_fingerprint(profile)
        answers_sha256 = self._compute_answers_hash(content, pasted_text)

        documents = []
        for doc in source_documents:
            documents.append({
                "document_id": doc.id,
                "normalized_sha256": doc.normalized_sha256,
            })

        pasted_sha256 = None
        if pasted_text:
            pasted_sha256 = hashlib.sha256(pasted_text.encode("utf-8")).hexdigest()

        return CommercialSourceSnapshot(
            seller_profile_id=profile_id,
            seller_profile_sha256=profile_sha256,
            documents=documents,
            pasted_text_sha256=pasted_sha256,
            answers_sha256=answers_sha256,
        )

    def create_draft(
        self,
        workspace_id: UUID,
        content: CommercialContextContent | None,
        pasted_text: str | None,
        source_document_ids: list[UUID] | None,
        idempotency_key: str | None = None,
        assisted: bool = False,
    ) -> SalesPlaybook:
        """Create a DRAFT commercial context v4.0 playbook."""
        # Validate mutual exclusivity
        if source_document_ids and pasted_text:
            raise ValueError("Choose uploaded documents or pasted text, not both")

        request_content = content
        source_documents = self._load_source_documents(workspace_id, source_document_ids or [])
        profile = self._get_profile(workspace_id)
        if profile is None:
            raise ValueError("A seller profile is required to bind a commercial context")
        seller_profile_id = profile.id

        source_type = self._determine_source_type(content, pasted_text, source_document_ids or [])

        # Check idempotency
        fingerprint = None
        if idempotency_key:
            request_input = {
                "content": request_content.model_dump(mode="json") if request_content else None,
                "pasted_text": pasted_text,
                "source_documents": [
                    (str(d.id), d.original_sha256, d.normalized_sha256) for d in source_documents
                ],
                "assisted": assisted,
            }
            fingerprint = hashlib.sha256(json.dumps({
                "contract_version": COMMERCIAL_CONTEXT_VERSION,
                "producer": "commercial_context_assisted" if assisted else "commercial_context_manual",
                "workspace_id": str(workspace_id),
                "source_type": source_type.value,
                "request_input": request_input,
                "seller_profile_id": str(profile.id) if profile else None,
                "seller_profile_sha256": self._compute_profile_fingerprint(profile),
            }, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()

            existing = self._get_by_key(workspace_id, idempotency_key)
            if existing is not None:
                return self._replay(existing, fingerprint)

        source_snapshot = self._build_source_snapshot(
            workspace_id, profile, source_documents, pasted_text, request_content,
        )
        source_payload = self._build_source_payload(request_content, pasted_text, source_documents)
        generation_metadata = None

        if assisted:
            input_snapshot = self._assisted_input_snapshot(
                profile, request_content, pasted_text, source_documents,
            )
            profile_snapshot = dict(input_snapshot["profile"])
            serialized_snapshot = json.dumps(
                input_snapshot, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            )
            if len(serialized_snapshot) > 16_000:
                raise ValueError("Commercial context input exceeds 16,000 characters; reduce the source and retry")

            # End the source-read transaction before any provider inference.
            self.db.rollback()
            if self.generator is None:
                from app.modules.sales_playbook.commercial_generator import (
                    build_commercial_context_generator,
                )

                self.generator = build_commercial_context_generator()
            try:
                suggested = CommercialContextContent.model_validate(self.generator.generate(input_snapshot))
                content = _merge_assisted_content(request_content, suggested, profile_snapshot)
            except AIContractValidationError as exc:
                raise RuntimeError("Assisted suggestion returned invalid content; no draft was saved") from exc
            except ValidationError as exc:
                raise RuntimeError("Assisted suggestion returned invalid content; no draft was saved") from exc
            except Exception as exc:
                raise RuntimeError("Assisted suggestion failed; no draft was saved") from exc
            generation_metadata = getattr(self.generator, "last_metadata", None)
            if generation_metadata is None or not is_dataclass(generation_metadata):
                raise RuntimeError("Assisted suggestion audit metadata is unavailable; no draft was saved")
            metadata_values = asdict(generation_metadata)
            if metadata_values.get("validation_attempts") != 1:
                raise RuntimeError("Assisted suggestion exceeded its single-call limit; no draft was saved")
        elif content is None:
            # Build a truthful partial draft from explicit profile fields only.
            content = CommercialContextContent(
                business_name=profile.business_name,
                offer=OfferBlock(
                    summary=profile.offer_summary,
                    problem_solved=None,
                    differentiators=[profile.differentiators] if profile.differentiators else [],
                    price_context=profile.price_context,
                ),
                icp=ICPBlock(
                    industries=[],
                    geographies=[],
                ),
                signals=[],
                communication=CommunicationBlock(
                    tone=None,
                    call_to_action=None,
                    allowed_channels=[],
                    contact_restrictions=[profile.sales_constraints] if profile.sales_constraints else [],
                ),
            )

        # Readiness is advisory until a human explicitly approves.
        readiness = commercial_readiness(content)

        self.db.execute(
            select(Workspace.id).where(Workspace.id == workspace_id).with_for_update(),
        ).scalar_one_or_none()
        self.validate_commercial_bindings(workspace_id, source_snapshot, source_payload)
        if idempotency_key:
            existing = self._get_by_key(workspace_id, idempotency_key)
            if existing is not None:
                return self._replay(existing, fingerprint)
        version = (self.db.scalar(
            select(func.max(SalesPlaybook.version)).where(SalesPlaybook.workspace_id == workspace_id)
        ) or 0) + 1

        playbook = SalesPlaybook(
            workspace_id=workspace_id,
            seller_profile_id=seller_profile_id,
            version=version,
            status=SalesPlaybookStatus.DRAFT,
            source_type=source_type,
            structured_content=content.model_dump(mode="json"),
            content_contract_version=COMMERCIAL_CONTEXT_VERSION,
            source_payload=source_payload,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            model_name=metadata_values.get("model") if generation_metadata else None,
            prompt_version=metadata_values.get("prompt_version") if generation_metadata else None,
            generation_provider=metadata_values.get("provider") if generation_metadata else None,
            generation_operation="commercial_context_suggestion" if generation_metadata else "commercial_context_manual",
            generation_latency_ms=metadata_values.get("latency_ms") if generation_metadata else None,
            generation_success=True,
            generation_tokens=metadata_values.get("tokens") if generation_metadata else None,
            generation_audit={
                "commercial_context": {
                    "operation": "assisted_suggestion" if generation_metadata else "manual",
                    "prompt_version": metadata_values.get("prompt_version") if generation_metadata else None,
                    "readiness": readiness.model_dump(mode="json"),
                    "source_snapshot": source_snapshot.model_dump(mode="json"),
                    "generation": metadata_values if generation_metadata else None,
                }
            },
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        try:
            self.db.add(playbook)
            self.db.flush()
        except IntegrityError as exc:
            self.db.rollback()
            if idempotency_key:
                raise IdempotencyConflict("Commercial context creation conflicted with an existing request") from exc
            raise ValueError("Commercial context version conflict; retry the request") from exc
        return playbook

    def update_draft(
        self,
        playbook_id: UUID,
        workspace_id: UUID,
        expected_digest: str,
        content: CommercialContextContent,
        *,
        edited_by: UUID,
    ) -> SalesPlaybook | None:
        """Update a DRAFT commercial context v4.0 playbook."""
        self.db.execute(
            select(Workspace.id).where(Workspace.id == workspace_id).with_for_update(),
        ).scalar_one_or_none()
        playbook = self.db.execute(select(SalesPlaybook).where(
            SalesPlaybook.id == playbook_id,
            SalesPlaybook.workspace_id == workspace_id,
        ).execution_options(populate_existing=True)).scalar_one_or_none()
        if playbook is None:
            return None
        if playbook.status != SalesPlaybookStatus.DRAFT:
            raise ValueError("Only draft playbooks can be updated")
        if playbook.content_contract_version != COMMERCIAL_CONTEXT_VERSION:
            raise ValueError("Only commercial context v4.0 playbooks can use this endpoint")

        # Verify expected digest matches current content
        current_content = CommercialContextContent.model_validate(playbook.structured_content)
        audit = playbook.generation_audit or {}
        cc_audit = audit.get("commercial_context", {})
        source_snapshot_data = cc_audit.get("source_snapshot")
        if not source_snapshot_data:
            raise ValueError("Missing source snapshot in audit")
        source_snapshot = CommercialSourceSnapshot.model_validate(source_snapshot_data)

        current_digest = commercial_review_digest(workspace_id, current_content, source_snapshot)
        if current_digest != expected_digest:
            raise ValueError("Content digest mismatch; draft has been modified by another request")

        # Verify new content is v4.0
        if content.contract_version != COMMERCIAL_CONTEXT_VERSION:
            raise ValueError("Content must be commercial context v4.0")

        # Update content and recompute digest
        new_readiness = commercial_readiness(content)
        new_digest = commercial_review_digest(workspace_id, content, source_snapshot)
        edit_record = {
            "actor_id": str(edited_by),
            "edited_at": datetime.now(UTC).isoformat(),
            "content_digest": new_digest,
        }

        playbook.structured_content = content.model_dump(mode="json")
        playbook.generation_audit = {
            **audit,
            "commercial_context": {
                **cc_audit,
                "readiness": new_readiness.model_dump(mode="json"),
                "edits": [*cc_audit.get("edits", []), edit_record],
            }
        }
        playbook.updated_at = datetime.now(UTC)
        try:
            self.db.flush()
        except IntegrityError as exc:
            self.db.rollback()
            raise ValueError("Commercial context update conflicted with another workspace operation") from exc
        return playbook

    def validate_commercial_bindings(
        self,
        workspace_id: UUID,
        source_snapshot: CommercialSourceSnapshot,
        source_payload: dict[str, Any] | None = None,
    ) -> None:
        """Validate that profile and sources haven't drifted since capture."""
        profile = self._get_profile(workspace_id, source_snapshot.seller_profile_id)
        if profile is None:
            raise ValueError("Seller profile no longer exists in this workspace")

        current_profile_sha256 = self._compute_profile_fingerprint(profile)
        if current_profile_sha256 != source_snapshot.seller_profile_sha256:
            raise ValueError("Seller profile has changed since commercial context capture")

        payload = source_payload or {}
        pasted_text = payload.get("pasted_text")
        if source_snapshot.pasted_text_sha256 is not None:
            if not isinstance(pasted_text, str) or hashlib.sha256(pasted_text.encode("utf-8")).hexdigest() != source_snapshot.pasted_text_sha256:
                raise ValueError("Pasted source changed since commercial context capture")
        elif pasted_text is not None:
            raise ValueError("Unexpected pasted source is bound to this commercial context")

        if "questionnaire" in payload:
            answer_payload = CommercialContextContent.model_validate(payload["questionnaire"]).model_dump(
                mode="json", exclude_none=True,
            )
        elif pasted_text is not None:
            answer_payload = {"pasted_text": pasted_text}
        else:
            answer_payload = {}
        answer_bytes = json.dumps(
            answer_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
        if hashlib.sha256(answer_bytes).hexdigest() != source_snapshot.answers_sha256:
            raise ValueError("Commercial context answers changed since capture")

        # Verify documents with the same metadata and byte checks as legacy
        # approval, including both original and normalized object contents.
        if source_snapshot.documents:
            manifest = payload.get("source_document_manifest")
            if not isinstance(manifest, list) or len(manifest) != len(source_snapshot.documents):
                raise ValueError("Commercial context source document manifest is incomplete")
            by_id = {item.get("id"): item for item in manifest if isinstance(item, dict)}
            refs = []
            for binding in source_snapshot.documents:
                source = by_id.get(str(binding.document_id))
                if source is None or source.get("normalized_sha256") != binding.normalized_sha256:
                    raise ValueError("Commercial context source document binding changed")
                refs.append({
                    "document_id": str(binding.document_id),
                    "filename": source.get("filename"),
                    "original_sha256": source.get("original_sha256"),
                    "normalized_sha256": binding.normalized_sha256,
                })
            from app.modules.sales_playbook.semantic_playbook import SourceDocumentReference
            from app.modules.sales_playbook.service import SalesPlaybookService

            if self.storage is None:
                from app.integrations.storage import ReadOnlyStorageProvider

                try:
                    storage = ReadOnlyStorageProvider()
                except Exception as exc:
                    raise ValueError("Reviewed source document could not be verified") from exc
            else:
                storage = self.storage
            SalesPlaybookService(self.db, generator=None)._verify_source_documents(
                workspace_id, [SourceDocumentReference.model_validate(item) for item in refs],
                storage=storage,
            )
        elif payload.get("source_document_manifest"):
            raise ValueError("Unexpected source document manifest is bound to this commercial context")

    def approve_commercial(
        self,
        playbook_id: UUID,
        workspace_id: UUID,
        approved_by: UUID,
        confirmed: bool,
        content_digest: str,
        *,
        workspace_locked: bool = False,
    ) -> SalesPlaybook | None:
        """Approve a commercial context v4.0 playbook."""
        if not workspace_locked:
            self.db.execute(
                select(Workspace.id).where(Workspace.id == workspace_id).with_for_update(),
            ).scalar_one_or_none()

        playbook = self.db.execute(select(SalesPlaybook).where(
            SalesPlaybook.id == playbook_id,
            SalesPlaybook.workspace_id == workspace_id,
        ).execution_options(populate_existing=True)).scalar_one_or_none()
        if playbook is None:
            return None
        if playbook.status != SalesPlaybookStatus.DRAFT:
            raise ValueError("Only draft playbooks can be approved")
        if playbook.content_contract_version != COMMERCIAL_CONTEXT_VERSION:
            raise ValueError("Only commercial context v4.0 playbooks can use this approval path")

        if not confirmed:
            raise ValueError("Commercial context approval requires confirmed=true")

        # Verify readiness
        content = CommercialContextContent.model_validate(playbook.structured_content)
        readiness = commercial_readiness(content)
        if not readiness.ready_for_approval:
            raise ValueError("Commercial context not ready for approval: " + "; ".join(readiness.missing_fields + readiness.blockers))

        # Verify digest
        audit = playbook.generation_audit or {}
        cc_audit = audit.get("commercial_context", {})
        source_snapshot_data = cc_audit.get("source_snapshot")
        if not source_snapshot_data:
            raise ValueError("Missing source snapshot")
        source_snapshot = CommercialSourceSnapshot.model_validate(source_snapshot_data)

        computed_digest = commercial_review_digest(workspace_id, content, source_snapshot)
        if computed_digest != content_digest:
            raise ValueError("Content digest does not match current draft")

        # Validate source bindings (profile/documents haven't drifted)
        self.validate_commercial_bindings(workspace_id, source_snapshot, playbook.source_payload)

        try:
            # Archive only after confirmation, readiness, digest, and source checks.
            active = self.get_active(workspace_id)
            if active is not None and active.id != playbook.id:
                active.status = SalesPlaybookStatus.ARCHIVED
                active.updated_at = datetime.now(UTC)
                self.db.flush()

            approval_snapshot = {
                "version": COMMERCIAL_CONTEXT_VERSION,
                "source_snapshot": source_snapshot.model_dump(mode="json"),
                "digest": content_digest,
                "approved_at": datetime.now(UTC).isoformat(),
                "approved_by": str(approved_by),
            }
            playbook.status = SalesPlaybookStatus.APPROVED
            playbook.approved_by = approved_by
            playbook.approved_at = datetime.now(UTC)
            playbook.generation_audit = {
                **audit,
                "approval_snapshot": approval_snapshot,
            }
            playbook.updated_at = datetime.now(UTC)
            self.db.flush()
        except IntegrityError as exc:
            self.db.rollback()
            raise ValueError("Commercial context approval conflict; retry the request") from exc
        return playbook

    def get_active(self, workspace_id: UUID) -> SalesPlaybook | None:
        return self.db.execute(
            select(SalesPlaybook).where(
                SalesPlaybook.workspace_id == workspace_id,
                SalesPlaybook.status == SalesPlaybookStatus.APPROVED,
            ).execution_options(populate_existing=True)
        ).scalar_one_or_none()

    def get(self, playbook_id: UUID, workspace_id: UUID) -> SalesPlaybook | None:
        return self.db.execute(
            select(SalesPlaybook).where(
                SalesPlaybook.id == playbook_id,
                SalesPlaybook.workspace_id == workspace_id,
            )
        ).scalar_one_or_none()

    def list(self, workspace_id: UUID) -> list[SalesPlaybook]:
        return list(self.db.execute(
            select(SalesPlaybook)
            .where(SalesPlaybook.workspace_id == workspace_id)
            .order_by(SalesPlaybook.version.desc())
        ).scalars().all())
