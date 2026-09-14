from dataclasses import dataclass

import httpx

from app.core.config import get_settings


@dataclass(frozen=True)
class PlaceCandidate:
    place_id: str
    name: str
    address: str | None = None
    phone: str | None = None
    website: str | None = None
    rating: float | None = None
    reviews_total: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    raw_provider_data: dict | None = None


@dataclass(frozen=True)
class PlacesQuery:
    giro: str
    location_label: str
    center_latitude: float
    center_longitude: float
    radius_km: float | None = None
    polygon_geometry: str | None = None  # GeoJSON


class PlacesProviderError(Exception):
    """Base exception for Places provider errors."""
    pass


class PlacesProviderTimeout(PlacesProviderError):
    pass


class PlacesProviderRateLimit(PlacesProviderError):
    pass


class GooglePlacesProvider:
    """Google Places API provider implementation."""

    def __init__(self, api_key: str | None = None, timeout: float = 30.0):
        settings = get_settings()
        self.api_key = api_key or settings.google_places_api_key
        self.timeout = timeout
        self.base_url = "https://maps.googleapis.com/maps/api/place"
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def search(self, query: PlacesQuery) -> list[PlaceCandidate]:
        """Search for places using Google Places Nearby Search."""
        if not self.api_key:
            raise PlacesProviderError("Google Places API key not configured")

        client = await self._get_client()

        # Build location and radius parameters
        location = f"{query.center_latitude},{query.center_longitude}"
        radius = int((query.radius_km or 5.0) * 1000)  # Convert km to meters

        # Build keyword from giro
        keyword = query.giro

        params: dict[str, str] = {
            "key": self.api_key,
            "location": location,
            "radius": str(radius),
            "keyword": keyword,
            "language": "es",
        }

        try:
            response = await client.get(
                f"{self.base_url}/nearbysearch/json",
                params=params,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as e:
            raise PlacesProviderTimeout("Google Places request timed out") from e
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                raise PlacesProviderRateLimit("Google Places rate limit exceeded") from e
            raise PlacesProviderError(f"Google Places API error: {e.response.text}") from e

        status = data.get("status")
        if status == "ZERO_RESULTS":
            return []
        elif status != "OK":
            raise PlacesProviderError(f"Google Places API returned status: {status}")

        candidates = []
        for place in data.get("results", []):
            candidate = self._parse_place(place)
            candidates.append(candidate)

        # Handle pagination if needed
        next_page_token = data.get("next_page_token")
        while next_page_token:
            await self._wait_for_next_page()
            page_candidates = await self._fetch_next_page(next_page_token)
            candidates.extend(page_candidates)
            # In practice, you'd get a new token from the response

        return candidates

    async def _wait_for_next_page(self) -> None:
        """Wait before requesting next page (Google requires a short delay)."""
        import asyncio
        await asyncio.sleep(2)

    async def _fetch_next_page(self, page_token: str) -> list[PlaceCandidate]:
        if not self.api_key:
            raise PlacesProviderError("Google Places API key not configured")

        client = await self._get_client()
        params: dict[str, str] = {
            "key": self.api_key,
            "pagetoken": page_token,
        }

        try:
            response = await client.get(
                f"{self.base_url}/nearbysearch/json",
                params=params,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as e:
            raise PlacesProviderTimeout("Google Places request timed out") from e
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                raise PlacesProviderRateLimit("Google Places rate limit exceeded") from e
            raise PlacesProviderError(f"Google Places API error: {e.response.text}") from e

        status = data.get("status")
        if status != "OK":
            return []

        candidates = []
        for place in data.get("results", []):
            candidate = self._parse_place(place)
            candidates.append(candidate)

        return candidates

    async def details(self, place_id: str) -> PlaceCandidate:
        """Get detailed information for a specific place."""
        if not self.api_key:
            raise PlacesProviderError("Google Places API key not configured")

        client = await self._get_client()

        params: dict[str, str] = {
            "key": self.api_key,
            "place_id": place_id,
            "fields": "place_id,name,formatted_address,formatted_phone_number,website,rating,user_ratings_total,geometry",
            "language": "es",
        }

        try:
            response = await client.get(
                f"{self.base_url}/details/json",
                params=params,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as e:
            raise PlacesProviderTimeout("Google Places request timed out") from e
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                raise PlacesProviderRateLimit("Google Places rate limit exceeded") from e
            raise PlacesProviderError(f"Google Places API error: {e.response.text}") from e

        status = data.get("status")
        if status != "OK":
            raise PlacesProviderError(f"Google Places API returned status: {status}")

        result = data.get("result", {})
        return self._parse_place(result, is_detail=True)

    def _parse_place(self, place: dict, is_detail: bool = False) -> PlaceCandidate:
        geometry = place.get("geometry", {})
        location = geometry.get("location", {}) if geometry else {}

        # Handle different field names between search and details
        if is_detail:
            address = place.get("formatted_address")
            phone = place.get("formatted_phone_number")
            website = place.get("website")
        else:
            address = place.get("vicinity") or place.get("formatted_address")
            phone = None
            website = None

        return PlaceCandidate(
            place_id=place.get("place_id", ""),
            name=place.get("name", ""),
            address=address,
            phone=phone,
            website=website,
            rating=place.get("rating"),
            reviews_total=place.get("user_ratings_total"),
            latitude=location.get("lat"),
            longitude=location.get("lng"),
            raw_provider_data=place,
        )


class N8NPlacesProvider:
    """n8n-based Places provider (fallback/transition)."""

    def __init__(self, webhook_url: str, timeout: float = 60.0):
        self.webhook_url = webhook_url
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def search(self, query: PlacesQuery) -> list[PlaceCandidate]:
        """Search via n8n webhook."""
        client = await self._get_client()

        payload = {
            "giro": query.giro,
            "location_label": query.location_label,
            "center_latitude": query.center_latitude,
            "center_longitude": query.center_longitude,
            "radius_km": query.radius_km,
            "polygon_geometry": query.polygon_geometry,
        }

        try:
            response = await client.post(self.webhook_url, json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as e:
            raise PlacesProviderTimeout("n8n webhook request timed out") from e
        except httpx.HTTPStatusError as e:
            raise PlacesProviderError(f"n8n webhook error: {e.response.text}") from e

        # Expect n8n to return canonical format: {"leads": [...], "success": true}
        if not data.get("success", False):
            raise PlacesProviderError(f"n8n webhook returned error: {data.get('error', 'Unknown')}")

        candidates = []
        for lead_data in data.get("leads", []):
            candidate = PlaceCandidate(
                place_id=lead_data.get("place_id", ""),
                name=lead_data.get("name", ""),
                address=lead_data.get("address"),
                phone=lead_data.get("phone"),
                website=lead_data.get("website"),
                rating=lead_data.get("rating"),
                reviews_total=lead_data.get("reviews_total"),
                latitude=lead_data.get("latitude"),
                longitude=lead_data.get("longitude"),
                raw_provider_data=lead_data,
            )
            candidates.append(candidate)

        return candidates

    async def details(self, place_id: str) -> PlaceCandidate:
        """n8n doesn't support details lookup; raise not implemented."""
        raise NotImplementedError("n8n provider doesn't support details lookup")
