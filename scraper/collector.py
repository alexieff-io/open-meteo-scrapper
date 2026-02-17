"""Open-Meteo API client for collecting weather and air quality data."""

from __future__ import annotations

import asyncio
import json as json_mod
from datetime import datetime, timezone
from typing import Any

import aiohttp
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from scraper.models import Location, MetricPoint
from scraper.validation import validate_url

logger = structlog.get_logger()

_MAX_RESPONSE_SIZE = 10 * 1024 * 1024  # 10 MB

_CURRENT_WEATHER_FIELD_MAP = {
    "temperature": "weather_temperature_celsius",
    "windspeed": "weather_wind_speed_kmh",
    "winddirection": "weather_wind_direction_degrees",
    "weathercode": "weather_condition_code",
    "is_day": "weather_is_day",
}


class OpenMeteoCollector:
    """Async client for the Open-Meteo API with retry logic."""

    def __init__(self, base_url: str, request_timeout: int = 30) -> None:
        validate_url(base_url, "open_meteo.base_url")
        self._base_url = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=request_timeout)
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Lazily create the aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
        reraise=True,
    )
    async def _fetch(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        """Fetch JSON data from an Open-Meteo endpoint with retries.

        Args:
            endpoint: API path, e.g. ``/v1/forecast``.
            params: Query parameters.

        Returns:
            Parsed JSON response body.

        Raises:
            aiohttp.ClientError: On HTTP-level failures after retries.
            ValueError: When the Open-Meteo API returns an error payload.
        """
        session = await self._ensure_session()
        url = f"{self._base_url}{endpoint}"
        logger.debug("fetching_open_meteo", url=url, params=params)

        async with session.get(url, params=params) as response:
            response.raise_for_status()
            body = await response.read()
            if len(body) > _MAX_RESPONSE_SIZE:
                raise ValueError(
                    f"Response too large ({len(body)} bytes, "
                    f"max {_MAX_RESPONSE_SIZE})"
                )
            data = json_mod.loads(body)

        if data.get("error"):
            reason = data.get("reason", "unknown")
            raise ValueError(f"Open-Meteo API error: {reason}")

        return data

    async def collect_current_weather(self, location: Location) -> list[MetricPoint]:
        """Collect current weather conditions for a location.

        Args:
            location: The target location.

        Returns:
            List of metric points for the current weather snapshot.
        """
        params = {
            "latitude": location.latitude,
            "longitude": location.longitude,
            "timezone": location.timezone,
            "current_weather": "true",
        }
        data = await self._fetch("/v1/forecast", params)
        current = data.get("current_weather", {})

        if not current:
            logger.warning("no_current_weather_data", location=location.name)
            return []

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        labels = location.base_labels()
        metrics: list[MetricPoint] = []

        for api_field, metric_name in _CURRENT_WEATHER_FIELD_MAP.items():
            value = current.get(api_field)
            if value is not None:
                metrics.append(MetricPoint(
                    name=metric_name,
                    value=float(value),
                    timestamp_ms=now_ms,
                    labels=labels,
                ))

        return metrics

    async def collect_hourly(
        self, location: Location, variables: list[str]
    ) -> list[MetricPoint]:
        """Collect hourly forecast data for a location.

        Args:
            location: The target location.
            variables: List of hourly variable names to request.

        Returns:
            List of metric points for each variable and timestep.
        """
        params = {
            "latitude": location.latitude,
            "longitude": location.longitude,
            "timezone": location.timezone,
            "hourly": ",".join(variables),
            "past_days": 1,
            "forecast_days": 2,
        }
        data = await self._fetch("/v1/forecast", params)
        hourly = data.get("hourly", {})
        time_array = hourly.get("time", [])

        if not time_array:
            logger.warning("no_hourly_data", location=location.name)
            return []

        base_labels = {**location.base_labels(), "frequency": "hourly"}
        metrics: list[MetricPoint] = []

        for i, time_str in enumerate(time_array):
            ts_ms = _parse_iso_timestamp_ms(time_str)
            for var in variables:
                values = hourly.get(var, [])
                if i < len(values) and values[i] is not None:
                    metrics.append(MetricPoint(
                        name=f"weather_{var}",
                        value=float(values[i]),
                        timestamp_ms=ts_ms,
                        labels=base_labels,
                    ))

        return metrics

    async def collect_daily(
        self, location: Location, variables: list[str]
    ) -> list[MetricPoint]:
        """Collect daily forecast data for a location.

        Args:
            location: The target location.
            variables: List of daily variable names to request.

        Returns:
            List of metric points for each variable and day.
        """
        params = {
            "latitude": location.latitude,
            "longitude": location.longitude,
            "timezone": location.timezone,
            "daily": ",".join(variables),
            "forecast_days": 7,
        }
        data = await self._fetch("/v1/forecast", params)
        daily = data.get("daily", {})
        time_array = daily.get("time", [])

        if not time_array:
            logger.warning("no_daily_data", location=location.name)
            return []

        base_labels = {**location.base_labels(), "frequency": "daily"}
        metrics: list[MetricPoint] = []

        for i, time_str in enumerate(time_array):
            ts_ms = _parse_iso_timestamp_ms(time_str)
            for var in variables:
                values = daily.get(var, [])
                if i < len(values) and values[i] is not None:
                    if isinstance(values[i], str):
                        continue
                    metrics.append(MetricPoint(
                        name=f"weather_daily_{var}",
                        value=float(values[i]),
                        timestamp_ms=ts_ms,
                        labels=base_labels,
                    ))

        return metrics

    async def collect_air_quality(
        self, location: Location, variables: list[str]
    ) -> list[MetricPoint]:
        """Collect air quality data for a location.

        Args:
            location: The target location.
            variables: List of air quality variable names to request.

        Returns:
            List of metric points for each pollutant and timestep.
        """
        params = {
            "latitude": location.latitude,
            "longitude": location.longitude,
            "timezone": location.timezone,
            "hourly": ",".join(variables),
        }
        data = await self._fetch("/v1/air-quality", params)
        hourly = data.get("hourly", {})
        time_array = hourly.get("time", [])

        if not time_array:
            logger.warning("no_air_quality_data", location=location.name)
            return []

        base_labels = location.base_labels()
        metrics: list[MetricPoint] = []

        for i, time_str in enumerate(time_array):
            ts_ms = _parse_iso_timestamp_ms(time_str)
            for var in variables:
                values = hourly.get(var, [])
                if i < len(values) and values[i] is not None:
                    metrics.append(MetricPoint(
                        name=f"air_quality_{var}",
                        value=float(values[i]),
                        timestamp_ms=ts_ms,
                        labels=base_labels,
                    ))

        return metrics


def _parse_iso_timestamp_ms(time_str: str) -> int:
    """Parse an ISO 8601 timestamp string to epoch milliseconds.

    Handles both ``2024-01-15T12:00`` and ``2024-01-15`` formats.
    """
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(time_str, fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    raise ValueError(f"Unable to parse timestamp: {time_str}")
