"""Tests for scraper.collector module."""

from __future__ import annotations

import re

import aiohttp
import pytest
from aioresponses import aioresponses

from scraper.collector import OpenMeteoCollector, _parse_iso_timestamp_ms
from scraper.models import Location

# Patterns that match the base URL regardless of query params
_FORECAST_PATTERN = re.compile(r"^http://10\.0\.0\.1:8080/v1/forecast")
_AIR_QUALITY_PATTERN = re.compile(r"^http://10\.0\.0\.1:8080/v1/air-quality")


@pytest.fixture
def collector():
    """Create a collector with a test base URL."""
    return OpenMeteoCollector(
        base_url="http://10.0.0.1:8080",
        request_timeout=5,
    )


@pytest.fixture
def sample_loc():
    return Location(
        name="TestCity",
        latitude=48.85,
        longitude=2.35,
        timezone="UTC",
    )


class TestParseIsoTimestampMs:
    """Tests for _parse_iso_timestamp_ms()."""

    def test_datetime_format(self):
        ms = _parse_iso_timestamp_ms("2024-01-15T12:00")
        assert ms == 1705320000000

    def test_date_only_format(self):
        ms = _parse_iso_timestamp_ms("2024-01-15")
        assert ms == 1705276800000

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError, match="Unable to parse"):
            _parse_iso_timestamp_ms("not-a-date")


class TestFetch:
    """Tests for OpenMeteoCollector._fetch()."""

    async def test_fetch_success(self, collector):
        with aioresponses() as m:
            m.get(
                _FORECAST_PATTERN,
                payload={"current_weather": {"temperature": 20}},
            )
            result = await collector._fetch("/v1/forecast", {"latitude": 48.85})
            assert result["current_weather"]["temperature"] == 20
        await collector.close()

    async def test_fetch_api_error(self, collector):
        with aioresponses() as m:
            m.get(
                _FORECAST_PATTERN,
                payload={"error": True, "reason": "bad params"},
            )
            with pytest.raises(ValueError, match="bad params"):
                await collector._fetch("/v1/forecast", {})
        await collector.close()

    async def test_fetch_http_error(self, collector):
        with aioresponses() as m:
            m.get(_FORECAST_PATTERN, status=500, repeat=True)
            with pytest.raises(aiohttp.ClientResponseError):
                await collector._fetch("/v1/forecast", {})
        await collector.close()

    async def test_fetch_oversized_response(self, collector):
        with aioresponses() as m:
            m.get(
                _FORECAST_PATTERN,
                body=b"x" * (10 * 1024 * 1024 + 1),
                repeat=True,
            )
            with pytest.raises(ValueError, match="Response too large"):
                await collector._fetch("/v1/forecast", {})
        await collector.close()


class TestCollectCurrentWeather:
    """Tests for collect_current_weather()."""

    async def test_collects_metrics(self, collector, sample_loc):
        with aioresponses() as m:
            m.get(
                _FORECAST_PATTERN,
                payload={
                    "current_weather": {
                        "temperature": 22.5,
                        "windspeed": 10.0,
                        "winddirection": 180,
                        "weathercode": 3,
                        "is_day": 1,
                    }
                },
            )
            metrics = await collector.collect_current_weather(sample_loc)
            assert len(metrics) == 5
            names = {m.name for m in metrics}
            assert "weather_temperature_celsius" in names
            assert "weather_wind_speed_kmh" in names
        await collector.close()

    async def test_empty_current_weather(self, collector, sample_loc):
        with aioresponses() as m:
            m.get(
                _FORECAST_PATTERN,
                payload={"current_weather": {}},
            )
            metrics = await collector.collect_current_weather(sample_loc)
            assert metrics == []
        await collector.close()


class TestCollectHourly:
    """Tests for collect_hourly()."""

    async def test_collects_hourly_metrics(self, collector, sample_loc):
        with aioresponses() as m:
            m.get(
                _FORECAST_PATTERN,
                payload={
                    "hourly": {
                        "time": ["2024-01-15T12:00", "2024-01-15T13:00"],
                        "temperature_2m": [20.0, 21.0],
                    }
                },
            )
            metrics = await collector.collect_hourly(sample_loc, ["temperature_2m"])
            assert len(metrics) == 2
            assert metrics[0].name == "weather_temperature_2m"
            assert metrics[0].labels["frequency"] == "hourly"
        await collector.close()

    async def test_empty_hourly_data(self, collector, sample_loc):
        with aioresponses() as m:
            m.get(
                _FORECAST_PATTERN,
                payload={"hourly": {"time": []}},
            )
            metrics = await collector.collect_hourly(sample_loc, ["temperature_2m"])
            assert metrics == []
        await collector.close()

    async def test_skips_none_values(self, collector, sample_loc):
        with aioresponses() as m:
            m.get(
                _FORECAST_PATTERN,
                payload={
                    "hourly": {
                        "time": ["2024-01-15T12:00"],
                        "temperature_2m": [None],
                    }
                },
            )
            metrics = await collector.collect_hourly(sample_loc, ["temperature_2m"])
            assert metrics == []
        await collector.close()


class TestCollectDaily:
    """Tests for collect_daily()."""

    async def test_collects_daily_metrics(self, collector, sample_loc):
        with aioresponses() as m:
            m.get(
                _FORECAST_PATTERN,
                payload={
                    "daily": {
                        "time": ["2024-01-15"],
                        "temperature_2m_max": [25.0],
                    }
                },
            )
            metrics = await collector.collect_daily(sample_loc, ["temperature_2m_max"])
            assert len(metrics) == 1
            assert metrics[0].name == "weather_daily_temperature_2m_max"
            assert metrics[0].labels["frequency"] == "daily"
        await collector.close()

    async def test_skips_string_values(self, collector, sample_loc):
        with aioresponses() as m:
            m.get(
                _FORECAST_PATTERN,
                payload={
                    "daily": {
                        "time": ["2024-01-15"],
                        "sunrise": ["2024-01-15T07:30"],
                    }
                },
            )
            metrics = await collector.collect_daily(sample_loc, ["sunrise"])
            assert metrics == []
        await collector.close()


class TestCollectAirQuality:
    """Tests for collect_air_quality()."""

    async def test_collects_air_quality(self, collector, sample_loc):
        with aioresponses() as m:
            m.get(
                _AIR_QUALITY_PATTERN,
                payload={
                    "hourly": {
                        "time": ["2024-01-15T12:00"],
                        "pm10": [15.0],
                        "pm2_5": [8.0],
                    }
                },
            )
            metrics = await collector.collect_air_quality(sample_loc, ["pm10", "pm2_5"])
            assert len(metrics) == 2
            names = {m.name for m in metrics}
            assert "air_quality_pm10" in names
            assert "air_quality_pm2_5" in names
        await collector.close()

    async def test_empty_air_quality(self, collector, sample_loc):
        with aioresponses() as m:
            m.get(
                _AIR_QUALITY_PATTERN,
                payload={"hourly": {"time": []}},
            )
            metrics = await collector.collect_air_quality(sample_loc, ["pm10"])
            assert metrics == []
        await collector.close()


class TestCollectorClose:
    """Tests for collector session management."""

    async def test_close_without_session(self, collector):
        await collector.close()  # Should not raise

    async def test_close_after_use(self, collector):
        with aioresponses() as m:
            m.get(
                _FORECAST_PATTERN,
                payload={"current_weather": {}},
            )
            await collector._fetch("/v1/forecast", {})
        await collector.close()
        assert collector._session is None
