# Preserve Python 3.12.8; pin the original multi-architecture Bookworm base.
FROM python:3.12.8-slim@sha256:2199a62885a12290dc9c5be3ca0681d367576ab7bf037da120e564723292a2f0

# Debian's trust-store update removes the distrusted eMudhra/emSign roots.
# Keep verification enabled and use the OS store used by aiohttp by default.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates=20250419~deb12u2 \
    && rm -rf /var/lib/apt/lists/* \
    && test "$(dpkg-query -W -f='${Version}' ca-certificates)" = 20250419~deb12u2

LABEL maintainer="weather-scraper"
LABEL description="Weather data scraper collecting from Open-Meteo and exporting to Victoria Metrics"
LABEL version="1.0.0"

RUN groupadd -g 1000 scraper && \
    useradd -r -u 1000 -g scraper scraper

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scraper/ scraper/

RUN chown -R scraper:scraper /app

EXPOSE 9090

# The slim base has no wget/curl, so probe with the bundled Python.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:9090/metrics', timeout=4)"]

USER scraper

# Qualify the exact runtime layers that will be published, not a separate rebuild.
# CI disables build cache so this live gate runs before every publication.
RUN --mount=type=bind,source=ci,target=/qualification \
    python /qualification/tls_probe.py

ENTRYPOINT ["python", "-m", "scraper.main"]

