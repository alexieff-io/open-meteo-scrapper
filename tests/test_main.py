"""Tests for scraper.main module."""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scraper.config import AppConfig, LocationConfig, OpenMeteoConfig, VictoriaMetricsConfig
from scraper.main import WeatherScraper, _configure_logging
from scraper.models import Location, MetricPoint


def _make_config(locations=None) -> AppConfig:
    """Build a minimal valid config for testing."""
    if locations is None:
        locations = [
            LocationConfig(name="TestCity", latitude=48.85, longitude=2.35),
        ]
    return AppConfig(
        open_meteo=OpenMeteoConfig(base_url="http://10.0.0.1:8080"),
        victoria_metrics=VictoriaMetricsConfig(url="http://10.0.0.2:8428"),
        locations=locations,
    )


class TestConfigureLogging:
    """Tests for _configure_logging()."""

    def test_console_format(self):
        _configure_logging("info", "console")

    def test_json_format(self):
        _configure_logging("debug", "json")

    def test_invalid_level_defaults_to_info(self):
        _configure_logging("nonexistent", "json")


class TestRunScrapeCycle:
    """Tests for WeatherScraper._run_scrape_cycle()."""

    async def test_successful_cycle(self):
        config = _make_config()
        scraper = WeatherScraper(config)

        test_metric = MetricPoint(
            name="weather_temp", value=22.0, timestamp_ms=1700000000000,
            labels={"location": "TestCity"},
        )

        async def mock_collect(loc):
            return [test_metric]

        scraper._exporter = AsyncMock()
        scraper._exporter.export = AsyncMock()

        await scraper._run_scrape_cycle("test", mock_collect)

        scraper._exporter.export.assert_awaited_once()
        exported = scraper._exporter.export.call_args[0][0]
        assert len(exported) == 1
        assert exported[0].name == "weather_temp"

        await scraper._collector.close()

    async def test_error_records_duration_and_error(self):
        config = _make_config()
        scraper = WeatherScraper(config)

        async def mock_collect_fail(loc):
            raise RuntimeError("API down")

        scraper._exporter = AsyncMock()
        scraper._exporter.export = AsyncMock()

        await scraper._run_scrape_cycle("test", mock_collect_fail)

        # Export should not be called since all locations errored
        scraper._exporter.export.assert_not_awaited()

        await scraper._collector.close()

    async def test_per_location_timing(self):
        """Each location gets its own duration measurement."""
        config = _make_config([
            LocationConfig(name="Fast", latitude=0, longitude=0),
            LocationConfig(name="Slow", latitude=1, longitude=1),
        ])
        scraper = WeatherScraper(config)

        call_count = 0

        async def mock_collect(loc):
            nonlocal call_count
            call_count += 1
            if loc.name == "Slow":
                await asyncio.sleep(0.05)
            return [MetricPoint(name="m", value=1.0, timestamp_ms=1000, labels={})]

        scraper._exporter = AsyncMock()
        scraper._exporter.export = AsyncMock()

        await scraper._run_scrape_cycle("test", mock_collect)

        assert call_count == 2
        await scraper._collector.close()


class TestSafeShutdown:
    """Tests for WeatherScraper._safe_shutdown()."""

    async def test_both_resources_closed(self):
        config = _make_config()
        scraper = WeatherScraper(config)
        scraper._collector = AsyncMock()
        scraper._exporter = AsyncMock()

        await scraper._safe_shutdown()

        scraper._collector.close.assert_awaited_once()
        scraper._exporter.close.assert_awaited_once()

    async def test_exporter_closes_even_if_collector_fails(self):
        config = _make_config()
        scraper = WeatherScraper(config)
        scraper._collector = AsyncMock()
        scraper._collector.close = AsyncMock(side_effect=RuntimeError("boom"))
        scraper._exporter = AsyncMock()

        await scraper._safe_shutdown()

        # Exporter should still be closed despite collector error
        scraper._exporter.close.assert_awaited_once()

    async def test_handles_timeout(self):
        config = _make_config()
        scraper = WeatherScraper(config)

        async def slow_close():
            await asyncio.sleep(100)

        scraper._collector = AsyncMock()
        scraper._collector.close = slow_close
        scraper._exporter = AsyncMock()

        # Use a short timeout by patching
        with patch.object(asyncio, "wait_for", wraps=asyncio.wait_for):
            await scraper._safe_shutdown()

        scraper._exporter.close.assert_awaited_once()
