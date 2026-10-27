import math
import re
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
    max_results: int | None = None


class PlacesProviderError(Exception):
    """Base exception for Places provider errors."""
    pass


class PlacesProviderTimeout(PlacesProviderError):
    pass


class PlacesProviderRateLimit(PlacesProviderError):
    pass


class GooglePlacesProvider:
    """Places API New adapter; consumers keep the canonical PlaceCandidate."""

    _FIELDS = (
        "id", "displayName", "formattedAddress", "location", "nationalPhoneNumber",
        "websiteUri", "rating", "userRatingCount",
    )
    _EARTH_RADIUS_KM = 6371.0088

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = 30.0,
        max_results: int | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        settings = get_settings()
        maximum = settings.google_places_max_results if max_results is None else max_results
        if type(maximum) is not int or not 1 <= maximum <= 60:
            raise ValueError("Google Places result budget must be an integer from 1 to 60")
        self.max_results = maximum
        self.api_key = api_key or settings.google_places_api_key
        self.timeout = timeout
        self.base_url = "https://places.googleapis.com/v1/places"
        self.transport = transport
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout, trust_env=False, transport=self.transport,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def search(self, query: PlacesQuery) -> list[PlaceCandidate]:
        """Search free-text business categories within the selected radius."""
        maximum = self.max_results if query.max_results is None else query.max_results
        if type(maximum) is not int or not 1 <= maximum <= (60 if query.max_results is None else 10):
            raise PlacesProviderError("Invalid Google Places result budget")
        rectangle, radius = self._search_bounds(query)
        payload = {
            "textQuery": query.giro.strip(), "languageCode": "es",
            "pageSize": min(20, maximum),
            "locationRestriction": {"rectangle": rectangle},
        }
        fields = self._FIELDS
        if query.max_results is not None:
            fields += ("primaryType", "primaryTypeDisplayName")
        field_mask = ",".join(f"places.{field}" for field in fields) + ",nextPageToken"
        candidates = []
        seen_tokens: set[str] = set()
        for _ in range(3):
            data = await self._request("POST", ":searchText", field_mask, json=payload)
            rows = data.get("places", [])
            token = data.get("nextPageToken")
            if not isinstance(rows, list) or (token is not None and not isinstance(token, str)):
                raise PlacesProviderError("Google Places returned an invalid search response")
            for row in rows:
                candidate = self._parse_place(row)
                if self._within_radius(candidate, query, radius):
                    candidates.append(candidate)
            if len(candidates) >= maximum or not token:
                break
            if token in seen_tokens:
                raise PlacesProviderError("Google Places returned a repeated page token")
            seen_tokens.add(token)
            payload["pageToken"] = token
        return candidates[:maximum]

    async def _request(self, method: str, path: str, field_mask: str, **kwargs) -> dict:
        """Never echo Google bodies, headers, URLs or credential-bearing exceptions."""
        if not self.api_key:
            raise PlacesProviderError("Google Places API key not configured")
        client = await self._get_client()
        try:
            response = await client.request(
                method, f"{self.base_url}{path}",
                headers={"X-Goog-Api-Key": self.api_key, "X-Goog-FieldMask": field_mask},
                **kwargs,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException:
            raise PlacesProviderTimeout("Google Places request timed out") from None
        except httpx.HTTPStatusError as error:
            code = error.response.status_code
            exception = PlacesProviderRateLimit if code == 429 else PlacesProviderError
            raise exception(f"Google Places API New returned HTTP {code}") from None
        except httpx.RequestError:
            raise PlacesProviderError("Google Places transport failed") from None
        except ValueError:
            raise PlacesProviderError("Google Places returned invalid JSON") from None
        if not isinstance(data, dict) or any(key in data for key in ("error", "status", "results")):
            raise PlacesProviderError("Google Places returned an invalid API New response")
        return data

    async def details(self, place_id: str) -> PlaceCandidate:
        """Get contacts without falling back to the Legacy API."""
        if not isinstance(place_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", place_id):
            raise PlacesProviderError("Invalid Google Place ID")
        data = await self._request(
            "GET", f"/{place_id}", ",".join(self._FIELDS), params={"languageCode": "es"},
        )
        candidate = self._parse_place(data)
        if candidate.place_id != place_id:
            raise PlacesProviderError("Google Places returned a different Place ID")
        return candidate

    @staticmethod
    def _coordinate(value, limit: float) -> bool:
        return type(value) in (int, float) and math.isfinite(value) and -limit <= value <= limit

    def _search_bounds(self, query: PlacesQuery) -> tuple[dict, float]:
        latitude, longitude = query.center_latitude, query.center_longitude
        radius = 5.0 if query.radius_km is None else query.radius_km
        if (
            not self._coordinate(latitude, 90) or not self._coordinate(longitude, 180)
            or type(radius) not in (int, float) or not math.isfinite(radius) or not 0 < radius <= 50
            or not isinstance(query.giro, str) or not query.giro.strip()
        ):
            raise PlacesProviderError("Invalid Google Places search geography or text")
        angle = radius / self._EARTH_RADIUS_KM
        delta_lat = math.degrees(angle)
        low_lat, high_lat = max(-90, latitude - delta_lat), min(90, latitude + delta_lat)
        if low_lat == -90 or high_lat == 90:
            low_lng, high_lng = -180, 180
        else:
            delta_lng = math.degrees(math.asin(min(1, math.sin(angle) / math.cos(math.radians(latitude)))))
            low_lng = (longitude - delta_lng + 180) % 360 - 180
            high_lng = (longitude + delta_lng + 180) % 360 - 180
        return {
            "low": {"latitude": low_lat, "longitude": low_lng},
            "high": {"latitude": high_lat, "longitude": high_lng},
        }, radius

    def _within_radius(self, place: PlaceCandidate, query: PlacesQuery, radius: float) -> bool:
        latitude, longitude = math.radians(place.latitude), math.radians(place.longitude)
        center_latitude = math.radians(query.center_latitude)
        distance_term = (
            math.sin((latitude - center_latitude) / 2) ** 2
            + math.cos(latitude) * math.cos(center_latitude)
            * math.sin((longitude - math.radians(query.center_longitude)) / 2) ** 2
        )
        distance = 2 * self._EARTH_RADIUS_KM * math.asin(math.sqrt(min(1, max(0, distance_term))))
        return distance <= radius + 1e-9

    def _parse_place(self, place: dict) -> PlaceCandidate:
        if not isinstance(place, dict):
            raise PlacesProviderError("Google Places returned an invalid place")
        identifier = place.get("id")
        display_name = place.get("displayName")
        location = place.get("location")
        if (
            not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", identifier)
            or not isinstance(display_name, dict) or not isinstance(display_name.get("text"), str)
            or not display_name["text"].strip() or not isinstance(location, dict)
            or not self._coordinate(location.get("latitude"), 90)
            or not self._coordinate(location.get("longitude"), 180)
        ):
            raise PlacesProviderError("Google Places returned an invalid place identity or location")
        return PlaceCandidate(
            place_id=identifier,
            name=display_name["text"],
            address=place.get("formattedAddress"),
            phone=place.get("nationalPhoneNumber"),
            website=place.get("websiteUri"),
            rating=place.get("rating"),
            reviews_total=place.get("userRatingCount"),
            latitude=location["latitude"],
            longitude=location["longitude"],
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
