"""Victoria Metrics exporter using the Prometheus import API."""

from __future__ import annotations

import asyncio

import aiohttp
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from scraper.models import MetricPoint
from scraper.validation import validate_url

logger = structlog.get_logger()


class VictoriaMetricsExporter:
    """Async exporter that pushes metrics to Victoria Metrics in Prometheus format."""

    def __init__(
        self,
        vm_url: str,
        import_endpoint: str = "/api/v1/import/prometheus",
        batch_size: int = 1000,
        request_timeout: int = 30,
    ) -> None:
        validate_url(vm_url, "victoria_metrics.url")
        self._url = f"{vm_url.rstrip('/')}{import_endpoint}"
        self._batch_size = batch_size
        self._timeout = aiohttp.ClientTimeout(
            total=request_timeout,
            connect=5,
            sock_read=10,
        )
        self._session: aiohttp.ClientSession | None = None
        self._metrics_exported: int = 0
        self._export_errors: int = 0

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

    @property
    def stats(self) -> dict[str, int]:
        """Return internal export counters."""
        return {
            "metrics_exported": self._metrics_exported,
            "export_errors": self._export_errors,
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
        reraise=True,
    )
    async def _send_batch(self, payload: str) -> None:
        """Send a batch of Prometheus-formatted lines to Victoria Metrics.

        Args:
            payload: Newline-separated Prometheus exposition lines.
        """
        session = await self._ensure_session()
        headers = {"Content-Type": "text/plain"}

        async with session.post(self._url, data=payload, headers=headers) as response:
            if response.status not in (200, 204):
                body = await response.text()
                if len(body) > 200:
                    body = body[:200] + "... (truncated)"
                raise aiohttp.ClientResponseError(
                    request_info=response.request_info,
                    history=response.history,
                    status=response.status,
                    message=f"VM import failed (HTTP {response.status}): {body}",
                )

    async def export(self, metrics: list[MetricPoint]) -> None:
        """Export a list of metric points to Victoria Metrics in batches.

        Args:
            metrics: The metric points to export.
        """
        if not metrics:
            return

        lines = [m.to_prometheus_line() for m in metrics]
        total = len(lines)
        exported = 0

        for i in range(0, total, self._batch_size):
            batch = lines[i : i + self._batch_size]
            payload = "\n".join(batch) + "\n"
            try:
                await self._send_batch(payload)
                exported += len(batch)
            except Exception:
                self._export_errors += 1
                logger.exception(
                    "export_batch_failed",
                    batch_start=i,
                    batch_size=len(batch),
                    url=self._url,
                )
                raise

        self._metrics_exported += exported
        logger.debug("metrics_exported", count=exported, total_lifetime=self._metrics_exported)
