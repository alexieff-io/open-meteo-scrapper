"""Tests for scraper.models module."""

from __future__ import annotations

from scraper.models import Location, MetricPoint


class TestLocation:
    """Tests for Location model."""

    def test_base_labels_default(self, sample_location):
        labels = sample_location.base_labels()
        assert labels["location"] == "TestCity"
        assert labels["latitude"] == "48.8566"
        assert labels["longitude"] == "2.3522"
        assert len(labels) == 3

    def test_base_labels_with_custom(self, sample_location_with_labels):
        labels = sample_location_with_labels.base_labels()
        assert labels["location"] == "TestCity"
        assert labels["region"] == "europe"
        assert labels["country"] == "france"
        assert len(labels) == 5

    def test_frozen(self, sample_location):
        import pytest
        with pytest.raises(AttributeError):
            sample_location.name = "other"


class TestMetricPoint:
    """Tests for MetricPoint model."""

    def test_to_prometheus_line_with_labels(self):
        mp = MetricPoint(
            name="weather_temp",
            value=22.5,
            timestamp_ms=1700000000000,
            labels={"location": "Paris", "region": "EU"},
        )
        line = mp.to_prometheus_line()
        assert line == 'weather_temp{location="Paris",region="EU"} 22.5 1700000000000'

    def test_to_prometheus_line_no_labels(self):
        mp = MetricPoint(
            name="weather_temp",
            value=22.5,
            timestamp_ms=1700000000000,
        )
        line = mp.to_prometheus_line()
        assert line == "weather_temp 22.5 1700000000000"

    def test_to_prometheus_line_escapes_label_values(self):
        mp = MetricPoint(
            name="weather_temp",
            value=1.0,
            timestamp_ms=1000,
            labels={"city": 'New "York"'},
        )
        line = mp.to_prometheus_line()
        assert 'city="New \\"York\\""' in line

    def test_labels_sorted(self):
        mp = MetricPoint(
            name="m",
            value=1.0,
            timestamp_ms=1000,
            labels={"z": "1", "a": "2"},
        )
        line = mp.to_prometheus_line()
        assert line.startswith('m{a="2",z="1"}')
