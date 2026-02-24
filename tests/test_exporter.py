"""Tests for scraper.exporter module."""

from __future__ import annotations

import re

import aiohttp
import pytest
from aioresponses import aioresponses

from scraper.exporter import VictoriaMetricsExporter
from scraper.models import MetricPoint

_VM_IMPORT_PATTERN = re.compile(r"^http://10\.0\.0\.1:8428/api/v1/import/prometheus")
_VM_IMPORT_URL = "http://10.0.0.1:8428/api/v1/import/prometheus"


@pytest.fixture
def exporter():
    """Create an exporter with a test URL."""
    return VictoriaMetricsExporter(
        vm_url="http://10.0.0.1:8428",
        import_endpoint="/api/v1/import/prometheus",
        batch_size=2,
        request_timeout=5,
    )


def _make_metrics(count: int) -> list[MetricPoint]:
    return [
        MetricPoint(
            name=f"metric_{i}",
            value=float(i),
            timestamp_ms=1700000000000 + i,
            labels={"loc": "test"},
        )
        for i in range(count)
    ]


class TestExport:
    """Tests for VictoriaMetricsExporter.export()."""

    async def test_export_single_batch(self, exporter):
        with aioresponses() as m:
            m.post(_VM_IMPORT_URL, status=204)
            await exporter.export(_make_metrics(2))
            assert exporter.stats["metrics_exported"] == 2
        await exporter.close()

    async def test_export_multiple_batches(self, exporter):
        with aioresponses() as m:
            m.post(_VM_IMPORT_URL, status=204, repeat=True)
            await exporter.export(_make_metrics(3))
            assert exporter.stats["metrics_exported"] == 3
        await exporter.close()

    async def test_export_empty_list(self, exporter):
        await exporter.export([])
        assert exporter.stats["metrics_exported"] == 0
        await exporter.close()

    async def test_export_error_raises(self, exporter):
        with aioresponses() as m:
            m.post(
                _VM_IMPORT_URL,
                status=500,
                body="Internal Server Error",
                repeat=True,
            )
            with pytest.raises(aiohttp.ClientResponseError):
                await exporter.export(_make_metrics(1))
            assert exporter.stats["export_errors"] == 1
        await exporter.close()

    async def test_export_payload_format(self, exporter):
        """Verify the payload is newline-separated Prometheus lines."""
        sent_payloads: list[str] = []

        with aioresponses() as m:
            def callback(url, **kwargs):
                sent_payloads.append(kwargs["data"])

            m.post(_VM_IMPORT_URL, callback=callback, status=204)
            metrics = _make_metrics(1)
            await exporter.export(metrics)

        assert len(sent_payloads) == 1
        payload = sent_payloads[0]
        assert payload.endswith("\n")
        assert "metric_0" in payload
        await exporter.close()


class TestExporterClose:
    """Tests for exporter session management."""

    async def test_close_without_session(self, exporter):
        await exporter.close()

    async def test_stats_initial(self, exporter):
        assert exporter.stats == {"metrics_exported": 0, "export_errors": 0}
        await exporter.close()
