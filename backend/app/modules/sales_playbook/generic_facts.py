"""Owner-neutral source claims used to make sparse knowledge reviewable."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from app.ai.validation import contract_response_format
from app.integrations.llm import LLMGeneration
from app.modules.sales_playbook.extraction import _section_audit
from app.modules.sales_playbook.generic_contracts import (
    ExtractionContractValidationError,
    build_contract_diagnostics,
)
from app.modules.sales_playbook.generic_identity import (
    claim_id as _application_claim_id,
)
from app.modules.sales_playbook.generic_identity import (
    conflict_group_id as _application_conflict_group_id,
)
from app.modules.sales_playbook.generic_provenance import (
    LocalSourceReference,
    ProvenanceValidationError,
    build_local_source_reference_map,
    resolve_local_source_references,
)
from app.modules.sales_playbook.source_planning import GenericExtractionUnit


class _GenericSourceClaimFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: Literal[
        "offer", "price", "target_customer", "geography", "channel",
        "qualification", "classification", "workflow", "setter_policy",
        "closer_policy", "message_template", "experiment", "other",
    ]
    statement: str = Field(min_length=1, max_length=2000)


class GenericSourceClaim(_GenericSourceClaimFields):
    """Application-side claim with resolved persistent source provenance."""

    claim_id: str = Field(min_length=1, max_length=160)
    conflict_group_id: str | None = Field(default=None, max_length=120)
    source_section_ids: list[str] = Field(min_length=1, max_length=27)


class _ProviderGenericSourceClaim(_GenericSourceClaimFields):
    """Transport DTO; source references are replaced per extraction unit."""

    source_refs: list[str] = Field(min_length=1, max_length=27)
    conflict_group_ref: str | None = Field(default=None, pattern=r"^GROUP_[0-9]{3}$")


class GenericSourceClaimsExtraction(BaseModel):
    """Resolved source claims retained for application-side compatibility."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1.0"] = "1.0"
    claims: list[GenericSourceClaim] = Field(default_factory=list, max_length=40)


class _ProviderGenericSourceClaimsExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1.0"] = "1.0"
    claims: list[_ProviderGenericSourceClaim] = Field(default_factory=list, max_length=40)


def _provider_contract_for_unit(aliases: tuple[str, ...]):
    local_ref_enum = Enum(
        "GenericSourceClaimRef", {alias: alias for alias in aliases}, type=str,
    )
    claim = create_model(
        "ProviderGenericSourceClaimForUnit",
        __base__=_ProviderGenericSourceClaim,
        source_refs=(list[local_ref_enum], Field(min_length=1, max_length=len(aliases))),
    )
    return create_model(
        "ProviderGenericSourceClaimsForUnit",
        __base__=_ProviderGenericSourceClaimsExtraction,
        claims=(list[claim], Field(default_factory=list, max_length=40)),
    )


def _validate_returned_aliases(
    response_text: str,
    local_refs: Sequence[LocalSourceReference],
    *,
    operation_id: str,
) -> None:
    try:
        payload = json.loads(response_text)
    except (TypeError, json.JSONDecodeError):
        return  # The typed structured-output validator will report malformed JSON.
    claims = payload.get("claims") if isinstance(payload, dict) else None
    if not isinstance(claims, list):
        return
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict) or not isinstance(claim.get("source_refs"), list):
            continue
        try:
            resolve_local_source_references(
                claim["source_refs"], local_refs, operation_id=operation_id,
                assertion_index=index,
            )
        except ProvenanceValidationError:
            raise


class GenericSourceClaimsExtractor:
    """Extract concise source-backed statements and resolve local refs privately."""

    def __init__(self, generate):
        self._generate = generate
        self.last_audits: tuple[object, ...] = ()

    def extract(
        self,
        units: tuple[GenericExtractionUnit, ...] | list[GenericExtractionUnit],
        *,
        on_unit_success: Callable[[GenericExtractionUnit], None] | None = None,
    ) -> tuple[GenericSourceClaim, ...]:
        outputs: list[GenericSourceClaim] = []
        audits = []
        for unit in units:
            operation = f"sales_playbook.generic_source_claims.{unit.unit_id}"
            sections = unit.source_sections
            local_refs = build_local_source_reference_map(sections)
            aliases = tuple(reference.alias for reference in local_refs)
            contract = _provider_contract_for_unit(aliases)
            prompt = "\n\n".join(
                f'<source_fragment ref="{reference.alias}">\n{section.text}\n</source_fragment>'
                for reference, section in zip(local_refs, sections, strict=True)
            )
            system_prompt = (
        "Extract concise factual or policy statements that the supplied source explicitly says. "
        "This is a source inventory, not approval authority: do not recommend, reconcile, fill blanks, "
        "or infer absent facts. Preserve incompatible source statements as separate claims and, only when "
        "they directly conflict, reuse one operation-local GROUP_### conflict_group_ref for that set. "
        "Do not use conflict_group_ref for a single claim. Cite only the opaque source_refs "
                "shown for this operation; never output internal source identifiers. "
                f"Return JSON matching this schema: {contract.model_json_schema()}"
            )
            response = self._generate(
                prompt, contract_response_format(contract), system_prompt, operation,
            )
            audit = _section_audit(
                kind=f"generic_source_claims.{unit.unit_id}", operation=operation,
                section_ids=tuple(section.section_id for section in sections),
                generations=[response] if isinstance(response, LLMGeneration) else [],
                repair_count=0, success=False, provider_name=getattr(response, "provider", None),
                json_parsed=isinstance(getattr(response, "text", None), str),
                pydantic_valid=False, prompt_version="generic-source-claims-v2",
            )
            audits.append(audit)
            self.last_audits = tuple(audits)
            if not isinstance(response, LLMGeneration) or not response.success:
                raise ValueError("generic source-claims provider request failed")
            envelope = response.response_envelope
            status = getattr(envelope, "http_status", None) if envelope else None
            if status is not None and not 200 <= status < 300:
                raise ValueError("generic source-claims provider request failed")
            if response.finish_reason != "stop":
                raise ValueError(f"generic source-claims finish_reason={response.finish_reason or 'missing'}")
            _validate_returned_aliases(response.text, local_refs, operation_id=unit.unit_id)
            try:
                parsed = contract.model_validate_json(response.text)
            except Exception as error:
                raise ValueError("generic source-claims structured output is invalid") from error

            group_counts: dict[str, int] = {}
            for claim in parsed.claims:
                if claim.conflict_group_ref:
                    group_counts[claim.conflict_group_ref] = group_counts.get(claim.conflict_group_ref, 0) + 1
            for group_alias, count in group_counts.items():
                if count < 2:
                    raise ExtractionContractValidationError(build_contract_diagnostics({
                        "operation": operation,
                        "unit": unit.unit_id,
                        "contract": "generic_source_claims_v1",
                        "validation_code": "conflict_group_requires_multiple_claims",
                        "field": "claims[].conflict_group_ref",
                        "received_value": group_alias,
                        "expected_constraint": "the same GROUP_### alias must label at least two claims in this unit",
                    }))

            group_ordinals: dict[str, str] = {}
            resolved_claims = []
            for index, claim in enumerate(parsed.claims):
                source_ids = resolve_local_source_references(
                    [reference.value if isinstance(reference, Enum) else str(reference)
                     for reference in claim.source_refs],
                    local_refs, operation_id=unit.unit_id, assertion_index=index,
                )
                persistent_group = None
                if claim.conflict_group_ref:
                    persistent_group = group_ordinals.setdefault(
                        claim.conflict_group_ref,
                        _application_conflict_group_id(unit.unit_id, len(group_ordinals) + 1),
                    )
                resolved_claims.append(GenericSourceClaim.model_validate(
                    claim.model_dump(mode="json", exclude={"source_refs", "conflict_group_ref"})
                    | {
                        "claim_id": _application_claim_id(unit.unit_id, index + 1),
                        "conflict_group_id": persistent_group,
                        "source_section_ids": source_ids,
                    }
                ))
            audits[-1] = _section_audit(
                kind=f"generic_source_claims.{unit.unit_id}", operation=operation,
                section_ids=tuple(section.section_id for section in sections), generations=[response],
                repair_count=0, success=True, provider_name=response.provider,
                json_parsed=True, pydantic_valid=True, prompt_version="generic-source-claims-v2",
            )
            self.last_audits = tuple(audits)
            outputs.extend(resolved_claims)
            if on_unit_success is not None:
                on_unit_success(unit)
        if len({claim.claim_id for claim in outputs}) != len(outputs):
            raise ValueError("generic source claim identifiers must be unique")
        self.last_audits = tuple(audits)
        return tuple(outputs)


__all__ = ["GenericSourceClaim", "GenericSourceClaimsExtraction", "GenericSourceClaimsExtractor"]
