

```markdown
# Build a Production Weather Scraper: Open-Meteo → Victoria Metrics → Grafana (Kubernetes)

## Project Overview
Build a Python async weather data scraper that collects weather data from a self-hosted Open-Meteo instance, exports metrics to an existing Victoria Metrics deployment, and includes Grafana dashboards that can be imported into an existing Grafana instance. The scraper is designed to run as a Kubernetes Deployment with full Helm chart packaging.

## Architecture Context
- **Victoria Metrics**: Already deployed in the cluster. The scraper will be configured to point to it via environment variable or config.
- **Grafana**: Already deployed in the cluster. Dashboards will be provided as ConfigMaps that work with Grafana's sidecar dashboard provisioning (standard kube-prometheus-stack pattern) AND as standalone JSON files for manual import.
- **Open-Meteo**: Self-hosted instance, already deployed. URL provided via config.

All target URLs (Open-Meteo, Victoria Metrics, Grafana) are configurable and should never be hardcoded.

## Project Structure
```
weather-scraper/
├── README.md
├── Dockerfile
├── requirements.txt
├── config.yml.example              # Example config for reference
├── scraper/
│   ├── __init__.py
│   ├── main.py                     # Entrypoint, async event loop, signal handling
│   ├── collector.py                # Open-Meteo API client
│   ├── exporter.py                 # Victoria Metrics exporter
│   ├── models.py                   # Dataclasses
│   └── config.py                   # Config loader supporting YAML file, env vars, and CLI args
├── helm/
│   └── weather-scraper/
│       ├── Chart.yaml
│       ├── values.yaml
│       ├── templates/
│       │   ├── _helpers.tpl
│       │   ├── deployment.yaml
│       │   ├── configmap.yaml
│       │   ├── configmap-dashboards.yaml
│       │   ├── service.yaml
│       │   ├── servicemonitor.yaml
│       │   ├── poddisruptionbudget.yaml
│       │   ├── serviceaccount.yaml
│       │   ├── hpa.yaml
│       │   └── NOTES.txt
│       └── dashboards/
│           ├── weather-overview.json
│           ├── weather-detail.json
│           ├── air-quality.json
│           └── scraper-health.json
└── dashboards/                     # Standalone dashboard JSONs for manual Grafana import
    ├── weather-overview.json
    ├── weather-detail.json
    ├── air-quality.json
    └── scraper-health.json
```

## Technical Requirements

### Python Scraper

**Runtime:** Python 3.12

**Dependencies:**
- `aiohttp` - async HTTP client for Open-Meteo API calls and VM export
- `pyyaml` - config file parsing
- `structlog` - structured JSON logging
- `tenacity` - retry logic with exponential backoff
- `prometheus-client` - self-monitoring metrics exposed on port 9090

**config.py:**
- Load config from YAML file (path configurable via `--config` CLI arg or `CONFIG_PATH` env var, default `/etc/weather-scraper/config.yml` for k8s, fallback to `./config.yml`)
- Support environment variable overrides for key connection settings:
  - `OPEN_METEO_URL` overrides `open_meteo.base_url`
  - `VICTORIA_METRICS_URL` overrides `victoria_metrics.url`
  - `VM_IMPORT_ENDPOINT` overrides `victoria_metrics.import_endpoint`
  - `VM_BATCH_SIZE` overrides `victoria_metrics.batch_size`
  - `LOG_LEVEL` overrides `logging.level`
  - `LOG_FORMAT` overrides `logging.format` (json or console)
- Validate all required fields exist
- Provide sensible defaults
- Log the resolved configuration at startup (mask no secrets, these are all infrastructure URLs)

**config.yml spec:**
```yaml
open_meteo:
  base_url: "http://open-meteo:8080"    # Overridden by OPEN_METEO_URL env var
  request_timeout: 30

victoria_metrics:
  url: "http://victoria-metrics:8428"   # Overridden by VICTORIA_METRICS_URL env var
  import_endpoint: "/api/v1/import/prometheus"
  batch_size: 1000

logging:
  level: "info"        # debug, info, warning, error
  format: "json"       # json or console

locations:
  - name: "berlin"
    latitude: 52.52
    longitude: 13.41
    timezone: "Europe/Berlin"
    labels:
      country: "germany"
      region: "europe"
  - name: "new_york"
    latitude: 40.71
    longitude: -74.01
    timezone: "America/New_York"
    labels:
      country: "usa"
      region: "north_america"
  - name: "london"
    latitude: 51.51
    longitude: -0.13
    timezone: "Europe/London"
    labels:
      country: "uk"
      region: "europe"
  - name: "tokyo"
    latitude: 35.68
    longitude: 139.69
    timezone: "Asia/Tokyo"
    labels:
      country: "japan"
      region: "asia"

scrape:
  current_weather_interval: 300
  hourly_forecast_interval: 3600
  daily_forecast_interval: 21600
  air_quality_interval: 3600
  hourly_variables:
    - temperature_2m
    - relative_humidity_2m
    - dewpoint_2m
    - apparent_temperature
    - pressure_msl
    - surface_pressure
    - cloudcover
    - wind_speed_10m
    - wind_direction_10m
    - wind_gusts_10m
    - precipitation
    - rain
    - snowfall
    - snow_depth
    - visibility
    - uv_index
    - is_day
  daily_variables:
    - temperature_2m_max
    - temperature_2m_min
    - apparent_temperature_max
    - apparent_temperature_min
    - precipitation_sum
    - rain_sum
    - snowfall_sum
    - precipitation_hours
    - wind_speed_10m_max
    - wind_gusts_10m_max
    - wind_direction_10m_dominant
    - uv_index_max
  air_quality_variables:
    - pm10
    - pm2_5
    - carbon_monoxide
    - nitrogen_dioxide
    - sulphur_dioxide
    - ozone
    - european_aqi
    - us_aqi
```

**models.py:**
- `Location` dataclass: name, latitude, longitude, timezone, labels dict
- `MetricPoint` dataclass: name, value (float), timestamp_ms (int), labels dict
- `MetricPoint.to_prometheus_line()` method that outputs valid Prometheus exposition format: `metric_name{label1="val1",label2="val2"} value timestamp_ms`

**collector.py - `OpenMeteoCollector` class:**
- Manages an `aiohttp.ClientSession` with configurable timeout
- `async close()` for cleanup
- Private `_fetch(endpoint, params)` method with tenacity retry decorator: 3 attempts, exponential backoff (min 2s, max 30s), retry on `aiohttp.ClientError` and `asyncio.TimeoutError`
- Check for Open-Meteo error responses in the JSON body (`"error": true`)
- `async collect_current_weather(location) -> list[MetricPoint]` - calls `/v1/forecast` with `current_weather=true`, maps fields: temperature→weather_temperature_celsius, windspeed→weather_wind_speed_kmh, winddirection→weather_wind_direction_degrees, weathercode→weather_condition_code, is_day→weather_is_day. Labels include location name, lat, lon, and any custom labels from config.
- `async collect_hourly(location, variables) -> list[MetricPoint]` - calls `/v1/forecast` with `hourly=<variables>&past_days=1&forecast_days=2`. Iterates time array, creates `weather_{variable_name}` metrics for each non-null value. Adds `frequency="hourly"` label. Parses ISO timestamps from the time array.
- `async collect_daily(location, variables) -> list[MetricPoint]` - calls `/v1/forecast` with `daily=<variables>&forecast_days=7`. Creates `weather_daily_{variable_name}` metrics. Skip string values like sunrise/sunset. Adds `frequency="daily"` label.
- `async collect_air_quality(location, variables) -> list[MetricPoint]` - calls `/v1/air-quality` with `hourly=<variables>`. Creates `air_quality_{variable_name}` metrics.

**exporter.py - `VictoriaMetricsExporter` class:**
- Manages its own `aiohttp.ClientSession`
- Posts to `{vm_url}{import_endpoint}` (both configurable)
- `async export(metrics: list[MetricPoint])` - converts to Prometheus lines, sends in configurable batch sizes
- Retry with tenacity: 3 attempts, exponential backoff
- Content-Type: text/plain
- Accept HTTP 200 or 204 as success
- Track internal counters for metrics_exported and export_errors
- Expose stats property

**main.py - `WeatherScraper` class:**
- Loads config via config.py
- Creates `Location` objects from config
- Runs 4 concurrent async loops via `asyncio.create_task`: current_weather, hourly, daily, air_quality
- Each loop: iterates all locations, gathers results concurrently with `asyncio.gather(*tasks, return_exceptions=True)`, exports collected metrics, sleeps for configured interval
- Wraps each collection call with prometheus_client monitoring:
  - `Histogram` for scrape duration (labels: scrape_type, location)
  - `Counter` for scrape errors (labels: scrape_type, location)
  - `Counter` for total metrics exported
  - `Gauge` for last scrape timestamp (label: scrape_type)
- `prometheus_client.start_http_server(9090)` for self-monitoring
- Graceful shutdown: handle SIGTERM and SIGINT, set `_running = False`, await cleanup of aiohttp sessions
- Structured logging throughout with structlog
- When `logging.format` is `json`, use structlog JSON renderer (suitable for k8s log aggregation)
- When `logging.format` is `console`, use structlog console renderer (for local dev)
- At startup, log: resolved config, number of locations, scrape intervals, target URLs
- At each scrape cycle, log: scrape type, location count, metrics collected, duration, any errors

**Dockerfile:**
- Base: `python:3.12-slim`
- Create non-root user `scraper` with UID 1000
- Install dependencies with `--no-cache-dir`
- Copy scraper package
- Do NOT copy config.yml into the image (config comes from ConfigMap in k8s)
- Expose port 9090
- Add healthcheck: `wget -qO- http://localhost:9090/metrics || exit 1`
- Run as non-root user
- Entrypoint: `["python", "-m", "scraper.main"]`
- Add labels: maintainer, description, version

### Helm Chart (`helm/weather-scraper/`)

**Chart.yaml:**
- apiVersion: v2
- name: weather-scraper
- description: "Weather data scraper collecting from Open-Meteo and exporting to Victoria Metrics"
- version: 0.1.0
- appVersion: "1.0.0"
- type: application

**values.yaml:**
```yaml
replicaCount: 1

image:
  repository: weather-scraper
  pullPolicy: IfNotPresent
  tag: ""     # Defaults to appVersion

imagePullSecrets: []
nameOverride: ""
fullnameOverride: ""

serviceAccount:
  create: true
  automount: true
  annotations: {}
  name: ""

podAnnotations:
  prometheus.io/scrape: "true"
  prometheus.io/port: "9090"
  prometheus.io/path: "/metrics"

podLabels: {}

podSecurityContext:
  runAsNonRoot: true
  runAsUser: 1000
  runAsGroup: 1000
  fsGroup: 1000

securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities:
    drop:
      - ALL

service:
  type: ClusterIP
  port: 9090
  annotations: {}

# -- Existing Victoria Metrics connection
victoriaMetrics:
  url: "http://victoria-metrics-victoria-metrics-single-server:8428"
  importEndpoint: "/api/v1/import/prometheus"
  batchSize: 1000

# -- Existing Open-Meteo connection
openMeteo:
  url: "http://open-meteo:8080"
  requestTimeout: 30

# -- Logging configuration
logging:
  level: "info"
  format: "json"

# -- Weather locations to monitor
locations:
  - name: "berlin"
    latitude: 52.52
    longitude: 13.41
    timezone: "Europe/Berlin"
    labels:
      country: "germany"
      region: "europe"
  - name: "new_york"
    latitude: 40.71
    longitude: -74.01
    timezone: "America/New_York"
    labels:
      country: "usa"
      region: "north_america"
  - name: "london"
    latitude: 51.51
    longitude: -0.13
    timezone: "Europe/London"
    labels:
      country: "uk"
      region: "europe"
  - name: "tokyo"
    latitude: 35.68
    longitude: 139.69
    timezone: "Asia/Tokyo"
    labels:
      country: "japan"
      region: "asia"

# -- Scrape intervals and variables
scrape:
  currentWeatherInterval: 300
  hourlyForecastInterval: 3600
  dailyForecastInterval: 21600
  airQualityInterval: 3600
  hourlyVariables:
    - temperature_2m
    - relative_humidity_2m
    - dewpoint_2m
    - apparent_temperature
    - pressure_msl
    - surface_pressure
    - cloudcover
    - wind_speed_10m
    - wind_direction_10m
    - wind_gusts_10m
    - precipitation
    - rain
    - snowfall
    - snow_depth
    - visibility
    - uv_index
    - is_day
  dailyVariables:
    - temperature_2m_max
    - temperature_2m_min
    - apparent_temperature_max
    - apparent_temperature_min
    - precipitation_sum
    - rain_sum
    - snowfall_sum
    - precipitation_hours
    - wind_speed_10m_max
    - wind_gusts_10m_max
    - wind_direction_10m_dominant
    - uv_index_max
  airQualityVariables:
    - pm10
    - pm2_5
    - carbon_monoxide
    - nitrogen_dioxide
    - sulphur_dioxide
    - ozone
    - european_aqi
    - us_aqi

resources:
  requests:
    cpu: 50m
    memory: 128Mi
  limits:
    cpu: 200m
    memory: 256Mi

# -- Horizontal Pod Autoscaler (disabled by default, single replica is usually sufficient)
autoscaling:
  enabled: false
  minReplicas: 1
  maxReplicas: 3
  targetCPUUtilizationPercentage: 80

# -- Pod Disruption Budget
podDisruptionBudget:
  enabled: false
  minAvailable: 1

# -- Prometheus ServiceMonitor for scraper self-monitoring
serviceMonitor:
  enabled: false
  interval: "30s"
  scrapeTimeout: "10s"
  additionalLabels: {}
  # e.g., release: kube-prometheus-stack

# -- Grafana dashboard ConfigMaps
# Enable if Grafana is using sidecar for dashboard provisioning (kube-prometheus-stack default)
grafanaDashboards:
  enabled: true
  # Label that Grafana sidecar watches for
  label: grafana_dashboard
  labelValue: "1"
  # Folder in Grafana where dashboards appear
  folder: "Weather"
  # Annotations for the ConfigMap
  annotations: {}

nodeSelector: {}
tolerations: []
affinity: {}

# -- Extra environment variables
extraEnv: []
#  - name: MY_VAR
#    value: "my-value"

# -- Extra volume mounts
extraVolumeMounts: []

# -- Extra volumes
extraVolumes: []

# -- Liveness and readiness probes
probes:
  liveness:
    enabled: true
    path: /metrics
    port: 9090
    initialDelaySeconds: 10
    periodSeconds: 30
    timeoutSeconds: 5
    failureThreshold: 3
  readiness:
    enabled: true
    path: /metrics
    port: 9090
    initialDelaySeconds: 5
    periodSeconds: 10
    timeoutSeconds: 5
    failureThreshold: 3
```

**templates/_helpers.tpl:**
- Standard Helm helpers: name, fullname, chart, labels (common + selector), serviceAccountName

**templates/configmap.yaml:**
- Render the scraper config.yml from values
- Mount path: `/etc/weather-scraper/config.yml`
- Map values.yaml fields to the config.yml structure the Python app expects:
  - `victoriaMetrics.url` → `victoria_metrics.url`
  - `openMeteo.url` → `open_meteo.base_url`
  - `locations` → `locations` (pass through as-is)
  - `scrape.*` → `scrape.*` (convert camelCase keys to snake_case)
  - `logging.*` → `logging.*`

**templates/deployment.yaml:**
- Standard Deployment with:
  - Pod annotations from values (including prometheus scrape annotations)
  - SecurityContext from values
  - Container running the scraper image
  - Environment variables:
    - `OPEN_METEO_URL` from `victoriaMetrics.url`
    - `VICTORIA_METRICS_URL` from `openMeteo.url`
    - `LOG_LEVEL` from `logging.level`
    - `LOG_FORMAT` from `logging.format`
    - `CONFIG_PATH` set to `/etc/weather-scraper/config.yml`
    - Any `extraEnv` from values
  - Volume mount: ConfigMap → `/etc/weather-scraper/`
  - tmpdir emptyDir volume mounted at `/tmp` (for readOnlyRootFilesystem)
  - Liveness probe: HTTP GET on metrics endpoint
  - Readiness probe: HTTP GET on metrics endpoint
  - Resource requests and limits from values
  - nodeSelector, tolerations, affinity from values
  - extraVolumes and extraVolumeMounts support

**templates/service.yaml:**
- ClusterIP service exposing port 9090

**templates/servicemonitor.yaml:**
- Conditional on `serviceMonitor.enabled`
- Standard Prometheus Operator ServiceMonitor
- Targets the scraper service port
- Configurable interval, scrapeTimeout, additionalLabels

**templates/configmap-dashboards.yaml:**
- Conditional on `grafanaDashboards.enabled`
- Creates one ConfigMap per dashboard JSON file
- Each ConfigMap labeled with `{{ .Values.grafanaDashboards.label }}: {{ .Values.grafanaDashboards.labelValue }}` so Grafana sidecar picks them up
- Annotation `grafana_folder: {{ .Values.grafanaDashboards.folder }}` to organize in Grafana
- Dashboard JSON loaded from `helm/weather-scraper/dashboards/*.json` using `.Files.Get`

**templates/poddisruptionbudget.yaml:**
- Conditional on `podDisruptionBudget.enabled`

**templates/serviceaccount.yaml:**
- Conditional on `serviceAccount.create`

**templates/hpa.yaml:**
- Conditional on `autoscaling.enabled`

**templates/NOTES.txt:**
- Print post-install instructions showing:
  - How to check scraper logs: `kubectl logs -f deployment/{{ .Release.Name }}`
  - How to port-forward to metrics: `kubectl port-forward svc/{{ .Release.Name }} 9090:9090`
  - How to verify VM is receiving data: curl command against VM query API
  - Note about Grafana dashboards being auto-provisioned (if enabled)
  - How to manually import dashboards if sidecar is not used

### Grafana Dashboards

Create dashboard JSON files in BOTH `helm/weather-scraper/dashboards/` and `dashboards/` (identical content, the standalone `dashboards/` dir is for manual import).

**Important for dashboards targeting existing Grafana:**
- Use `"datasource": {"type": "prometheus", "uid": "${DS_VICTORIAMETRICS}"}` as the datasource reference pattern
- Include a `__inputs` section at the top of each dashboard for import compatibility:
```json
"__inputs": [
  {
    "name": "DS_VICTORIAMETRICS",
    "label": "VictoriaMetrics",
    "description": "Victoria Metrics Prometheus-compatible datasource",
    "type": "datasource",
    "pluginId": "prometheus",
    "pluginName": "Prometheus"
  }
]
```
- Include `__requires` section listing grafana version and prometheus datasource
- For the ConfigMap-provisioned versions (in helm/), replace the `${DS_VICTORIAMETRICS}` variable with a hardcoded datasource uid or use `"datasource": {"type": "prometheus"}` to auto-select the default Prometheus datasource. Add a comment in the templates explaining this.

**1. weather-overview.json:**
- Template variable: `$location` populated from `label_values(weather_temperature_celsius, location)`, multi-select with "All" option
- Panels:
  - Gauge: Current temperature per location with color thresholds (blue<0, green 10-20, yellow 20-30, orange 30-35, red>35)
  - Time series: Temperature over time (smooth line, gradient fill) using `weather_temperature_2m{frequency="hourly", location=~"$location"}`
  - Time series: Wind speed + gusts overlay
  - Time series: Humidity (left Y axis, %) and Pressure (right Y axis, hPa) dual-axis
  - Bar chart: Precipitation in mm
  - Stat panel: UV Index with color thresholds (green<3, yellow 3-6, orange 6-8, red 8-11, purple>11)
  - Time series: Cloud cover percentage area fill
  - Stat panel row: Current conditions summary per location

**2. weather-detail.json:**
- Template variable `$location` single-select
- Panels:
  - Temperature: actual vs apparent temperature overlay time series
  - Temperature delta: `weather_apparent_temperature - weather_temperature_2m` (feels-like difference)
  - Dewpoint vs temperature time series
  - Wind direction scatter plot (wind_direction_10m vs wind_speed_10m)
  - Visibility time series
  - Snow depth time series
  - Daily high/low bar chart from weather_daily_temperature_2m_max and weather_daily_temperature_2m_min
  - 7-day forecast table using daily metrics (max temp, min temp, precipitation_sum, wind_speed_max)

**3. air-quality.json:**
- Template variable `$location`
- Panels:
  - EU AQI time series with threshold color bands (good<50, moderate 50-100, unhealthy 100-150, very unhealthy >150)
  - US AQI time series
  - PM2.5 and PM10 time series (dual series)
  - Ozone levels time series
  - NO2 and CO levels time series
  - SO2 levels time series
  - Stat panel: current AQI per location with color coding
  - Table: all current pollutant values per location

**4. scraper-health.json:**
- This dashboard targets the scraper's self-monitoring metrics (from prometheus-client on :9090)
- When deployed via Helm with ServiceMonitor, these metrics are in the same Prometheus/VM. When standalone, they may be on a separate datasource.
- Use the default Prometheus datasource.
- Panels:
  - Scrape duration heatmap: `weather_scrape_duration_seconds_bucket`
  - Error rate per scrape type: `rate(weather_scrape_errors_total[5m])`
  - Total metrics exported: `weather_metrics_exported_total`
  - Last successful scrape timestamps per type: `weather_last_scrape_timestamp`
  - Metrics export rate: `rate(weather_metrics_exported_total[5m])`
  - Scrape duration P50/P90/P99: `histogram_quantile(0.5, rate(weather_scrape_duration_seconds_bucket[5m]))`

### All dashboards should:
- Use Grafana JSON model schema version 38+
- Include unique panel IDs and non-overlapping grid positions
- Use `$location` template variable where applicable with proper regex filtering `location=~"$location"`
- Have sensible time range defaults (24h for current weather, 7d for forecasts)
- Include proper unit annotations (celsius, percent, pressurehpa, velocitykmh, lengthmm)
- Use weather-appropriate color thresholds
- Have meaningful panel titles and descriptions
- Work on both Grafana 9.x and 10.x

## README.md

Include a comprehensive README with these sections:

### Overview
- What the project does
- Architecture diagram (text-based/mermaid showing: Open-Meteo → Scraper → Victoria Metrics ← Grafana)

### Prerequisites
- Existing Kubernetes cluster
- Existing Victoria Metrics deployment
- Existing Grafana deployment
- Self-hosted Open-Meteo instance
- Helm 3.x
- Docker (for building the image)

### Quick Start
```bash
# Build the image
docker build -t weather-scraper:latest ./

# If using a private registry
docker tag weather-scraper:latest registry.example.com/weather-scraper:latest
docker push registry.example.com/weather-scraper:latest

# Install with Helm (minimum required values)
helm install weather-scraper ./helm/weather-scraper \
  --set openMeteo.url=http://your-open-meteo:8080 \
  --set victoriaMetrics.url=http://your-victoria-metrics:8428

# Install with custom locations
helm install weather-scraper ./helm/weather-scraper \
  -f my-values.yaml

# Verify
kubectl logs -f deployment/weather-scraper
kubectl port-forward svc/weather-scraper 9090:9090
curl -s http://localhost:9090/metrics
```

### Configuration
- Full values.yaml reference
- How to configure locations
- How to adjust scrape intervals
- How to connect to your specific VM and Open-Meteo instances
- Environment variable overrides

### Grafana Dashboards
- How dashboards are auto-provisioned via sidecar when `grafanaDashboards.enabled=true`
- How to manually import from `dashboards/` directory
- How to set the correct datasource during manual import
- Screenshots or descriptions of each dashboard

### Available Metrics
- Table listing all weather metrics, their labels, and units
- Table listing all self-monitoring metrics

### Useful PromQL Queries
```
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

### Troubleshooting
- How to check scraper logs
- How to verify data in Victoria Metrics directly via curl
- Common issues: DNS resolution for service names, network policies blocking traffic, VM import endpoint path differences
- How to run the scraper locally for debugging (docker run with env vars or mounted config)

### Local Development
- How to run outside k8s with docker or plain Python
- Example docker run command with env vars pointing to external VM and Open-Meteo
- Example: `docker run -e OPEN_METEO_URL=http://host:8080 -e VICTORIA_METRICS_URL=http://host:8428 -v $(pwd)/config.yml:/etc/weather-scraper/config.yml weather-scraper:latest`

## Critical Requirements
- All target URLs (Open-Meteo, Victoria Metrics) MUST be configurable via values.yaml and env vars. Nothing hardcoded.
- The scraper must run as non-root in k8s with read-only root filesystem.
- The scraper must handle graceful shutdown on SIGTERM (k8s pod termination).
- The scraper must survive and recover from temporary failures of both Open-Meteo and Victoria Metrics (retry logic, backoff, don't crash).
- All code must have docstrings and type hints.
- Structured JSON logging by default (for k8s log aggregation).
- Build all files completely. Do not leave any placeholder or TODO comments. Every file must be production-ready and fully functional.
- All Helm templates must pass `helm lint` and `helm template` without errors.
- Dashboard JSONs must be valid JSON and valid Grafana dashboard models.
```

---

## Usage

```bash
# Save the prompt to a file
cat > prompt.md << 'PROMPT'
<paste the above>
PROMPT

# Pass to Claude Code
claude "Read prompt.md and build everything described in it"

# After generation, validate
helm lint helm/weather-scraper/
helm template test helm/weather-scraper/ \
  --set openMeteo.url=http://open-meteo:8080 \
  --set victoriaMetrics.url=http://vm:8428

# Build and deploy
docker build -t weather-scraper:latest .
helm install weather-scraper ./helm/weather-scraper \
  --set image.repository=weather-scraper \
  --set image.tag=latest \
  --set openMeteo.url=http://your-open-meteo:8080 \
  --set victoriaMetrics.url=http://your-vm:8428 \
  --set serviceMonitor.enabled=true \
  --set serviceMonitor.additionalLabels.release=kube-prometheus-stack
```

Key changes from the Docker Compose version:

- **No VM or Grafana in the deployment** — they're existing infrastructure, just connection targets
- **Helm chart** replaces docker-compose for k8s-native packaging
- **ConfigMap** for config injection instead of bind mounts
- **ServiceMonitor** for Prometheus Operator integration
- **Dashboard ConfigMaps** with Grafana sidecar labels for automatic provisioning
- **Standalone dashboard JSONs** with `__inputs` for manual import into existing Grafana
- **Environment variable overrides** for the critical connection URLs
- **Pod security** hardened (non-root, read-only rootfs, dropped capabilities)
- **Probes, PDB, HPA** for production readiness
- **JSON structured logging** as default for k8s log pipelines
