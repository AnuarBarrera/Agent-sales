"""Adaptador productivo Jev/OpenRouter — Decisions API (spec §6).

Cliente de una sola llamada al endpoint fijo de decisiones de OpenRouter.
Sin retries automáticos, sin fallback a Nebius, sin chat/completions, sin tools,
sin modelo "latest" y sin router automático. Todo se valida estrictamente.
"""

from __future__ import annotations

import math
from contextlib import suppress
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import httpx

DECISIONS_ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_MODEL = "typesafe/jev-1.14"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_REQUEST_BYTES = 65536
DEFAULT_MAX_RESPONSE_BYTES = 65536

# Conjuntos cerrados de opciones admitidos por las preguntas Jev.
JEV_CHOICE_KEYS = frozenset(
    {"matches", "does_not_match", "not_established"}
)
JEV_ENTITY_CHOICE_KEYS = frozenset(
    {"same_business", "different_business", "insufficient"}
)
JEV_APPROVED_CHOICE_SETS = (JEV_CHOICE_KEYS, JEV_ENTITY_CHOICE_KEYS)

CONTRACT_ERROR_TEMPLATE = (
    "Jev/OpenRouter call failed: {error_code} (operation: {operation}, "
    "model: {model}, request_id: {request_id})"
)


@dataclass(frozen=True)
class JevChoice:
    """Una elección cerrada de Jev por una pregunta."""

    choice: str
    probabilities: dict[str, float]
    confidence: float


@dataclass(frozen=True)
class JevQuestionAnswer:
    """Respuesta de una pregunta de Jev validada."""

    question_id: str
    choice: JevChoice


@dataclass(frozen=True)
class JevCall:
    """Resultado de una llamada a Jev, con metadata de aplicación."""

    answers: dict[str, JevChoice]
    requested_model: str
    response_model: str | None
    request_id: str | None
    latency_ms: float
    provider: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None


@dataclass(frozen=True)
class JevError:
    """Error técnico de Jev que nunca se convierte en rechazo comercial."""

    error_code: str
    operation: str
    model: str
    request_id: str | None = None
    message: str = ""
    cost_usd: float | None = None


class JevClientError(RuntimeError):
    """Excepción interna capturada y convertida a JevError antes de salir del adaptador."""

    def __init__(
        self,
        error_code: str,
        *,
        operation: str | None = None,
        model: str | None = None,
        request_id: str | None = None,
        message: str = "",
    ) -> None:
        self.error_code = error_code
        self.operation = operation or "validation"
        self.model = model or ""
        self.request_id = request_id
        self.message = message

        if operation is None and model is None and request_id is None and not message:
            rendered = error_code
        else:
            rendered = CONTRACT_ERROR_TEMPLATE.format(
                error_code=error_code,
                operation=self.operation,
                model=self.model,
                request_id=request_id or "unknown",
            )
        super().__init__(rendered)


class JevDecisionClient:
    """Cliente productivo de la Decisions API de OpenRouter (Jev)."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        authorized_models: tuple[str, ...] = (DEFAULT_MODEL,),
        client: httpx.Client | None = None,
        enabled: bool = False,
    ) -> None:
        if not enabled:
            raise JevClientError(
                "JevDecisionClient está deshabilitado por defecto (JEV_ENABLED=false)"
            )
        if not api_key:
            raise JevClientError("OPENROUTER_API_KEY no configurada")
        if model not in authorized_models:
            raise JevClientError(
                f"Modelo {model!r} no está en la allowlist autorizada"
            )
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_request_bytes = max_request_bytes
        self._max_response_bytes = max_response_bytes
        self._authorized_models = tuple(authorized_models)
        self._enabled = enabled
        self._client = client or httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=False,
        )

    def close(self) -> None:
        with suppress(Exception):
            self._client.close()

    def evaluate(
        self,
        state: dict[str, Any],
        questions: dict[str, dict[str, Any]],
    ) -> JevCall:
        """Ejecuta una llamada cerrada a Jev y valida la respuesta completa."""
        if not self._enabled:
            raise JevClientError("JevDecisionClient deshabilitado")

        self._validate_request_payload(state, questions)

        payload = {
            "model": self._model,
            "state": state,
            "questions": questions,
        }
        body_bytes = self._serialize_body(payload)
        if len(body_bytes) > self._max_request_bytes:
            raise JevClientError(
                f"Request excede {self._max_request_bytes} bytes: {len(body_bytes)}"
            )

        started = perf_counter()
        try:
            response = self._client.post(
                DECISIONS_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                content=body_bytes,
                timeout=self._timeout_seconds,
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            request_error = JevClientError(
                "timeout", operation="http", model=self._model,
            )
        except httpx.TransportError:
            request_error = JevClientError(
                "transport_error", operation="http", model=self._model,
            )
        else:
            request_error = None
        if request_error is not None:
            raise request_error

        latency_ms = (perf_counter() - started) * 1000
        if len(response.content) > self._max_response_bytes:
            raise JevClientError(
                "response_too_large", operation="http", model=self._model,
            )
        request_id = response.headers.get("x-request-id")

        if 300 <= response.status_code < 400:
            raise JevClientError(
                "redirect_rejected", operation="http", model=self._model,
                request_id=request_id,
            )
        if response.status_code == 401:
            raise JevClientError(
                "unauthorized", operation="http", model=self._model,
                request_id=request_id,
            )
        if response.status_code == 402:
            raise JevClientError(
                "payment_required", operation="http", model=self._model,
                request_id=request_id,
            )
        if response.status_code == 429:
            raise JevClientError(
                "rate_limited", operation="http", model=self._model,
                request_id=request_id,
            )
        if response.status_code >= 500:
            raise JevClientError(
                "provider_error", operation="http", model=self._model,
                request_id=request_id,
                message=f"HTTP {response.status_code}",
            )
        if response.status_code >= 400:
            raise JevClientError(
                "client_error", operation="http", model=self._model,
                request_id=request_id,
                message=f"HTTP {response.status_code}",
            )

        try:
            data = response.json()
        except ValueError:
            decode_error = JevClientError(
                "malformed_json", operation="http", model=self._model,
                request_id=request_id,
            )
        else:
            decode_error = None
        if decode_error is not None:
            raise decode_error

        return self._normalize_response(
            data,
            expected_question_ids=set(questions),
            expected_question_options={
                question_id: frozenset(question["criteria"])
                for question_id, question in questions.items()
            },
            requested_model=self._model,
            request_id=request_id,
            latency_ms=latency_ms,
        )

    # ------------------------------------------------------------------
    # Validación de request
    # ------------------------------------------------------------------

    def _validate_request_payload(
        self,
        state: dict[str, Any],
        questions: dict[str, dict[str, Any]],
    ) -> None:
        if not isinstance(state, dict):
            raise JevClientError("state debe ser un objeto")
        if not questions:
            raise JevClientError("se requiere al menos una pregunta")
        for question_id, question in questions.items():
            if not isinstance(question_id, str) or not question_id:
                raise JevClientError("IDs de pregunta deben ser strings no vacíos")
            if not isinstance(question, dict):
                raise JevClientError(f"Pregunta {question_id!r} debe ser un objeto")
            if question.get("type") != "choice":
                raise JevClientError(
                    f"Pregunta {question_id!r} debe ser de tipo 'choice'"
                )
            instructions = question.get("instructions")
            if not isinstance(instructions, str) or not instructions:
                raise JevClientError(
                    f"Pregunta {question_id!r}: instructions requeridas y no vacías"
                )
            criteria = question.get("criteria")
            if not isinstance(criteria, dict) or not criteria:
                raise JevClientError(
                    f"Pregunta {question_id!r}: criteria requerido y no vacío"
                )
            if frozenset(criteria) not in JEV_APPROVED_CHOICE_SETS:
                raise JevClientError(
                    f"Pregunta {question_id!r}: conjunto de opciones no permitido"
                )

    def _serialize_body(self, payload: dict[str, Any]) -> bytes:
        import json

        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    # ------------------------------------------------------------------
    # Normalización y validación de respuesta
    # ------------------------------------------------------------------

    def _normalize_response(
        self,
        data: Any,
        *,
        expected_question_ids: set[str],
        expected_question_options: dict[str, frozenset[str]],
        requested_model: str,
        request_id: str | None,
        latency_ms: float,
    ) -> JevCall:
        if not isinstance(data, dict):
            raise JevClientError(
                "malformed_envelope", operation="http", model=requested_model,
                request_id=request_id,
                message="el cuerpo no es un objeto JSON",
            )

        response_model = data.get("model")
        if not isinstance(response_model, str):
            raise JevClientError(
                "model_missing", operation="http", model=requested_model,
                request_id=request_id,
                message="campo 'model' ausente o no string",
            )
        if response_model not in self._authorized_models:
            raise JevClientError(
                "unexpected_model", operation="http", model=requested_model,
                request_id=request_id,
                message=f"modelo servido {response_model!r} no autorizado",
            )

        answers_payload = data.get("answers")
        if not isinstance(answers_payload, dict):
            raise JevClientError(
                "answers_missing", operation="http", model=requested_model,
                request_id=request_id,
                message="campo 'answers' ausente o no objeto",
            )

        # El set de IDs de preguntas debe coincidir exactamente con el enviado.
        answer_ids = set(answers_payload.keys())
        if answer_ids != expected_question_ids:
            extra = answer_ids - expected_question_ids
            missing = expected_question_ids - answer_ids
            detail = []
            if extra:
                detail.append(f"respuestas adicionales: {sorted(extra)}")
            if missing:
                detail.append(f"respuestas faltantes: {sorted(missing)}")
            raise JevClientError(
                "answer_id_mismatch", operation="http", model=requested_model,
                request_id=request_id,
                message="; ".join(detail),
            )
        normalized_answers: dict[str, JevChoice] = {}
        for question_id, raw in answers_payload.items():
            if not isinstance(raw, dict):
                raise JevClientError(
                    "answer_malformed", operation="http", model=requested_model,
                    request_id=request_id,
                    message=f"respuesta {question_id!r} no es un objeto",
                )
            if raw.get("type") != "choice":
                raise JevClientError(
                    "answer_type", operation="http", model=requested_model,
                    request_id=request_id,
                    message=f"respuesta {question_id!r} no es de tipo 'choice'",
                )
            choice = raw.get("choice")
            option_keys = expected_question_options[question_id]
            if not isinstance(choice, str) or choice not in option_keys:
                raise JevClientError(
                    "answer_option_mismatch", operation="http", model=requested_model,
                    request_id=request_id,
                    message=f"elección incompatible con las opciones de {question_id!r}",
                )
            probabilities = raw.get("probabilities")
            if not isinstance(probabilities, dict):
                raise JevClientError(
                    "answer_probabilities", operation="http", model=requested_model,
                    request_id=request_id,
                    message=f"probabilities de {question_id!r} no es un dict",
                )
            if set(probabilities) != option_keys:
                raise JevClientError(
                    "answer_option_mismatch", operation="http", model=requested_model,
                    request_id=request_id,
                    message=f"probabilities de {question_id!r} no coincide con las opciones enviadas",
                )
            normalized_probs: dict[str, float] = {}
            for key, value in probabilities.items():
                if isinstance(value, bool) or not isinstance(value, int | float):
                    raise JevClientError(
                        "answer_prob_not_number", operation="http",
                        model=requested_model, request_id=request_id,
                        message=f"probabilidad de {key!r} no es número",
                    )
                if (
                    (isinstance(value, float) and not math.isfinite(value))
                    or value < 0.0
                    or value > 1.0
                ):
                    raise JevClientError(
                        "answer_prob_out_of_range", operation="http",
                        model=requested_model, request_id=request_id,
                        message=f"probabilidad de {key!r} fuera de 0..1",
                    )
                normalized_probs[key] = float(value)
            total = sum(normalized_probs.values())
            if abs(total - 1.0) > 0.01:
                raise JevClientError(
                    "answer_prob_sum", operation="http", model=requested_model,
                    request_id=request_id,
                    message=f"probabilidades de {question_id!r} suman {total}, no 1 ± 0.01",
                )
            confidence = raw.get("confidence")
            if isinstance(confidence, bool) or not isinstance(confidence, int | float):
                raise JevClientError(
                    "answer_confidence_invalid", operation="http",
                    model=requested_model, request_id=request_id,
                    message=f"confidence de {question_id!r} no es número",
                )
            if (
                (isinstance(confidence, float) and not math.isfinite(confidence))
                or confidence < 0.0
                or confidence > 1.0
            ):
                raise JevClientError(
                    "answer_confidence_out_of_range", operation="http",
                    model=requested_model, request_id=request_id,
                    message=f"confidence de {question_id!r} fuera de 0..1",
                )

            # La elección debe pertenecer al argmax de las probabilidades (permitir empate).
            if normalized_probs:
                max_prob = max(normalized_probs.values())
                if normalized_probs.get(choice, -1.0) < max_prob - 1e-9:
                    raise JevClientError(
                        "answer_choice_not_argmax", operation="http",
                        model=requested_model, request_id=request_id,
                        message=f"elección {choice!r} no es argmax en {question_id!r}",
                    )

            normalized_answers[question_id] = JevChoice(
                choice=choice,
                probabilities=normalized_probs,
                confidence=float(confidence),
            )

        usage = data.get("usage")
        input_tokens = output_tokens = cost_usd = None
        if isinstance(usage, dict):
            input_tokens = _optional_nonnegative_int(usage.get("input_tokens"))
            output_tokens = _optional_nonnegative_int(usage.get("output_tokens"))
            cost_usd = _optional_nonnegative_float(usage.get("cost"))

        provider = data.get("provider")
        if not isinstance(provider, str):
            provider = None

        return JevCall(
            answers=normalized_answers,
            requested_model=requested_model,
            response_model=response_model,
            request_id=request_id,
            latency_ms=latency_ms,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        )

    def validate_answers_match_questions(
        self,
        call: JevCall,
        expected_questions: dict[str, dict[str, Any]],
    ) -> None:
        """Rechaza respuestas con preguntas adicionales o faltantes."""
        if set(call.answers) != set(expected_questions):
            extra = set(call.answers) - set(expected_questions)
            missing = set(expected_questions) - set(call.answers)
            detail = []
            if extra:
                detail.append(f"adicionales: {sorted(extra)}")
            if missing:
                detail.append(f"faltantes: {sorted(missing)}")
            raise JevClientError(
                "answer_id_mismatch", operation="http", model=self._model,
                message="; ".join(detail),
            )


def _optional_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0 or value > 1_000_000_000:
        return None
    return value


def _optional_nonnegative_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if value < 0 or value > 1_000_000_000_000:
        return None
    result = float(value)
    return result if math.isfinite(result) else None
