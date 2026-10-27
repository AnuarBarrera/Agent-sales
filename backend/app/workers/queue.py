from redis import Redis
from rq import Queue

from app.core.config import get_settings
from app.modules.sales_playbook.execution_budget import ExecutionBudgetPolicy


def get_queue() -> Queue:
    settings = get_settings()
    return Queue(
        name=settings.rq_queue_name,
        connection=Redis.from_url(settings.redis_url),
        default_timeout=settings.rq_job_timeout_seconds,
    )


def enqueue_prospection_run(run_id: str) -> str:
    """Enqueue an idempotent prospection job and return its RQ job id."""
    queue = get_queue()
    job_id = f"prospection:{run_id}"
    existing = queue.fetch_job(job_id)
    if existing and existing.get_status() in {"queued", "started", "deferred"}:
        return existing.id

    job = queue.enqueue(
        "app.workers.jobs.execute_prospection_run",
        run_id,
        job_id=job_id,
        result_ttl=86400,
    )
    return job.id


def enqueue_validation_run(run_id: str) -> str:
    """Enqueue validation for all leads produced by a prospection run."""
    queue = get_queue()
    job_id = f"validation:{run_id}"
    existing = queue.fetch_job(job_id)
    if existing and existing.get_status() in {"queued", "started", "deferred"}:
        return existing.id

    job = queue.enqueue(
        "app.workers.jobs.execute_validation_run",
        run_id,
        job_id=job_id,
        result_ttl=86400,
    )
    return job.id


def enqueue_prospect_research(research_id: str) -> str:
    """Enqueue one research run; its UUID makes retries idempotent."""
    queue = get_queue()
    job_id = f"prospect-research:{research_id}"
    existing = queue.fetch_job(job_id)
    if existing and existing.get_status() in {"queued", "started", "deferred"}:
        return existing.id
    job = queue.enqueue(
        "app.workers.jobs.execute_prospect_research",
        research_id,
        job_id=job_id,
        result_ttl=86400,
    )
    return job.id


def enqueue_lead_fit_research(grant_id: str, *, queue=None) -> str:
    """Enqueue one authorized grant; a terminal receipt is never restarted."""
    from uuid import UUID

    parsed_id = UUID(grant_id)
    if queue is None:
        queue = get_queue()
    job_id = f"lead-fit-research:{parsed_id}"
    existing = queue.fetch_job(job_id)
    if existing is not None:
        if (
            existing.id != job_id
            or existing.func_name != "app.workers.jobs.execute_lead_fit_research"
            or tuple(existing.args) != (str(parsed_id),)
            or existing.kwargs != {}
            or getattr(existing, "origin", getattr(queue, "name", None)) != getattr(queue, "name", None)
        ):
            raise RuntimeError("Existing research job does not match the authorized grant")
        status = existing.get_status(refresh=True)
        status_value = getattr(status, "value", status)
        if status_value in {"queued", "started", "deferred", "finished"}:
            return existing.id
        raise RuntimeError("A failed research job requires a new confirmed grant")
    job = queue.enqueue_call(
        func="app.workers.jobs.execute_lead_fit_research",
        args=(str(parsed_id),),
        kwargs={},
        job_id=job_id,
        timeout=600,
        result_ttl=86400,
    )
    return job.id


def enqueue_sales_playbook_generation(generation_job_id: str, rq_job_id: str, total_steps: int) -> str:
    """Queue one generation with the hard timeout derived from its fixed provider budget."""
    settings = get_settings()
    queue = get_queue()
    existing = queue.fetch_job(rq_job_id)
    if existing is not None:
        status = existing.get_status(refresh=True)
        if status in {"queued", "started", "deferred", "finished"}:
            return existing.id
        raise RuntimeError("A prior RQ attempt for this generation job has failed")

    policy = ExecutionBudgetPolicy.from_settings(settings)
    timeout = policy.rq_timeout_seconds()
    job = queue.enqueue_call(
        func="app.workers.jobs.execute_sales_playbook_generation",
        args=(generation_job_id,),
        job_id=rq_job_id,
        timeout=timeout,
        result_ttl=86400,
    )
    return job.id
