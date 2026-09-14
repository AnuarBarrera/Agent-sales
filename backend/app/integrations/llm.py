import json
import logging
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class LLMProvider:
    """LLM provider for AI operations (message drafting, transcript analysis, embeddings)."""

    def __init__(self):
        self.api_url = settings.llm_api_url.rstrip("/")
        self.api_key = settings.llm_api_key or settings.openai_api_key
        self.model = settings.llm_model
        self.embedding_model = settings.embedding_model

    def generate(
        self, prompt: str, system_prompt: str | None = None, temperature: float = 0.7
    ) -> dict[str, Any]:
        """Generate text using an OpenAI-compatible chat-completions endpoint."""
        if not self.api_key:
            raise RuntimeError("LLM provider is not configured")
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        response = httpx.post(
            f"{self.api_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "messages": messages, "temperature": temperature},
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        result: dict[str, Any] = {
            "text": content,
            "model": data.get("model", self.model),
            "tokens": data.get("usage", {}).get("total_tokens", 0),
        }
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                result.update(parsed)
                result.setdefault("text", content)
        except (TypeError, json.JSONDecodeError):
            pass
        return result

    def embed(self, text: str) -> list[float]:
        """Create an embedding using the configured provider."""
        if not self.api_key:
            raise RuntimeError("Embedding provider is not configured")
        response = httpx.post(
            f"{self.api_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.embedding_model, "input": text},
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        return [float(value) for value in data["data"][0]["embedding"]]
