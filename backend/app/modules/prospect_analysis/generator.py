import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TypeVar

from pydantic import TypeAdapter

from app.ai.contracts import AIContract, EvidenceId, ProspectIntelligence, QualificationDecision
from app.ai.validation import (
    AIContractValidationError,
    RawLLMResponse,
    contract_response_format,
    request_validated_contract,
)
from app.integrations.llm import LLMGeneration

ContractT = TypeVar("ContractT", bound=AIContract)
_EVIDENCE_ID = TypeAdapter(EvidenceId)


def _evidence_scope(evidence_ids: Sequence[str]) -> tuple[str, ...]:
    """Snapshot server-selected references; do not normalize or invent identities."""
    if not isinstance(evidence_ids, tuple | list) or not evidence_ids:
        raise ValueError("An explicit nonempty evidence scope is required")
    scope = tuple(evidence_ids)
    for identifier in scope:
        if (
            not isinstance(identifier, str) or "..." in identifier
            or _EVIDENCE_ID.validate_python(identifier) != identifier
        ):
            raise ValueError("Invalid evidence scope")
    if len(scope) != len(set(scope)):
        raise ValueError("Duplicate evidence scope")
    return scope


def _evidence_response_format(contract: type[ContractT], scope: tuple[str, ...]) -> dict[str, Any]:
    """Constrain a fresh per-request schema, never the shared Pydantic contracts."""
    response_format = contract_response_format(contract)

    def constrain(node: Any) -> None:
        if isinstance(node, dict):
            for name, field in node.get("properties", {}).items():
                if name in {"evidence_ids", "supporting_evidence_ids"}:
                    field["items"]["enum"] = list(scope)
            for child in node.values():
                constrain(child)
        elif isinstance(node, list):
            for child in node:
                constrain(child)

    constrain(response_format["json_schema"]["schema"])
    return response_format


@dataclass(frozen=True)
class AnalysisGenerationAudit:
    provider: str
    model: str
    operation: str
    prompt_version: str
    contract_version: str
    latency_ms: int
    tokens: int
    validation_attempts: int
    repaired: bool
    generated_at: datetime


class ValidatedAnalysisGenerator:
    """Provider-neutral boundary: untrusted JSON becomes a validated contract."""

    def __init__(
        self,
        generate: Callable[[str, dict[str, Any], str, str], RawLLMResponse | LLMGeneration],
    ) -> None:
        self._generate = generate
        self._last_generation: LLMGeneration | None = None
        self.last_audit: AnalysisGenerationAudit | None = None

    def generate(
        self,
        prompt: str,
        contract: type[ContractT],
        *,
        system_prompt: str,
        prompt_version: str,
        operation: str,
        evidence_ids: Sequence[str],
    ) -> ContractT:
        self._last_generation = None
        self.last_audit = None
        if contract not in (ProspectIntelligence, QualificationDecision):
            raise ValueError("Unsupported evidence-bound analysis contract")
        scope = _evidence_scope(evidence_ids)
        scoped_system_prompt = (
            f"{system_prompt}\n"
            "Evidence references must copy an exact ID from this authorized list; never abbreviate "
            "or substitute placeholders. A valid ID does not establish relevance to the target business. "
            "Do not attribute facts about other businesses or directories to this prospect. If sources "
            "are insufficient, leave observations empty and record missing information; qualification "
            "must remain needs_more_research when fit is not supported.\n"
            f"Authorized evidence IDs: {json.dumps(scope, ensure_ascii=False)}"
        )
        result = request_validated_contract(
            lambda current_prompt: self._generate_for_validation(
                current_prompt,
                _evidence_response_format(contract, scope),
                scoped_system_prompt,
                operation,
            ),
            prompt,
            contract,
            repair_prompt=(
                f"{prompt}\n"
                f"Repair the response for {prompt_version}. Return only the requested JSON object. "
                "Use the authoritative schema supplied by the validator and do not include hidden chain-of-thought."
            ),
        )
        value = result.value
        if isinstance(value, ProspectIntelligence):
            references = [reference for observation in value.observations for reference in observation.evidence_ids]
            references.extend(reference for hypothesis in value.hypotheses for reference in hypothesis.supporting_evidence_ids)
        else:
            references = value.evidence_ids
        if set(references) - set(scope):
            raise AIContractValidationError(contract.__name__, result.attempts, "unknown evidence reference")
        if self._last_generation is not None:
            generation = self._last_generation
            self.last_audit = AnalysisGenerationAudit(
                provider=generation.provider,
                model=generation.model,
                operation=generation.operation,
                prompt_version=prompt_version,
                contract_version=result.contract_version,
                latency_ms=generation.latency_ms,
                tokens=generation.tokens,
                validation_attempts=result.attempts,
                repaired=result.repaired,
                generated_at=generation.generated_at,
            )
        return result.value

    def _generate_for_validation(
        self, prompt: str, response_format: dict[str, Any], system_prompt: str, operation: str
    ) -> RawLLMResponse:
        response = self._generate(prompt, response_format, system_prompt, operation)
        if isinstance(response, LLMGeneration):
            self._last_generation = response
            return response.text
        return response
def build_nebius_analysis_generator(settings=None) -> ValidatedAnalysisGenerator:
    from app.core.config import get_settings
    from app.integrations.llm import NebiusTokenFactoryProvider

    configured = settings or get_settings()
    if not configured.nebius_enabled or not configured.nebius_api_key:
        raise RuntimeError("Nebius está deshabilitado o no tiene API key")
    provider = NebiusTokenFactoryProvider(configured)

    def generate(prompt: str, response_format: dict[str, Any], system_prompt: str, operation: str):
        return provider.generate_with_metadata(
            prompt,
            system_prompt=system_prompt,
            operation=operation,
            response_format=response_format,
        )

    return ValidatedAnalysisGenerator(generate)
