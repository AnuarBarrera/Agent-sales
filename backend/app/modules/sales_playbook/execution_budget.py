"""Per-GenerationJob provider attempt admission and durable execution ledger."""

from __future__ import annotations

import copy
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from app.core.config import Settings, get_settings
from app.modules.sales_playbook.generation_models import (
    GenerationJobStatus,
    SalesPlaybookGenerationJob,
)

LEDGER_FORMAT = "sales_playbook_execution_budget_v1"
_BUDGET_METADATA_KEY = "execution_budget"
_MAX_OPERATION_ID_LENGTH = 180
_SAFE_ERROR_TYPES = {
    "ConnectError", "HTTPStatusError", "ReadTimeout", "TimeoutException",
    "WriteTimeout", "ValidationError", "ValueError", "RuntimeError",
}


class ExecutionBudgetExceeded(RuntimeError):
    """A generation cannot dispatch more work within its authorized budget."""

    def __init__(self, code: str, diagnostics: dict[str, Any] | None = None):
        self.code = code
        self.diagnostics = {
            "code": code,
            **(diagnostics or {}),
        }
        super().__init__(code)


@dataclass(frozen=True)
class ExecutionBudgetPolicy:
    max_provider_attempts: int = 24
    max_operations: int = 24
    max_dynamic_operations: int = 3
    execution_deadline_seconds: int = 3180
    closeout_reserve_seconds: int = 300
    max_provider_request_timeout_seconds: int = 120
    rq_termination_grace_seconds: int = 30

    def __post_init__(self) -> None:
        integer_values = (
            self.max_provider_attempts,
            self.max_operations,
            self.max_dynamic_operations,
            self.execution_deadline_seconds,
            self.closeout_reserve_seconds,
            self.max_provider_request_timeout_seconds,
            self.rq_termination_grace_seconds,
        )
        if any(not isinstance(value, int) or isinstance(value, bool) for value in integer_values):
            raise ValueError("execution budget settings must be integers")
        if min(integer_values) < 0 or min(
            self.max_provider_attempts,
            self.max_operations,
            self.execution_deadline_seconds,
            self.closeout_reserve_seconds,
            self.max_provider_request_timeout_seconds,
            self.rq_termination_grace_seconds,
        ) < 1:
            raise ValueError("execution budget settings must be positive, except dynamic operations")
        if self.max_provider_attempts > self.max_operations:
            raise ValueError("provider attempts cannot exceed the operation limit")
        if self.max_dynamic_operations > self.max_operations:
            raise ValueError("dynamic operations cannot exceed the operation limit")
        if self.closeout_reserve_seconds >= self.execution_deadline_seconds:
            raise ValueError("closeout reserve must be below the total deadline")
        if self.provider_execution_seconds < (
            self.max_provider_attempts * self.max_provider_request_timeout_seconds
        ):
            raise ValueError("execution deadline cannot accommodate every configured provider attempt")

    @property
    def provider_execution_seconds(self) -> int:
        return self.execution_deadline_seconds - self.closeout_reserve_seconds

    def rq_timeout_seconds(self) -> int:
        return self.execution_deadline_seconds + self.rq_termination_grace_seconds

    def to_dict(self) -> dict[str, int]:
        return {
            "max_provider_attempts": self.max_provider_attempts,
            "max_operations": self.max_operations,
            "max_dynamic_operations": self.max_dynamic_operations,
            "execution_deadline_seconds": self.execution_deadline_seconds,
            "provider_execution_seconds": self.provider_execution_seconds,
            "closeout_reserve_seconds": self.closeout_reserve_seconds,
            "max_provider_request_timeout_seconds": self.max_provider_request_timeout_seconds,
            "rq_termination_grace_seconds": self.rq_termination_grace_seconds,
        }

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> ExecutionBudgetPolicy:
        configured = settings or get_settings()
        return cls(
            max_provider_attempts=configured.sales_playbook_max_provider_attempts,
            max_operations=configured.sales_playbook_max_operations,
            max_dynamic_operations=configured.sales_playbook_max_dynamic_operations,
            execution_deadline_seconds=configured.sales_playbook_execution_deadline_seconds,
            closeout_reserve_seconds=configured.sales_playbook_closeout_reserve_seconds,
            max_provider_request_timeout_seconds=math.ceil(
                configured.sales_playbook_llm_timeout_seconds
            ),
            rq_termination_grace_seconds=configured.sales_playbook_rq_termination_grace_seconds,
        )


@dataclass(frozen=True)
class ProviderOperation:
    operation_id: str
    phase: str
    dynamic: bool = False


@dataclass(frozen=True)
class ExecutionBudgetPlan:
    operations: tuple[ProviderOperation, ...]
    base_relation_operation_ids: tuple[str, ...]
    conflict_operation_id: str
    policy: ExecutionBudgetPolicy
    dynamic_operation_count: int = 0

    @property
    def operation_count(self) -> int:
        return len(self.operations)

    @property
    def operation_ids(self) -> frozenset[str]:
        return frozenset(item.operation_id for item in self.operations)

    @property
    def by_phase(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.operations:
            counts[item.phase] = counts.get(item.phase, 0) + 1
        return counts

    def to_dict(self) -> list[dict[str, Any]]:
        return [
            {"operation_id": item.operation_id, "phase": item.phase, "dynamic": item.dynamic}
            for item in self.operations
        ]

    @classmethod
    def for_generation(
        cls,
        *,
        semantic_operation_ids: list[str] | tuple[str, ...],
        base_relation_operation_ids: list[str] | tuple[str, ...],
        supporting_operation_ids: list[str] | tuple[str, ...],
        conflict_operation_id: str,
        policy: ExecutionBudgetPolicy | None = None,
    ) -> ExecutionBudgetPlan:
        selected_policy = policy or ExecutionBudgetPolicy()
        operations = [
            *(ProviderOperation(item, "semantic") for item in semantic_operation_ids),
            *(ProviderOperation(item, "relation") for item in base_relation_operation_ids),
            ProviderOperation(conflict_operation_id, "conflict_synthesis"),
            *(ProviderOperation(item, "supporting") for item in supporting_operation_ids),
        ]
        plan = cls(
            operations=tuple(operations),
            base_relation_operation_ids=tuple(base_relation_operation_ids),
            conflict_operation_id=conflict_operation_id,
            policy=selected_policy,
        )
        plan._validate()
        return plan

    def with_relation_operations(
        self, operation_ids: list[str] | tuple[str, ...],
    ) -> ExecutionBudgetPlan:
        actual_relation_ids = tuple(operation_ids)
        if not actual_relation_ids or len(actual_relation_ids) != len(set(actual_relation_ids)):
            raise ExecutionBudgetExceeded("INVALID_DYNAMIC_OPERATION_PLAN")
        dynamic_count = max(0, len(actual_relation_ids) - len(self.base_relation_operation_ids))
        if dynamic_count > self.policy.max_dynamic_operations:
            raise self._limit_error(
                "DYNAMIC_OPERATION_LIMIT",
                operation_count=(len(self.operations) - len(self.base_relation_operation_ids)
                                 + len(actual_relation_ids)),
                dynamic_operation_count=dynamic_count,
            )
        non_relation = [item for item in self.operations if item.phase != "relation"]
        updated = ExecutionBudgetPlan(
            operations=(
                *non_relation,
                *(ProviderOperation(item, "relation", dynamic=index >= len(self.base_relation_operation_ids))
                  for index, item in enumerate(actual_relation_ids)),
            ),
            base_relation_operation_ids=self.base_relation_operation_ids,
            conflict_operation_id=self.conflict_operation_id,
            policy=self.policy,
            dynamic_operation_count=dynamic_count,
        )
        updated._validate()
        return updated

    def _validate(self) -> None:
        identifiers = [item.operation_id for item in self.operations]
        if any(not item or len(item) > _MAX_OPERATION_ID_LENGTH for item in identifiers):
            raise ExecutionBudgetExceeded("INVALID_OPERATION_ID")
        if len(identifiers) != len(set(identifiers)):
            raise ExecutionBudgetExceeded("DUPLICATE_OPERATION_ID")
        if len(identifiers) > self.policy.max_operations:
            raise self._limit_error(
                "OPERATION_LIMIT",
                operation_count=len(identifiers),
                dynamic_operation_count=self.dynamic_operation_count,
            )
        if len(identifiers) > self.policy.max_provider_attempts:
            raise self._limit_error(
                "PROVIDER_ATTEMPT_LIMIT",
                operation_count=len(identifiers),
                dynamic_operation_count=self.dynamic_operation_count,
            )

    def _limit_error(self, code: str, *, operation_count: int, dynamic_operation_count: int):
        return ExecutionBudgetExceeded(code, {
            "operation_count": operation_count,
            "max_operations": self.policy.max_operations,
            "dynamic_operation_count": dynamic_operation_count,
            "max_dynamic_operations": self.policy.max_dynamic_operations,
            "limits": self.policy.to_dict(),
        })


class PostgresExecutionBudgetRepository:
    """Serialize budget ledger transitions with the GenerationJob row lock."""

    def __init__(self, session_factory):
        self._session_factory = session_factory

    def begin(
        self, *, workspace_id, generation_job_id,
        request_fingerprint: str | None = None,
        invocation_id: str | None = None,
        policy: ExecutionBudgetPolicy,
    ):
        if not isinstance(workspace_id, UUID) or not isinstance(generation_job_id, UUID):
            raise ExecutionBudgetExceeded("TRUSTED_GENERATION_JOB_REQUIRED")
        session = self._session_factory()
        try:
            job = self._locked_job(session, workspace_id, generation_job_id)
            if job.status is not GenerationJobStatus.RUNNING or job.started_at is None:
                raise ExecutionBudgetExceeded("GENERATION_JOB_NOT_RUNNING")
            if not isinstance(job, SalesPlaybookGenerationJob):
                raise ExecutionBudgetExceeded("TRUSTED_GENERATION_JOB_REQUIRED")
            if (request_fingerprint is not None
                    and job.request_fingerprint != request_fingerprint):
                raise ExecutionBudgetExceeded("GENERATION_JOB_INVOCATION_MISMATCH")
            metadata = dict(job.progress_metadata or {})
            if _BUDGET_METADATA_KEY in metadata:
                raise ExecutionBudgetExceeded("EXECUTION_BUDGET_ALREADY_STARTED")
            database_now = session.execute(select(func.clock_timestamp())).scalar_one()
            if database_now.tzinfo is None:
                database_now = database_now.replace(tzinfo=UTC)
            job_started_at = job.started_at if job.started_at.tzinfo else job.started_at.replace(tzinfo=UTC)
            started_at = database_now
            total_deadline = started_at + timedelta(seconds=policy.execution_deadline_seconds)
            dispatch_deadline = total_deadline - timedelta(seconds=policy.closeout_reserve_seconds)
            ledger = {
                "format": LEDGER_FORMAT,
                "state": "running",
                "request_fingerprint": request_fingerprint,
                "invocation_id": invocation_id,
                "policy": policy.to_dict(),
                "started_at_utc": started_at.isoformat(),
                "generation_job_started_at_utc": job_started_at.isoformat(),
                "observed_at_utc": database_now.isoformat(),
                "provider_dispatch_deadline_utc": dispatch_deadline.isoformat(),
                "total_deadline_utc": total_deadline.isoformat(),
                "planned_operations": [],
                "attempts": {},
                "reserved_attempts": 0,
                "dispatched_attempts": 0,
                "finalized_attempts": 0,
                "successful_attempts": 0,
                "failed_attempts": 0,
                "dynamic_operations": 0,
            }
            metadata[_BUDGET_METADATA_KEY] = ledger
            job.progress_metadata = metadata
            session.commit()
            return {
                "started_at_utc": started_at,
                "database_now_utc": database_now,
                "provider_dispatch_deadline_utc": dispatch_deadline,
                "total_deadline_utc": total_deadline,
            }
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def register_plan(
        self, *, workspace_id, generation_job_id, plan: ExecutionBudgetPlan,
    ) -> None:
        self._update_ledger(workspace_id, generation_job_id, lambda ledger: self._set_plan(ledger, plan))

    def replace_relation_operations(
        self, *, workspace_id, generation_job_id, plan: ExecutionBudgetPlan,
    ) -> None:
        def replace(ledger):
            attempts = ledger["attempts"]
            relation_attempts = [
                item for item in attempts.values() if item.get("phase") in {"relation", "conflict_synthesis"}
            ]
            if relation_attempts:
                raise ExecutionBudgetExceeded("RELATION_PLAN_ALREADY_DISPATCHED")
            self._set_plan(ledger, plan)

        self._update_ledger(workspace_id, generation_job_id, replace)

    def reserve_attempt(
        self, *, workspace_id, generation_job_id, operation_id: str,
        operation_phase: str, timeout_seconds: int, policy: ExecutionBudgetPolicy,
    ) -> int:
        attempt_number = 0

        def reserve(ledger):
            nonlocal attempt_number
            if ledger["policy"] != policy.to_dict():
                raise ExecutionBudgetExceeded("EXECUTION_BUDGET_POLICY_MISMATCH")
            plan = {item["operation_id"]: item for item in ledger["planned_operations"]}
            planned = plan.get(operation_id)
            if planned is None or planned["phase"] != operation_phase:
                raise ExecutionBudgetExceeded("OPERATION_NOT_IN_AUTHORIZED_PLAN")
            if operation_id in ledger["attempts"]:
                raise ExecutionBudgetExceeded("DUPLICATE_OPERATION_DISPATCH", self._diagnostics(ledger, policy))
            if ledger["reserved_attempts"] >= policy.max_provider_attempts:
                raise ExecutionBudgetExceeded("PROVIDER_ATTEMPT_LIMIT", self._diagnostics(ledger, policy))
            if len(ledger["planned_operations"]) > policy.max_operations:
                raise ExecutionBudgetExceeded("OPERATION_LIMIT", self._diagnostics(ledger, policy))
            if ledger["dynamic_operations"] > policy.max_dynamic_operations:
                raise ExecutionBudgetExceeded("DYNAMIC_OPERATION_LIMIT", self._diagnostics(ledger, policy))
            dispatch_deadline = datetime.fromisoformat(ledger["provider_dispatch_deadline_utc"])
            database_now = ledger["database_now"]
            if database_now + timedelta(seconds=timeout_seconds) > dispatch_deadline:
                raise ExecutionBudgetExceeded("DEADLINE_CLOSEOUT_RESERVE", self._diagnostics(ledger, policy))
            attempt_number = ledger["reserved_attempts"] + 1
            ledger["attempts"][operation_id] = {
                "attempt_number": attempt_number,
                "phase": operation_phase,
                "dynamic": bool(planned["dynamic"]),
                "state": "reserved",
                "reserved_at_utc": database_now.isoformat(),
            }
            ledger["reserved_attempts"] += 1

        self._update_ledger(
            workspace_id, generation_job_id, reserve,
            before_mutate=lambda ledger, database_now: ledger.__setitem__("database_now", database_now),
        )
        return attempt_number

    def mark_dispatched(
        self, *, workspace_id, generation_job_id, operation_id: str,
        timeout_seconds: int, policy: ExecutionBudgetPolicy,
    ) -> None:
        def dispatched(ledger):
            if ledger["policy"] != policy.to_dict():
                raise ExecutionBudgetExceeded("EXECUTION_BUDGET_POLICY_MISMATCH")
            attempt = self._attempt(ledger, operation_id)
            if attempt["state"] != "reserved":
                raise ExecutionBudgetExceeded("INVALID_ATTEMPT_STATE")
            now = ledger["database_now"]
            deadline = datetime.fromisoformat(ledger["provider_dispatch_deadline_utc"])
            if now + timedelta(seconds=timeout_seconds) > deadline:
                raise ExecutionBudgetExceeded("DEADLINE_CLOSEOUT_RESERVE", self._diagnostics(ledger, policy))
            attempt["state"] = "dispatch_started"
            attempt["dispatch_started_at_utc"] = now.isoformat()
            ledger["dispatched_attempts"] += 1

        self._update_ledger(
            workspace_id, generation_job_id, dispatched,
            before_mutate=lambda ledger, database_now: ledger.__setitem__("database_now", database_now),
        )

    def finalize_attempt(
        self, *, workspace_id, generation_job_id, operation_id: str,
        outcome: str, error_type: str | None = None,
    ) -> None:
        if outcome not in {"succeeded", "failed"}:
            raise ValueError("invalid provider attempt outcome")

        def finalized(ledger):
            attempt = self._attempt(ledger, operation_id)
            if attempt["state"] != "dispatch_started":
                raise ExecutionBudgetExceeded("INVALID_ATTEMPT_STATE")
            now = ledger.pop("database_now")
            attempt["state"] = outcome
            attempt["finalized_at_utc"] = now.isoformat()
            if error_type:
                attempt["safe_error_type"] = (
                    error_type if error_type in _SAFE_ERROR_TYPES else "ProviderCallError"
                )
            ledger["finalized_attempts"] += 1
            counter = "successful_attempts" if outcome == "succeeded" else "failed_attempts"
            ledger[counter] += 1

        self._update_ledger(
            workspace_id, generation_job_id, finalized,
            before_mutate=lambda ledger, database_now: ledger.__setitem__("database_now", database_now),
        )

    def _update_ledger(
        self, workspace_id, generation_job_id, mutate, *, before_mutate=None,
    ):
        session = self._session_factory()
        try:
            job = self._locked_job(session, workspace_id, generation_job_id)
            if job.status is not GenerationJobStatus.RUNNING:
                raise ExecutionBudgetExceeded("GENERATION_JOB_NOT_RUNNING")
            metadata = dict(job.progress_metadata or {})
            ledger = copy.deepcopy(metadata.get(_BUDGET_METADATA_KEY))
            if not isinstance(ledger, dict) or ledger.get("format") != LEDGER_FORMAT:
                raise ExecutionBudgetExceeded("EXECUTION_BUDGET_LEDGER_MISSING")
            if before_mutate is not None:
                database_now = session.execute(select(func.clock_timestamp())).scalar_one()
                if database_now.tzinfo is None:
                    database_now = database_now.replace(tzinfo=UTC)
                before_mutate(ledger, database_now)
            mutate(ledger)
            ledger.pop("database_now", None)
            metadata[_BUDGET_METADATA_KEY] = ledger
            job.progress_metadata = metadata
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def _locked_job(session, workspace_id, generation_job_id):
        job = session.execute(select(SalesPlaybookGenerationJob).where(
            SalesPlaybookGenerationJob.id == generation_job_id,
            SalesPlaybookGenerationJob.workspace_id == workspace_id,
        ).with_for_update()).scalar_one_or_none()
        if job is None:
            raise ExecutionBudgetExceeded("GENERATION_JOB_NOT_AUTHORIZED")
        return job

    @staticmethod
    def _set_plan(ledger: dict[str, Any], plan: ExecutionBudgetPlan) -> None:
        if ledger["policy"] != plan.policy.to_dict():
            raise ExecutionBudgetExceeded("EXECUTION_BUDGET_POLICY_MISMATCH")
        prior = {item["operation_id"]: item for item in ledger["planned_operations"]}
        proposed = {item["operation_id"]: item for item in plan.to_dict()}
        if any(operation_id not in proposed for operation_id in ledger["attempts"]):
            raise ExecutionBudgetExceeded("PLAN_REMOVED_RESERVED_OPERATION")
        if any(prior.get(key) != value for key, value in prior.items() if key in proposed):
            raise ExecutionBudgetExceeded("PLAN_CHANGED_AFTER_REGISTRATION")
        if len(proposed) > plan.policy.max_operations:
            raise ExecutionBudgetExceeded("OPERATION_LIMIT", {
                "operation_count": len(proposed), "limits": plan.policy.to_dict(),
            })
        if plan.dynamic_operation_count > plan.policy.max_dynamic_operations:
            raise ExecutionBudgetExceeded("DYNAMIC_OPERATION_LIMIT", {
                "dynamic_operation_count": plan.dynamic_operation_count,
                "limits": plan.policy.to_dict(),
            })
        ledger["planned_operations"] = plan.to_dict()
        ledger["dynamic_operations"] = plan.dynamic_operation_count

    @staticmethod
    def _attempt(ledger: dict[str, Any], operation_id: str) -> dict[str, Any]:
        attempt = ledger["attempts"].get(operation_id)
        if not isinstance(attempt, dict):
            raise ExecutionBudgetExceeded("ATTEMPT_RESERVATION_MISSING")
        return attempt

    @staticmethod
    def _diagnostics(ledger: dict[str, Any], policy: ExecutionBudgetPolicy) -> dict[str, Any]:
        return {
            "reserved_attempts": ledger.get("reserved_attempts", 0),
            "dispatched_attempts": ledger.get("dispatched_attempts", 0),
            "finalized_attempts": ledger.get("finalized_attempts", 0),
            "failed_attempts": ledger.get("failed_attempts", 0),
            "dynamic_operations": ledger.get("dynamic_operations", 0),
            "limits": policy.to_dict(),
        }


class ProviderExecutionBudget:
    """Monotonic deadline and provider-dispatch guard for one GenerationJob."""

    def __init__(
        self, *, repository, workspace_id, generation_job_id,
        policy: ExecutionBudgetPolicy, started_at_utc: datetime,
        started_monotonic: float, monotonic_clock: Callable[[], float],
        utc_clock: Callable[[], datetime],
    ):
        self.repository = repository
        self.workspace_id = workspace_id
        self.generation_job_id = generation_job_id
        self.policy = policy
        self.started_at_utc = started_at_utc
        self.started_monotonic = started_monotonic
        self._monotonic = monotonic_clock
        self._utc = utc_clock
        self._plan: ExecutionBudgetPlan | None = None
        self._provider_deadline_monotonic = (
            started_monotonic + policy.provider_execution_seconds
        )

    @classmethod
    def begin_for_job(
        cls, *, repository, trusted_invocation, policy: ExecutionBudgetPolicy,
        monotonic_clock: Callable[[], float], utc_clock: Callable[[], datetime],
    ) -> ProviderExecutionBudget:
        from app.modules.sales_playbook.relation_invocation import TrustedGenerationInvocation

        if not isinstance(trusted_invocation, TrustedGenerationInvocation):
            raise ExecutionBudgetExceeded("TRUSTED_GENERATION_CONTEXT_REQUIRED")
        trusted_invocation.assert_issued()
        if (not trusted_invocation.generation_job_id
                or not trusted_invocation.workspace_id
                or not trusted_invocation.invocation_id):
            raise ExecutionBudgetExceeded("TRUSTED_GENERATION_JOB_REQUIRED")
        started_monotonic = monotonic_clock()
        anchor = repository.begin(
            workspace_id=UUID(trusted_invocation.workspace_id),
            generation_job_id=UUID(trusted_invocation.generation_job_id),
            request_fingerprint=trusted_invocation.request_fingerprint,
            invocation_id=trusted_invocation.invocation_id,
            policy=policy,
        )
        return cls(
            repository=repository,
            workspace_id=UUID(trusted_invocation.workspace_id),
            generation_job_id=UUID(trusted_invocation.generation_job_id),
            policy=policy,
            started_at_utc=anchor["started_at_utc"],
            started_monotonic=started_monotonic,
            monotonic_clock=monotonic_clock,
            utc_clock=utc_clock,
        )

    @property
    def plan(self) -> ExecutionBudgetPlan:
        if self._plan is None:
            raise ExecutionBudgetExceeded("OPERATION_PLAN_NOT_REGISTERED")
        return self._plan

    def register_plan(self, plan: ExecutionBudgetPlan) -> None:
        if self._plan is not None:
            raise ExecutionBudgetExceeded("OPERATION_PLAN_ALREADY_REGISTERED")
        self._plan = plan
        self.repository.register_plan(
            workspace_id=self.workspace_id,
            generation_job_id=self.generation_job_id,
            plan=plan,
        )

    def replace_relation_plan(self, plan: ExecutionBudgetPlan) -> None:
        if self._plan is None or plan.policy != self.policy:
            raise ExecutionBudgetExceeded("OPERATION_PLAN_NOT_REGISTERED")
        self.repository.replace_relation_operations(
            workspace_id=self.workspace_id,
            generation_job_id=self.generation_job_id,
            plan=plan,
        )
        self._plan = plan

    def assert_execution_deadline(self) -> None:
        remaining = self.started_monotonic + self.policy.execution_deadline_seconds - self._monotonic()
        if remaining <= 0:
            raise ExecutionBudgetExceeded("EXECUTION_DEADLINE_EXCEEDED", {
                "operation_phase": "closeout",
                "remaining_provider_seconds": 0,
                "limits": self.policy.to_dict(),
            })

    def reserve(self, *, operation_id: str, operation_phase: str, timeout_seconds: int) -> int:
        self._check_dispatch_time(operation_id, operation_phase, timeout_seconds)
        operation = next(
            (item for item in self.plan.operations if item.operation_id == operation_id), None,
        )
        if operation is None or operation.phase != operation_phase:
            raise ExecutionBudgetExceeded("OPERATION_NOT_IN_AUTHORIZED_PLAN", {
                "operation_id": operation_id[:_MAX_OPERATION_ID_LENGTH],
                "operation_phase": operation_phase[:40],
                "limits": self.policy.to_dict(),
            })
        return self.repository.reserve_attempt(
            workspace_id=self.workspace_id,
            generation_job_id=self.generation_job_id,
            operation_id=operation_id,
            operation_phase=operation_phase,
            timeout_seconds=timeout_seconds,
            policy=self.policy,
        )

    def dispatch(
        self, *, operation_id: str, operation_phase: str,
        provider_call: Callable[[], Any], timeout_seconds: int,
    ):
        self.reserve(
            operation_id=operation_id,
            operation_phase=operation_phase,
            timeout_seconds=timeout_seconds,
        )
        # Reservation is durable before the adapter can be invoked. If this second
        # deadline check fails, the reservation remains consumed by design.
        self._check_dispatch_time(operation_id, operation_phase, timeout_seconds)
        self.repository.mark_dispatched(
            workspace_id=self.workspace_id,
            generation_job_id=self.generation_job_id,
            operation_id=operation_id,
            timeout_seconds=timeout_seconds,
            policy=self.policy,
        )
        # This marker records the application handing control to the adapter. It
        # does not prove that the provider received bytes or produced a response.
        try:
            response = provider_call()
        except Exception as error:
            self.repository.finalize_attempt(
                workspace_id=self.workspace_id,
                generation_job_id=self.generation_job_id,
                operation_id=operation_id,
                outcome="failed",
                error_type=type(error).__name__,
            )
            raise
        else:
            self.repository.finalize_attempt(
                workspace_id=self.workspace_id,
                generation_job_id=self.generation_job_id,
                operation_id=operation_id,
                outcome="succeeded",
            )
            return response

    def _check_dispatch_time(self, operation_id: str, operation_phase: str, timeout_seconds: int) -> None:
        if (not isinstance(timeout_seconds, int) or timeout_seconds < 1
                or timeout_seconds > self.policy.max_provider_request_timeout_seconds):
            raise ExecutionBudgetExceeded("PROVIDER_TIMEOUT_EXCEEDS_BUDGET", {
                "operation_id": operation_id[:_MAX_OPERATION_ID_LENGTH],
                "operation_phase": operation_phase[:40],
                "request_timeout_seconds": timeout_seconds,
                "limits": self.policy.to_dict(),
            })
        remaining = self._provider_deadline_monotonic - self._monotonic()
        if remaining < timeout_seconds:
            raise ExecutionBudgetExceeded("DEADLINE_CLOSEOUT_RESERVE", {
                "operation_id": operation_id[:_MAX_OPERATION_ID_LENGTH],
                "operation_phase": operation_phase[:40],
                "remaining_provider_seconds": max(0, math.floor(remaining)),
                "required_provider_seconds": timeout_seconds,
                "limits": self.policy.to_dict(),
            })
