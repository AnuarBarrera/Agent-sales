from dataclasses import dataclass

import httpx

from app.core.config import get_settings


@dataclass(frozen=True)
class GeoPoint:
    latitude: float
    longitude: float
    formatted_address: str | None = None


class GeocodingProviderError(Exception):
    """Base exception for Geocoding provider errors."""
    pass


class GeocodingProviderTimeout(GeocodingProviderError):
    pass


class GeocodingProviderRateLimit(GeocodingProviderError):
    pass


class GoogleGeocodingProvider:
    """Google Geocoding API provider implementation."""

    def __init__(self, api_key: str | None = None, timeout: float = 30.0):
        settings = get_settings()
        self.api_key = api_key or settings.google_geocoding_api_key
        self.timeout = timeout
        self.base_url = "https://maps.googleapis.com/maps/api/geocode/json"
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def resolve(self, text: str) -> GeoPoint:
        """Resolve an address/location text to coordinates."""
        if not self.api_key:
            raise GeocodingProviderError("Google Geocoding API key not configured")

        client = await self._get_client()

        params: dict[str, str] = {
            "key": self.api_key,
            "address": text,
            "language": "es",
            "region": "mx",  # Bias towards Mexico
        }

        try:
            response = await client.get(self.base_url, params=params)
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as e:
            raise GeocodingProviderTimeout("Google Geocoding request timed out") from e
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                raise GeocodingProviderRateLimit("Google Geocoding rate limit exceeded") from e
            raise GeocodingProviderError(f"Google Geocoding API error: {e.response.text}") from e

        status = data.get("status")
        if status == "ZERO_RESULTS":
            raise GeocodingProviderError(f"No results found for: {text}")
        elif status != "OK":
            raise GeocodingProviderError(f"Google Geocoding API returned status: {status}")

        results = data.get("results", [])
        if not results:
            raise GeocodingProviderError(f"No results found for: {text}")

        result = results[0]
        geometry = result.get("geometry", {})
        location = geometry.get("location", {})

        return GeoPoint(
            latitude=location.get("lat", 0.0),
            longitude=location.get("lng", 0.0),
            formatted_address=result.get("formatted_address"),
        )


class NominatimGeocodingProvider:
    """OpenStreetMap Nominatim geocoding provider (free, no API key needed)."""

    def __init__(self, timeout: float = 30.0, user_agent: str = "tuwebmx-crm/1.0"):
        self.timeout = timeout
        self.user_agent = user_agent
        self.base_url = "https://nominatim.openstreetmap.org/search"
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                headers={"User-Agent": self.user_agent},
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def resolve(self, text: str) -> GeoPoint:
        """Resolve an address/location text to coordinates using Nominatim."""
        client = await self._get_client()

        params: dict[str, str] = {
            "q": text,
            "format": "json",
            "limit": "1",
            "countrycodes": "mx",  # Limit to Mexico
            "addressdetails": "1",
        }

        try:
            response = await client.get(self.base_url, params=params)
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as e:
            raise GeocodingProviderTimeout("Nominatim request timed out") from e
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                raise GeocodingProviderRateLimit("Nominatim rate limit exceeded") from e
            raise GeocodingProviderError(f"Nominatim error: {e.response.text}") from e

        if not data:
            raise GeocodingProviderError(f"No results found for: {text}")

        result = data[0]
        return GeoPoint(
            latitude=float(result.get("lat", 0.0)),
            longitude=float(result.get("lon", 0.0)),
            formatted_address=result.get("display_name"),
        )
