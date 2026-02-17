"""Data models for weather metrics and location configuration."""

from __future__ import annotations

from dataclasses import dataclass, field

from scraper.validation import escape_label_value


@dataclass(frozen=True, slots=True)
class Location:
    """A geographic location to collect weather data for."""

    name: str
    latitude: float
    longitude: float
    timezone: str
    labels: dict[str, str] = field(default_factory=dict)

    def base_labels(self) -> dict[str, str]:
        """Return the standard set of labels for this location."""
        labels = {
            "location": self.name,
            "latitude": str(self.latitude),
            "longitude": str(self.longitude),
        }
        labels.update(self.labels)
        return labels


@dataclass(frozen=True, slots=True)
class MetricPoint:
    """A single metric data point in Prometheus exposition format."""

    name: str
    value: float
    timestamp_ms: int
    labels: dict[str, str] = field(default_factory=dict)

    def to_prometheus_line(self) -> str:
        """Serialize to Prometheus exposition format.

        Returns a string like:
            metric_name{label1="val1",label2="val2"} 1.23 1700000000000
        """
        if self.labels:
            label_pairs = ",".join(
                f'{k}="{escape_label_value(v)}"'
                for k, v in sorted(self.labels.items())
            )
            return f"{self.name}{{{label_pairs}}} {self.value} {self.timestamp_ms}"
        return f"{self.name} {self.value} {self.timestamp_ms}"
