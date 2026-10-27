"""Sealed, invocation-scoped authorization for generic relation compilation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from app.modules.sales_playbook.generic_contracts import ExtractionContractValidationError

_TRUSTED_INVOCATION_ISSUER = object()
_ADMITTED_RESPONSE_ISSUER = object()
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
RELATION_CONTRACT_REVISION = "relation_candidate_ir_v1"


def _canonical_fingerprint(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _model_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _model_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_model_value(item) for item in value]
    return value


def _fragment_id(section: object) -> str:
    fragment_id = getattr(section, "fragment_id", None)
    if not fragment_id:
        fragment_id = getattr(section, "extraction_section_id", None)
    return str(fragment_id or "")


def _section_snapshot(section: object) -> dict[str, Any]:
    text = getattr(section, "text", None)
    document_id = getattr(section, "document_id", None)
    return {
        "fragment_id": _fragment_id(section),
        "document_id": str(document_id) if document_id is not None else None,
        "section_id": str(getattr(section, "section_id", getattr(section, "original_section_id", ""))),
        "heading_path": list(getattr(section, "heading_path", ()) or ()),
        "order": getattr(section, "order", None),
        "start_line": getattr(section, "start_line", None),
        "end_line": getattr(section, "end_line", None),
        "start_byte_offset": getattr(section, "start_byte_offset", None),
        "end_byte_offset": getattr(section, "end_byte_offset", None),
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()
        if isinstance(text, str) else None,
    }


@dataclass(frozen=True)
class AuthorizedSourceDocument:
    document_id: str
    filename: str
    original_sha256: str
    normalized_sha256: str

    def as_dict(self) -> dict[str, str]:
        return {
            "document_id": self.document_id,
            "filename": self.filename,
            "original_sha256": self.original_sha256,
            "normalized_sha256": self.normalized_sha256,
        }


@dataclass(frozen=True)
class AuthorizedSourceSection:
    fragment_id: str
    document_id: str | None
    section_id: str
    heading_path: tuple[str, ...]
    order: int | None
    start_line: int | None
    end_line: int | None
    start_byte_offset: int | None
    end_byte_offset: int | None
    text_sha256: str | None

    @classmethod
    def from_section(cls, section: object) -> AuthorizedSourceSection:
        return cls(**_section_snapshot(section))

    def as_dict(self) -> dict[str, Any]:
        return {
            "fragment_id": self.fragment_id,
            "document_id": self.document_id,
            "section_id": self.section_id,
            "heading_path": list(self.heading_path),
            "order": self.order,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "start_byte_offset": self.start_byte_offset,
            "end_byte_offset": self.end_byte_offset,
            "text_sha256": self.text_sha256,
        }


@dataclass(frozen=True)
class TrustedGenerationInvocation:
    """Application-issued snapshot of already-authorized generation inputs."""

    workspace_id: str
    generation_job_id: str | None
    request_fingerprint: str
    source_fingerprint: str
    source_input_sha256: str
    source_document_ids: tuple[str, ...]
    source_document_manifest: tuple[AuthorizedSourceDocument, ...]
    source_manifest_fingerprint: str
    seller_profile_id: str | None
    seller_profile_fingerprint: str | None
    semantic_checkpoint_enabled: bool
    semantic_checkpoint_context_fingerprint: str | None
    semantic_checkpoint_input_identity: str | None
    semantic_checkpoint_resume_source_job_id: str | None
    invocation_id: str
    authorized_sections: tuple[AuthorizedSourceSection, ...]
    _issuer: object = field(repr=False, compare=False)

    def assert_issued(self) -> None:
        if self._issuer is not _TRUSTED_INVOCATION_ISSUER:
            raise RelationInvocationRejected("UNTRUSTED_GENERATION_CONTEXT")
        if not _SHA256.fullmatch(self.request_fingerprint):
            raise RelationInvocationRejected("INVALID_REQUEST_FINGERPRINT")
        if not _SHA256.fullmatch(self.source_fingerprint):
            raise RelationInvocationRejected("INVALID_SOURCE_FINGERPRINT")

    def assert_authorized_sections(self, sections: tuple[object, ...] | list[object]) -> None:
        self.assert_issued()
        allowed = {item.fragment_id: item.document_id for item in self.authorized_sections}
        authorized_by_id = {item.fragment_id: item for item in self.authorized_sections}
        seen: set[str] = set()
        for section in sections:
            snapshot = _section_snapshot(section)
            fragment_id = snapshot["fragment_id"]
            if (not fragment_id or fragment_id in seen
                    or fragment_id not in allowed
                    or allowed[fragment_id] != snapshot["document_id"]):
                raise RelationInvocationRejected("SOURCE_CATALOG_OUTSIDE_INVOCATION")
            authorized = authorized_by_id[fragment_id]
            if snapshot != authorized.as_dict():
                raise RelationInvocationRejected("SOURCE_FRAGMENT_CHANGED")
            seen.add(fragment_id)

    def assert_source_payload(
        self,
        source_payload: dict[str, Any],
        seller_profile: object | None,
    ) -> None:
        self.assert_issued()
        documents = source_payload.get("source_documents")
        if isinstance(documents, list) and documents:
            actual = []
            for document in documents:
                if not isinstance(document, dict) or not isinstance(document.get("content"), str):
                    raise RelationInvocationRejected("SOURCE_PAYLOAD_INVALID")
                content_hash = hashlib.sha256(document["content"].encode("utf-8")).hexdigest()
                actual.append({
                    "document_id": str(document.get("document_id", "")),
                    "normalized_sha256": content_hash,
                })
            expected = [{
                "document_id": item.document_id,
                "normalized_sha256": item.normalized_sha256,
            } for item in self.source_document_manifest]
            if actual != expected or source_payload.get("pasted_text"):
                raise RelationInvocationRejected("SOURCE_PAYLOAD_FINGERPRINT_MISMATCH")
        else:
            pasted_text = source_payload.get("pasted_text")
            if (self.source_document_ids or not isinstance(pasted_text, str)
                    or hashlib.sha256(pasted_text.encode("utf-8")).hexdigest()
                    != self.source_input_sha256):
                raise RelationInvocationRejected("SOURCE_PAYLOAD_FINGERPRINT_MISMATCH")
        profile_id = str(getattr(seller_profile, "id", "")) if seller_profile is not None else None
        if profile_id != self.seller_profile_id:
            raise RelationInvocationRejected("SELLER_PROFILE_IDENTITY_MISMATCH")
        if seller_profile is not None:
            from app.modules.sales_playbook.generation_service import seller_profile_fingerprint

            if seller_profile_fingerprint(seller_profile) != self.seller_profile_fingerprint:
                raise RelationInvocationRejected("SELLER_PROFILE_FINGERPRINT_MISMATCH")


@dataclass(frozen=True)
class TrustedRelationWindowContext:
    """Immutable authorization and request identity for one relation provider call."""

    generation: TrustedGenerationInvocation
    window_id: str
    operation: str
    invocation_id: str
    relation_contract_revision: str
    effective_schema_fingerprint: str
    prompt_revision: str
    prompt_fingerprint: str
    assertion_aliases: tuple[tuple[str, object], ...]
    assertion_catalog_fingerprint: str
    request_fingerprint: str
    source_sections: tuple[object, ...]
    source_catalog_fingerprint: str
    node_aliases: tuple[str, ...]
    node_catalog_fingerprint: str
    _issuer: object = field(repr=False, compare=False)

    def assert_current(self) -> None:
        if (self._issuer is not _TRUSTED_INVOCATION_ISSUER
                or self.relation_contract_revision != RELATION_CONTRACT_REVISION):
            raise RelationInvocationRejected("UNTRUSTED_RELATION_CONTEXT")
        self.generation.assert_authorized_sections(self.source_sections)
        if _fingerprint_assertions(self.assertion_aliases) != self.assertion_catalog_fingerprint:
            raise RelationInvocationRejected("ASSERTION_CATALOG_CHANGED")
        if _fingerprint_sections(self.source_sections) != self.source_catalog_fingerprint:
            raise RelationInvocationRejected("SOURCE_CATALOG_CHANGED")
        if _canonical_fingerprint(list(self.node_aliases)) != self.node_catalog_fingerprint:
            raise RelationInvocationRejected("NODE_ALIAS_CATALOG_CHANGED")
        aliases = [alias for alias, _ in self.assertion_aliases]
        assertion_ids = [str(getattr(item, "assertion_id", "")) for _, item in self.assertion_aliases]
        if len(aliases) != len(set(aliases)) or len(assertion_ids) != len(set(assertion_ids)):
            raise RelationInvocationRejected("DUPLICATE_ASSERTION_ALIAS")


@dataclass(frozen=True)
class TrustedProviderOperationContext:
    generation: TrustedGenerationInvocation
    operation: str
    invocation_id: str
    contract_revision: str
    effective_schema_fingerprint: str
    prompt_revision: str
    prompt_fingerprint: str
    catalog_fingerprint: str
    request_fingerprint: str
    _issuer: object = field(repr=False, compare=False)

    def assert_current(self) -> None:
        self.generation.assert_issued()
        if (self._issuer is not _TRUSTED_INVOCATION_ISSUER
                or not self.operation or not self.contract_revision
                or not _SHA256.fullmatch(self.effective_schema_fingerprint)
                or not _SHA256.fullmatch(self.prompt_fingerprint)
                or not _SHA256.fullmatch(self.catalog_fingerprint)
                or not _SHA256.fullmatch(self.request_fingerprint)):
            raise RelationInvocationRejected("UNTRUSTED_PROVIDER_OPERATION_CONTEXT")

    def assert_response(self, response: object) -> None:
        from app.integrations.llm import LLMGeneration

        self.assert_current()
        if (not isinstance(response, LLMGeneration)
                or response.invocation_id != self.invocation_id
                or response.operation != self.operation
                or response.finish_reason != "stop"
                or not response.success):
            raise RelationInvocationRejected("STALE_OR_UNBOUND_PROVIDER_RESPONSE", operation=self.operation)


@dataclass(frozen=True)
class AdmittedRelationResponse:
    """Validated provider output whose call and catalogs are sealed to one window."""

    invocation: TrustedRelationWindowContext
    value: object
    node_catalog_fingerprint: str
    candidate_fingerprint: str
    _issuer: object = field(repr=False, compare=False)

    def assert_admitted(self) -> None:
        if self._issuer is not _ADMITTED_RESPONSE_ISSUER:
            raise RelationInvocationRejected("UNADMITTED_RELATION_RESPONSE")
        self.invocation.assert_current()
        if _fingerprint_nodes(getattr(self.value, "nodes", ())) != self.node_catalog_fingerprint:
            raise RelationInvocationRejected("NODE_CATALOG_CHANGED")
        if _canonical_fingerprint(_model_value(self.value)) != self.candidate_fingerprint:
            raise RelationInvocationRejected("ADMITTED_CANDIDATE_CHANGED")


class RelationInvocationRejected(ExtractionContractValidationError):
    """Safe fail-closed error for authorization, freshness, or catalog mismatch."""

    def __init__(self, code: str, *, operation: str | None = None, window_id: str | None = None):
        self.code = code
        diagnostics = {
            "category": "relation_invocation_validation",
            "validation_code": code,
            "contract": RELATION_CONTRACT_REVISION,
        }
        if operation:
            diagnostics["operation"] = operation[:160]
        if window_id:
            diagnostics["window"] = window_id[:80]
        super().__init__(diagnostics)


def _issue_trusted_generation_invocation(
    *,
    workspace_id: object,
    generation_job: object | None,
    source_type: str,
    request_fingerprint: str | None,
    source_input_sha256: str,
    source_documents: tuple[tuple[object, str], ...] | list[tuple[object, str]],
    seller_profile: object | None,
    source_sections: tuple[object, ...] | list[object],
    relation_contract_revision: str,
) -> TrustedGenerationInvocation:
    """Issue context only from service-loaded, workspace-checked source/profile rows."""
    workspace = str(workspace_id)
    if relation_contract_revision != RELATION_CONTRACT_REVISION:
        raise RelationInvocationRejected("RELATION_CONTRACT_MISMATCH")
    if not _SHA256.fullmatch(source_input_sha256):
        raise RelationInvocationRejected("INVALID_SOURCE_INPUT_HASH")

    generation_job_id = None
    if generation_job is not None:
        from app.modules.sales_playbook.generation_service import (
            generation_job_payload_matches_fingerprint,
        )

        generation_job_id = getattr(generation_job, "id", None)
        if (str(getattr(generation_job, "workspace_id", "")) != workspace
                or getattr(generation_job, "source_type", None) != source_type
                or getattr(getattr(generation_job, "status", None), "value", None) != "running"
                or not generation_job_payload_matches_fingerprint(generation_job)
                or getattr(generation_job, "request_fingerprint", None) != request_fingerprint):
            raise RelationInvocationRejected("GENERATION_JOB_NOT_VERIFIED")

    document_rows: list[AuthorizedSourceDocument] = []
    document_ids: list[str] = []
    normalized_contents: list[str] = []
    for record, content in source_documents:
        document_id = str(getattr(record, "id", ""))
        record_workspace = str(getattr(record, "workspace_id", ""))
        original_hash = getattr(record, "original_sha256", None)
        normalized_hash = getattr(record, "normalized_sha256", None)
        if (not document_id or record_workspace != workspace
                or not isinstance(original_hash, str) or not _SHA256.fullmatch(original_hash)
                or not isinstance(normalized_hash, str) or not _SHA256.fullmatch(normalized_hash)
                or document_id in document_ids
                or hashlib.sha256(content.encode("utf-8")).hexdigest() != normalized_hash):
            raise RelationInvocationRejected("SOURCE_DOCUMENT_NOT_AUTHORIZED")
        document_ids.append(document_id)
        normalized_contents.append(content)
        document_rows.append(AuthorizedSourceDocument(
            document_id=document_id,
            filename=str(getattr(record, "original_filename", "")),
            original_sha256=original_hash,
            normalized_sha256=normalized_hash,
        ))
    if source_documents:
        actual_source_input_hash = hashlib.sha256(
            "\n\n".join(normalized_contents).encode("utf-8"),
        ).hexdigest()
        if actual_source_input_hash != source_input_sha256:
            raise RelationInvocationRejected("SOURCE_INPUT_FINGERPRINT_MISMATCH")
    elif not document_ids and not _SHA256.fullmatch(source_input_sha256):
        raise RelationInvocationRejected("INVALID_SOURCE_INPUT_HASH")

    profile_id = None
    profile_fingerprint = None
    if seller_profile is not None:
        if str(getattr(seller_profile, "workspace_id", "")) != workspace:
            raise RelationInvocationRejected("SELLER_PROFILE_NOT_AUTHORIZED")
        from app.modules.sales_playbook.generation_service import seller_profile_fingerprint

        profile_id = str(getattr(seller_profile, "id", ""))
        profile_fingerprint = seller_profile_fingerprint(seller_profile)
        if not profile_id or not profile_fingerprint:
            raise RelationInvocationRejected("SELLER_PROFILE_FINGERPRINT_MISSING")

    section_snapshots = tuple(_section_snapshot(item) for item in source_sections)
    authorized_sections = tuple(
        AuthorizedSourceSection(**{**item, "heading_path": tuple(item["heading_path"])})
        for item in section_snapshots
    )
    fragment_ids = [item.fragment_id for item in authorized_sections]
    if (not section_snapshots or any(not item for item in fragment_ids)
            or len(fragment_ids) != len(set(fragment_ids))):
        raise RelationInvocationRejected("INVALID_AUTHORIZED_SOURCE_SECTIONS")
    allowed_documents = set(document_ids)
    manifest = getattr(generation_job, "source_manifest", ()) if generation_job is not None else ()
    expected_manifest = [{
        "document_id": str(getattr(record, "id", "")),
        "filename": getattr(record, "original_filename", None),
        "normalized_sha256": getattr(record, "normalized_sha256", None),
        "original_sha256": getattr(record, "original_sha256", None),
    } for record, _content in source_documents]
    job_profile_id = getattr(generation_job, "seller_profile_id", None)
    actual_profile_id = getattr(seller_profile, "id", None) if seller_profile is not None else None
    if generation_job is not None and (
        list(manifest) != expected_manifest
        or (str(job_profile_id) if job_profile_id is not None else None)
        != (str(actual_profile_id) if actual_profile_id is not None else None)
    ):
        raise RelationInvocationRejected("GENERATION_JOB_INPUT_MISMATCH")

    checkpoint_metadata = (
        getattr(generation_job, "progress_metadata", {}) or {}
        if generation_job is not None else {}
    )
    checkpoint_enabled = (
        checkpoint_metadata.get("semantic_checkpoint_format") == "semantic_operation_checkpoint_v1"
    )
    checkpoint_context_fingerprint = None
    checkpoint_input_identity = None
    checkpoint_resume_source_job_id = None
    if checkpoint_enabled:
        checkpoint_context_fingerprint = checkpoint_metadata.get(
            "semantic_checkpoint_context_fingerprint",
        )
        checkpoint_input_identity = checkpoint_metadata.get("semantic_checkpoint_input_identity")
        checkpoint_resume_source_job_id = (
            str(getattr(generation_job, "resume_from_generation_job_id", None))
            if getattr(generation_job, "resume_from_generation_job_id", None) is not None
            else None
        )
        if (not isinstance(checkpoint_context_fingerprint, str)
                or not _SHA256.fullmatch(checkpoint_context_fingerprint)
                or not isinstance(checkpoint_input_identity, str)
                or not checkpoint_input_identity.strip()
                or len(checkpoint_input_identity) > 128
                or checkpoint_metadata.get("semantic_checkpoint_resume_source_job_id")
                != checkpoint_resume_source_job_id):
            raise RelationInvocationRejected("GENERATION_CHECKPOINT_CONTEXT_INVALID")
        if checkpoint_resume_source_job_id is None and (
            checkpoint_context_fingerprint != getattr(generation_job, "request_fingerprint", None)
            or checkpoint_input_identity != getattr(generation_job, "idempotency_key", None)
        ):
            raise RelationInvocationRejected("GENERATION_CHECKPOINT_CONTEXT_MISMATCH")
    for item in authorized_sections:
        document_id = item.document_id
        if (allowed_documents and document_id not in allowed_documents) or (
            not allowed_documents and document_id is not None
        ):
            raise RelationInvocationRejected("SOURCE_SECTION_DOCUMENT_NOT_AUTHORIZED")

    manifest_fingerprint = _canonical_fingerprint([item.as_dict() for item in document_rows])
    effective_request_fingerprint = request_fingerprint or _canonical_fingerprint({
        "workspace_id": workspace,
        "source_input_sha256": source_input_sha256,
        "source_manifest_fingerprint": manifest_fingerprint,
        "seller_profile_id": profile_id,
        "seller_profile_fingerprint": profile_fingerprint,
        "relation_contract_revision": relation_contract_revision,
    })
    if not _SHA256.fullmatch(effective_request_fingerprint):
        raise RelationInvocationRejected("INVALID_REQUEST_FINGERPRINT")
    source_fingerprint = _canonical_fingerprint({
        "workspace_id": workspace,
        "generation_job_id": str(generation_job_id) if generation_job_id is not None else None,
        "source_input_sha256": source_input_sha256,
        "source_manifest_fingerprint": manifest_fingerprint,
        "source_sections": section_snapshots,
        "seller_profile_id": profile_id,
        "seller_profile_fingerprint": profile_fingerprint,
        "relation_contract_revision": relation_contract_revision,
    })
    return TrustedGenerationInvocation(
        workspace_id=workspace,
        generation_job_id=str(generation_job_id) if generation_job_id is not None else None,
        request_fingerprint=effective_request_fingerprint,
        source_fingerprint=source_fingerprint,
        source_input_sha256=source_input_sha256,
        source_document_ids=tuple(document_ids),
        source_document_manifest=tuple(document_rows),
        source_manifest_fingerprint=manifest_fingerprint,
        seller_profile_id=profile_id,
        seller_profile_fingerprint=profile_fingerprint,
        semantic_checkpoint_enabled=checkpoint_enabled,
        semantic_checkpoint_context_fingerprint=checkpoint_context_fingerprint,
        semantic_checkpoint_input_identity=checkpoint_input_identity,
        semantic_checkpoint_resume_source_job_id=checkpoint_resume_source_job_id,
        invocation_id=str(uuid4()),
        authorized_sections=authorized_sections,
        _issuer=_TRUSTED_INVOCATION_ISSUER,
    )


def _issue_trusted_relation_window_context(
    generation: TrustedGenerationInvocation,
    *,
    window_id: str,
    operation: str,
    assertion_aliases: tuple[tuple[str, object], ...],
    source_sections: tuple[object, ...],
    contract: type,
    response_format: dict[str, Any],
    prompt: str,
    system_prompt: str,
    prompt_revision: str,
    node_aliases: tuple[str, ...],
) -> TrustedRelationWindowContext:
    generation.assert_issued()
    generation.assert_authorized_sections(source_sections)
    if not window_id or not operation or not prompt_revision or not prompt or not system_prompt:
        raise RelationInvocationRejected("INCOMPLETE_RELATION_REQUEST", operation=operation, window_id=window_id)
    aliases = [alias for alias, _ in assertion_aliases]
    assertions = [item for _, item in assertion_aliases]
    assertion_ids = [str(getattr(item, "assertion_id", "")) for item in assertions]
    if (len(aliases) != len(set(aliases))
            or len(assertion_ids) != len(set(assertion_ids))
            or any(not alias or not assertion_id for alias, assertion_id in zip(aliases, assertion_ids, strict=True))):
        raise RelationInvocationRejected("DUPLICATE_ASSERTION_ALIAS", operation=operation, window_id=window_id)
    source_fragment_ids = {item.fragment_id for item in generation.authorized_sections}
    for assertion in assertions:
        refs = tuple(getattr(assertion, "source_section_ids", ()))
        if not refs or not set(refs) <= source_fragment_ids:
                raise RelationInvocationRejected(
                    "ASSERTION_SOURCE_OUTSIDE_INVOCATION", operation=operation, window_id=window_id,
                )

    schema = contract.model_json_schema()
    schema_fingerprint = _canonical_fingerprint(response_format)
    if response_format.get("type") != "json_schema" or response_format.get("json_schema", {}).get("schema") != schema:
        raise RelationInvocationRejected("EFFECTIVE_SCHEMA_MISMATCH", operation=operation, window_id=window_id)
    schema_contract = schema.get("properties", {}).get("contract_version", {}).get("const")
    if schema_contract != RELATION_CONTRACT_REVISION:
        raise RelationInvocationRejected("RELATION_CONTRACT_MISMATCH", operation=operation, window_id=window_id)
    if not node_aliases or len(node_aliases) != len(set(node_aliases)):
        raise RelationInvocationRejected("INVALID_NODE_ALIAS_CATALOG", operation=operation, window_id=window_id)

    assertion_catalog_fingerprint = _fingerprint_assertions(assertion_aliases)
    source_catalog_fingerprint = _fingerprint_sections(source_sections)
    node_catalog_fingerprint = _canonical_fingerprint(list(node_aliases))
    request_fingerprint = _canonical_fingerprint({
        "generation_invocation_id": generation.invocation_id,
        "workspace_id": generation.workspace_id,
        "generation_job_id": generation.generation_job_id,
        "request_fingerprint": generation.request_fingerprint,
        "source_fingerprint": generation.source_fingerprint,
        "window_id": window_id,
        "operation": operation,
        "assertion_catalog_fingerprint": assertion_catalog_fingerprint,
        "source_catalog_fingerprint": source_catalog_fingerprint,
        "node_catalog_fingerprint": node_catalog_fingerprint,
        "relation_contract_revision": RELATION_CONTRACT_REVISION,
        "effective_schema_fingerprint": schema_fingerprint,
        "prompt_revision": prompt_revision,
        "prompt_fingerprint": _canonical_fingerprint({"system": system_prompt, "user": prompt}),
    })
    return TrustedRelationWindowContext(
        generation=generation,
        window_id=window_id,
        operation=operation,
        invocation_id=str(uuid4()),
        relation_contract_revision=RELATION_CONTRACT_REVISION,
        effective_schema_fingerprint=schema_fingerprint,
        prompt_revision=prompt_revision,
        prompt_fingerprint=_canonical_fingerprint({"system": system_prompt, "user": prompt}),
        assertion_aliases=tuple(assertion_aliases),
        assertion_catalog_fingerprint=assertion_catalog_fingerprint,
        source_sections=tuple(source_sections),
        source_catalog_fingerprint=source_catalog_fingerprint,
        node_aliases=tuple(node_aliases),
        node_catalog_fingerprint=node_catalog_fingerprint,
        request_fingerprint=request_fingerprint,
        _issuer=_TRUSTED_INVOCATION_ISSUER,
    )


def _issue_trusted_provider_operation_context(
    generation: TrustedGenerationInvocation,
    *,
    operation: str,
    contract_revision: str,
    response_format: dict[str, Any],
    prompt: str,
    system_prompt: str,
    prompt_revision: str,
    catalog_fingerprint: str,
) -> TrustedProviderOperationContext:
    generation.assert_issued()
    if not operation or not prompt or not system_prompt or not prompt_revision:
        raise RelationInvocationRejected("INCOMPLETE_PROVIDER_OPERATION_CONTEXT", operation=operation)
    schema_fingerprint = _canonical_fingerprint(response_format)
    prompt_fingerprint = _canonical_fingerprint({"system": system_prompt, "user": prompt})
    request_fingerprint = _canonical_fingerprint({
        "generation_invocation_id": generation.invocation_id,
        "workspace_id": generation.workspace_id,
        "generation_job_id": generation.generation_job_id,
        "request_fingerprint": generation.request_fingerprint,
        "source_fingerprint": generation.source_fingerprint,
        "operation": operation,
        "contract_revision": contract_revision,
        "schema_fingerprint": schema_fingerprint,
        "prompt_revision": prompt_revision,
        "prompt_fingerprint": prompt_fingerprint,
        "catalog_fingerprint": catalog_fingerprint,
    })
    return TrustedProviderOperationContext(
        generation=generation,
        operation=operation,
        invocation_id=str(uuid4()),
        contract_revision=contract_revision,
        effective_schema_fingerprint=schema_fingerprint,
        prompt_revision=prompt_revision,
        prompt_fingerprint=prompt_fingerprint,
        catalog_fingerprint=catalog_fingerprint,
        request_fingerprint=request_fingerprint,
        _issuer=_TRUSTED_INVOCATION_ISSUER,
    )


def _admit_relation_response(
    response: object,
    parsed: object,
    contract: type,
    context: TrustedRelationWindowContext,
) -> AdmittedRelationResponse:
    from app.integrations.llm import LLMGeneration

    context.assert_current()
    if (not isinstance(response, LLMGeneration)
            or response.invocation_id != context.invocation_id
            or response.operation != context.operation
            or response.finish_reason != "stop"
            or not response.success):
        raise RelationInvocationRejected(
            "STALE_OR_UNBOUND_PROVIDER_RESPONSE",
            operation=context.operation,
            window_id=context.window_id,
        )
    if _canonical_fingerprint(response_format_for(contract)) != context.effective_schema_fingerprint:
        raise RelationInvocationRejected("EFFECTIVE_SCHEMA_MISMATCH", operation=context.operation, window_id=context.window_id)
    if getattr(parsed, "contract_version", None) != context.relation_contract_revision:
        raise RelationInvocationRejected("RELATION_CONTRACT_MISMATCH", operation=context.operation, window_id=context.window_id)
    try:
        parsed_again = contract.model_validate_json(response.text)
    except Exception as error:
        raise RelationInvocationRejected("UNVALIDATED_PROVIDER_RESPONSE", operation=context.operation, window_id=context.window_id) from error
    if _model_value(parsed_again) != _model_value(parsed):
        raise RelationInvocationRejected("PARSED_RESPONSE_MISMATCH", operation=context.operation, window_id=context.window_id)
    node_fingerprint = _fingerprint_nodes(getattr(parsed, "nodes", ()))
    return AdmittedRelationResponse(
        invocation=context,
        value=parsed,
        node_catalog_fingerprint=node_fingerprint,
        candidate_fingerprint=_canonical_fingerprint(_model_value(parsed)),
        _issuer=_ADMITTED_RESPONSE_ISSUER,
    )


def response_format_for(contract: type) -> dict[str, Any]:
    from app.ai.validation import contract_response_format

    return contract_response_format(contract)


def _fingerprint_assertions(assertion_aliases: tuple[tuple[str, object], ...]) -> str:
    rows = []
    for alias, assertion in assertion_aliases:
        value = _model_value(assertion)
        rows.append({"alias": alias, "assertion": value})
    return _canonical_fingerprint(rows)


def _fingerprint_sections(sections: tuple[object, ...] | list[object]) -> str:
    return _canonical_fingerprint([_section_snapshot(item) for item in sections])


def _fingerprint_nodes(nodes: object) -> str:
    return _canonical_fingerprint([_model_value(item) for item in nodes])


def fingerprint_catalog(value: Any) -> str:
    return _canonical_fingerprint(_model_value(value))
