# Weather Scraper

Async Python weather data scraper that collects from a self-hosted [Open-Meteo](https://open-meteo.com/) instance and exports metrics to [Victoria Metrics](https://victoriametrics.com/). Packaged as a Helm chart for Kubernetes with Grafana dashboards included.

## Architecture

```
┌─────────────┐       ┌──────────────────┐       ┌──────────────────┐
│  Open-Meteo │ ────▶│  Weather Scraper │──────▶│ Victoria Metrics │
│(self-hosted)│  HTTP │  (this project)  │  HTTP │  (existing)      │
└─────────────┘       └──────────────────┘       └──────────────────┘
                              │ :9090                      ▲
                              │ /metrics                   │ PromQL
                              ▼                            │
                      ┌──────────────────┐          ┌──────┴───────┐
                      │ Prometheus/VM    │          │   Grafana    │
                      │ (scrape self-    │          │  (existing)  │
                      │  monitoring)     │          └──────────────┘
                      └──────────────────┘
```

The scraper runs 4 concurrent collection loops:
- **Current weather** — every 5 minutes
- **Hourly forecast** — every hour
- **Daily forecast** — every 6 hours
- **Air quality** — every hour

## Prerequisites

- Kubernetes cluster (1.25+)
- Existing Victoria Metrics deployment
- Existing Grafana deployment
- Self-hosted Open-Meteo instance
- Helm 3.x
- Docker (for building the image)

## Quick Start

### Build the image

```bash
docker build -t weather-scraper:latest .
```

The GitHub Actions workflow automatically builds multi-arch images (amd64/arm64) and pushes to GitHub Container Registry on every push to `main` and on version tags.

### Using the pre-built image

```bash
# Image is available at:
# ghcr.io/<owner>/weather-scraper:latest   (from main branch)
# ghcr.io/<owner>/weather-scraper:1.0.0    (from v1.0.0 tag)
```

### Install with Helm

```bash
# Minimum required values
helm install weather-scraper ./helm/weather-scraper \
  --set openMeteo.url=http://your-open-meteo:8080 \
  --set victoriaMetrics.url=http://your-victoria-metrics:8428

# With a custom values file
helm install weather-scraper ./helm/weather-scraper -f my-values.yaml

# With a private registry image
helm install weather-scraper ./helm/weather-scraper \
  --set image.repository=ghcr.io/myorg/weather-scraper \
  --set image.tag=1.0.0 \
  --set openMeteo.url=http://your-open-meteo:8080 \
  --set victoriaMetrics.url=http://your-victoria-metrics:8428
```

### Verify

```bash
kubectl logs -f deployment/weather-scraper
kubectl port-forward svc/weather-scraper 9090:9090
curl -s http://localhost:9090/metrics
```

## Configuration

### Helm Values Reference

| Parameter | Description | Default |
|---|---|---|
| `replicaCount` | Number of replicas | `1` |
| `image.repository` | Container image repository | `ghcr.io/OWNER/weather-scraper` |
| `image.tag` | Image tag (defaults to appVersion) | `""` |
| `openMeteo.url` | Open-Meteo base URL | `http://open-meteo:8080` |
| `openMeteo.requestTimeout` | HTTP request timeout (seconds) | `30` |
| `victoriaMetrics.url` | Victoria Metrics base URL | `http://victoria-metrics-...:8428` |
| `victoriaMetrics.importEndpoint` | VM import API path | `/api/v1/import/prometheus` |
| `victoriaMetrics.batchSize` | Metrics per export batch | `1000` |
| `logging.level` | Log level (debug/info/warning/error) | `info` |
| `logging.format` | Log format (json/console) | `json` |
| `scrape.currentWeatherInterval` | Current weather scrape interval (s) | `300` |
| `scrape.hourlyForecastInterval` | Hourly forecast scrape interval (s) | `3600` |
| `scrape.dailyForecastInterval` | Daily forecast scrape interval (s) | `21600` |
| `scrape.airQualityInterval` | Air quality scrape interval (s) | `3600` |
| `serviceMonitor.enabled` | Create Prometheus ServiceMonitor | `false` |
| `grafanaDashboards.enabled` | Create dashboard ConfigMaps | `true` |
| `grafanaDashboards.label` | Grafana sidecar discovery label | `grafana_dashboard` |
| `grafanaDashboards.folder` | Grafana folder name | `Weather` |

### Configuring Locations

Add locations in `values.yaml` or via a custom values file:

```yaml
locations:
  - name: "paris"
    latitude: 48.86
    longitude: 2.35
    timezone: "Europe/Paris"
    labels:
      country: "france"
      region: "europe"
  - name: "sydney"
    latitude: -33.87
    longitude: 151.21
    timezone: "Australia/Sydney"
    labels:
      country: "australia"
      region: "oceania"
```

### Adjusting Scrape Intervals

```yaml
scrape:
  currentWeatherInterval: 120   # Every 2 minutes
  hourlyForecastInterval: 1800  # Every 30 minutes
  dailyForecastInterval: 43200  # Every 12 hours
  airQualityInterval: 1800      # Every 30 minutes
```

### Environment Variable Overrides

These environment variables override their corresponding config file values:

| Variable | Overrides |
|---|---|
| `OPEN_METEO_URL` | `open_meteo.base_url` |
| `VICTORIA_METRICS_URL` | `victoria_metrics.url` |
| `VM_IMPORT_ENDPOINT` | `victoria_metrics.import_endpoint` |
| `VM_BATCH_SIZE` | `victoria_metrics.batch_size` |
| `LOG_LEVEL` | `logging.level` |
| `LOG_FORMAT` | `logging.format` |
| `CONFIG_PATH` | Path to config YAML file |

## Grafana Dashboards

### Auto-Provisioning (Recommended)

When `grafanaDashboards.enabled=true` (default), the Helm chart creates ConfigMaps labeled for Grafana sidecar discovery. If your Grafana is deployed via kube-prometheus-stack (or has a sidecar watching for `grafana_dashboard: "1"` labels), dashboards appear automatically in the "Weather" folder.

### Manual Import

Dashboard JSON files are in the `dashboards/` directory for manual import:

1. Open Grafana → Dashboards → Import
2. Upload the JSON file or paste its contents
3. Select your VictoriaMetrics/Prometheus datasource when prompted
4. Click Import

### Available Dashboards

| Dashboard | Description |
|---|---|
| **Weather Overview** | Temperature gauges, wind, humidity, pressure, precipitation, UV index, cloud cover across all locations |
| **Weather Detail** | Deep dive for a single location: actual vs apparent temperature, dewpoint, wind direction, visibility, snow depth, 7-day forecast |
| **Air Quality** | EU/US AQI indices, PM2.5, PM10, ozone, NO2, CO, SO2 levels per location |
| **Scraper Health** | Self-monitoring: scrape durations, error rates, export throughput, latency percentiles |

## Available Metrics

### Weather Metrics

| Metric | Labels | Unit | Source |
|---|---|---|---|
| `weather_temperature_celsius` | location, latitude, longitude, + custom | celsius | Current weather |
| `weather_wind_speed_kmh` | location, latitude, longitude, + custom | km/h | Current weather |
| `weather_wind_direction_degrees` | location, latitude, longitude, + custom | degrees | Current weather |
| `weather_condition_code` | location, latitude, longitude, + custom | code | Current weather |
| `weather_is_day` | location, latitude, longitude, + custom | boolean | Current weather |
| `weather_temperature_2m` | location, ..., frequency=hourly | celsius | Hourly forecast |
| `weather_relative_humidity_2m` | location, ..., frequency=hourly | % | Hourly forecast |
| `weather_dewpoint_2m` | location, ..., frequency=hourly | celsius | Hourly forecast |
| `weather_apparent_temperature` | location, ..., frequency=hourly | celsius | Hourly forecast |
| `weather_pressure_msl` | location, ..., frequency=hourly | hPa | Hourly forecast |
| `weather_wind_speed_10m` | location, ..., frequency=hourly | km/h | Hourly forecast |
| `weather_precipitation` | location, ..., frequency=hourly | mm | Hourly forecast |
| `weather_cloudcover` | location, ..., frequency=hourly | % | Hourly forecast |
| `weather_uv_index` | location, ..., frequency=hourly | index | Hourly forecast |
| `weather_daily_temperature_2m_max` | location, ..., frequency=daily | celsius | Daily forecast |
| `weather_daily_temperature_2m_min` | location, ..., frequency=daily | celsius | Daily forecast |
| `weather_daily_precipitation_sum` | location, ..., frequency=daily | mm | Daily forecast |
| `air_quality_european_aqi` | location, latitude, longitude, + custom | index | Air quality |
| `air_quality_us_aqi` | location, latitude, longitude, + custom | index | Air quality |
| `air_quality_pm2_5` | location, latitude, longitude, + custom | ug/m3 | Air quality |
| `air_quality_pm10` | location, latitude, longitude, + custom | ug/m3 | Air quality |
| `air_quality_ozone` | location, latitude, longitude, + custom | ug/m3 | Air quality |

### Self-Monitoring Metrics

| Metric | Labels | Type | Description |
|---|---|---|---|
| `weather_scrape_duration_seconds` | scrape_type, location | Histogram | Duration of scrape operations |
| `weather_scrape_errors_total` | scrape_type, location | Counter | Total scrape errors |
| `weather_metrics_exported_total` | — | Counter | Total metrics exported to VM |
| `weather_last_scrape_timestamp` | scrape_type | Gauge | Unix timestamp of last scrape |

## Useful PromQL Queries

```promql
# Current temperature per location
weather_temperature_celsius

# Temperature change over last hour
weather_temperature_celsius - weather_temperature_celsius offset 1h

# Max temperature today
max_over_time(weather_temperature_2m{frequency="hourly"}[24h])

# Average humidity by region
avg by (region) (weather_relative_humidity_2m{frequency="hourly"})

# Precipitation total last 24h
sum_over_time(weather_precipitation{frequency="hourly"}[24h])

# Wind chill delta
weather_apparent_temperature{frequency="hourly"} - weather_temperature_2m{frequency="hourly"}

# Locations where temperature exceeds 30C
weather_temperature_celsius > 30

# Air quality degradation rate
deriv(air_quality_european_aqi[2h])

# Scraper error rate
rate(weather_scrape_errors_total[5m])
```

## Troubleshooting

### Check scraper logs

```bash
kubectl logs -f deployment/weather-scraper
# For JSON log parsing:
kubectl logs deployment/weather-scraper | jq .
```

### Verify data in Victoria Metrics

```bash
kubectl port-forward svc/victoria-metrics 8428:8428
curl -s 'http://localhost:8428/api/v1/query?query=weather_temperature_celsius' | jq .
```

### Common Issues

**DNS resolution failures for service names**
- Ensure the scraper is in the same namespace or use fully qualified service names (e.g., `http://victoria-metrics.monitoring.svc.cluster.local:8428`)

**Network policies blocking traffic**
- Verify NetworkPolicies allow egress from the scraper pod to Open-Meteo and Victoria Metrics ports

**Victoria Metrics import endpoint differences**
- VMSingle uses `/api/v1/import/prometheus`
- VMCluster uses `/insert/0/prometheus/api/v1/import/prometheus` — set `victoriaMetrics.importEndpoint` accordingly

**No data appearing in Grafana**
- Verify the scraper logs show successful exports
- Check the datasource in Grafana points to the correct Victoria Metrics instance
- Confirm the time range in Grafana covers when data was collected

## Local Development

### Run with Docker

```bash
docker run --rm \
  -e OPEN_METEO_URL=http://host.docker.internal:8080 \
  -e VICTORIA_METRICS_URL=http://host.docker.internal:8428 \
  -v $(pwd)/config.yml.example:/etc/weather-scraper/config.yml:ro \
  -p 9090:9090 \
  weather-scraper:latest
```

### Run with Python

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export OPEN_METEO_URL=http://localhost:8080
export VICTORIA_METRICS_URL=http://localhost:8428
export LOG_FORMAT=console

python -m scraper.main --config config.yml.example
```

## CI/CD

The included GitHub Actions workflow (`.github/workflows/build.yaml`) automatically:

- Builds multi-architecture images (`linux/amd64`, `linux/arm64`)
- Pushes to GitHub Container Registry (`ghcr.io`)
- Tags images based on git refs:
  - `main` branch → `main` tag
  - `v1.2.3` tag → `1.2.3`, `1.2`, `1` tags
  - Every commit → `sha-<short>` tag
- Uses BuildKit layer caching for fast rebuilds

No secrets configuration is needed — it uses the built-in `GITHUB_TOKEN`.
