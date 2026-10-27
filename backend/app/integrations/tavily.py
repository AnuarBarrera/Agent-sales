"""Small provider adapter for Tavily Search and selected Extract calls."""

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings, get_settings


class TavilyProviderError(RuntimeError):
    """A safe, provider-facing error without credentials or response bodies."""


@dataclass(frozen=True)
class TavilySearchResult:
    url: str
    title: str | None
    content: str
    score: float | None = None


@dataclass(frozen=True)
class TavilySearchResponse:
    results: list[TavilySearchResult]
    request_id: str | None
    usage: dict[str, Any]


@dataclass(frozen=True)
class TavilyExtractResult:
    url: str
    content: str
    title: str | None = None


class TavilyProvider:
    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = client
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()

    def _request(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.settings.tavily_enabled or not self.settings.tavily_api_key:
            raise TavilyProviderError("Tavily no está configurado")
        client = self._client or httpx.Client(timeout=self.settings.tavily_timeout_seconds)
        try:
            response = client.post(
                f"{self.settings.tavily_base_url.rstrip('/')}/{path.lstrip('/')}",
                json={"api_key": self.settings.tavily_api_key, **payload},
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise TavilyProviderError("Tavily devolvió una respuesta inválida")
            return data
        except httpx.TimeoutException as exc:
            raise TavilyProviderError("Tavily agotó el tiempo de espera") from exc
        except httpx.HTTPStatusError as exc:
            raise TavilyProviderError(f"Tavily rechazó la solicitud ({exc.response.status_code})") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise TavilyProviderError("No se pudo leer la respuesta de Tavily") from exc
        finally:
            if self._owns_client:
                client.close()

    @staticmethod
    def _usage(data: dict[str, Any]) -> dict[str, Any]:
        usage = data.get("usage")
        return usage if isinstance(usage, dict) else {}

    def search(self, query: str, *, max_results: int, search_depth: str) -> TavilySearchResponse:
        data = self._request(
            "/search",
            {
                "query": query,
                "search_depth": search_depth,
                "max_results": max_results,
                "include_answer": False,
                "include_raw_content": False,
                "include_usage": True,
                "topic": "general",
            },
        )
        results: list[TavilySearchResult] = []
        for item in data.get("results", []):
            if not isinstance(item, dict) or not item.get("url") or not item.get("content"):
                continue
            score = item.get("score")
            results.append(
                TavilySearchResult(
                    url=str(item["url"]),
                    title=str(item["title"]) if item.get("title") else None,
                    content=str(item["content"]),
                    score=float(score) if isinstance(score, int | float) else None,
                )
            )
        return TavilySearchResponse(results, data.get("request_id"), self._usage(data))

    def extract(self, urls: list[str]) -> tuple[list[TavilyExtractResult], dict[str, Any]]:
        if not urls:
            return [], {}
        data = self._request(
            "/extract",
            {
                "urls": urls,
                "extract_depth": "basic",
                "format": "markdown",
                "include_images": False,
                "include_usage": True,
            },
        )
        results: list[TavilyExtractResult] = []
        for item in data.get("results", []):
            if not isinstance(item, dict) or not item.get("url") or not item.get("raw_content"):
                continue
            results.append(
                TavilyExtractResult(
                    url=str(item["url"]),
                    content=str(item["raw_content"]),
                    title=str(item["title"]) if item.get("title") else None,
                )
            )
        return results, self._usage(data)
