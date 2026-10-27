"""Validated, immutable Semantic IR checkpoint contracts and fingerprints.

This module deliberately contains no provider integration. It accepts only the
application-owned Semantic IR produced after the normal extraction validators.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from app.modules.sales_playbook.generation_models import (
    SalesPlaybookGenerationJob,
    SemanticOperationCheckpoint,
)
from app.modules.sales_playbook.semantic_policy import SemanticPolicyAssertion

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_GENERATION_CONFIG_FIELDS = frozenset({
    "temperature", "top_p", "top_k", "timeout_seconds", "max_completion_tokens",
    "automatic_retries", "structured_output_mode", "strict", "endpoint_identity",
})


class CheckpointIntegrityError(ValueError):
    """A checkpoint input, payload or provenance snapshot is not trustworthy."""


class SemanticCheckpointConflict(RuntimeError):
    """An immutable operation key already names different checkpoint data."""


@dataclass(frozen=True)
class SemanticOperationPlanEntry:
    operation_id: str
    operation_order: int
    partition_plan_fingerprint: str
    chunk_fingerprint: str
    input_fingerprint: str
    prompt_revision: str
    prompt_fingerprint: str
    schema_fingerprint: str


def build_semantic_operation_plan(chunks: tuple[Any, ...] | list[Any]) -> tuple[SemanticOperationPlanEntry, ...]:
    """Fingerprint the exact ordered generic Semantic IR requests, including their plan."""
    from app.ai.validation import contract_response_format
    from app.modules.sales_playbook.partition import (
        PROMPT_VERSION,
        _chunk_prompt,
        _system_prompt,
    )


    if not chunks:
        raise CheckpointIntegrityError("semantic operation plan is empty")
    chunk_fingerprints: list[tuple[str, str, str, str, str]] = []
    for chunk in chunks:
        if getattr(chunk, "validation_mode", None) != "generic":
            raise CheckpointIntegrityError("checkpoint plan accepts only generic semantic chunks")
        refs = tuple(getattr(chunk, "local_source_references", ()))
        sections = tuple(getattr(chunk, "source_sections", ()))
        if not refs or len(refs) != len(sections):
            raise CheckpointIntegrityError("semantic chunk source alias map is incomplete")
        if tuple(ref.fragment_id for ref in refs) != tuple(chunk.source_section_ids):
            raise CheckpointIntegrityError("semantic source alias order differs from chunk ownership")
        response_format = contract_response_format(chunk.contract)
        schema_fingerprint = canonical_sha256(response_format)
        prompt = _chunk_prompt(chunk, sections)
        system_prompt = _system_prompt(chunk.contract, generic=True)
        prompt_fingerprint = canonical_sha256({"system": system_prompt, "user": prompt})
        section_snapshots = []
        for section in sections:
            section_snapshots.append({
                "document_id": section.document_id,
                "fragment_id": section.fragment_id,
                "section_id": section.section_id,
                "start_line": section.start_line,
                "end_line": section.end_line,
                "start_byte_offset": section.start_byte_offset,
                "end_byte_offset": section.end_byte_offset,
                "order": section.order,
                "text_sha256": hashlib.sha256(section.text.encode("utf-8")).hexdigest(),
            })
        operation_id = f"sales_playbook.semantic_chunk.{chunk.chunk_id}"
        chunk_fingerprint = canonical_sha256({
            "format": "generic-semantic-chunk-v1",
            "chunk_id": chunk.chunk_id,
            "workflow_owner": chunk.workflow_owner,
            "source_section_ids": list(chunk.source_section_ids),
            "source_bytes": chunk.source_bytes,
            "max_assertions": chunk.max_assertions,
            "source_aliases": [
                {"alias": ref.alias, "fragment_id": ref.fragment_id,
                 "document_id": ref.document_id, "section_id": ref.section_id,
                 "start_line": ref.start_line, "end_line": ref.end_line,
                 "start_byte_offset": ref.start_byte_offset,
                 "end_byte_offset": ref.end_byte_offset}
                for ref in refs
            ],
            "sections": section_snapshots,
            "prompt_fingerprint": prompt_fingerprint,
            "schema_fingerprint": schema_fingerprint,
        })
        chunk_fingerprints.append((operation_id, chunk_fingerprint, prompt_fingerprint,
                                   schema_fingerprint, chunk.chunk_id))

    plan_fingerprint = canonical_sha256({
        "format": "generic_semantic_partition_v1",
        "operations": [
            {"operation_id": operation_id, "operation_order": index,
             "chunk_fingerprint": chunk_fingerprint}
            for index, (operation_id, chunk_fingerprint, *_rest) in enumerate(chunk_fingerprints)
        ],
    })
    return tuple(
        SemanticOperationPlanEntry(
            operation_id=operation_id,
            operation_order=index,
            partition_plan_fingerprint=plan_fingerprint,
            chunk_fingerprint=chunk_fingerprint,
            input_fingerprint=canonical_sha256({
                "partition_plan_fingerprint": plan_fingerprint,
                "operation_id": operation_id,
                "operation_order": index,
                "chunk_fingerprint": chunk_fingerprint,
            }),
            prompt_revision=PROMPT_VERSION,
            prompt_fingerprint=prompt_fingerprint,
            schema_fingerprint=schema_fingerprint,
        )
        for index, (operation_id, chunk_fingerprint, prompt_fingerprint,
                    schema_fingerprint, _chunk_id) in enumerate(chunk_fingerprints)
    )


def checkpoint_identity_for_operation(
    invocation: object,
    operation: SemanticOperationPlanEntry,
    *,
    provider_name: str,
    model_name: str,
    generation_config: dict[str, Any],
) -> SemanticCheckpointIdentity:
    """Bind a planned chunk to the service-issued generation/source snapshot."""
    from app.modules.sales_playbook.relation_invocation import TrustedGenerationInvocation

    if not isinstance(invocation, TrustedGenerationInvocation):
        raise CheckpointIntegrityError("trusted generation invocation is required")
    invocation.assert_issued()
    if getattr(invocation, "semantic_checkpoint_enabled", False) is not True:
        raise CheckpointIntegrityError("trusted generation is not checkpoint-enabled")
    workspace_id = getattr(invocation, "workspace_id", None)
    context_fingerprint = getattr(invocation, "semantic_checkpoint_context_fingerprint", None)
    input_identity = getattr(invocation, "semantic_checkpoint_input_identity", None)
    if not isinstance(workspace_id, str) or not isinstance(input_identity, str):
        raise CheckpointIntegrityError("trusted checkpoint context is incomplete")
    source_document_ids = tuple(getattr(invocation, "source_document_ids", ()))
    authorized_documents = tuple(getattr(invocation, "source_document_manifest", ()))
    source_type = "document" if source_document_ids else "manual"
    source_manifest = tuple(
        document.as_dict() if hasattr(document, "as_dict") else dict(document)
        for document in authorized_documents
    )
    safe_config = validate_safe_generation_config(generation_config)
    input_fingerprint = canonical_sha256({
        "format": "generic-semantic-operation-input-v1",
        "workspace_context_fingerprint": context_fingerprint,
        "pasted_input_identity": input_identity if source_type == "manual" else None,
        "source_fingerprint": getattr(invocation, "source_input_sha256", None),
        "operation_id": operation.operation_id,
        "operation_order": operation.operation_order,
        "partition_plan_fingerprint": operation.partition_plan_fingerprint,
        "chunk_fingerprint": operation.chunk_fingerprint,
        "prompt_revision": operation.prompt_revision,
        "prompt_fingerprint": operation.prompt_fingerprint,
        "schema_fingerprint": operation.schema_fingerprint,
        "provider_name": provider_name,
        "model_name": model_name,
        "generation_config": safe_config,
    })
    return SemanticCheckpointIdentity(
        workspace_id=workspace_id,
        source_type=source_type,
        source_document_ids=source_document_ids,
        source_manifest=source_manifest,
        source_content_fingerprint=getattr(invocation, "source_input_sha256", ""),
        pasted_input_identity=input_identity if source_type == "manual" else None,
        seller_profile_id=getattr(invocation, "seller_profile_id", None),
        seller_profile_fingerprint=getattr(invocation, "seller_profile_fingerprint", None),
        workspace_context_fingerprint=context_fingerprint,
        operation_id=operation.operation_id,
        operation_order=operation.operation_order,
        operation_version="generic_semantic_ir_v1",
        partition_plan_fingerprint=operation.partition_plan_fingerprint,
        chunk_fingerprint=operation.chunk_fingerprint,
        input_fingerprint=input_fingerprint,
        semantic_contract_revision="generic_semantic_ir_v1",
        provider_name=provider_name,
        model_name=model_name,
        prompt_revision=operation.prompt_revision,
        schema_fingerprint=operation.schema_fingerprint,
        generation_config=safe_config,
        dependency_fingerprints=(),
    )


def canonical_json_bytes(value: Any) -> bytes:
    """Canonical UTF-8 JSON; array order is significant and object keys are sorted."""
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise CheckpointIntegrityError("checkpoint input is not canonical JSON") from error


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _require_sha256(value: str, field: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise CheckpointIntegrityError(f"invalid {field} fingerprint")


def validate_safe_generation_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return a canonical-safe copy of output-affecting, non-secret settings."""
    if not isinstance(config, dict) or set(config) - _SAFE_GENERATION_CONFIG_FIELDS:
        raise CheckpointIntegrityError("generation config contains unsupported fields")
    numeric_fields = {"temperature", "top_p", "timeout_seconds"}
    integer_fields = {"top_k", "max_completion_tokens", "automatic_retries"}
    for key in numeric_fields & set(config):
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise CheckpointIntegrityError("generation config has an invalid numeric value")
    for key in integer_fields & set(config):
        if isinstance(config[key], bool) or not isinstance(config[key], int):
            raise CheckpointIntegrityError("generation config has an invalid integer value")
    if "strict" in config and not isinstance(config["strict"], bool):
        raise CheckpointIntegrityError("generation config has an invalid strict-mode value")
    if ("structured_output_mode" in config
            and config["structured_output_mode"] not in {"json_schema", "json_object"}):
        raise CheckpointIntegrityError("generation config has an unsupported structured-output mode")
    if "endpoint_identity" in config:
        endpoint = config["endpoint_identity"]
        if not isinstance(endpoint, str):
            raise CheckpointIntegrityError("invalid endpoint identity")
        parsed = urlsplit(endpoint)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise CheckpointIntegrityError("endpoint identity contains private components")
    try:
        normalized = json.loads(canonical_json_bytes(config))
    except (TypeError, ValueError) as error:
        raise CheckpointIntegrityError("generation config is not serializable") from error
    return normalized


@dataclass(frozen=True)
class SemanticCheckpointIdentity:
    """All authorized inputs that determine one Semantic IR operation result."""

    workspace_id: str
    source_type: str
    source_document_ids: tuple[str, ...]
    source_manifest: tuple[dict[str, Any], ...]
    source_content_fingerprint: str
    pasted_input_identity: str | None
    seller_profile_id: str | None
    seller_profile_fingerprint: str | None
    workspace_context_fingerprint: str
    operation_id: str
    operation_order: int
    operation_version: str
    partition_plan_fingerprint: str
    chunk_fingerprint: str
    input_fingerprint: str
    semantic_contract_revision: str
    provider_name: str
    model_name: str
    prompt_revision: str
    schema_fingerprint: str
    generation_config: dict[str, Any]
    dependency_fingerprints: tuple[str, ...]

    def __post_init__(self) -> None:
        for field in (
            "source_content_fingerprint", "workspace_context_fingerprint",
            "partition_plan_fingerprint", "chunk_fingerprint", "input_fingerprint",
            "schema_fingerprint",
        ):
            _require_sha256(getattr(self, field), field)
        if self.seller_profile_fingerprint is not None:
            _require_sha256(self.seller_profile_fingerprint, "seller profile")
        for dependency in self.dependency_fingerprints:
            _require_sha256(dependency, "dependency")
        if self.operation_order < 0 or not self.operation_id.strip():
            raise CheckpointIntegrityError("invalid semantic operation identity")
        if len(self.source_document_ids) != len(set(self.source_document_ids)):
            raise CheckpointIntegrityError("duplicate source document identity")
        if (self.seller_profile_id is None) != (self.seller_profile_fingerprint is None):
            raise CheckpointIntegrityError("seller profile identity is incomplete")
        if any(not isinstance(item, dict) for item in self.source_manifest):
            raise CheckpointIntegrityError("invalid source manifest")
        manifest_ids = tuple(str(item.get("document_id", "")) for item in self.source_manifest)
        if self.source_type == "document":
            if (not self.source_document_ids or manifest_ids != self.source_document_ids
                    or self.pasted_input_identity is not None):
                raise CheckpointIntegrityError("source document identity differs from manifest")
            for item in self.source_manifest:
                if not isinstance(item, dict):
                    raise CheckpointIntegrityError("invalid source manifest")
                for key in ("original_sha256", "normalized_sha256"):
                    _require_sha256(item.get(key), f"source manifest {key}")
        elif self.source_type in {"manual", "pasted_text"}:
            if self.source_document_ids or self.source_manifest or not self.pasted_input_identity:
                raise CheckpointIntegrityError("pasted source identity is incomplete")
        else:
            raise CheckpointIntegrityError("unsupported source type")
        validate_safe_generation_config(self.generation_config)

    @property
    def operation_identity_fingerprint(self) -> str:
        return canonical_sha256({
            "format": "semantic-operation-identity-v1",
            "operation_id": self.operation_id,
            "partition_plan_fingerprint": self.partition_plan_fingerprint,
            "chunk_fingerprint": self.chunk_fingerprint,
        })

    @property
    def generation_config_fingerprint(self) -> str:
        return canonical_sha256(validate_safe_generation_config(self.generation_config))

    def fingerprint(self) -> str:
        """Fingerprint all authorized source, profile, plan and model inputs."""
        return canonical_sha256({
            "format": "semantic-operation-checkpoint-input-v1",
            "workspace_id": self.workspace_id,
            "source_type": self.source_type,
            "source_document_ids": list(self.source_document_ids),
            "source_manifest": list(self.source_manifest),
            "source_content_fingerprint": self.source_content_fingerprint,
            "pasted_input_identity": self.pasted_input_identity,
            "seller_profile_id": self.seller_profile_id,
            "seller_profile_fingerprint": self.seller_profile_fingerprint,
            "workspace_context_fingerprint": self.workspace_context_fingerprint,
            "operation_id": self.operation_id,
            "operation_order": self.operation_order,
            "operation_identity_fingerprint": self.operation_identity_fingerprint,
            "operation_version": self.operation_version,
            "input_fingerprint": self.input_fingerprint,
            "semantic_contract_revision": self.semantic_contract_revision,
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "prompt_revision": self.prompt_revision,
            "schema_fingerprint": self.schema_fingerprint,
            "generation_config": validate_safe_generation_config(self.generation_config),
            "generation_config_fingerprint": self.generation_config_fingerprint,
            "dependency_fingerprints": list(self.dependency_fingerprints),
        })


def _assert_identity_matches_trusted_generation(
    identity: SemanticCheckpointIdentity,
    invocation: object,
) -> None:
    documents = tuple(getattr(invocation, "source_document_ids", ()))
    manifest = tuple(item.as_dict() for item in getattr(invocation, "source_document_manifest", ()))
    expected_source_type = "document" if documents else "manual"
    input_identity = getattr(invocation, "semantic_checkpoint_input_identity", None)
    if (
        identity.workspace_id != getattr(invocation, "workspace_id", None)
        or identity.source_type != expected_source_type
        or identity.source_document_ids != documents
        or identity.source_manifest != manifest
        or identity.source_content_fingerprint != getattr(invocation, "source_input_sha256", None)
        or identity.seller_profile_id != getattr(invocation, "seller_profile_id", None)
        or identity.seller_profile_fingerprint
        != getattr(invocation, "seller_profile_fingerprint", None)
        or identity.workspace_context_fingerprint
        != getattr(invocation, "semantic_checkpoint_context_fingerprint", None)
        or identity.pasted_input_identity != (input_identity if expected_source_type == "manual" else None)
    ):
        raise CheckpointIntegrityError("checkpoint identity differs from trusted generation")


class SourceProvenanceSnapshot(BaseModel):
    """Source-text-free location and digest for one authorized source fragment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fragment_id: str = Field(min_length=1, max_length=200)
    document_id: str | None = Field(default=None, max_length=100)
    section_id: str = Field(min_length=1, max_length=512)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    start_byte_offset: int = Field(ge=0)
    end_byte_offset: int = Field(ge=0)
    order: int = Field(ge=0)
    text_sha256: str

    @field_validator("text_sha256")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        _require_sha256(value, "provenance text")
        return value

    @model_validator(mode="after")
    def _valid_range(self):
        if self.end_line < self.start_line or self.end_byte_offset < self.start_byte_offset:
            raise ValueError("invalid provenance range")
        return self


class ValidatedSemanticChunkPayload(BaseModel):
    """The only content permitted in the checkpoint's validated IR column."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: str = Field(min_length=1, max_length=180)
    accepted: Literal[True]
    assertions: tuple[SemanticPolicyAssertion, ...]


class ValidatedSemanticCheckpointPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    validated_ir: dict[str, Any]
    source_provenance: tuple[SourceProvenanceSnapshot, ...]
    output_fingerprint: str


def build_validated_semantic_payload(
    semantic_ir: dict[str, Any], provenance: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> ValidatedSemanticCheckpointPayload:
    """Revalidate Semantic IR and prove its source references are snapshotted."""
    try:
        chunk = ValidatedSemanticChunkPayload.model_validate(semantic_ir)
        snapshots = tuple(SourceProvenanceSnapshot.model_validate(item) for item in provenance)
    except (ValidationError, TypeError, ValueError) as error:
        raise CheckpointIntegrityError("invalid validated Semantic IR or provenance") from error
    fragment_ids = [item.fragment_id for item in snapshots]
    if len(fragment_ids) != len(set(fragment_ids)):
        raise CheckpointIntegrityError("duplicate provenance fragment")
    authorized = set(fragment_ids)
    for assertion in chunk.assertions:
        if not assertion.source_section_ids or not set(assertion.source_section_ids) <= authorized:
            raise CheckpointIntegrityError("assertion provenance is not fully represented")
    validated_ir = chunk.model_dump(mode="json")
    source_provenance = tuple(sorted(snapshots, key=lambda item: (item.order, item.fragment_id)))
    output_fingerprint = canonical_sha256({
        "format": "validated-semantic-ir-output-v1",
        "validated_ir": validated_ir,
        "source_provenance": [item.model_dump(mode="json") for item in source_provenance],
    })
    return ValidatedSemanticCheckpointPayload(
        validated_ir=validated_ir,
        source_provenance=source_provenance,
        output_fingerprint=output_fingerprint,
    )


def verify_checkpoint_output(
    validated_ir: dict[str, Any],
    provenance: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    expected_output_fingerprint: str,
) -> ValidatedSemanticCheckpointPayload:
    """Revalidate persisted output and reject a corrupt stored digest."""
    payload = build_validated_semantic_payload(validated_ir, provenance)
    if payload.output_fingerprint != expected_output_fingerprint:
        raise CheckpointIntegrityError("checkpoint output fingerprint mismatch")
    return payload


@dataclass(frozen=True)
class CheckpointPersistResult:
    checkpoint_id: uuid.UUID
    output_fingerprint: str
    idempotent: bool


class SemanticOperationCheckpointRepository:
    """Insert-only checkpoint persistence; result returns only after DB commit."""

    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    def mark_plan_eligible(
        self,
        *,
        trusted_invocation: object,
        workspace_id: str | uuid.UUID,
        generation_job_id: str | uuid.UUID,
        plan_fingerprint: str,
        operations: tuple[SemanticCheckpointIdentity, ...],
    ) -> None:
        """Atomically freeze the validated plan before any checkpoint can be stored."""
        from app.modules.sales_playbook.relation_invocation import TrustedGenerationInvocation

        if not isinstance(trusted_invocation, TrustedGenerationInvocation):
            raise CheckpointIntegrityError("trusted generation invocation is required")
        trusted_invocation.assert_issued()
        if (trusted_invocation.semantic_checkpoint_enabled is not True
                or trusted_invocation.workspace_id != str(workspace_id)
                or trusted_invocation.generation_job_id != str(generation_job_id)):
            raise CheckpointIntegrityError("checkpoint plan is outside trusted invocation")
        _require_sha256(plan_fingerprint, "partition plan")
        if not operations or any(item.partition_plan_fingerprint != plan_fingerprint for item in operations):
            raise CheckpointIntegrityError("semantic operation plan is inconsistent")
        for item in operations:
            _assert_identity_matches_trusted_generation(item, trusted_invocation)
        operation_plan = {
            item.operation_id: {
                "operation_order": item.operation_order,
                "operation_identity_fingerprint": item.operation_identity_fingerprint,
                "input_fingerprint": item.fingerprint(),
            }
            for item in operations
        }
        if len(operation_plan) != len(operations):
            raise CheckpointIntegrityError("duplicate semantic operation in plan")
        workspace_uuid = uuid.UUID(str(workspace_id))
        job_uuid = uuid.UUID(str(generation_job_id))
        with self._session_factory.begin() as session:
            job = session.execute(
                select(SalesPlaybookGenerationJob)
                .where(
                    SalesPlaybookGenerationJob.id == job_uuid,
                    SalesPlaybookGenerationJob.workspace_id == workspace_uuid,
                ).with_for_update()
            ).scalar_one_or_none()
            if job is None:
                raise CheckpointIntegrityError("checkpoint owner job is unavailable")
            metadata = dict(job.progress_metadata or {})
            if (getattr(job.status, "value", job.status) != "running"
                    or job.request_fingerprint != trusted_invocation.request_fingerprint
                    or list(job.source_manifest or []) != [
                        item.as_dict() for item in trusted_invocation.source_document_manifest
                    ]
                    or (str(job.seller_profile_id) if job.seller_profile_id else None)
                    != trusted_invocation.seller_profile_id
                    or job.seller_profile_fingerprint != trusted_invocation.seller_profile_fingerprint):
                raise CheckpointIntegrityError("checkpoint plan owner differs from trusted job")
            if metadata.get("semantic_checkpoint_format") != "semantic_operation_checkpoint_v1":
                raise CheckpointIntegrityError("job is not checkpoint-enabled")
            if (metadata.get("semantic_checkpoint_context_fingerprint")
                    != trusted_invocation.semantic_checkpoint_context_fingerprint
                    or metadata.get("semantic_checkpoint_input_identity")
                    != trusted_invocation.semantic_checkpoint_input_identity):
                raise CheckpointIntegrityError("checkpoint context differs from persisted job")
            state = metadata.get("semantic_checkpoint_source_status")
            current_plan = metadata.get("semantic_checkpoint_plan")
            expected_plan = {
                "partition_plan_fingerprint": plan_fingerprint,
                "operations": operation_plan,
            }
            if state == "blocked":
                raise CheckpointIntegrityError("job is blocked as a checkpoint source")
            if state == "eligible":
                if current_plan != expected_plan:
                    raise SemanticCheckpointConflict("eligible semantic operation plan changed")
                return
            if state != "pending" or current_plan is not None:
                raise CheckpointIntegrityError("semantic checkpoint plan state is invalid")
            metadata["semantic_checkpoint_plan"] = expected_plan
            metadata["semantic_checkpoint_source_status"] = "eligible"
            job.progress_metadata = metadata

    def prepare_explicit_resume(
        self,
        *,
        trusted_invocation: object,
        workspace_id: str | uuid.UUID,
        generation_job_id: str | uuid.UUID,
        source_generation_job_id: str | uuid.UUID,
        plan_fingerprint: str,
        operations: tuple[SemanticCheckpointIdentity, ...],
    ) -> dict[str, dict[str, Any]]:
        """Preflight all origin checkpoints, then clone the compatible subset atomically."""
        from app.modules.sales_playbook.generation_models import (
            GenerationJobStatus,
        )
        from app.modules.sales_playbook.relation_invocation import TrustedGenerationInvocation

        if not isinstance(trusted_invocation, TrustedGenerationInvocation):
            raise CheckpointIntegrityError("trusted generation invocation is required")
        trusted_invocation.assert_issued()
        target_id = uuid.UUID(str(generation_job_id))
        source_id = uuid.UUID(str(source_generation_job_id))
        workspace_uuid = uuid.UUID(str(workspace_id))
        if (trusted_invocation.semantic_checkpoint_enabled is not True
                or trusted_invocation.generation_job_id != str(target_id)
                or trusted_invocation.semantic_checkpoint_resume_source_job_id != str(source_id)
                or trusted_invocation.workspace_id != str(workspace_uuid)):
            raise CheckpointIntegrityError("resume source differs from trusted invocation")
        if not operations or any(item.partition_plan_fingerprint != plan_fingerprint for item in operations):
            raise CheckpointIntegrityError("resume operation plan is inconsistent")
        for identity in operations:
            _assert_identity_matches_trusted_generation(identity, trusted_invocation)
        expected_operations = {
            item.operation_id: {
                "operation_order": item.operation_order,
                "operation_identity_fingerprint": item.operation_identity_fingerprint,
                "input_fingerprint": item.fingerprint(),
            }
            for item in operations
        }
        if len(expected_operations) != len(operations):
            raise CheckpointIntegrityError("duplicate semantic operation in resume plan")
        operation_by_id = {item.operation_id: item for item in operations}
        rejection: Exception | None = None
        reused: list[dict[str, Any]] = []
        reused_results: dict[str, dict[str, Any]] = {}
        try:
            with self._session_factory.begin() as session:
                jobs = session.execute(
                    select(SalesPlaybookGenerationJob)
                    .where(
                        SalesPlaybookGenerationJob.id.in_((source_id, target_id)),
                        SalesPlaybookGenerationJob.workspace_id == workspace_uuid,
                    )
                    .order_by(SalesPlaybookGenerationJob.id)
                    .with_for_update()
                ).scalars().all()
                by_id = {item.id: item for item in jobs}
                source = by_id.get(source_id)
                target = by_id.get(target_id)
                if source is None or target is None or source_id == target_id:
                    raise CheckpointIntegrityError("resume job pair is unavailable")
                source_metadata = dict(source.progress_metadata or {})
                target_metadata = dict(target.progress_metadata or {})
                if (source.status != GenerationJobStatus.FAILED
                        or target.status != GenerationJobStatus.RUNNING
                        or source_metadata.get("semantic_checkpoint_format")
                        != "semantic_operation_checkpoint_v1"
                        or target_metadata.get("semantic_checkpoint_format")
                        != "semantic_operation_checkpoint_v1"
                        or source_metadata.get("semantic_checkpoint_source_status") == "blocked"):
                    raise CheckpointIntegrityError("resume source or target state is not eligible")
                if (source.resume_from_generation_job_id is not None
                        and source_metadata.get("semantic_checkpoint_source_status") != "eligible"):
                    raise CheckpointIntegrityError("incomplete resume job cannot be a checkpoint source")
                if (target.resume_from_generation_job_id != source_id
                        or target.request_fingerprint != trusted_invocation.request_fingerprint
                        or source.workspace_id != target.workspace_id
                        or {key: value for key, value in source.request_payload.items()
                            if key != "idempotency_key"}
                        != {key: value for key, value in target.request_payload.items()
                            if key != "idempotency_key"}
                        or source.source_type != target.source_type
                        or list(source.source_manifest or []) != list(target.source_manifest or [])
                        or source.seller_profile_id != target.seller_profile_id
                        or source.seller_profile_fingerprint != target.seller_profile_fingerprint
                        or source_metadata.get("semantic_checkpoint_context_fingerprint")
                        != target_metadata.get("semantic_checkpoint_context_fingerprint")
                        or source_metadata.get("semantic_checkpoint_input_identity")
                        != target_metadata.get("semantic_checkpoint_input_identity")
                        or target_metadata.get("semantic_checkpoint_context_fingerprint")
                        != trusted_invocation.semantic_checkpoint_context_fingerprint
                        or target_metadata.get("semantic_checkpoint_input_identity")
                        != trusted_invocation.semantic_checkpoint_input_identity):
                    raise CheckpointIntegrityError("resume job input snapshot differs from origin")
                source_plan = source_metadata.get("semantic_checkpoint_plan")
                source_manifest = source_metadata.get("semantic_checkpoint_manifest") or {}
                source_rows = session.execute(
                    select(SemanticOperationCheckpoint).where(
                        SemanticOperationCheckpoint.workspace_id == workspace_uuid,
                        SemanticOperationCheckpoint.generation_job_id == source_id,
                    ).order_by(SemanticOperationCheckpoint.operation_order)
                ).scalars().all()
                row_by_operation = {row.operation_id: row for row in source_rows}
                if len(row_by_operation) != len(source_rows):
                    raise CheckpointIntegrityError("duplicate source checkpoint operation")
                if set(row_by_operation) - set(expected_operations):
                    raise CheckpointIntegrityError("source contains an unplanned checkpoint")
                if set(source_manifest) - set(expected_operations):
                    raise CheckpointIntegrityError("source manifest contains an unplanned operation")
                if set(row_by_operation) != set(source_manifest):
                    raise CheckpointIntegrityError("source checkpoint rows and manifest disagree")
                if source_metadata.get("semantic_checkpoint_source_status") == "eligible":
                    if source_plan != {
                        "partition_plan_fingerprint": plan_fingerprint,
                        "operations": expected_operations,
                    }:
                        raise CheckpointIntegrityError("source semantic operation plan changed")
                elif source_metadata.get("semantic_checkpoint_source_status") == "pending":
                    if source_plan is not None or source_rows or source_manifest:
                        raise CheckpointIntegrityError("pending source contains checkpoint state")
                else:
                    raise CheckpointIntegrityError("source checkpoint status is unknown")

                for operation_id, row in row_by_operation.items():
                    identity = operation_by_id[operation_id]
                    if row.workspace_id != workspace_uuid or row.generation_job_id != source_id:
                        raise CheckpointIntegrityError("source checkpoint ownership mismatch")
                    if (
                        row.operation_identity_fingerprint != identity.operation_identity_fingerprint
                        or row.operation_order != identity.operation_order
                        or row.checkpoint_format != "semantic_operation_checkpoint_v1"
                        or row.semantic_contract_revision != identity.semantic_contract_revision
                        or row.partition_plan_fingerprint != identity.partition_plan_fingerprint
                        or row.chunk_fingerprint != identity.chunk_fingerprint
                        or row.input_fingerprint != identity.fingerprint()
                        or row.workspace_context_fingerprint != identity.workspace_context_fingerprint
                        or row.source_type != identity.source_type
                        or row.source_document_ids != list(identity.source_document_ids)
                        or row.source_manifest != list(identity.source_manifest)
                        or row.source_content_fingerprint != identity.source_content_fingerprint
                        or row.seller_profile_id != (uuid.UUID(identity.seller_profile_id)
                                                     if identity.seller_profile_id else None)
                        or row.seller_profile_fingerprint != identity.seller_profile_fingerprint
                        or row.operation_version != identity.operation_version
                        or row.provider_name != identity.provider_name
                        or row.model_name != identity.model_name
                        or row.prompt_revision != identity.prompt_revision
                        or row.schema_fingerprint != identity.schema_fingerprint
                        or row.generation_config != identity.generation_config
                        or row.generation_config_fingerprint != identity.generation_config_fingerprint
                        or row.dependency_fingerprints != list(identity.dependency_fingerprints)
                        or row.validation_status != "validated"
                        or source_manifest.get(operation_id) != {
                            "operation_identity_fingerprint": identity.operation_identity_fingerprint,
                            "input_fingerprint": identity.fingerprint(),
                            "output_fingerprint": row.output_fingerprint,
                        }
                    ):
                        raise CheckpointIntegrityError("source checkpoint fingerprint or manifest mismatch")
                    verified = verify_checkpoint_output(
                        row.validated_ir, row.source_provenance, row.output_fingerprint,
                    )
                    source_doc_ids = set(identity.source_document_ids)
                    cited_docs = {item.document_id for item in verified.source_provenance
                                  if item.document_id is not None}
                    if (identity.source_type == "document"
                            and (any(item.document_id is None for item in verified.source_provenance)
                                 or not cited_docs <= source_doc_ids)):
                        raise CheckpointIntegrityError("source checkpoint provenance is unauthorized")
                    if identity.source_type != "document" and cited_docs:
                        raise CheckpointIntegrityError("pasted checkpoint has document provenance")
                    reused_results[operation_id] = verified.validated_ir
                    reused.append({
                        column.name: getattr(row, column.name)
                        for column in SemanticOperationCheckpoint.__table__.columns
                        if column.name not in {"id", "generation_job_id", "origin_checkpoint_id", "created_at"}
                    } | {
                        "id": uuid.uuid4(),
                        "generation_job_id": target_id,
                        "origin_checkpoint_id": row.id,
                        "created_at": datetime.now(UTC),
                    })

                target_plan = {
                    "partition_plan_fingerprint": plan_fingerprint,
                    "operations": expected_operations,
                }
                if target_metadata.get("semantic_checkpoint_source_status") != "pending":
                    raise CheckpointIntegrityError("resume target checkpoint state is not pending")
                if reused:
                    session.execute(pg_insert(SemanticOperationCheckpoint), reused)
                target_metadata["semantic_checkpoint_plan"] = target_plan
                target_metadata["semantic_checkpoint_manifest"] = dict(source_manifest)
                target_metadata["semantic_checkpoint_source_status"] = "eligible"
                target.progress_metadata = target_metadata
        except Exception as error:
            rejection = error
        if rejection is not None:
            # Record only a bounded blocked marker after the all-or-nothing clone transaction rolls back.
            with self._session_factory.begin() as session:
                target = session.execute(
                    select(SalesPlaybookGenerationJob).where(
                        SalesPlaybookGenerationJob.id == target_id,
                        SalesPlaybookGenerationJob.workspace_id == workspace_uuid,
                    ).with_for_update()
                ).scalar_one_or_none()
                if target is not None:
                    metadata = dict(target.progress_metadata or {})
                    metadata["semantic_checkpoint_source_status"] = "blocked"
                    metadata["semantic_checkpoint_failure_code"] = "RESUME_PREFLIGHT_REJECTED"
                    target.progress_metadata = metadata
            raise CheckpointIntegrityError("explicit resume checkpoint preflight failed") from rejection
        return reused_results

    def persist_validated(
        self,
        *,
        trusted_invocation: object,
        workspace_id: str | uuid.UUID,
        generation_job_id: str | uuid.UUID,
        identity: SemanticCheckpointIdentity,
        semantic_ir: dict[str, Any],
        provenance: list[dict[str, Any]] | tuple[dict[str, Any], ...],
        origin_checkpoint_id: uuid.UUID | None = None,
    ) -> CheckpointPersistResult:
        """Atomically persist the validated IR and its completion-manifest entry."""
        from app.modules.sales_playbook.relation_invocation import TrustedGenerationInvocation

        if not isinstance(trusted_invocation, TrustedGenerationInvocation):
            raise CheckpointIntegrityError("trusted generation invocation is required")
        trusted_invocation.assert_issued()
        if (trusted_invocation.semantic_checkpoint_enabled is not True
                or trusted_invocation.workspace_id != str(workspace_id)
                or trusted_invocation.generation_job_id != str(generation_job_id)):
            raise CheckpointIntegrityError("checkpoint write is outside trusted invocation")
        payload = build_validated_semantic_payload(semantic_ir, provenance)
        workspace_uuid = uuid.UUID(str(workspace_id))
        job_uuid = uuid.UUID(str(generation_job_id))
        if str(workspace_uuid) != identity.workspace_id:
            raise CheckpointIntegrityError("checkpoint workspace differs from trusted identity")
        _assert_identity_matches_trusted_generation(identity, trusted_invocation)
        provenance_document_ids = {
            item.document_id for item in payload.source_provenance if item.document_id is not None
        }
        if identity.source_type == "document":
            if any(item.document_id is None for item in payload.source_provenance):
                raise CheckpointIntegrityError("document checkpoint has unbound source provenance")
            if not provenance_document_ids <= set(identity.source_document_ids):
                raise CheckpointIntegrityError("provenance references a foreign source document")
        elif provenance_document_ids:
            raise CheckpointIntegrityError("pasted-text checkpoint references a document")
        source_document_ids = [uuid.UUID(item) for item in identity.source_document_ids]
        seller_profile_id = (
            uuid.UUID(identity.seller_profile_id) if identity.seller_profile_id is not None else None
        )
        config = validate_safe_generation_config(identity.generation_config)
        operation_fp = identity.operation_identity_fingerprint
        input_fp = identity.fingerprint()
        expected_values = {
            "workspace_id": workspace_uuid,
            "generation_job_id": job_uuid,
            "operation_id": identity.operation_id,
            "operation_identity_fingerprint": operation_fp,
            "operation_order": identity.operation_order,
            "checkpoint_format": "semantic_operation_checkpoint_v1",
            "semantic_contract_revision": identity.semantic_contract_revision,
            "partition_plan_fingerprint": identity.partition_plan_fingerprint,
            "chunk_fingerprint": identity.chunk_fingerprint,
            "input_fingerprint": input_fp,
            "workspace_context_fingerprint": identity.workspace_context_fingerprint,
            "source_type": identity.source_type,
            "source_document_ids": [str(item) for item in source_document_ids],
            "source_manifest": list(identity.source_manifest),
            "source_content_fingerprint": identity.source_content_fingerprint,
            "seller_profile_id": seller_profile_id,
            "seller_profile_fingerprint": identity.seller_profile_fingerprint,
            "operation_version": identity.operation_version,
            "provider_name": identity.provider_name,
            "model_name": identity.model_name,
            "prompt_revision": identity.prompt_revision,
            "schema_fingerprint": identity.schema_fingerprint,
            "generation_config": config,
            "generation_config_fingerprint": canonical_sha256(config),
            "dependency_fingerprints": list(identity.dependency_fingerprints),
            "validated_ir": payload.validated_ir,
            "source_provenance": [item.model_dump(mode="json") for item in payload.source_provenance],
            "output_fingerprint": payload.output_fingerprint,
            "validation_status": "validated",
            "origin_checkpoint_id": origin_checkpoint_id,
        }

        with self._session_factory.begin() as session:
            job = session.execute(
                select(SalesPlaybookGenerationJob)
                .where(
                    SalesPlaybookGenerationJob.id == job_uuid,
                    SalesPlaybookGenerationJob.workspace_id == workspace_uuid,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if job is None:
                raise CheckpointIntegrityError("checkpoint owner job is unavailable")
            metadata = dict(job.progress_metadata or {})
            if (getattr(job.status, "value", job.status) != "running"
                    or job.request_fingerprint != trusted_invocation.request_fingerprint
                    or list(job.source_manifest or []) != [
                        item.as_dict() for item in trusted_invocation.source_document_manifest
                    ]
                    or (str(job.seller_profile_id) if job.seller_profile_id else None)
                    != trusted_invocation.seller_profile_id
                    or job.seller_profile_fingerprint != trusted_invocation.seller_profile_fingerprint):
                raise CheckpointIntegrityError("checkpoint owner differs from trusted job")
            if metadata.get("semantic_checkpoint_format") != "semantic_operation_checkpoint_v1":
                raise CheckpointIntegrityError("job is not checkpoint-enabled")
            if (metadata.get("semantic_checkpoint_context_fingerprint")
                    != trusted_invocation.semantic_checkpoint_context_fingerprint
                    or metadata.get("semantic_checkpoint_input_identity")
                    != trusted_invocation.semantic_checkpoint_input_identity):
                raise CheckpointIntegrityError("checkpoint context differs from persisted job")
            if metadata.get("semantic_checkpoint_source_status") != "eligible":
                raise CheckpointIntegrityError("job is blocked as a checkpoint source")

            plan = metadata.get("semantic_checkpoint_plan") or {}
            plan_operation = (plan.get("operations") or {}).get(identity.operation_id)
            if (plan.get("partition_plan_fingerprint") != identity.partition_plan_fingerprint
                    or plan_operation is None
                    or plan_operation.get("operation_order") != identity.operation_order
                    or plan_operation.get("operation_identity_fingerprint")
                    != operation_fp
                    or plan_operation.get("input_fingerprint") != input_fp):
                raise CheckpointIntegrityError("operation differs from the eligible checkpoint plan")

            manifest = dict(metadata.get("semantic_checkpoint_manifest") or {})
            manifest_entry = manifest.get(identity.operation_id)
            existing = session.execute(
                select(SemanticOperationCheckpoint).where(
                    SemanticOperationCheckpoint.workspace_id == workspace_uuid,
                    SemanticOperationCheckpoint.generation_job_id == job_uuid,
                    SemanticOperationCheckpoint.operation_id == identity.operation_id,
                ).with_for_update()
            ).scalar_one_or_none()
            if (existing is None) != (manifest_entry is None):
                raise CheckpointIntegrityError("checkpoint row and completion manifest disagree")
            if existing is None:
                statement = pg_insert(SemanticOperationCheckpoint).values(**expected_values)
                statement = statement.on_conflict_do_nothing(
                    index_elements=["workspace_id", "generation_job_id", "operation_id"],
                )
                session.execute(statement)
                existing = session.execute(
                    select(SemanticOperationCheckpoint).where(
                        SemanticOperationCheckpoint.workspace_id == workspace_uuid,
                        SemanticOperationCheckpoint.generation_job_id == job_uuid,
                        SemanticOperationCheckpoint.operation_id == identity.operation_id,
                    ).with_for_update()
                ).scalar_one()
                idempotent = False
            else:
                idempotent = True

            for field, expected in expected_values.items():
                actual = getattr(existing, field)
                if isinstance(expected, uuid.UUID):
                    matches = actual == expected
                else:
                    try:
                        matches = canonical_json_bytes(actual) == canonical_json_bytes(expected)
                    except CheckpointIntegrityError as error:
                        raise CheckpointIntegrityError(
                            f"checkpoint database field is not canonical: {field}",
                        ) from error
                if not matches:
                    raise SemanticCheckpointConflict("immutable semantic operation result differs")

            new_entry = {
                "operation_identity_fingerprint": operation_fp,
                "input_fingerprint": input_fp,
                "output_fingerprint": payload.output_fingerprint,
            }
            if manifest_entry is not None and manifest_entry != new_entry:
                raise SemanticCheckpointConflict("semantic checkpoint manifest differs")
            if manifest_entry is None:
                manifest[identity.operation_id] = new_entry
                metadata["semantic_checkpoint_manifest"] = manifest
                job.progress_metadata = metadata
            checkpoint_id = existing.id

        # sessionmaker.begin() has committed before this success can escape.
        return CheckpointPersistResult(checkpoint_id, payload.output_fingerprint, idempotent)
