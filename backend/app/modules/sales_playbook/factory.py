from urllib.parse import urlsplit

from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.integrations.llm import NebiusTokenFactoryProvider
from app.modules.sales_playbook.execution_budget import (
    ExecutionBudgetPolicy,
    PostgresExecutionBudgetRepository,
)
from app.modules.sales_playbook.generator import (
    ContractReadyDraftGenerator,
    SalesPlaybookGenerator,
    SectionAwareSalesPlaybookGenerator,
)
from app.modules.sales_playbook.semantic_checkpoints import SemanticOperationCheckpointRepository
from app.workers.test_mode import (
    IsolatedTestWorkerConfigurationError,
    assert_isolated_sales_playbook_test_worker,
)


class NebiusConfigurationError(RuntimeError):
    """Raised when Nebius is explicitly enabled without usable configuration."""


def get_sales_playbook_generator(settings: Settings | None = None) -> SalesPlaybookGenerator:
    configured = settings or get_settings()
    if configured.sales_playbook_test_execution:
        assert_isolated_sales_playbook_test_worker(configured)
        raise IsolatedTestWorkerConfigurationError(
            "the isolated test worker must inject its fake generator explicitly",
        )
    if not configured.nebius_enabled:
        return ContractReadyDraftGenerator()
    if not configured.nebius_api_key:
        raise NebiusConfigurationError("NEBIUS_API_KEY is required when NEBIUS_ENABLED=true")
    if not configured.nebius_base_url or not configured.nebius_model:
        raise NebiusConfigurationError("NEBIUS_BASE_URL and NEBIUS_MODEL are required")

    provider = NebiusTokenFactoryProvider(configured)
    semantic_provider = NebiusTokenFactoryProvider(
        configured,
        model=configured.sales_playbook_llm_model,
        timeout_seconds=configured.sales_playbook_llm_timeout_seconds,
        max_completion_tokens=configured.sales_playbook_llm_max_completion_tokens,
    )

    def generate_with(provider_instance):
        def generate_call(
            prompt, response_format, system_prompt, operation, *, invocation_id=None,
        ):
            return provider_instance.generate_with_metadata(
                prompt,
                system_prompt=system_prompt,
                operation=operation,
                temperature=0.0,
                response_format=response_format,
                invocation_id=invocation_id,
            )

        return generate_call

    return SectionAwareSalesPlaybookGenerator(
        generate_with(provider),
        provider_name=provider.provider_name,
        semantic_generate=generate_with(semantic_provider),
        semantic_provider_name=semantic_provider.provider_name,
        semantic_model_name=semantic_provider.model,
        semantic_generation_config={
            "temperature": 0.0,
            "timeout_seconds": configured.sales_playbook_llm_timeout_seconds,
            "max_completion_tokens": configured.sales_playbook_llm_max_completion_tokens,
            "automatic_retries": 0,
            "structured_output_mode": "json_schema",
            "strict": True,
            "endpoint_identity": _safe_endpoint_identity(semantic_provider.api_url),
        },
        semantic_checkpoint_repository=SemanticOperationCheckpointRepository(SessionLocal),
        execution_budget_repository=PostgresExecutionBudgetRepository(SessionLocal),
        execution_budget_policy=ExecutionBudgetPolicy.from_settings(configured),
    )


def _safe_endpoint_identity(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("invalid provider endpoint identity")
    netloc = parsed.hostname
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return f"{parsed.scheme}://{netloc}{parsed.path.rstrip('/')}"
