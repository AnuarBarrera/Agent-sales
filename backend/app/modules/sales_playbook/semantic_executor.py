"""Reusable execution boundary for validated generic semantic operations."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil
from time import monotonic
from typing import Any

from app.modules.sales_playbook.generic_relations import GenericWorkflowRelationExtractor
from app.modules.sales_playbook.partition import (
    SemanticChunkObservation,
    SemanticChunkResult,
    SemanticPolicyChunkExtractor,
    partition_generic_semantic_extraction,
)
from app.modules.sales_playbook.relation_invocation import (
    RelationInvocationRejected,
    TrustedGenerationInvocation,
)
from app.modules.sales_playbook.semantic_policy import SemanticPolicyAssertion
from app.modules.sales_playbook.source_planning import (
    ExtractionSourceSection,
    GenericExtractionUnit,
    build_generic_extraction_plan,
    generic_source_sections,
)


@dataclass(frozen=True)
class SemanticOperationExecution:
    """Validated semantic-stage output plus dispatch state for production continuation."""

    source_sections: tuple[ExtractionSourceSection, ...]
    units: tuple[GenericExtractionUnit, ...]
    chunks: tuple[Any, ...]
    results: tuple[SemanticChunkResult, ...]
    observations: tuple[SemanticChunkObservation, ...]
    execution_budget: Any | None
    budgeted_generate: Callable[..., Any]
    total_steps: int
    completed_steps: int


@dataclass(frozen=True)
class _PreparedSemanticOperations:
    """A validated operation plan ready to execute, without downstream synthesis."""

    _trusted_invocation: TrustedGenerationInvocation
    _source_sections: tuple[ExtractionSourceSection, ...]
    _units: tuple[GenericExtractionUnit, ...]
    _chunks: tuple[Any, ...]
    _provider_name: str | None
    _model_name: str | None
    _budgeted_generate: Callable[..., Any]
    _execution_budget: Any | None
    _checkpoint_repository: Any | None
    _checkpoint_operations: dict[str, Any]
    _checkpoint_identities: dict[str, Any]
    _resumed_semantic_ir: dict[str, dict[str, Any]]
    total_steps: int
    _observations: tuple[SemanticChunkObservation, ...] = ()
    _started: bool = False

    @property
    def execution_budget(self) -> Any | None:
        return self._execution_budget

    @property
    def observations(self) -> tuple[SemanticChunkObservation, ...]:
        return self._observations

    @property
    def started(self) -> bool:
        return self._started

    def execute(
        self,
        *,
        progress_callback: Callable[[str, int, int], None] | None = None,
        on_started: Callable[[], None] | None = None,
    ) -> SemanticOperationExecution:
        """Run and validate each planned chunk; return before relation operations."""
        extractor = self.create_extractor()
        object.__setattr__(self, "_started", True)
        if on_started is not None:
            on_started()
        results: list[SemanticChunkResult] = []
        completed_steps = 0

        def persist_semantic_checkpoint(chunk, result, _sections, response):
            from app.modules.sales_playbook.semantic_checkpoints import (
                CheckpointIntegrityError,
            )

            if response.provider != self._provider_name or response.model != self._model_name:
                raise CheckpointIntegrityError("semantic provider response identity mismatch")
            operation = self._checkpoint_operations.get(chunk.chunk_id)
            if operation is None:
                raise CheckpointIntegrityError("semantic operation is absent from checkpoint plan")
            identity = self._checkpoint_identities[chunk.chunk_id]
            provenance = [{
                "fragment_id": section.fragment_id,
                "document_id": section.document_id,
                "section_id": section.section_id,
                "start_line": section.start_line,
                "end_line": section.end_line,
                "start_byte_offset": section.start_byte_offset,
                "end_byte_offset": section.end_byte_offset,
                "order": section.order,
                "text_sha256": hashlib.sha256(section.text.encode("utf-8")).hexdigest(),
            } for section in chunk.source_sections]
            self._checkpoint_repository.persist_validated(
                trusted_invocation=self._trusted_invocation,
                workspace_id=self._trusted_invocation.workspace_id,
                generation_job_id=self._trusted_invocation.generation_job_id,
                identity=identity,
                semantic_ir={
                    "chunk_id": result.chunk_id,
                    "accepted": result.accepted,
                    "assertions": [item.model_dump(mode="json") for item in result.assertions],
                },
                provenance=provenance,
            )

        try:
            for chunk in self._chunks:
                restored = self._resumed_semantic_ir.get(
                    f"sales_playbook.semantic_chunk.{chunk.chunk_id}",
                )
                if restored is not None:
                    result = SemanticChunkResult(
                        chunk_id=restored["chunk_id"],
                        accepted=restored["accepted"],
                        assertions=tuple(
                            SemanticPolicyAssertion.model_validate(item)
                            for item in restored["assertions"]
                        ),
                    )
                else:
                    result = extractor.extract_chunk(
                        chunk,
                        chunk.source_sections,
                        on_validated=persist_semantic_checkpoint
                        if self._trusted_invocation.semantic_checkpoint_enabled else None,
                    )
                results.append(result)
                completed_steps += 1
                if progress_callback is not None:
                    progress_callback("semantic", completed_steps, self.total_steps)
        finally:
            object.__setattr__(self, "_observations", tuple(extractor.observations))

        return SemanticOperationExecution(
            source_sections=self._source_sections,
            units=self._units,
            chunks=self._chunks,
            results=tuple(results),
            observations=self.observations,
            execution_budget=self._execution_budget,
            budgeted_generate=self._budgeted_generate,
            total_steps=self.total_steps,
            completed_steps=completed_steps,
        )

    def create_extractor(self) -> SemanticPolicyChunkExtractor:
        """Create the unchanged strict validator at production's historical boundary."""
        return SemanticPolicyChunkExtractor(self._budgeted_generate)

class SemanticOperationExecutor:
    """Prepare and execute the validated Semantic IR stage used by production."""

    def __init__(
        self,
        generate: Callable[..., Any],
        *,
        semantic_provider_name: str | None = None,
        semantic_model_name: str | None = None,
        semantic_generation_config: dict[str, Any] | None = None,
        semantic_checkpoint_repository: Any | None = None,
        execution_budget_repository: Any | None = None,
        execution_budget_policy: Any | None = None,
        monotonic_clock: Callable[[], float] = monotonic,
        utc_clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._generate = generate
        self._provider_name = semantic_provider_name
        self._model_name = semantic_model_name
        self._generation_config = semantic_generation_config
        self._checkpoint_repository = semantic_checkpoint_repository
        self._execution_budget_repository = execution_budget_repository
        self._execution_budget_policy = execution_budget_policy
        self._active_execution_budget = None
        self._monotonic_clock = monotonic_clock
        self._utc_clock = utc_clock or (lambda: datetime.now(UTC))

    def prepare(
        self,
        *,
        source_payload: dict[str, Any],
        seller_profile: Any | None,
        trusted_invocation: Any,
        on_budget_started: Callable[[], None] | None = None,
    ) -> _PreparedSemanticOperations:
        """Validate trusted inputs, freeze the operation plan and prepare resume state."""
        if not isinstance(trusted_invocation, TrustedGenerationInvocation):
            raise RelationInvocationRejected("TRUSTED_GENERATION_CONTEXT_REQUIRED")
        trusted_invocation.assert_source_payload(source_payload, seller_profile)
        raw_documents = source_payload.get("source_documents")
        documents = raw_documents if isinstance(raw_documents, list) and raw_documents else [
            {"content": source_payload.get("pasted_text"), "document_id": None},
        ]
        if any(not isinstance(document, dict) for document in documents):
            raise ValueError("source document content is unavailable")
        source_sections = tuple(generic_source_sections(documents))
        usable_chars = sum(
            len(line.strip())
            for section in source_sections
            for line in section.text.splitlines()
            if not line.lstrip().startswith("#")
        )
        if usable_chars < 40:
            raise ValueError("source document contains insufficient usable sales content")
        units = tuple(build_generic_extraction_plan(source_sections))
        trusted_invocation.assert_authorized_sections(tuple(
            section for unit in units for section in unit.source_sections
        ))
        chunks = tuple(partition_generic_semantic_extraction(units))

        execution_budget = None
        budgeted_generate = self._generate
        if trusted_invocation.generation_job_id and self._execution_budget_repository is None:
            from app.modules.sales_playbook.execution_budget import ExecutionBudgetExceeded

            raise ExecutionBudgetExceeded("EXECUTION_BUDGET_NOT_CONFIGURED")
        if self._execution_budget_repository is not None:
            from app.modules.sales_playbook.execution_budget import (
                ExecutionBudgetPlan,
                ExecutionBudgetPolicy,
                ProviderExecutionBudget,
            )

            policy = self._execution_budget_policy or ExecutionBudgetPolicy.from_settings()
            execution_budget = ProviderExecutionBudget.begin_for_job(
                repository=self._execution_budget_repository,
                trusted_invocation=trusted_invocation,
                policy=policy,
                monotonic_clock=self._monotonic_clock,
                utc_clock=self._utc_clock,
            )
            self._active_execution_budget = execution_budget
            if on_budget_started is not None:
                on_budget_started()
            base_relation_windows = GenericWorkflowRelationExtractor.build_windows(units, ())
            initial_budget_plan = ExecutionBudgetPlan.for_generation(
                semantic_operation_ids=[
                    f"sales_playbook.semantic_chunk.{chunk.chunk_id}" for chunk in chunks
                ],
                base_relation_operation_ids=[
                    f"sales_playbook.generic_workflow.relations.{window.window_id}"
                    for window in base_relation_windows
                ],
                supporting_operation_ids=[
                    f"sales_playbook.generic_source_claims.{unit.unit_id}" for unit in units
                ],
                conflict_operation_id="sales_playbook.generic_workflow.conflicts",
                policy=policy,
            )
            execution_budget.register_plan(initial_budget_plan)

            def budgeted_generate(
                prompt, response_format, system_prompt, operation, *, invocation_id=None,
            ):
                if operation.startswith("sales_playbook.semantic_chunk."):
                    phase = "semantic"
                elif operation.startswith("sales_playbook.generic_workflow.relations."):
                    phase = "relation"
                elif operation == "sales_playbook.generic_workflow.conflicts":
                    phase = "conflict_synthesis"
                elif operation.startswith("sales_playbook.generic_source_claims."):
                    phase = "supporting"
                else:
                    from app.modules.sales_playbook.execution_budget import ExecutionBudgetExceeded

                    raise ExecutionBudgetExceeded("PROVIDER_OPERATION_PHASE_UNAUTHORIZED", {
                        "operation_id": str(operation)[:180],
                    })
                timeout_seconds = ceil(float(policy.max_provider_request_timeout_seconds))
                return execution_budget.dispatch(
                    operation_id=operation,
                    operation_phase=phase,
                    provider_call=lambda: self._generate(
                        prompt,
                        response_format,
                        system_prompt,
                        operation,
                        **({"invocation_id": invocation_id} if invocation_id is not None else {}),
                    ),
                    timeout_seconds=timeout_seconds,
                )

        checkpoint_operations = {}
        checkpoint_identities = {}
        resumed_semantic_ir = {}
        if trusted_invocation.semantic_checkpoint_enabled:
            if (self._checkpoint_repository is None
                    or not self._provider_name
                    or not self._model_name
                    or self._generation_config is None):
                raise RuntimeError("checkpoint-enabled generation lacks durable provider configuration")
            from app.modules.sales_playbook.semantic_checkpoints import (
                build_semantic_operation_plan,
                checkpoint_identity_for_operation,
            )

            checkpoint_plan = build_semantic_operation_plan(chunks)
            checkpoint_operations = {
                entry.operation_id.rsplit(".", 1)[-1]: entry for entry in checkpoint_plan
            }
            checkpoint_identities = {
                chunk_id: checkpoint_identity_for_operation(
                    trusted_invocation,
                    entry,
                    provider_name=self._provider_name,
                    model_name=self._model_name,
                    generation_config=self._generation_config,
                )
                for chunk_id, entry in checkpoint_operations.items()
            }
            if trusted_invocation.semantic_checkpoint_resume_source_job_id is not None:
                resumed_semantic_ir = self._checkpoint_repository.prepare_explicit_resume(
                    trusted_invocation=trusted_invocation,
                    workspace_id=trusted_invocation.workspace_id,
                    generation_job_id=trusted_invocation.generation_job_id,
                    source_generation_job_id=(
                        trusted_invocation.semantic_checkpoint_resume_source_job_id
                    ),
                    plan_fingerprint=checkpoint_plan[0].partition_plan_fingerprint,
                    operations=tuple(checkpoint_identities.values()),
                )
            else:
                self._checkpoint_repository.mark_plan_eligible(
                    trusted_invocation=trusted_invocation,
                    workspace_id=trusted_invocation.workspace_id,
                    generation_job_id=trusted_invocation.generation_job_id,
                    plan_fingerprint=checkpoint_plan[0].partition_plan_fingerprint,
                    operations=tuple(checkpoint_identities.values()),
                )

        total_steps = (
            2 * len(chunks)
            + GenericWorkflowRelationExtractor.expected_operation_count(len(units))
        )
        prepared = _PreparedSemanticOperations(
            _trusted_invocation=trusted_invocation,
            _source_sections=source_sections,
            _units=units,
            _chunks=chunks,
            _provider_name=self._provider_name,
            _model_name=self._model_name,
            _budgeted_generate=budgeted_generate,
            _execution_budget=execution_budget,
            _checkpoint_repository=self._checkpoint_repository,
            _checkpoint_operations=checkpoint_operations,
            _checkpoint_identities=checkpoint_identities,
            _resumed_semantic_ir=resumed_semantic_ir,
            total_steps=total_steps,
        )
        return prepared
