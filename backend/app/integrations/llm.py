import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import httpx

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMResponseEnvelopeMetadata:
    """Safe structural metadata captured before content normalization."""

    http_status: int | None
    choice_count: int
    selected_choice_index: int | None
    response_id: str | None
    model: str | None
    finish_reason: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    content_field_present: bool
    content_kind: str
    choice_fields: tuple[str, ...]
    message_fields: tuple[str, ...]
    tool_calls_present: bool
    tool_call_count: int | None
    provider_fields: tuple[str, ...]
    provider: str
    operation: str
    latency_ms: int


class LLMResponseNormalizationError(RuntimeError):
    """The provider response is not a documented textual assistant message."""

    def __init__(
        self,
        reason: str,
        *,
        content_kind: str,
        part_count: int | None = None,
        part_types: tuple[str, ...] = (),
        unsupported_part_type: str | None = None,
        response_envelope: LLMResponseEnvelopeMetadata | None = None,
    ) -> None:
        self.reason = reason
        self.content_kind = content_kind
        self.part_count = part_count
        self.part_types = part_types
        self.unsupported_part_type = unsupported_part_type
        self.response_envelope = response_envelope
        details = [f"message.content {reason}", f"kind={content_kind}"]
        if part_count is not None:
            details.append(f"parts={part_count}")
        if part_types:
            details.append(f"part_types={','.join(part_types)}")
        super().__init__("; ".join(details))


@dataclass(frozen=True)
class LLMGeneration:
    """Provider response plus safe metadata for auditable text generation."""

    text: str
    provider: str
    model: str
    operation: str
    latency_ms: int
    tokens: int
    success: bool
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: str | None = None
    total_tokens: int | None = None
    response_envelope: LLMResponseEnvelopeMetadata | None = None
    invocation_id: str | None = None


class LLMProvider:
    """LLM provider for AI operations (message drafting, transcript analysis, embeddings)."""

    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.Client | None = None,
        api_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        provider_name: str = "openai_compatible",
        timeout_seconds: float = 120.0,
        ai_project_id: str = "",
        max_completion_tokens: int | None = None,
    ):
        configured = settings or get_settings()
        self.api_url = (api_url if api_url is not None else configured.llm_api_url).rstrip("/")
        self.api_key = (
            api_key if api_key is not None else configured.llm_api_key or configured.openai_api_key
        )
        self.model = model if model is not None else configured.llm_model
        self.embedding_model = configured.embedding_model
        self.provider_name = provider_name
        self.timeout_seconds = timeout_seconds
        self.timeout = httpx.Timeout(timeout_seconds)
        self.ai_project_id = ai_project_id
        self.max_completion_tokens = max_completion_tokens
        self._client = client

    def generate(
        self, prompt: str, system_prompt: str | None = None, temperature: float = 0.7
    ) -> dict[str, Any]:
        """Preserve the existing parsed dictionary interface for current consumers."""
        generation = self.generate_with_metadata(prompt, system_prompt, temperature)
        result: dict[str, Any] = {
            "text": generation.text,
            "model": generation.model,
            "tokens": generation.tokens,
        }
        try:
            parsed = json.loads(generation.text)
            if isinstance(parsed, dict):
                result.update(parsed)
                result.setdefault("text", generation.text)
        except (TypeError, json.JSONDecodeError):
            pass
        return result

    def generate_with_metadata(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        operation: str = "chat_generation",
        response_format: dict[str, Any] | None = None,
        invocation_id: str | None = None,
    ) -> LLMGeneration:
        """Generate text and expose non-sensitive operational metadata."""
        if not self.api_key:
            raise RuntimeError("LLM provider is not configured")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "response_format": response_format or {"type": "json_object"},
            "stream": False,
        }
        if self.max_completion_tokens is not None:
            payload["max_completion_tokens"] = self.max_completion_tokens
        started = perf_counter()
        try:
            response = self._post("/chat/completions", payload)
            response.raise_for_status()
            data = response.json()
            choices = data.get("choices") if isinstance(data, dict) else None
            choice_count = len(choices) if isinstance(choices, list) else 0
            choice = choices[0] if choice_count and isinstance(choices[0], dict) else {}
            message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
            content_field_present = "content" in message
            content = message.get("content")
            response_model = data.get("model") if isinstance(data.get("model"), str) else self.model
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            tokens = _optional_int(usage.get("total_tokens")) or 0
            input_tokens = _optional_int(usage.get("prompt_tokens"))
            output_tokens = _optional_int(usage.get("completion_tokens"))
            latency_ms = max(0, round((perf_counter() - started) * 1000))
            envelope = LLMResponseEnvelopeMetadata(
                http_status=response.status_code,
                choice_count=choice_count,
                selected_choice_index=choice.get("index") if isinstance(choice.get("index"), int) else None,
                response_id=data.get("id") if isinstance(data.get("id"), str) else None,
                model=response_model,
                finish_reason=choice.get("finish_reason") if isinstance(choice.get("finish_reason"), str) else None,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=tokens,
                content_field_present=content_field_present,
                content_kind=_safe_content_kind(content),
                choice_fields=tuple(sorted(choice.keys())),
                message_fields=tuple(sorted(message.keys())),
                tool_calls_present="tool_calls" in message,
                tool_call_count=(
                    len(message["tool_calls"])
                    if isinstance(message.get("tool_calls"), list)
                    else None
                ),
                provider_fields=tuple(
                    sorted(
                        set(data.keys())
                        - {
                            "id",
                            "object",
                            "created",
                            "model",
                            "choices",
                            "usage",
                            "service_tier",
                            "system_fingerprint",
                        }
                    )
                ),
                provider=self.provider_name,
                operation=operation,
                latency_ms=latency_ms,
            )
            try:
                content = _normalize_message_content(content)
            except LLMResponseNormalizationError as error:
                error.response_envelope = envelope
                raise
            generation = LLMGeneration(
                text=content,
                provider=self.provider_name,
                model=response_model,
                operation=operation,
                latency_ms=latency_ms,
                tokens=tokens,
                success=True,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                finish_reason=choice.get("finish_reason"),
                total_tokens=tokens,
                response_envelope=envelope,
                invocation_id=invocation_id,
            )
            logger.info(
                "llm_generation provider=%s model=%s operation=%s latency_ms=%s success=true tokens=%s",
                generation.provider,
                generation.model,
                generation.operation,
                generation.latency_ms,
                generation.tokens,
            )
            return generation
        except LLMResponseNormalizationError as error:
            logger.warning(
                "llm_response_normalization_failed provider=%s model=%s operation=%s "
                "content_kind=%s part_count=%s part_types=%s unsupported_part_type=%s "
                "http_request_succeeded=true finish_reason=%s total_tokens=%s latency_ms=%s",
                self.provider_name,
                self.model,
                operation,
                error.content_kind,
                error.part_count,
                error.part_types,
                error.unsupported_part_type,
                error.response_envelope.finish_reason if error.response_envelope else None,
                error.response_envelope.total_tokens if error.response_envelope else None,
                error.response_envelope.latency_ms if error.response_envelope else None,
            )
            raise
        except Exception:
            latency_ms = max(0, round((perf_counter() - started) * 1000))
            logger.warning(
                "llm_generation provider=%s model=%s operation=%s latency_ms=%s success=false",
                self.provider_name,
                self.model,
                operation,
                latency_ms,
            )
            raise

    def _post(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        params = {"ai_project_id": self.ai_project_id} if self.ai_project_id else None
        url = f"{self.api_url}{path}"
        if self._client is not None:
            return self._client.post(url, headers=headers, params=params, json=payload, timeout=self.timeout)
        with httpx.Client(timeout=self.timeout) as client:
            return client.post(url, headers=headers, params=params, json=payload)

    def embed(self, text: str) -> list[float]:
        """Create an embedding using the configured provider."""
        if not self.api_key:
            raise RuntimeError("Embedding provider is not configured")
        response = self._post("/embeddings", {"model": self.embedding_model, "input": text})
        response.raise_for_status()
        data = response.json()
        return [float(value) for value in data["data"][0]["embedding"]]


class NebiusTokenFactoryProvider(LLMProvider):
    """OpenAI-compatible Nebius Token Factory text provider."""

    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.Client | None = None,
        *,
        model: str | None = None,
        timeout_seconds: float | None = None,
        max_completion_tokens: int | None = None,
    ):
        configured = settings or get_settings()
        if not configured.nebius_api_key:
            raise RuntimeError("NEBIUS_API_KEY is required for the Nebius provider")
        super().__init__(
            settings=configured,
            client=client,
            api_url=configured.nebius_base_url,
            api_key=configured.nebius_api_key,
            model=model if model is not None else configured.nebius_model,
            provider_name="nebius_token_factory",
            timeout_seconds=(
                timeout_seconds if timeout_seconds is not None else configured.nebius_timeout_seconds
            ),
            ai_project_id=configured.nebius_ai_project_id,
            max_completion_tokens=(
                max_completion_tokens
                if max_completion_tokens is not None
                else configured.nebius_max_completion_tokens
            ),
        )

    def embed(self, text: str) -> list[float]:
        raise RuntimeError("NebiusTokenFactoryProvider is text-only; keep embeddings on LLMProvider")


def _normalize_message_content(content: Any) -> str:
    """Normalize the documented Nebius Chat Completions textual message content."""
    if isinstance(content, str):
        if not content.strip():
            raise LLMResponseNormalizationError(
                "is empty",
                content_kind="string",
            )
        return content
    if isinstance(content, list):
        part_types = tuple(_safe_part_type(part) for part in content)
        raise LLMResponseNormalizationError(
            "is an unsupported content-parts array",
            content_kind="list",
            part_count=len(content),
            part_types=part_types,
            unsupported_part_type=part_types[0] if part_types else None,
        )
    if content is None:
        raise LLMResponseNormalizationError(
            "is null",
            content_kind="null",
        )
    raise LLMResponseNormalizationError(
        "has an unsupported representation",
        content_kind=_safe_content_kind(content),
    )


def _safe_content_kind(content: Any) -> str:
    if content is None:
        return "null"
    if isinstance(content, str):
        return "string"
    if isinstance(content, list):
        return "list"
    if isinstance(content, dict):
        return "object"
    return type(content).__name__


def _safe_part_type(part: Any) -> str:
    if isinstance(part, dict):
        discriminator = part.get("type")
        if isinstance(discriminator, str) and discriminator:
            return discriminator
        return "object"
    return type(part).__name__


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
