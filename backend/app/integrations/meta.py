from dataclasses import dataclass

import httpx

from app.core.config import get_settings


@dataclass(frozen=True)
class MetaAd:
    ad_id: str
    page_name: str
    ad_creative_body: str | None = None
    ad_creative_link_title: str | None = None
    ad_delivery_start_time: str | None = None


class MetaAdsProviderError(Exception):
    """Base exception for Meta Ads provider errors."""
    pass


class MetaAdsProviderTimeout(MetaAdsProviderError):
    pass


class MetaAdsProvider:
    """Meta Ads Library API provider implementation."""

    def __init__(self, access_token: str | None = None, timeout: float = 30.0):
        settings = get_settings()
        self.access_token = access_token or settings.meta_access_token
        self.timeout = timeout
        self.base_url = "https://graph.facebook.com/v19.0/ads_archive"
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def search_active_ads(self, search_terms: str) -> list[MetaAd]:
        """Search for active ads matching search terms."""
        if not self.access_token:
            raise MetaAdsProviderError("Meta access token not configured")

        client = await self._get_client()

        params: dict[str, str] = {
            "access_token": self.access_token,
            "search_terms": search_terms,
            "ad_active_status": "ACTIVE",
            "ad_reached_countries": '["MX"]',
            "fields": "id,page_name,ad_creative_body,ad_creative_link_title,ad_delivery_start_time",
            "limit": "10",
        }

        try:
            response = await client.get(self.base_url, params=params)
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as e:
            raise MetaAdsProviderTimeout("Meta Ads Library request timed out") from e
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                raise MetaAdsProviderError("Meta Ads rate limit exceeded") from e
            raise MetaAdsProviderError(f"Meta Ads API error: {e.response.text}") from e

        ads = []
        for ad_data in data.get("data", []):
            ad = MetaAd(
                ad_id=ad_data.get("id", ""),
                page_name=ad_data.get("page_name", ""),
                ad_creative_body=ad_data.get("ad_creative_body"),
                ad_creative_link_title=ad_data.get("ad_creative_link_title"),
                ad_delivery_start_time=ad_data.get("ad_delivery_start_time"),
            )
            ads.append(ad)

        return ads
