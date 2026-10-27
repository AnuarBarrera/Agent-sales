"""Fail-closed configuration checks for isolated SalesPlaybook smoke workers."""

from urllib.parse import urlsplit

from app.core.config import Settings, get_settings

SALES_PLAYBOOK_SMOKE_QUEUE = "sales-playbook-smoke"
SALES_PLAYBOOK_SMOKE_REDIS_HOST = "smoke-redis"
SALES_PLAYBOOK_SMOKE_DATABASE_HOST = "smoke-postgres"
SALES_PLAYBOOK_SMOKE_DATABASE = "tuwebmx_crm_smoke"
SALES_PLAYBOOK_SMOKE_STORAGE_HOST = "garage-smoke"
SALES_PLAYBOOK_SMOKE_STORAGE_BUCKET = "tuwebmx-crm-smoke"


class IsolatedTestWorkerConfigurationError(RuntimeError):
    """The smoke worker is not isolated from production provider/queue settings."""


def assert_isolated_sales_playbook_test_worker(settings: Settings | None = None) -> None:
    """Validate this process is an explicitly fake-only worker on test Redis/queue."""
    configured = settings or get_settings()
    if not configured.sales_playbook_test_execution:
        raise IsolatedTestWorkerConfigurationError("isolated test execution mode is required")
    if configured.sales_playbook_test_provider != "fake":
        raise IsolatedTestWorkerConfigurationError("an explicit fake test provider is required")
    if configured.rq_queue_name != SALES_PLAYBOOK_SMOKE_QUEUE:
        raise IsolatedTestWorkerConfigurationError("the isolated smoke queue is required")

    redis_url = urlsplit(configured.redis_url)
    if redis_url.hostname != SALES_PLAYBOOK_SMOKE_REDIS_HOST or redis_url.path != "/0":
        raise IsolatedTestWorkerConfigurationError("the isolated smoke Redis instance is required")
    database_url = urlsplit(configured.database_url)
    if (database_url.hostname != SALES_PLAYBOOK_SMOKE_DATABASE_HOST
            or database_url.path.lstrip("/") != SALES_PLAYBOOK_SMOKE_DATABASE):
        raise IsolatedTestWorkerConfigurationError("the isolated smoke database is required")
    storage_url = urlsplit(configured.storage_endpoint)
    if (storage_url.hostname != SALES_PLAYBOOK_SMOKE_STORAGE_HOST
            or configured.storage_bucket != SALES_PLAYBOOK_SMOKE_STORAGE_BUCKET):
        raise IsolatedTestWorkerConfigurationError("the isolated smoke object store is required")

    external_provider_configured = any((
        configured.nebius_enabled,
        configured.nebius_api_key,
        configured.llm_api_key,
        configured.openai_api_key,
        configured.tavily_enabled,
        configured.tavily_api_key,
        configured.google_places_api_key,
        configured.google_geocoding_api_key,
        configured.meta_access_token,
        configured.transcription_api_key,
    ))
    if external_provider_configured:
        raise IsolatedTestWorkerConfigurationError(
            "external provider configuration is forbidden in isolated smoke mode",
        )


def assert_test_worker_queues(queue_names: list[str], settings: Settings | None = None) -> None:
    """A test-mode process may subscribe only to its dedicated smoke queue."""
    configured = settings or get_settings()
    assert_isolated_sales_playbook_test_worker(configured)
    if queue_names != [SALES_PLAYBOOK_SMOKE_QUEUE]:
        raise IsolatedTestWorkerConfigurationError(
            "the isolated smoke worker may subscribe only to its dedicated queue",
        )
