from redis import Redis
from rq import Queue

from app.core.config import get_settings


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
