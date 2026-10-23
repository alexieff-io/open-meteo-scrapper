"""Dashboards must parse and the Helm chart must ship the current top-level edits."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / 'dashboards'
CHART_DIR = ROOT / 'helm' / 'weather-scraper' / 'dashboards'
NAMES = sorted(path.name for path in SOURCE_DIR.glob('*.json'))
# Import-only keys: the chart copies are sidecar-provisioned, so they carry neither
# Grafana's import inputs nor an instance-specific id.
IMPORT_ONLY = ('__inputs', '__requires', 'id')


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def without_import_fields(dashboard):
    text = json.dumps({k: v for k, v in dashboard.items() if k not in IMPORT_ONLY})
    return json.loads(text.replace('"uid": "${DS_VICTORIAMETRICS}", ', '').replace(', "uid": "${DS_VICTORIAMETRICS}"', ''))


def test_chart_and_source_list_the_same_dashboards():
    assert NAMES == sorted(path.name for path in CHART_DIR.glob('*.json'))


@pytest.mark.parametrize('path', [*sorted(SOURCE_DIR.glob('*.json')), *sorted(CHART_DIR.glob('*.json'))],
                         ids=lambda path: path.relative_to(ROOT).as_posix())
def test_dashboard_parses(path):
    assert isinstance(load(path), dict)


@pytest.mark.parametrize('name', NAMES)
def test_chart_copy_is_sidecar_format(name):
    dashboard = load(CHART_DIR / name)
    assert not {'__inputs', '__requires'} & dashboard.keys()
    assert dashboard.get('id') is None
    assert '${DS_' not in json.dumps(dashboard)


@pytest.mark.parametrize('name', NAMES)
def test_chart_copy_matches_source(name):
    assert without_import_fields(load(CHART_DIR / name)) == without_import_fields(load(SOURCE_DIR / name))
