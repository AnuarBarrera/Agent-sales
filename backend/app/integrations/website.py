from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class WebsiteCheckResult:
    is_valid: bool
    status_code: int | None = None
    error: str | None = None
    request_id: str | None = None


class WebsiteCheckerError(Exception):
    """Base exception for Website checker errors."""
    pass


class WebsiteCheckerTimeout(WebsiteCheckerError):
    pass


class WebsiteChecker:
    """Website availability checker."""

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def check(self, url: str) -> WebsiteCheckResult:
        """Check if a website is accessible."""
        client = await self._get_client()

        try:
            response = await client.get(url)
            if response.is_success:
                return WebsiteCheckResult(
                    is_valid=True,
                    status_code=response.status_code,
                )
            else:
                return WebsiteCheckResult(
                    is_valid=False,
                    status_code=response.status_code,
                    error=f"HTTP {response.status_code}",
                )
        except httpx.TimeoutException as e:
            return WebsiteCheckResult(
                is_valid=False,
                error=f"Timeout: {e}",
            )
        except httpx.RequestError as e:
            return WebsiteCheckResult(
                is_valid=False,
                error=f"Request error: {e}",
            )
        except Exception as e:
            return WebsiteCheckResult(
                is_valid=False,
                error=f"Unexpected error: {e}",
            )
