"""Tests for scraper.config module."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scraper.config import (
    AppConfig,
    LocationConfig,
    LoggingConfig,
    OpenMeteoConfig,
    ScrapeConfig,
    VictoriaMetricsConfig,
    _apply_env_overrides,
    _build_config,
    _find_config_path,
    _load_yaml,
    _validate,
)


# --- _find_config_path ---

class TestFindConfigPath:
    """Tests for _find_config_path()."""

    def test_cli_path_exists(self, tmp_path):
        config_file = tmp_path / "config.yml"
        config_file.write_text("locations: []")
        result = _find_config_path(str(config_file))
        assert result == config_file

    def test_cli_path_not_exists(self):
        result = _find_config_path("/nonexistent/config.yml")
        assert result is None

    def test_env_path_exists(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yml"
        config_file.write_text("locations: []")
        monkeypatch.setenv("CONFIG_PATH", str(config_file))
        result = _find_config_path(None)
        assert result == config_file

    def test_env_path_not_exists(self, monkeypatch):
        monkeypatch.setenv("CONFIG_PATH", "/nonexistent/config.yml")
        result = _find_config_path(None)
        assert result is None

    def test_no_config_anywhere(self, monkeypatch, tmp_path):
        """Returns None when no CLI arg, no env var, and no default path exists."""
        monkeypatch.delenv("CONFIG_PATH", raising=False)
        # Run from a temp dir that has no config.yml
        monkeypatch.chdir(tmp_path)
        result = _find_config_path(None)
        assert result is None

    def test_cli_takes_precedence_over_env(self, tmp_path, monkeypatch):
        cli_file = tmp_path / "cli.yml"
        cli_file.write_text("locations: []")
        env_file = tmp_path / "env.yml"
        env_file.write_text("locations: []")
        monkeypatch.setenv("CONFIG_PATH", str(env_file))
        result = _find_config_path(str(cli_file))
        assert result == cli_file


# --- _load_yaml ---

class TestLoadYaml:
    """Tests for _load_yaml()."""

    def test_loads_valid_yaml(self, tmp_path):
        config_file = tmp_path / "config.yml"
        config_file.write_text("open_meteo:\n  base_url: http://test:8080\n")
        data = _load_yaml(config_file)
        assert data["open_meteo"]["base_url"] == "http://test:8080"

    def test_rejects_non_dict(self, tmp_path):
        config_file = tmp_path / "config.yml"
        config_file.write_text("- item1\n- item2\n")
        with pytest.raises(SystemExit):
            _load_yaml(config_file)

    def test_rejects_oversized_file(self, tmp_path):
        config_file = tmp_path / "config.yml"
        config_file.write_text("x" * (1024 * 1024 + 1))
        with pytest.raises(SystemExit):
            _load_yaml(config_file)


# --- _build_config ---

class TestBuildConfig:
    """Tests for _build_config()."""

    def test_defaults_when_empty(self):
        config = _build_config({})
        assert config.open_meteo.base_url == "http://open-meteo:8080"
        assert config.victoria_metrics.url == "http://victoria-metrics:8428"
        assert config.logging.level == "info"

    def test_overrides_open_meteo(self):
        raw = {"open_meteo": {"base_url": "http://custom:9090", "request_timeout": 60}}
        config = _build_config(raw)
        assert config.open_meteo.base_url == "http://custom:9090"
        assert config.open_meteo.request_timeout == 60

    def test_overrides_victoria_metrics(self):
        raw = {"victoria_metrics": {"url": "http://vm:8428", "batch_size": 500}}
        config = _build_config(raw)
        assert config.victoria_metrics.url == "http://vm:8428"
        assert config.victoria_metrics.batch_size == 500

    def test_parses_locations(self):
        raw = {
            "locations": [
                {"name": "Paris", "latitude": 48.8, "longitude": 2.3, "timezone": "CET"},
            ]
        }
        config = _build_config(raw)
        assert len(config.locations) == 1
        assert config.locations[0].name == "Paris"
        assert config.locations[0].latitude == 48.8

    def test_location_default_timezone(self):
        raw = {
            "locations": [
                {"name": "Test", "latitude": 0.0, "longitude": 0.0},
            ]
        }
        config = _build_config(raw)
        assert config.locations[0].timezone == "UTC"

    def test_scrape_overrides(self):
        raw = {"scrape": {"current_weather_interval": 60}}
        config = _build_config(raw)
        assert config.scrape.current_weather_interval == 60


# --- _apply_env_overrides ---

class TestApplyEnvOverrides:
    """Tests for _apply_env_overrides()."""

    def test_overrides_open_meteo_url(self, monkeypatch):
        config = AppConfig()
        monkeypatch.setenv("OPEN_METEO_URL", "http://env-meteo:1234")
        _apply_env_overrides(config)
        assert config.open_meteo.base_url == "http://env-meteo:1234"

    def test_overrides_vm_url(self, monkeypatch):
        config = AppConfig()
        monkeypatch.setenv("VICTORIA_METRICS_URL", "http://env-vm:5678")
        _apply_env_overrides(config)
        assert config.victoria_metrics.url == "http://env-vm:5678"

    def test_overrides_log_level(self, monkeypatch):
        config = AppConfig()
        monkeypatch.setenv("LOG_LEVEL", "debug")
        _apply_env_overrides(config)
        assert config.logging.level == "debug"

    def test_no_override_when_unset(self):
        config = AppConfig()
        original_url = config.open_meteo.base_url
        _apply_env_overrides(config)
        assert config.open_meteo.base_url == original_url

    def test_overrides_batch_size(self, monkeypatch):
        config = AppConfig()
        monkeypatch.setenv("VM_BATCH_SIZE", "2000")
        _apply_env_overrides(config)
        assert config.victoria_metrics.batch_size == 2000


# --- _validate ---

class TestValidate:
    """Tests for _validate()."""

    def _make_valid_config(self) -> AppConfig:
        return AppConfig(
            open_meteo=OpenMeteoConfig(base_url="http://10.0.0.1:8080"),
            victoria_metrics=VictoriaMetricsConfig(url="http://10.0.0.2:8428"),
            logging=LoggingConfig(level="info", format="json"),
            locations=[
                LocationConfig(name="Test", latitude=0.0, longitude=0.0),
            ],
        )

    def test_valid_config_passes(self):
        config = self._make_valid_config()
        _validate(config)

    def test_rejects_no_locations(self):
        config = self._make_valid_config()
        config.locations = []
        with pytest.raises(SystemExit):
            _validate(config)

    def test_rejects_invalid_log_level(self):
        config = self._make_valid_config()
        config.logging.level = "verbose"
        with pytest.raises(SystemExit):
            _validate(config)

    def test_rejects_invalid_log_format(self):
        config = self._make_valid_config()
        config.logging.format = "yaml"
        with pytest.raises(SystemExit):
            _validate(config)

    def test_rejects_latitude_out_of_range(self):
        config = self._make_valid_config()
        config.locations[0].latitude = 91.0
        with pytest.raises(SystemExit):
            _validate(config)

    def test_rejects_longitude_out_of_range(self):
        config = self._make_valid_config()
        config.locations[0].longitude = -181.0
        with pytest.raises(SystemExit):
            _validate(config)

    def test_rejects_invalid_backfill_days(self):
        config = self._make_valid_config()
        config.backfill = True
        config.past_days = 100
        with pytest.raises(SystemExit):
            _validate(config)

    def test_accepts_valid_backfill_days(self):
        config = self._make_valid_config()
        config.backfill = True
        config.past_days = 30
        _validate(config)
