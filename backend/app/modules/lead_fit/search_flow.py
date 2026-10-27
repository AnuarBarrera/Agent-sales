"""RQ dispatch and bounded result projection for Lead Fit prospection runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class FitRunResults:
    run_id: UUID
    total: int
    qualified: int
    needs_more_research: int
    not_qualified: int
    evaluation_failed: int
    pending: int
    items: list[dict[str, Any]]


def enqueue_lead_fit_evaluation(
    evaluation_id: str,
    *,
    queue=None,
    allow_terminal_reenqueue: bool = False,
) -> str:
    """Enqueue the actual worker under a stable ID; never invent a job receipt."""
    parsed_id = UUID(evaluation_id)
    if queue is None:
        from app.workers.queue import get_queue

        queue = get_queue()
    job_id = f"lead-fit-evaluation:{parsed_id}"
    existing = queue.fetch_job(job_id)
    if existing is not None:
        if (
            existing.id != job_id
            or existing.func_name != "app.workers.jobs.execute_lead_fit_evaluation"
            or tuple(existing.args) != (str(parsed_id),)
            or existing.kwargs != {}
            or getattr(existing, "origin", getattr(queue, "name", None)) != getattr(queue, "name", None)
        ):
            raise RuntimeError("Existing Lead Fit queue job does not match the validated worker request")
        existing_status = existing.get_status(refresh=True)
        status_value = getattr(existing_status, "value", existing_status)
        if status_value in {"queued", "started", "deferred"}:
            return existing.id
        if status_value not in {"failed", "finished"} or allow_terminal_reenqueue is not True:
            raise RuntimeError(
                f"{status_value} Lead Fit queue job requires explicit reenqueue authorization"
            )
        if status_value == "failed":
            return existing.requeue().id
        from rq.registry import FinishedJobRegistry

        FinishedJobRegistry(
            existing.origin,
            connection=existing.connection,
            serializer=existing.serializer,
        ).remove(existing)
        job = queue.enqueue_call(
            func="app.workers.jobs.execute_lead_fit_evaluation",
            args=(str(parsed_id),),
            kwargs={},
            job_id=job_id,
            timeout=300,
            result_ttl=86400,
        )
        return job.id
    job = queue.enqueue_call(
        func="app.workers.jobs.execute_lead_fit_evaluation",
        args=(str(parsed_id),),
        kwargs={},
        job_id=job_id,
        timeout=300,
        result_ttl=86400,
    )
    return job.id


def compute_fit_run_results(
    *, run_id: UUID, evaluations: list[dict[str, Any]], limit: int = 50, offset: int = 0,
) -> FitRunResults:
    """Project already tenant-filtered evaluation rows without changing discovery status."""
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("invalid pagination")
    return FitRunResults(
        run_id=run_id,
        total=len(evaluations),
        qualified=sum(e.get("classification") == "qualified" for e in evaluations),
        needs_more_research=sum(e.get("classification") == "needs_more_research" for e in evaluations),
        not_qualified=sum(e.get("classification") == "not_qualified" for e in evaluations),
        evaluation_failed=sum(e.get("status") == "failed" for e in evaluations),
        pending=sum(e.get("status") in {"pending", "running"} for e in evaluations),
        items=evaluations[offset:offset + limit],
    )
