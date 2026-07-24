"""Tests for the SmartyPlants API client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.smartyplants.api import (
    SmartyPlantsClient,
    _parse_metadata,
    _parse_reading,
    _parse_sensor_entry,
    _parse_thresholds,
)
from custom_components.smartyplants.exceptions import (
    SmartyPlantsAuthError,
    SmartyPlantsConnectionError,
)

from .const import (
    MOCK_ACCESS_TOKEN,
    MOCK_EMAIL,
    MOCK_PASSWORD,
    MOCK_PLANT_ID,
    MOCK_REFRESH_TOKEN,
    MOCK_SENSOR_ID,
    MOCK_USER_ID,
)


def _make_response(status: int, json_data: dict | list | None = None) -> AsyncMock:
    """Create a mock aiohttp response."""
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data)
    # Make it work as an async context manager
    return resp


def _make_session() -> AsyncMock:
    """Create a mock aiohttp ClientSession."""
    session = AsyncMock(spec=aiohttp.ClientSession)
    return session


def _set_post_response(session: AsyncMock, response: AsyncMock) -> None:
    """Configure session.post to return the given response as context manager."""
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session.post.return_value = ctx


def _set_get_response(session: AsyncMock, response: AsyncMock) -> None:
    """Configure session.get to return the given response as context manager."""
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session.get.return_value = ctx


def _set_get_responses(session: AsyncMock, responses: list[AsyncMock]) -> None:
    """Configure session.get to return multiple responses in sequence."""
    ctxs = []
    for resp in responses:
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=False)
        ctxs.append(ctx)
    session.get.side_effect = ctxs


def _login_response_json() -> dict:
    """Return a successful login response body."""
    return {
        "success": True,
        "data": {
            "user": {"userId": MOCK_USER_ID},
            "token": {
                "accessToken": MOCK_ACCESS_TOKEN,
                "refreshToken": MOCK_REFRESH_TOKEN,
            },
        },
        "message": "Login successful",
    }


def _refresh_response_json(new_access_token: str) -> dict:
    """Return a successful token refresh response body."""
    return {
        "success": True,
        "data": {
            "token": {
                "accessToken": new_access_token,
                "refreshToken": None,
            },
        },
        "message": "Token refreshed",
    }


def _sensor_status_data() -> dict:
    """Return a lastSensorStatusData block as /sensors returns it."""
    return {
        "temperature": {
            "value": 22.5,
            "status": "OPTIMAL",
            "message": "Temperature is great",
        },
        "humidity": {
            "value": 55.0,
            "status": "OPTIMAL",
            "message": "Humidity is good",
        },
        "waterLevel": {
            "value": 40.0,
            "status": "LOW",
            "message": "Needs water",
        },
        "light": {
            "value": 800.0,
            "status": "OPTIMAL",
            "message": "Good light",
        },
        # The API returns nutrient as a human-readable string, not a number.
        "nutrient": {
            "value": "Well fertilised",
            "status": "OPTIMAL",
            "message": "Nutrients OK",
        },
        "batteryPercent": {
            "value": 85.0,
            "status": "OPTIMAL",
            "message": "Battery good",
        },
        "voltage": {
            "value": 3.7,
            "status": "OPTIMAL",
            "message": "Voltage OK",
        },
        # lightQuality can be a bare "-" when there is no reading.
        "lightQuality": {"value": "-", "status": "-", "message": ""},
    }


def _sensors_entry(
    *,
    plant_id: str = MOCK_PLANT_ID,
    sensor_id: str = MOCK_SENSOR_ID,
    name: str = "Monstera",
    online: bool = True,
) -> dict:
    """Return a single item from the /sensors response."""
    return {
        "sensor": {
            "id": sensor_id,
            "identifier": "SP-001",
            "isOnline": online,
            "batteryPercentage": 85,
            "lastSensorStatusData": _sensor_status_data(),
        },
        "currentPlant": {
            "id": plant_id,
            "name": name,
            "imageUrl": "https://example.com/monstera.jpg",
            "sensorId": sensor_id,
        },
    }


def _sensors_response(entries: list[dict], *, has_next: bool = False) -> dict:
    """Wrap /sensors entries in the paginated response envelope."""
    return {
        "success": True,
        "data": entries,
        "message": "Sensors fetched successfully",
        "meta": {"page": 1, "limit": 50, "hasNextPage": has_next},
    }


def _plant_detail_data(
    *,
    plant_id: str = MOCK_PLANT_ID,
    name: str = "Monstera",
) -> dict:
    """Return the `data` block of a /plant/{id} detail response."""
    return {
        "id": plant_id,
        "name": name,
        "environment": {"name": "Living Room"},
        "plantReference": {
            "scientificNameWithoutAuthor": "deliciosa",
            "genus": "Monstera",
            "family": "Araceae",
            "commonNames": ["Swiss cheese plant"],
            "plantConfigurations": [
                {
                    "variant": "TEMPERATURE",
                    "valueOne": 10.0,
                    "valueTwo": 18.0,
                    "valueThree": 27.0,
                    "valueFour": 35.0,
                },
                {
                    "variant": "SALINITY",
                    "valueOne": 0.5,
                    "valueTwo": 1.0,
                    "valueThree": 2.5,
                    "valueFour": 3.5,
                },
            ],
        },
    }


def _plant_detail_response(
    *,
    plant_id: str = MOCK_PLANT_ID,
    name: str = "Monstera",
) -> dict:
    """Wrap plant detail data in the response envelope."""
    return {
        "success": True,
        "data": _plant_detail_data(plant_id=plant_id, name=name),
        "message": "Plant fetched successfully",
    }


class TestLogin:
    """Tests for login functionality."""

    async def test_login_success(self) -> None:
        """Test successful login returns tokens and user ID."""
        session = _make_session()
        resp = _make_response(201, _login_response_json())
        _set_post_response(session, resp)

        client = SmartyPlantsClient(session)
        result = await client.async_login(MOCK_EMAIL, MOCK_PASSWORD)

        if isinstance(result, Exception):
            raise result
        assert result["user_id"] == MOCK_USER_ID
        assert result["access_token"] == MOCK_ACCESS_TOKEN
        assert result["refresh_token"] == MOCK_REFRESH_TOKEN

    async def test_login_invalid_credentials(self) -> None:
        """Test login with invalid credentials raises AuthError."""
        session = _make_session()
        resp = _make_response(401, {"success": False, "message": "Invalid credentials"})
        _set_post_response(session, resp)

        client = SmartyPlantsClient(session)
        with pytest.raises(SmartyPlantsAuthError):
            await client.async_login(MOCK_EMAIL, "wrong_password")

    async def test_login_connection_error(self) -> None:
        """Test login with connection error raises ConnectionError."""
        session = _make_session()
        session.post.side_effect = aiohttp.ClientError("Connection refused")

        client = SmartyPlantsClient(session)
        with pytest.raises(SmartyPlantsConnectionError):
            await client.async_login(MOCK_EMAIL, MOCK_PASSWORD)


class TestTokenRefresh:
    """Tests for token refresh functionality."""

    async def test_refresh_success(self) -> None:
        """Test successful token refresh returns new access token."""
        session = _make_session()
        new_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIwMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDEiLCJleHAiOjk5OTk5OTk5OTl9.new"  # noqa: E501
        resp = _make_response(201, _refresh_response_json(new_token))
        _set_post_response(session, resp)

        client = SmartyPlantsClient(session)
        # Use an expired access token so refresh is needed
        client.set_tokens(
            access_token="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0IiwiZXhwIjowfQ.x",
            refresh_token=MOCK_REFRESH_TOKEN,
        )

        result = await client.async_refresh_access_token()

        if isinstance(result, Exception):
            raise result
        assert result == new_token

    async def test_refresh_expired_refresh_token(self) -> None:
        """Test refresh with expired refresh token raises AuthError."""
        session = _make_session()
        resp = _make_response(
            401,
            {"success": False, "message": "Refresh token expired"},
        )
        _set_post_response(session, resp)

        client = SmartyPlantsClient(session)
        client.set_tokens(
            access_token="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0IiwiZXhwIjowfQ.x",
            refresh_token=MOCK_REFRESH_TOKEN,
        )

        with pytest.raises(SmartyPlantsAuthError):
            await client.async_refresh_access_token()

    async def test_refresh_calls_callback(self) -> None:
        """Test that token refresh calls the callback with new tokens."""
        session = _make_session()
        new_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIwMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDEiLCJleHAiOjk5OTk5OTk5OTl9.new"  # noqa: E501
        resp = _make_response(201, _refresh_response_json(new_token))
        _set_post_response(session, resp)

        callback = AsyncMock()
        client = SmartyPlantsClient(session)
        client.set_tokens(
            access_token="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ0ZXN0IiwiZXhwIjowfQ.x",
            refresh_token=MOCK_REFRESH_TOKEN,
        )
        client.set_token_updated_callback(callback)

        await client.async_refresh_access_token()

        callback.assert_awaited_once_with(new_token, MOCK_REFRESH_TOKEN)


class TestParsingHelpers:
    """Tests for parsing helper functions."""

    def test_parse_reading_normal(self) -> None:
        """Test parsing a normal sensor reading."""
        raw = {"value": 22.5, "status": "OPTIMAL", "message": "Looking good"}
        result = _parse_reading(raw)
        assert result is not None
        assert result.value == 22.5
        assert result.status == "OPTIMAL"
        assert result.message == "Looking good"

    def test_parse_reading_none(self) -> None:
        """Test parsing None returns None."""
        result = _parse_reading(None)
        assert result is None

    def test_parse_reading_null_value(self) -> None:
        """Test parsing a reading with null value."""
        raw = {"value": None, "status": "LOW", "message": "No data"}
        result = _parse_reading(raw)
        assert result is not None
        assert result.value is None
        assert result.status == "LOW"

    def test_parse_reading_non_numeric_value(self) -> None:
        """Test that a non-numeric value (e.g. nutrient text) yields value None."""
        raw = {"value": "Well fertilised", "status": "OPTIMAL", "message": "Great"}
        result = _parse_reading(raw)
        assert result is not None
        assert result.value is None
        assert result.status == "OPTIMAL"
        assert result.message == "Great"

    def test_parse_thresholds_temperature(self) -> None:
        """Test parsing temperature thresholds."""
        configs = [
            {
                "variant": "TEMPERATURE",
                "valueOne": 10.0,
                "valueTwo": 18.0,
                "valueThree": 27.0,
                "valueFour": 35.0,
            }
        ]
        result = _parse_thresholds(configs)
        assert "temperature" in result
        t = result["temperature"]
        assert t.critical_low == 10.0
        assert t.low_optimal == 18.0
        assert t.high_optimal == 27.0
        assert t.critical_high == 35.0

    def test_parse_thresholds_salinity_maps_to_nutrient(self) -> None:
        """Test that SALINITY variant maps to 'nutrient' key."""
        configs = [
            {
                "variant": "SALINITY",
                "valueOne": 0.5,
                "valueTwo": 1.0,
                "valueThree": 2.5,
                "valueFour": 3.5,
            }
        ]
        result = _parse_thresholds(configs)
        assert "nutrient" in result
        assert "salinity" not in result

    def test_parse_sensor_entry_no_current_plant_returns_none(self) -> None:
        """Test that a /sensors entry with no assigned plant returns None."""
        entry = {
            "sensor": {"id": MOCK_SENSOR_ID, "isOnline": True},
            "currentPlant": None,
        }
        result = _parse_sensor_entry(entry)
        assert result is None

    def test_parse_sensor_entry_full(self) -> None:
        """Test parsing a /sensors entry into base plant data (no metadata yet)."""
        result = _parse_sensor_entry(_sensors_entry())
        assert result is not None
        assert result.plant_id == MOCK_PLANT_ID
        assert result.name == "Monstera"
        assert result.image_url == "https://example.com/monstera.jpg"
        assert result.sensor_id == MOCK_SENSOR_ID
        assert result.sensor_identifier == "SP-001"
        assert result.sensor_online is True

        # Sensor readings come straight from lastSensorStatusData.
        assert result.temperature is not None
        assert result.temperature.value == 22.5
        assert result.humidity is not None
        assert result.humidity.value == 55.0
        assert result.moisture is not None  # from waterLevel
        assert result.moisture.value == 40.0
        assert result.light is not None
        assert result.light.value == 800.0
        assert result.battery is not None  # from batteryPercent
        assert result.battery.value == 85.0
        assert result.voltage is not None
        assert result.voltage.value == 3.7
        # nutrient is textual -> reading present, value None
        assert result.nutrient is not None
        assert result.nutrient.value is None
        assert result.nutrient.status == "OPTIMAL"

        # Metadata is not available from /sensors and must default empty.
        assert result.species == ""
        assert result.common_names == []
        assert result.thresholds == {}
        assert result.environment_name is None

    def test_parse_metadata(self) -> None:
        """Test extracting species/thresholds/environment from plant detail."""
        meta = _parse_metadata(_plant_detail_data())
        assert meta.species == "Monstera deliciosa"
        assert meta.common_names == ["Swiss cheese plant"]
        assert meta.environment_name == "Living Room"
        assert "temperature" in meta.thresholds
        assert "nutrient" in meta.thresholds  # SALINITY maps to nutrient
        assert meta.thresholds["temperature"].critical_low == 10.0
        assert meta.thresholds["nutrient"].critical_low == 0.5


class TestGetPlants:
    """Tests for fetching plants (hybrid: /sensors + cached /plant/{id})."""

    async def test_get_plants_merges_readings_and_metadata(self) -> None:
        """Readings come from /sensors, species/thresholds from plant detail."""
        session = _make_session()
        _set_get_responses(
            session,
            [
                _make_response(200, _sensors_response([_sensors_entry()])),
                _make_response(200, _plant_detail_response()),
            ],
        )

        client = SmartyPlantsClient(session)
        client.set_tokens(MOCK_ACCESS_TOKEN, MOCK_REFRESH_TOKEN)

        result = await client.async_get_plants()

        assert len(result) == 1
        plant = result[0]
        assert plant.plant_id == MOCK_PLANT_ID
        assert plant.name == "Monstera"
        # Readings from /sensors
        assert plant.temperature is not None
        assert plant.temperature.value == 22.5
        assert plant.moisture is not None
        assert plant.moisture.value == 40.0
        # Metadata from /plant/{id}
        assert plant.species == "Monstera deliciosa"
        assert plant.common_names == ["Swiss cheese plant"]
        assert plant.environment_name == "Living Room"
        assert "temperature" in plant.thresholds
        assert plant.thresholds["temperature"].critical_low == 10.0

    async def test_get_plants_skips_unassigned_sensors(self) -> None:
        """A sensor with no assigned plant is skipped (no detail fetched)."""
        session = _make_session()
        _set_get_responses(
            session,
            [
                _make_response(
                    200,
                    _sensors_response(
                        [
                            _sensors_entry(),
                            {
                                "sensor": {"id": "orphan", "isOnline": False},
                                "currentPlant": None,
                            },
                        ]
                    ),
                ),
                _make_response(200, _plant_detail_response()),
            ],
        )

        client = SmartyPlantsClient(session)
        client.set_tokens(MOCK_ACCESS_TOKEN, MOCK_REFRESH_TOKEN)

        result = await client.async_get_plants()
        assert len(result) == 1
        # /sensors + one detail fetch only
        assert session.get.call_count == 2

    async def test_get_plants_caches_metadata_across_calls(self) -> None:
        """Plant detail is fetched once and reused on subsequent polls."""
        session = _make_session()
        _set_get_responses(
            session,
            [
                _make_response(200, _sensors_response([_sensors_entry()])),
                _make_response(200, _plant_detail_response()),
                _make_response(200, _sensors_response([_sensors_entry()])),
            ],
        )

        client = SmartyPlantsClient(session)
        client.set_tokens(MOCK_ACCESS_TOKEN, MOCK_REFRESH_TOKEN)

        first = await client.async_get_plants()
        second = await client.async_get_plants()

        # 2 x /sensors + 1 x /plant/{id}
        assert session.get.call_count == 3
        assert first[0].species == "Monstera deliciosa"
        assert second[0].species == "Monstera deliciosa"

    async def test_get_plants_pagination(self) -> None:
        """Test that /sensors pagination fetches all pages."""
        session = _make_session()
        _set_get_responses(
            session,
            [
                _make_response(
                    200,
                    _sensors_response([_sensors_entry()], has_next=True),
                ),
                _make_response(
                    200,
                    _sensors_response(
                        [_sensors_entry(plant_id="plant-2", name="Ficus")],
                    ),
                ),
                _make_response(200, _plant_detail_response()),
                _make_response(
                    200, _plant_detail_response(plant_id="plant-2", name="Ficus")
                ),
            ],
        )

        client = SmartyPlantsClient(session)
        client.set_tokens(MOCK_ACCESS_TOKEN, MOCK_REFRESH_TOKEN)

        result = await client.async_get_plants()
        assert len(result) == 2
        ids = {p.plant_id for p in result}
        assert ids == {MOCK_PLANT_ID, "plant-2"}


class TestGetRequiresAttention:
    """Tests for fetching plants that require attention."""

    async def test_requires_attention_returns_ids(self) -> None:
        """Test that requires-attention returns a set of plant IDs."""
        session = _make_session()

        resp = _make_response(
            200,
            {
                "success": True,
                "data": [
                    {"id": "plant-1", "name": "Monstera"},
                    {"id": "plant-2", "name": "Ficus"},
                ],
                "meta": {"page": 1, "limit": 50, "hasNextPage": False},
            },
        )
        _set_get_response(session, resp)

        client = SmartyPlantsClient(session)
        client.set_tokens(MOCK_ACCESS_TOKEN, MOCK_REFRESH_TOKEN)

        result = await client.async_get_requires_attention()

        if isinstance(result, Exception):
            raise result
        assert result == {"plant-1", "plant-2"}
