import logging
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class TranscriptionProvider:
    """Transcription provider using external API (e.g., Whisper)."""

    def __init__(self):
        self.api_url = settings.transcription_api_url
        self.api_key = settings.transcription_api_key or settings.openai_api_key
        self.model = settings.transcription_model

    def transcribe(self, audio_data: bytes, content_type: str, filename: str = "call.audio") -> dict[str, Any]:
        """Transcribe audio through an OpenAI-compatible Whisper endpoint."""
        if not self.api_key:
            raise RuntimeError("Transcription provider is not configured")
        response = httpx.post(
            self.api_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            files={"file": (filename, audio_data, content_type)},
            data={"model": self.model, "language": "es", "response_format": "verbose_json"},
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        return {
            "text": data.get("text", ""),
            "language": data.get("language", "es"),
            "duration": data.get("duration"),
            "provider": "openai-compatible-whisper",
            "request_id": response.headers.get("x-request-id"),
        }
