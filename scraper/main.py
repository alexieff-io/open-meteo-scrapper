"""Main entrypoint for the weather scraper application."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time

import structlog
from prometheus_client import Counter, Gauge, Histogram, start_http_server

from scraper.collector import OpenMeteoCollector
from scraper.config import AppConfig, load_config, log_config
from scraper.exporter import VictoriaMetricsExporter
from scraper.models import Location

SCRAPE_DURATION = Histogram(
    "weather_scrape_duration_seconds",
    "Duration of weather data scrape operations",
    labelnames=["scrape_type", "location"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)
SCRAPE_ERRORS = Counter(
    "weather_scrape_errors_total",
    "Total number of scrape errors",
    labelnames=["scrape_type", "location"],
)
METRICS_EXPORTED = Counter(
    "weather_metrics_exported_total",
    "Total number of individual metrics exported to Victoria Metrics",
)
LAST_SCRAPE_TIMESTAMP = Gauge(
    "weather_last_scrape_timestamp",
    "Unix timestamp of the last successful scrape",
    labelnames=["scrape_type"],
)


def _configure_logging(level: str, fmt: str) -> None:
    """Set up structlog with the chosen renderer and log level."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    if fmt == "console":
        renderer = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


class WeatherScraper:
    """Orchestrates concurrent weather data collection and export loops."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._running = True
        self._logger = structlog.get_logger()
        self._scrape_semaphore = asyncio.Semaphore(config.scrape.max_concurrent_locations)

        self._locations = [
            Location(
                name=loc.name,
                latitude=loc.latitude,
                longitude=loc.longitude,
                timezone=loc.timezone,
                labels=loc.labels,
            )
            for loc in config.locations
        ]

        self._collector = OpenMeteoCollector(
            base_url=config.open_meteo.base_url,
            request_timeout=config.open_meteo.request_timeout,
        )
        self._exporter = VictoriaMetricsExporter(
            vm_url=config.victoria_metrics.url,
            import_endpoint=config.victoria_metrics.import_endpoint,
            batch_size=config.victoria_metrics.batch_size,
            request_timeout=config.victoria_metrics.request_timeout,
        )

    async def _scrape_current_weather(self) -> None:
        """Scrape loop for current weather conditions."""
        interval = self._config.scrape.current_weather_interval
        scrape_type = "current_weather"

        while self._running:
            await self._run_scrape_cycle(
                scrape_type=scrape_type,
                collect_fn=lambda loc: self._collector.collect_current_weather(loc),
            )
            await self._interruptible_sleep(interval)

    async def _scrape_hourly(self) -> None:
        """Scrape loop for hourly forecast data."""
        interval = self._config.scrape.hourly_forecast_interval
        variables = self._config.scrape.hourly_variables
        scrape_type = "hourly_forecast"

        while self._running:
            await self._run_scrape_cycle(
                scrape_type=scrape_type,
                collect_fn=lambda loc: self._collector.collect_hourly(loc, variables),
            )
            await self._interruptible_sleep(interval)

    async def _scrape_daily(self) -> None:
        """Scrape loop for daily forecast data."""
        interval = self._config.scrape.daily_forecast_interval
        variables = self._config.scrape.daily_variables
        scrape_type = "daily_forecast"

        while self._running:
            await self._run_scrape_cycle(
                scrape_type=scrape_type,
                collect_fn=lambda loc: self._collector.collect_daily(loc, variables),
            )
            await self._interruptible_sleep(interval)

    async def _scrape_air_quality(self) -> None:
        """Scrape loop for air quality data."""
        interval = self._config.scrape.air_quality_interval
        variables = self._config.scrape.air_quality_variables
        scrape_type = "air_quality"

        while self._running:
            await self._run_scrape_cycle(
                scrape_type=scrape_type,
                collect_fn=lambda loc: self._collector.collect_air_quality(loc, variables),
            )
            await self._interruptible_sleep(interval)

    async def _run_scrape_cycle(
        self,
        scrape_type: str,
        collect_fn: object,
    ) -> None:
        """Execute a single scrape cycle across all locations.

        Args:
            scrape_type: Identifier for logging and metrics labels.
            collect_fn: Async callable taking a Location and returning MetricPoints.
        """
        self._logger.info("scrape_cycle_start", scrape_type=scrape_type, locations=len(self._locations))
        cycle_start = time.monotonic()
        all_metrics = []

        async def _collect_with_limit(loc: Location) -> list:
            async with self._scrape_semaphore:
                return await collect_fn(loc)

        tasks = [_collect_with_limit(loc) for loc in self._locations]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for loc, result in zip(self._locations, results):
            if isinstance(result, Exception):
                SCRAPE_ERRORS.labels(scrape_type=scrape_type, location=loc.name).inc()
                self._logger.error(
                    "scrape_location_error",
                    scrape_type=scrape_type,
                    location=loc.name,
                    error=str(result),
                )
            else:
                duration = time.monotonic() - cycle_start
                SCRAPE_DURATION.labels(scrape_type=scrape_type, location=loc.name).observe(duration)
                all_metrics.extend(result)

        if all_metrics:
            try:
                await self._exporter.export(all_metrics)
                METRICS_EXPORTED.inc(len(all_metrics))
            except Exception as exc:
                self._logger.error(
                    "export_failed",
                    scrape_type=scrape_type,
                    metric_count=len(all_metrics),
                    error=str(exc),
                )

        LAST_SCRAPE_TIMESTAMP.labels(scrape_type=scrape_type).set_to_current_time()
        duration = time.monotonic() - cycle_start
        self._logger.info(
            "scrape_cycle_complete",
            scrape_type=scrape_type,
            metrics_collected=len(all_metrics),
            duration_seconds=round(duration, 3),
        )

    async def _interruptible_sleep(self, seconds: int) -> None:
        """Sleep in small increments so shutdown signals are handled promptly."""
        end = time.monotonic() + seconds
        while self._running and time.monotonic() < end:
            await asyncio.sleep(min(1.0, end - time.monotonic()))

    def _handle_shutdown(self, sig: signal.Signals) -> None:
        """Signal handler for graceful shutdown."""
        self._logger.info("shutdown_signal_received", signal=sig.name)
        self._running = False

    async def run(self) -> None:
        """Start all scrape loops and the Prometheus metrics server."""
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._handle_shutdown, sig)

        start_http_server(9090)
        self._logger.info("prometheus_metrics_server_started", port=9090)

        tasks = [
            asyncio.create_task(self._scrape_current_weather(), name="current_weather"),
            asyncio.create_task(self._scrape_hourly(), name="hourly_forecast"),
            asyncio.create_task(self._scrape_daily(), name="daily_forecast"),
            asyncio.create_task(self._scrape_air_quality(), name="air_quality"),
        ]

        self._logger.info(
            "scraper_started",
            locations=len(self._locations),
            scrape_tasks=len(tasks),
        )

        try:
            await asyncio.gather(*tasks)
        except Exception:
            self._logger.exception("unexpected_scraper_error")
        finally:
            self._logger.info("shutting_down")
            await self._collector.close()
            await self._exporter.close()
            self._logger.info("shutdown_complete")


def main() -> None:
    """Application entrypoint."""
    config = load_config()
    _configure_logging(config.logging.level, config.logging.format)

    logger = structlog.get_logger()
    logger.info("weather_scraper_starting")
    log_config(config)

    scraper = WeatherScraper(config)
    try:
        asyncio.run(scraper.run())
    except KeyboardInterrupt:
        logger.info("interrupted_by_user")
    sys.exit(0)


if __name__ == "__main__":
    main()
