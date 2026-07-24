"""API client for the SmartyPlants service."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import aiohttp

from .const import API_BASE_URL
from .exceptions import (
    SmartyPlantsAuthError,
    SmartyPlantsConnectionError,
    SmartyPlantsError,
)
from .models import PlantData, PlantThresholds, SensorReading

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

_LOGGER = logging.getLogger(__name__)

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
_TOKEN_EXPIRY_BUFFER = 300
_PAGE_LIMIT = 50

_VARIANT_MAP: dict[str, str] = {
    "TEMPERATURE": "temperature",
    "HUMIDITY": "humidity",
    "LIGHT": "light",
    "SALINITY": "nutrient",
    "MOISTURE": "moisture",
}


def _coerce_float(value: object) -> float | None:
    """Coerce an API value to float, or None if it isn't numeric.

    Some readings (e.g. nutrient "Well fertilised" or a bare "-") are text,
    so we keep the reading for its status/message but leave value as None.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _parse_reading(raw: dict[str, object] | None) -> SensorReading | None:
    """Parse a raw sensor reading dict into a SensorReading, or None."""
    if raw is None:
        return None
    return SensorReading(
        value=_coerce_float(raw.get("value")),
        status=str(raw.get("status", "")),
        message=str(raw.get("message", "")),
    )


def _parse_thresholds(configs: list[dict[str, object]]) -> dict[str, PlantThresholds]:
    """Parse plantConfigurations into a mapping of variant name to thresholds."""
    result: dict[str, PlantThresholds] = {}
    for cfg in configs:
        variant_raw = cfg.get("variant")
        if not isinstance(variant_raw, str):
            continue
        key = _VARIANT_MAP.get(variant_raw)
        if key is None:
            continue
        result[key] = PlantThresholds(
            critical_low=float(cfg.get("valueOne", 0)),
            low_optimal=float(cfg.get("valueTwo", 0)),
            high_optimal=float(cfg.get("valueThree", 0)),
            critical_high=float(cfg.get("valueFour", 0)),
        )
    return result


@dataclass
class _PlantMetadata:
    """Rarely-changing plant metadata sourced from the /plant/{id} detail."""

    species: str = ""
    common_names: list[str] = field(default_factory=list)
    environment_name: str | None = None
    thresholds: dict[str, PlantThresholds] = field(default_factory=dict)


def _parse_metadata(detail: dict[str, object]) -> _PlantMetadata:
    """Extract species, common names, environment, and thresholds from detail.

    The detail is the `data` block of a GET /plant/{id} response.
    """
    plant_ref = detail.get("plantReference")
    if not isinstance(plant_ref, dict):
        plant_ref = {}

    genus = str(plant_ref.get("genus", ""))
    species_epithet = str(plant_ref.get("scientificNameWithoutAuthor", ""))
    species = f"{genus} {species_epithet}".strip() if genus else species_epithet

    common_names_raw = plant_ref.get("commonNames")
    common_names = list(common_names_raw) if isinstance(common_names_raw, list) else []

    configs_raw = plant_ref.get("plantConfigurations")
    configs = list(configs_raw) if isinstance(configs_raw, list) else []
    thresholds = _parse_thresholds(configs)

    environment = detail.get("environment")
    environment_name: str | None = None
    if isinstance(environment, dict):
        env_name = environment.get("name")
        if isinstance(env_name, str) and env_name:
            environment_name = env_name

    return _PlantMetadata(
        species=species,
        common_names=common_names,
        environment_name=environment_name,
        thresholds=thresholds,
    )


def _parse_sensor_entry(entry: dict[str, object]) -> PlantData | None:
    """Parse a /sensors entry into PlantData, or None if unassigned.

    Readings come from the sensor's lastSensorStatusData. Metadata fields
    (species, common_names, environment_name, thresholds) are left empty and
    filled in separately from the plant detail endpoint.
    """
    sensor = entry.get("sensor")
    if not isinstance(sensor, dict):
        return None

    current = entry.get("currentPlant")
    if not isinstance(current, dict):
        return None

    plant_id = str(current.get("id", ""))
    if not plant_id:
        return None

    status_data = sensor.get("lastSensorStatusData")
    sensor_data = status_data if isinstance(status_data, dict) else {}

    def _reading(key: str) -> SensorReading | None:
        val = sensor_data.get(key)
        return _parse_reading(val) if isinstance(val, dict) else None

    image_url = current.get("imageUrl")

    return PlantData(
        plant_id=plant_id,
        name=str(current.get("name", "")),
        species="",
        common_names=[],
        image_url=image_url if isinstance(image_url, str) else None,
        sensor_id=(str(sensor.get("id", "")) if sensor.get("id") else None),
        sensor_identifier=(
            str(sensor.get("identifier", "")) if sensor.get("identifier") else None
        ),
        sensor_online=bool(sensor.get("isOnline", False)),
        temperature=_reading("temperature"),
        humidity=_reading("humidity"),
        moisture=_reading("waterLevel"),
        light=_reading("light"),
        nutrient=_reading("nutrient"),
        battery=_reading("batteryPercent"),
        voltage=_reading("voltage"),
    )


class SmartyPlantsClient:
    """Client for the SmartyPlants API."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialize the client."""
        self._session = session
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._metadata_cache: dict[str, _PlantMetadata] = {}
        self._lock = asyncio.Lock()
        self._token_updated_callback: (
            Callable[[str, str | None], Coroutine[Any, Any, None]] | None
        ) = None

    def set_tokens(self, access_token: str, refresh_token: str) -> None:
        """Set the current access and refresh tokens."""
        self._access_token = access_token
        self._refresh_token = refresh_token

    def set_token_updated_callback(
        self,
        callback: Callable[[str, str | None], Coroutine[Any, Any, None]],
    ) -> None:
        """Set a callback to be called when tokens are updated."""
        self._token_updated_callback = callback

    @staticmethod
    def _is_token_expired(token: str) -> bool:
        """Check if a JWT token is expired (with buffer)."""
        try:
            parts = token.split(".")
            if len(parts) < 2:
                return True
            # Base64url decode the payload
            payload_b64 = parts[1]
            # Add padding if needed
            padding = 4 - len(payload_b64) % 4
            if padding != 4:
                payload_b64 += "=" * padding
            payload_bytes = base64.urlsafe_b64decode(payload_b64)
            payload = json.loads(payload_bytes)
            exp = payload.get("exp")
            if exp is None:
                return True
            return time.time() >= (exp - _TOKEN_EXPIRY_BUFFER)
        except (ValueError, KeyError, json.JSONDecodeError):
            return True

    async def _async_ensure_token_valid(self) -> None:
        """Ensure the access token is valid, refreshing if needed."""
        if self._access_token is None:
            raise SmartyPlantsAuthError("No access token available")
        if self._is_token_expired(self._access_token):
            await self.async_refresh_access_token()

    async def _async_post(
        self,
        path: str,
        data: dict[str, object],
        *,
        authenticated: bool = True,
    ) -> dict[str, object]:
        """Make an authenticated POST request."""
        if authenticated:
            await self._async_ensure_token_valid()

        headers: dict[str, str] = {}
        if authenticated and self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"

        url = f"{API_BASE_URL}{path}"
        try:
            async with self._session.post(
                url,
                json=data,
                headers=headers,
                timeout=_REQUEST_TIMEOUT,
            ) as resp:
                if resp.status in (401, 403, 406):
                    raise SmartyPlantsAuthError(f"Authentication failed: {resp.status}")
                if resp.status >= 400:
                    raise SmartyPlantsError(f"API error: {resp.status}")
                result = await resp.json()
                if not isinstance(result, dict):
                    raise SmartyPlantsError("Unexpected response format")
                return result
        except SmartyPlantsError:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SmartyPlantsConnectionError(f"Connection error: {err}") from err

    async def _async_get(
        self,
        path: str,
        params: dict[str, str | int] | None = None,
    ) -> dict[str, object]:
        """Make an authenticated GET request."""
        await self._async_ensure_token_valid()

        headers: dict[str, str] = {}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"

        url = f"{API_BASE_URL}{path}"
        try:
            async with self._session.get(
                url,
                params=params,
                headers=headers,
                timeout=_REQUEST_TIMEOUT,
            ) as resp:
                if resp.status in (401, 403):
                    raise SmartyPlantsAuthError(f"Authentication failed: {resp.status}")
                if resp.status >= 400:
                    raise SmartyPlantsError(f"API error: {resp.status}")
                result = await resp.json()
                if not isinstance(result, dict):
                    raise SmartyPlantsError("Unexpected response format")
                return result
        except SmartyPlantsError:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SmartyPlantsConnectionError(f"Connection error: {err}") from err

    async def _async_get_paginated(
        self,
        path: str,
    ) -> list[dict[str, object]]:
        """Fetch all pages from a paginated GET endpoint."""
        all_items: list[dict[str, object]] = []
        page = 1
        while True:
            result = await self._async_get(
                path, params={"page": page, "limit": _PAGE_LIMIT}
            )
            data = result.get("data")
            if isinstance(data, list):
                all_items.extend(data)

            meta = result.get("meta")
            if not isinstance(meta, dict) or not meta.get("hasNextPage"):
                break
            page += 1
        return all_items

    async def async_login(self, email: str, password: str) -> dict[str, str]:
        """Log in and return user_id, access_token, refresh_token."""
        result = await self._async_post(
            "/auth/login",
            {"emailAddress": email, "password": password},
            authenticated=False,
        )
        data = result.get("data")
        if not isinstance(data, dict):
            raise SmartyPlantsError("Unexpected login response format")

        user = data.get("user")
        token = data.get("token")
        if not isinstance(user, dict) or not isinstance(token, dict):
            raise SmartyPlantsError("Unexpected login response format")

        access_token = str(token["accessToken"])
        refresh_token = str(token["refreshToken"])

        self._access_token = access_token
        self._refresh_token = refresh_token

        return {
            "user_id": str(user["userId"]),
            "access_token": access_token,
            "refresh_token": refresh_token,
        }

    async def async_refresh_access_token(self) -> str:
        """Refresh the access token using the refresh token."""
        async with self._lock:
            # Re-check after acquiring lock - another coroutine may have refreshed
            if self._access_token is not None and not self._is_token_expired(
                self._access_token
            ):
                return self._access_token

            if self._refresh_token is None:
                raise SmartyPlantsAuthError("No refresh token available")

            result = await self._async_post(
                "/auth/refresh-token",
                {"refreshToken": self._refresh_token},
                authenticated=False,
            )
            data = result.get("data")
            if not isinstance(data, dict):
                raise SmartyPlantsError("Unexpected refresh response format")

            token = data.get("token")
            if not isinstance(token, dict):
                raise SmartyPlantsError("Unexpected refresh response format")

            new_access_token = str(token["accessToken"])
            self._access_token = new_access_token

            # refreshToken may be null in response
            new_refresh = token.get("refreshToken")
            if new_refresh is not None:
                self._refresh_token = str(new_refresh)

            if self._token_updated_callback is not None:
                await self._token_updated_callback(
                    new_access_token, self._refresh_token
                )

            return new_access_token

    async def async_get_plants(self) -> list[PlantData]:
        """Fetch all plants, merging live readings with cached metadata.

        Readings come from /sensors (one request for all plants). Species,
        common names, environment, and thresholds come from the per-plant
        /plant/{id} detail, which is fetched once and cached since it rarely
        changes.
        """
        entries = await self._async_get_paginated("/sensors")
        plants: list[PlantData] = []
        for entry in entries:
            plant = _parse_sensor_entry(entry)
            if plant is None:
                continue
            metadata = await self._async_get_plant_metadata(plant.plant_id)
            plant.species = metadata.species
            plant.common_names = metadata.common_names
            plant.environment_name = metadata.environment_name
            plant.thresholds = metadata.thresholds
            plants.append(plant)
        return plants

    async def _async_get_plant_metadata(self, plant_id: str) -> _PlantMetadata:
        """Return cached plant metadata, fetching /plant/{id} once per plant.

        Metadata is best-effort: if the detail request fails (other than an
        auth error, which must propagate for reauth), the plant is still
        returned with its readings and the fetch is retried on the next poll.
        """
        cached = self._metadata_cache.get(plant_id)
        if cached is not None:
            return cached

        try:
            detail = await self._async_get(f"/plant/{plant_id}")
        except SmartyPlantsAuthError:
            raise
        except SmartyPlantsError:
            return _PlantMetadata()

        data = detail.get("data")
        metadata = _parse_metadata(data if isinstance(data, dict) else {})
        self._metadata_cache[plant_id] = metadata
        return metadata

    async def async_get_requires_attention(self) -> set[str]:
        """Fetch plant IDs that require attention."""
        raw_items = await self._async_get_paginated("/plant/requires-attention")
        return {str(item.get("id", "")) for item in raw_items if item.get("id")}
