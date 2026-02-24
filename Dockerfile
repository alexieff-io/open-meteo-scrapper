FROM python:3.12.8-slim

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

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://localhost:9090/metrics || exit 1

USER scraper

ENTRYPOINT ["python", "-m", "scraper.main"]
