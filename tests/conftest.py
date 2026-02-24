"""Shared test fixtures."""

from __future__ import annotations

import pytest

from scraper.models import Location, MetricPoint


@pytest.fixture
def sample_location() -> Location:
    """A minimal location for use in tests."""
    return Location(
        name="TestCity",
        latitude=48.8566,
        longitude=2.3522,
        timezone="Europe/Paris",
    )


@pytest.fixture
def sample_location_with_labels() -> Location:
    """A location with custom labels."""
    return Location(
        name="TestCity",
        latitude=48.8566,
        longitude=2.3522,
        timezone="Europe/Paris",
        labels={"region": "europe", "country": "france"},
    )


@pytest.fixture
def sample_metric_point() -> MetricPoint:
    """A minimal metric point for use in tests."""
    return MetricPoint(
        name="weather_temperature_celsius",
        value=22.5,
        timestamp_ms=1700000000000,
        labels={"location": "TestCity"},
    )
