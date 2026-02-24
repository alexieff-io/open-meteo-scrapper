# Backfill Mode

The scraper supports a one-shot backfill mode for loading historical weather data into Victoria Metrics. It collects hourly forecasts, daily forecasts, and air quality data for all configured locations, exports the metrics, then exits.

## Usage

```bash
# Full 92-day backfill (default)
python -m scraper.main --backfill

# Custom number of past days
python -m scraper.main --backfill --past-days 30

# With a specific config file
python -m scraper.main --config config.yml --backfill --past-days 30

# Docker
docker run weather-scraper --backfill --past-days 45
```

## Options

| Flag | Description | Default |
|------|-------------|---------|
| `--backfill` | Enable backfill mode (run once and exit) | Off |
| `--past-days N` | Number of historical days to collect (1-92) | 92 |

The `--past-days` limit of 92 comes from the Open-Meteo forecast API maximum.

## What It Does

Backfill runs three sequential phases:

1. **Hourly forecast** — collects all configured `hourly_variables` for the past N days
2. **Daily forecast** — collects all configured `daily_variables` for the past N days
3. **Air quality** — collects all configured `air_quality_variables` for the past N days

Each phase collects data from all configured locations concurrently (respecting `max_concurrent_locations`), then exports the batch to Victoria Metrics.

Current weather is not included since it only provides a point-in-time snapshot.

## Differences from Normal Mode

| | Normal Mode | Backfill Mode |
|---|---|---|
| Prometheus server | Started on :9090 | Not started |
| Execution | Continuous loop | Single run, then exit |
| `past_days` (hourly) | 1 | User-specified (default 92) |
| `forecast_days` | Varies by type | 0 (historical only) |
| Exit code | N/A (runs until stopped) | 0 on success, 1 on failure |

## Examples

### Quick test with 2 days

```bash
python -m scraper.main --config config.yml --backfill --past-days 2
```

Expected log output:

```
backfill_starting (past_days=2, locations=N)
backfill_phase (phase=hourly_forecast)
scrape_cycle_complete (metrics_collected=...)
backfill_phase (phase=daily_forecast)
scrape_cycle_complete (metrics_collected=...)
backfill_phase (phase=air_quality)
scrape_cycle_complete (metrics_collected=...)
backfill_complete
```

### Full backfill then start normal scraping

```bash
python -m scraper.main --config config.yml --backfill && \
python -m scraper.main --config config.yml
```
