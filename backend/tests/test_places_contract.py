import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.integrations.places import (
    GooglePlacesProvider,
    N8NPlacesProvider,
    PlacesProviderRateLimit,
    PlacesProviderTimeout,
    PlacesQuery,
)


@pytest.fixture
def places_response():
    """Load the Google Places API response fixture."""
    fixture_path = Path(__file__).parent / "fixtures" / "places_response.json"
    with open(fixture_path) as f:
        return json.load(f)


class TestGooglePlacesProvider:
    @pytest.fixture
    def provider(self):
        return GooglePlacesProvider(api_key="test-key", timeout=5.0)

    @pytest.mark.asyncio
    async def test_search_parses_full_response(self, provider, places_response):
        """Test parsing a full Google Places response with all fields."""
        with patch.object(provider, "_get_client", new_callable=AsyncMock) as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = places_response
            mock_response.raise_for_status = MagicMock()
            mock_client.return_value.get.return_value = mock_response

            query = PlacesQuery(
                giro="restaurantes",
                location_label="Roma Norte, CDMX",
                center_latitude=19.4155,
                center_longitude=-99.1635,
                radius_km=5.0,
            )

            results = await provider.search(query)

            assert len(results) == 3

            # Check first result (restaurant)
            r1 = results[0]
            assert r1.place_id == "ChIJN1t_tDeuEmsRUsoyG83frY4"
            assert r1.name == "Restaurante El Cardenal"
            assert r1.address == "Av. de los Insurgentes Sur 123, Col. Roma Norte, Cuauhtémoc, CDMX"
            assert r1.rating == 4.5
            assert r1.reviews_total == 1250
            assert r1.latitude == 19.4155
            assert r1.longitude == -99.1635
            assert r1.raw_provider_data is not None

            # Check second result (mechanic)
            r2 = results[1]
            assert r2.place_id == "ChIJ2Q8fjReuEmsR4zJ5dJzJ7X0"
            assert r2.name == "Taller Mecánico Pérez"
            assert r2.rating == 4.2
            assert r2.reviews_total == 89

            # Check third result (dentist)
            r3 = results[2]
            assert r3.place_id == "ChIJ7dfVZDeuEmsR8JQ3hGf5K1A"
            assert r3.name == "Clínica Dental Sonríe"
            assert r3.rating == 4.8
            assert r3.reviews_total == 234

    @pytest.mark.asyncio
    async def test_search_handles_missing_phone_website(self, provider):
        """Test parsing when phone and website are missing."""
        mock_response_data = {
            "status": "OK",
            "results": [
                {
                    "place_id": "ChIJTest123",
                    "name": "Business Without Contact",
                    "vicinity": "Some Address, CDMX",
                    "geometry": {"location": {"lat": 19.0, "lng": -99.0}},
                    "rating": 4.0,
                    "user_ratings_total": 10,
                }
            ],
        }

        with patch.object(provider, "_get_client", new_callable=AsyncMock) as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = mock_response_data
            mock_response.raise_for_status = MagicMock()
            mock_client.return_value.get.return_value = mock_response

            query = PlacesQuery(
                giro="test",
                location_label="Test",
                center_latitude=19.0,
                center_longitude=-99.0,
            )

            results = await provider.search(query)

            assert len(results) == 1
            assert results[0].phone is None
            assert results[0].website is None
            assert results[0].name == "Business Without Contact"

    @pytest.mark.asyncio
    async def test_search_zero_results_returns_empty(self, provider):
        """Test ZERO_RESULTS returns empty list."""
        mock_response_data = {"status": "ZERO_RESULTS", "results": []}

        with patch.object(provider, "_get_client", new_callable=AsyncMock) as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = mock_response_data
            mock_response.raise_for_status = MagicMock()
            mock_client.return_value.get.return_value = mock_response

            query = PlacesQuery(
                giro="nonexistent",
                location_label="Test",
                center_latitude=19.0,
                center_longitude=-99.0,
            )

            results = await provider.search(query)
            assert results == []

    @pytest.mark.asyncio
    async def test_search_invalid_status_raises(self, provider):
        """Test non-OK status raises error."""
        mock_response_data = {"status": "REQUEST_DENIED", "error_message": "Invalid key"}

        with patch.object(provider, "_get_client", new_callable=AsyncMock) as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = mock_response_data
            mock_response.raise_for_status = MagicMock()
            mock_client.return_value.get.return_value = mock_response

            query = PlacesQuery(
                giro="test",
                location_label="Test",
                center_latitude=19.0,
                center_longitude=-99.0,
            )

            with pytest.raises(Exception) as exc_info:
                await provider.search(query)
            assert "REQUEST_DENIED" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_search_timeout_raises(self, provider):
        """Test timeout raises PlacesProviderTimeout."""
        import httpx

        with patch.object(provider, "_get_client", new_callable=AsyncMock) as mock_client:
            mock_client.return_value.get.side_effect = httpx.TimeoutException("Timeout")

            query = PlacesQuery(
                giro="test",
                location_label="Test",
                center_latitude=19.0,
                center_longitude=-99.0,
            )

            with pytest.raises(PlacesProviderTimeout):
                await provider.search(query)

    @pytest.mark.asyncio
    async def test_search_rate_limit_raises(self, provider):
        """Test 429 raises PlacesProviderRateLimit."""
        import httpx

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Rate limit exceeded"

        with patch.object(provider, "_get_client", new_callable=AsyncMock) as mock_client:
            mock_client.return_value.get.side_effect = httpx.HTTPStatusError(
                "Rate limit", request=MagicMock(), response=mock_response
            )

            query = PlacesQuery(
                giro="test",
                location_label="Test",
                center_latitude=19.0,
                center_longitude=-99.0,
            )

            with pytest.raises(PlacesProviderRateLimit):
                await provider.search(query)

    @pytest.mark.asyncio
    async def test_details_parses_full_response(self, provider):
        """Test details endpoint parses all fields."""
        mock_detail_data = {
            "status": "OK",
            "result": {
                "place_id": "ChIJDetail123",
                "name": "Detailed Business",
                "formatted_address": "Full Address, CDMX",
                "formatted_phone_number": "+52 55 1234 5678",
                "website": "https://example.com",
                "rating": 4.7,
                "user_ratings_total": 500,
                "geometry": {"location": {"lat": 19.123, "lng": -99.456}},
            },
        }

        with patch.object(provider, "_get_client", new_callable=AsyncMock) as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = mock_detail_data
            mock_response.raise_for_status = MagicMock()
            mock_client.return_value.get.return_value = mock_response

            result = await provider.details("ChIJDetail123")

            assert result.place_id == "ChIJDetail123"
            assert result.name == "Detailed Business"
            assert result.address == "Full Address, CDMX"
            assert result.phone == "+52 55 1234 5678"
            assert result.website == "https://example.com"
            assert result.rating == 4.7
            assert result.reviews_total == 500
            assert result.latitude == 19.123
            assert result.longitude == -99.456


class TestN8NPlacesProvider:
    @pytest.fixture
    def provider(self):
        return N8NPlacesProvider(webhook_url="https://n8n.example.com/webhook/places", timeout=10.0)

    @pytest.mark.asyncio
    async def test_search_parses_canonical_response(self, provider):
        """Test parsing n8n canonical response with leads array."""
        mock_response_data = {
            "success": True,
            "leads": [
                {
                    "place_id": "n8n-place-1",
                    "name": "N8N Business 1",
                    "address": "N8N Address 1",
                    "phone": "+52 55 1111 2222",
                    "website": "https://n8n1.com",
                    "rating": 4.3,
                    "reviews_total": 150,
                    "latitude": 19.5,
                    "longitude": -99.2,
                },
                {
                    "place_id": "n8n-place-2",
                    "name": "N8N Business 2",
                    "address": "N8N Address 2",
                    "rating": 4.1,
                    "reviews_total": 75,
                    "latitude": 19.6,
                    "longitude": -99.3,
                },
            ],
        }

        with patch.object(provider, "_get_client", new_callable=AsyncMock) as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = mock_response_data
            mock_response.raise_for_status = MagicMock()
            mock_client.return_value.post.return_value = mock_response

            query = PlacesQuery(
                giro="test",
                location_label="Test",
                center_latitude=19.5,
                center_longitude=-99.2,
            )

            results = await provider.search(query)

            assert len(results) == 2
            assert results[0].place_id == "n8n-place-1"
            assert results[0].name == "N8N Business 1"
            assert results[0].phone == "+52 55 1111 2222"
            assert results[0].website == "https://n8n1.com"

            assert results[1].place_id == "n8n-place-2"
            assert results[1].phone is None
            assert results[1].website is None

    @pytest.mark.asyncio
    async def test_search_failed_success_false_raises(self, provider):
        """Test n8n response with success=false raises error."""
        mock_response_data = {"success": False, "error": "Internal n8n error"}

        with patch.object(provider, "_get_client", new_callable=AsyncMock) as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = mock_response_data
            mock_response.raise_for_status = MagicMock()
            mock_client.return_value.post.return_value = mock_response

            query = PlacesQuery(
                giro="test",
                location_label="Test",
                center_latitude=19.0,
                center_longitude=-99.0,
            )

            with pytest.raises(Exception) as exc_info:
                await provider.search(query)
            assert "Internal n8n error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_details_not_implemented(self, provider):
        """Test details raises NotImplementedError."""
        with pytest.raises(NotImplementedError):
            await provider.details("any-place-id")


class TestPlacesQuery:
    def test_places_query_creation(self):
        """Test PlacesQuery dataclass creation with all fields."""
        query = PlacesQuery(
            giro="restaurantes",
            location_label="Roma Norte, CDMX",
            center_latitude=19.4155,
            center_longitude=-99.1635,
            radius_km=5.0,
            polygon_geometry='{"type": "Polygon", "coordinates": [...]}',
        )

        assert query.giro == "restaurantes"
        assert query.location_label == "Roma Norte, CDMX"
        assert query.center_latitude == 19.4155
        assert query.center_longitude == -99.1635
        assert query.radius_km == 5.0
        assert query.polygon_geometry is not None

    def test_places_query_minimal(self):
        """Test PlacesQuery with only required fields."""
        query = PlacesQuery(
            giro="test",
            location_label="Test",
            center_latitude=19.0,
            center_longitude=-99.0,
        )

        assert query.radius_km is None
        assert query.polygon_geometry is None
