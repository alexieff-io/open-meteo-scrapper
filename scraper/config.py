"""Configuration loader supporting YAML files, environment variables, and CLI args."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
import yaml

from scraper.validation import (
    sanitize_url,
    validate_labels,
    validate_location_name,
    validate_url,
)

logger = structlog.get_logger()

_DEFAULT_CONFIG_PATHS = [
    "/etc/weather-scraper/config.yml",
    "./config.yml",
]


@dataclass
class OpenMeteoConfig:
    """Open-Meteo connection settings."""

    base_url: str = "http://open-meteo:8080"
    request_timeout: int = 30


@dataclass
class VictoriaMetricsConfig:
    """Victoria Metrics connection settings."""

    url: str = "http://victoria-metrics:8428"
    import_endpoint: str = "/api/v1/import/prometheus"
    batch_size: int = 1000
    request_timeout: int = 30


@dataclass
class LoggingConfig:
    """Logging configuration."""

    level: str = "info"
    format: str = "json"


@dataclass
class ScrapeConfig:
    """Scrape intervals and variable lists."""

    current_weather_interval: int = 300
    max_concurrent_locations: int = 10
    hourly_forecast_interval: int = 3600
    daily_forecast_interval: int = 21600
    air_quality_interval: int = 3600
    hourly_variables: list[str] = field(default_factory=lambda: [
        "temperature_2m", "relative_humidity_2m", "dewpoint_2m",
        "apparent_temperature", "pressure_msl", "surface_pressure",
        "cloudcover", "wind_speed_10m", "wind_direction_10m",
        "wind_gusts_10m", "precipitation", "rain", "snowfall",
        "snow_depth", "visibility", "uv_index", "is_day",
    ])
    daily_variables: list[str] = field(default_factory=lambda: [
        "temperature_2m_max", "temperature_2m_min",
        "apparent_temperature_max", "apparent_temperature_min",
        "precipitation_sum", "rain_sum", "snowfall_sum",
        "precipitation_hours", "wind_speed_10m_max",
        "wind_gusts_10m_max", "wind_direction_10m_dominant",
        "uv_index_max",
    ])
    air_quality_variables: list[str] = field(default_factory=lambda: [
        "pm10", "pm2_5", "carbon_monoxide", "nitrogen_dioxide",
        "sulphur_dioxide", "ozone", "european_aqi", "us_aqi",
    ])


@dataclass
class LocationConfig:
    """A single location entry from configuration."""

    name: str
    latitude: float
    longitude: float
    timezone: str = "UTC"
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class AppConfig:
    """Root application configuration."""

    open_meteo: OpenMeteoConfig = field(default_factory=OpenMeteoConfig)
    victoria_metrics: VictoriaMetricsConfig = field(default_factory=VictoriaMetricsConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    locations: list[LocationConfig] = field(default_factory=list)
    scrape: ScrapeConfig = field(default_factory=ScrapeConfig)


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Weather data scraper")
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to config YAML file",
    )
    return parser.parse_args()


def _find_config_path(cli_path: str | None) -> Path | None:
    """Resolve the configuration file path from CLI arg, env var, or defaults."""
    if cli_path:
        p = Path(cli_path)
        if p.exists():
            return p
        logger.warning("config_file_not_found", path=cli_path)
        return None

    env_path = os.environ.get("CONFIG_PATH")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p
        logger.warning("config_file_not_found", path=env_path, source="CONFIG_PATH")
        return None

    for default_path in _DEFAULT_CONFIG_PATHS:
        p = Path(default_path)
        if p.exists():
            return p

    return None


_MAX_CONFIG_SIZE = 1024 * 1024  # 1 MB


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load and parse a YAML configuration file."""
    file_size = path.stat().st_size
    if file_size > _MAX_CONFIG_SIZE:
        logger.error("config_file_too_large", path=str(path), size_bytes=file_size)
        sys.exit(1)
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        logger.error("invalid_config_format", path=str(path))
        sys.exit(1)
    return data


def _build_config(raw: dict[str, Any]) -> AppConfig:
    """Build an AppConfig from raw YAML data."""
    om_raw = raw.get("open_meteo", {})
    vm_raw = raw.get("victoria_metrics", {})
    log_raw = raw.get("logging", {})
    scrape_raw = raw.get("scrape", {})
    locations_raw = raw.get("locations", [])

    open_meteo = OpenMeteoConfig(
        base_url=om_raw.get("base_url", OpenMeteoConfig.base_url),
        request_timeout=int(om_raw.get("request_timeout", OpenMeteoConfig.request_timeout)),
    )

    victoria_metrics = VictoriaMetricsConfig(
        url=vm_raw.get("url", VictoriaMetricsConfig.url),
        import_endpoint=vm_raw.get("import_endpoint", VictoriaMetricsConfig.import_endpoint),
        batch_size=int(vm_raw.get("batch_size", VictoriaMetricsConfig.batch_size)),
        request_timeout=int(vm_raw.get("request_timeout", VictoriaMetricsConfig.request_timeout)),
    )

    logging_cfg = LoggingConfig(
        level=log_raw.get("level", LoggingConfig.level),
        format=log_raw.get("format", LoggingConfig.format),
    )

    scrape = ScrapeConfig(
        current_weather_interval=int(scrape_raw.get(
            "current_weather_interval", ScrapeConfig.current_weather_interval)),
        max_concurrent_locations=int(scrape_raw.get(
            "max_concurrent_locations", ScrapeConfig.max_concurrent_locations)),
        hourly_forecast_interval=int(scrape_raw.get(
            "hourly_forecast_interval", ScrapeConfig.hourly_forecast_interval)),
        daily_forecast_interval=int(scrape_raw.get(
            "daily_forecast_interval", ScrapeConfig.daily_forecast_interval)),
        air_quality_interval=int(scrape_raw.get(
            "air_quality_interval", ScrapeConfig.air_quality_interval)),
        hourly_variables=scrape_raw.get(
            "hourly_variables", ScrapeConfig().hourly_variables),
        daily_variables=scrape_raw.get(
            "daily_variables", ScrapeConfig().daily_variables),
        air_quality_variables=scrape_raw.get(
            "air_quality_variables", ScrapeConfig().air_quality_variables),
    )

    locations = []
    for loc in locations_raw:
        locations.append(LocationConfig(
            name=loc["name"],
            latitude=float(loc["latitude"]),
            longitude=float(loc["longitude"]),
            timezone=loc.get("timezone", "UTC"),
            labels=loc.get("labels", {}),
        ))

    return AppConfig(
        open_meteo=open_meteo,
        victoria_metrics=victoria_metrics,
        logging=logging_cfg,
        locations=locations,
        scrape=scrape,
    )


def _apply_env_overrides(config: AppConfig) -> None:
    """Apply environment variable overrides to the configuration."""
    env_map = {
        "OPEN_METEO_URL": lambda v: setattr(config.open_meteo, "base_url", v),
        "VICTORIA_METRICS_URL": lambda v: setattr(config.victoria_metrics, "url", v),
        "VM_IMPORT_ENDPOINT": lambda v: setattr(config.victoria_metrics, "import_endpoint", v),
        "VM_BATCH_SIZE": lambda v: setattr(config.victoria_metrics, "batch_size", int(v)),
        "VM_REQUEST_TIMEOUT": lambda v: setattr(config.victoria_metrics, "request_timeout", int(v)),
        "LOG_LEVEL": lambda v: setattr(config.logging, "level", v),
        "LOG_FORMAT": lambda v: setattr(config.logging, "format", v),
    }
    for env_var, setter in env_map.items():
        value = os.environ.get(env_var)
        if value is not None:
            setter(value)
            logger.debug("env_override_applied", variable=env_var)


def _validate(config: AppConfig) -> None:
    """Validate that all required configuration fields are present and valid."""
    errors: list[str] = []

    # Validate URLs (scheme, hostname, block metadata endpoints)
    for url, field_name in [
        (config.open_meteo.base_url, "open_meteo.base_url"),
        (config.victoria_metrics.url, "victoria_metrics.url"),
    ]:
        try:
            validate_url(url, field_name)
        except ValueError as exc:
            errors.append(str(exc))

    if not config.locations:
        errors.append("At least one location must be configured")
    if config.logging.level not in ("debug", "info", "warning", "error"):
        errors.append(f"Invalid log level: {config.logging.level}")
    if config.logging.format not in ("json", "console"):
        errors.append(f"Invalid log format: {config.logging.format}")

    for i, loc in enumerate(config.locations):
        # Validate location name format
        try:
            validate_location_name(loc.name, i)
        except ValueError as exc:
            errors.append(str(exc))

        if not (-90 <= loc.latitude <= 90):
            errors.append(f"locations[{i}].latitude must be between -90 and 90")
        if not (-180 <= loc.longitude <= 180):
            errors.append(f"locations[{i}].longitude must be between -180 and 180")

        # Validate label keys and values
        try:
            validate_labels(loc.labels, loc.name)
        except ValueError as exc:
            errors.append(str(exc))

    if errors:
        for err in errors:
            logger.error("config_validation_error", error=err)
        sys.exit(1)


def load_config() -> AppConfig:
    """Load, merge, validate, and return the application configuration.

    Resolution order (later wins):
        1. Built-in defaults
        2. YAML config file
        3. Environment variables
    """
    args = _parse_args()
    config_path = _find_config_path(args.config)

    if config_path:
        logger.info("loading_config", path=str(config_path))
        raw = _load_yaml(config_path)
        config = _build_config(raw)
    else:
        logger.warning("no_config_file_found", using="defaults")
        config = AppConfig()

    _apply_env_overrides(config)
    _validate(config)

    return config


def log_config(config: AppConfig) -> None:
    """Log the resolved configuration at startup with sanitized URLs."""
    logger.info(
        "resolved_configuration",
        open_meteo_url=sanitize_url(config.open_meteo.base_url),
        open_meteo_timeout=config.open_meteo.request_timeout,
        victoria_metrics_url=sanitize_url(config.victoria_metrics.url),
        vm_import_endpoint=config.victoria_metrics.import_endpoint,
        vm_batch_size=config.victoria_metrics.batch_size,
        log_level=config.logging.level,
        log_format=config.logging.format,
        location_count=len(config.locations),
        locations=[loc.name for loc in config.locations],
        current_weather_interval=config.scrape.current_weather_interval,
        hourly_forecast_interval=config.scrape.hourly_forecast_interval,
        daily_forecast_interval=config.scrape.daily_forecast_interval,
        air_quality_interval=config.scrape.air_quality_interval,
    )
