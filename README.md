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

For a one-shot load of historical data, see [BACKFILL.md](BACKFILL.md).

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

The build runs a live TLS trust check inside the image, so it needs outbound HTTPS. See [ci/README.md](ci/README.md).

The GitHub Actions workflow builds multi-arch images (amd64/arm64) and pushes them to GitHub Container Registry on every push to `main` and on `v*` version tags. Pull requests build and test the image without pushing it. See [CI/CD](#cicd).

### Using the pre-built image

```bash
# Image is available at:
# ghcr.io/alexieff-io/open-meteo-scrapper:main     (from main branch)
# ghcr.io/alexieff-io/open-meteo-scrapper:1.0.0    (from v1.0.0 tag; latest tracks the newest version tag)
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
| `image.repository` | Container image repository | `ghcr.io/alexieff-io/open-meteo-scrapper` |
| `image.tag` | Image tag (defaults to appVersion) | `""` |
| `openMeteo.url` | Open-Meteo base URL | `http://open-meteo:8080` |
| `openMeteo.requestTimeout` | HTTP request timeout (seconds) | `30` |
| `victoriaMetrics.url` | Victoria Metrics base URL | `http://victoria-metrics-...:8428` |
| `victoriaMetrics.importEndpoint` | VM import API path | `/api/v1/import/prometheus` |
| `victoriaMetrics.batchSize` | Metrics per export batch | `1000` |
| `victoriaMetrics.requestTimeout` | Victoria Metrics export request timeout (seconds) | `30` |
| `logging.level` | Log level (debug/info/warning/error) | `info` |
| `logging.format` | Log format (json/console) | `json` |
| `scrape.maxConcurrentLocations` | Maximum locations scraped concurrently | `10` |
| `scrape.currentWeatherInterval` | Current weather scrape interval (s) | `300` |
| `scrape.hourlyForecastInterval` | Hourly forecast scrape interval (s) | `3600` |
| `scrape.dailyForecastInterval` | Daily forecast scrape interval (s) | `21600` |
| `scrape.airQualityInterval` | Air quality scrape interval (s) | `3600` |
| `scrape.hourlyVariables` | Open-Meteo hourly variables to collect | see `values.yaml` |
| `scrape.dailyVariables` | Open-Meteo daily variables to collect | see `values.yaml` |
| `scrape.airQualityVariables` | Open-Meteo air quality variables to collect | see `values.yaml` |
| `locations` | Locations to collect (see below) | Berlin, New York, London, Tokyo |
| `resources` | Container resource requests and limits | `100m`/`192Mi` requests, `200m`/`256Mi` limits |
| `extraEnv` | Extra container environment variables | `[]` |
| `probes.liveness.enabled` / `probes.readiness.enabled` | HTTP probes against `/metrics` | `true` |
| `serviceMonitor.enabled` | Create Prometheus ServiceMonitor | `false` |
| `serviceMonitor.interval` | ServiceMonitor scrape interval | `30s` |
| `grafanaDashboards.enabled` | Create dashboard ConfigMaps | `true` |
| `grafanaDashboards.label` | Grafana sidecar discovery label | `grafana_dashboard` |
| `grafanaDashboards.labelValue` | Value of the discovery label | `"1"` |
| `grafanaDashboards.folder` | Grafana folder name (`grafana_folder` annotation) | `Weather` |
| `grafanaDashboards.annotations` | Extra annotations on the dashboard ConfigMaps | `{}` |
| `networkPolicy.enabled` | Create a NetworkPolicy (egress allows only DNS plus `additionalEgress`) | `false` |
| `networkPolicy.prometheusNamespace` | Namespace allowed to reach `:9090` (empty: pods in the release namespace) | `monitoring` |
| `networkPolicy.additionalEgress` | Extra egress rules; must allow Open-Meteo and Victoria Metrics | `[]` |
| `autoscaling.enabled` | Create a HorizontalPodAutoscaler | `false` |
| `podDisruptionBudget.enabled` | Create a PodDisruptionBudget | `false` |

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
| `VM_REQUEST_TIMEOUT` | `victoria_metrics.request_timeout` |
| `LOG_LEVEL` | `logging.level` |
| `LOG_FORMAT` | `logging.format` |
| `CONFIG_PATH` | Path to config YAML file |

With Helm, `VM_REQUEST_TIMEOUT` set through `extraEnv` still overrides `victoriaMetrics.requestTimeout`. See [config.yml.example](config.yml.example) for every config file key.

## Grafana Dashboards

### Auto-Provisioning (Recommended)

When `grafanaDashboards.enabled=true` (default), the Helm chart creates ConfigMaps labeled for Grafana sidecar discovery. If your Grafana is deployed via kube-prometheus-stack (or has a sidecar watching for `grafana_dashboard: "1"` labels), the dashboards are loaded automatically.

The chart puts them in the "Weather" folder through a `grafana_folder` annotation. The sidecar only reads that annotation when it's configured to. In the Grafana Helm chart, set `sidecar.dashboards.folderAnnotation: grafana_folder` and `sidecar.dashboards.provider.foldersFromFilesStructure: true` (under `grafana:` in kube-prometheus-stack). Otherwise the dashboards land in the sidecar's default folder.

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
| **Migraine Weather Triggers** | Common migraine triggers per location: 6h pressure change, 24h temperature change, humidity and dewpoint, UV index, wind gusts, US AQI, PM2.5 and ozone |
| **Scraper Health** | Self-monitoring: scrape durations, error rates, export throughput, latency percentiles |

## Available Metrics

### Weather Metrics

Every weather metric carries the `location`, `latitude` and `longitude` labels plus the location's custom `labels`.

**Current weather** (fixed names):

| Metric | Unit |
|---|---|
| `weather_temperature_celsius` | celsius |
| `weather_wind_speed_kmh` | km/h |
| `weather_wind_direction_degrees` | degrees |
| `weather_condition_code` | WMO weather code |
| `weather_is_day` | 0/1 |

**Forecasts and air quality** are named after the Open-Meteo variable, so the variable lists in `scrape` decide which series exist. Hourly and daily samples are timestamped at the forecast time, so they extend up to 16 days ahead.

| Source | Metric name | Extra label | Default variables (unit) |
|---|---|---|---|
| Hourly forecast | `weather_<variable>` | `frequency="hourly"` | `temperature_2m`, `dewpoint_2m`, `apparent_temperature` (celsius); `relative_humidity_2m`, `cloudcover` (%); `pressure_msl`, `surface_pressure` (hPa); `wind_speed_10m`, `wind_gusts_10m` (km/h); `wind_direction_10m` (degrees); `precipitation`, `rain` (mm); `snowfall` (cm); `snow_depth`, `visibility` (m); `uv_index` (index); `is_day` (0/1) |
| Daily forecast | `weather_daily_<variable>` | `frequency="daily"` | `temperature_2m_max`, `temperature_2m_min`, `apparent_temperature_max`, `apparent_temperature_min` (celsius); `precipitation_sum`, `rain_sum` (mm); `snowfall_sum` (cm); `precipitation_hours` (h); `wind_speed_10m_max`, `wind_gusts_10m_max` (km/h); `wind_direction_10m_dominant` (degrees); `uv_index_max` (index) |
| Air quality | `air_quality_<variable>` | — | `pm10`, `pm2_5`, `carbon_monoxide`, `nitrogen_dioxide`, `sulphur_dioxide`, `ozone` (ug/m3); `european_aqi`, `us_aqi` (index) |

For example, `weather_temperature_2m{frequency="hourly"}`, `weather_daily_precipitation_sum{frequency="daily"}` and `air_quality_pm2_5`.

### Self-Monitoring Metrics

These are served on the pod's `:9090/metrics` endpoint for Prometheus/VM to scrape (ServiceMonitor or pod annotations). They are not pushed to Victoria Metrics.

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
- The chart's own policy (`networkPolicy.enabled=true`) allows only DNS egress, so add both services to `networkPolicy.additionalEgress`

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

### Run tests

```bash
pip install -c ci/test-constraints.txt -e '.[test]'
pytest --cov=scraper --cov-fail-under=80
```

The constraint file keeps aiohttp below 3.14 for tests only, because aioresponses doesn't support aiohttp 3.14 yet. The image still installs the latest aiohttp allowed by `requirements.txt`.

## CI/CD

The GitHub Actions workflow (`.github/workflows/build.yaml`) runs on pull requests, on pushes to `main` and on `v*` tags:

- **test**: runs the test suite with an 80% coverage floor.
- **qualify-image**: builds the image natively on amd64 and arm64 runners and checks its TLS trust store against live hosts. See [ci/README.md](ci/README.md).
- **build**: builds multi-architecture images (`linux/amd64`, `linux/arm64`) with the build cache disabled, so the in-image TLS check runs again before every push. On `main` and tags it pushes to GitHub Container Registry (`ghcr.io`) and signs the image with cosign (keyless). Pull requests build without pushing.
- **publish-helm-chart** (`v*` tags only): sets the chart's `version` and `appVersion` from the tag, then pushes the chart to `oci://ghcr.io/<owner>/charts/weather-scraper` and signs it.

Images are tagged from the git ref:
- `main` branch → `main`
- `v1.2.3` tag → `1.2.3`, `1.2`, `1` and `latest`
- Every pushed build → `sha-<short>`

No secrets configuration is needed — it uses the built-in `GITHUB_TOKEN`.
