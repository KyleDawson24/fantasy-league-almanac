"""Tests for output/sheets_target.resolve_sheets_target -- the dev/prod
Sheets target resolver (v1.3).

Pure-function: no Snowflake, no network, so this runs in the default
(non-warehouse) suite. conftest.py puts output/ on sys.path.
"""

import pytest

import sheets_target


_SHEET_VARS = ('SHEETS_DEV_ID', 'SHEETS_PROD_ID', 'SHEETS_OUTPUT_ID')


@pytest.fixture(autouse=True)
def _clear_sheet_env(monkeypatch):
    """Start every test from a clean slate -- no sheet env vars set."""
    for var in _SHEET_VARS:
        monkeypatch.delenv(var, raising=False)


def test_dev_is_the_default_target(monkeypatch):
    monkeypatch.setenv('SHEETS_DEV_ID', 'dev123')
    assert sheets_target.resolve_sheets_target(False) == ('dev123', 'dev')


def test_dev_unset_returns_none_for_preview_only(monkeypatch):
    assert sheets_target.resolve_sheets_target(False) == (None, 'dev')


def test_dev_empty_string_normalized_to_none(monkeypatch):
    monkeypatch.setenv('SHEETS_DEV_ID', '')
    assert sheets_target.resolve_sheets_target(False) == (None, 'dev')


def test_prod_uses_prod_id(monkeypatch):
    monkeypatch.setenv('SHEETS_DEV_ID', 'dev123')
    monkeypatch.setenv('SHEETS_PROD_ID', 'prod456')
    assert sheets_target.resolve_sheets_target(True) == ('prod456', 'PROD')


def test_prod_falls_back_to_legacy_output_id(monkeypatch):
    monkeypatch.setenv('SHEETS_OUTPUT_ID', 'legacy789')
    assert sheets_target.resolve_sheets_target(True) == ('legacy789', 'PROD')


def test_prod_prefers_prod_id_over_legacy_alias(monkeypatch):
    monkeypatch.setenv('SHEETS_PROD_ID', 'prod456')
    monkeypatch.setenv('SHEETS_OUTPUT_ID', 'legacy789')
    assert sheets_target.resolve_sheets_target(True) == ('prod456', 'PROD')


def test_prod_without_any_id_raises(monkeypatch):
    with pytest.raises(RuntimeError, match='no production sheet'):
        sheets_target.resolve_sheets_target(True)


# ---------------------------------------------------------------------------
# Registry-league resolution (MLB-58). A lightweight stand-in for
# config.league_registry.League -- the resolver only touches .key and
# .sinks.
# ---------------------------------------------------------------------------

class _FakeLeague:
    def __init__(self, key, sinks):
        self.key = key
        self.sinks = sinks


_CBS = _FakeLeague('cbs-bsb', {
    'bbcode': False,
    'sheets_almanac_env': 'CBS_SHEETS_OUTPUT_ID',
    'sheets_dev_env': 'CBS_SHEETS_DEV_ID',
})


@pytest.fixture(autouse=True)
def _clear_cbs_env(monkeypatch):
    for var in ('CBS_SHEETS_OUTPUT_ID', 'CBS_SHEETS_DEV_ID'):
        monkeypatch.delenv(var, raising=False)


def test_league_dev_resolves_registry_named_var(monkeypatch):
    monkeypatch.setenv('CBS_SHEETS_DEV_ID', 'cbsdev1')
    assert sheets_target.resolve_sheets_target(False, _CBS) == ('cbsdev1', 'dev')


def test_league_dev_unset_is_preview_only(monkeypatch):
    assert sheets_target.resolve_sheets_target(False, _CBS) == (None, 'dev')


def test_league_without_dev_sink_is_preview_only(monkeypatch):
    league = _FakeLeague('x', {'sheets_almanac_env': 'CBS_SHEETS_OUTPUT_ID'})
    assert sheets_target.resolve_sheets_target(False, league) == (None, 'dev')


def test_league_prod_resolves_registry_named_var(monkeypatch):
    monkeypatch.setenv('CBS_SHEETS_OUTPUT_ID', 'cbsprod1')
    assert sheets_target.resolve_sheets_target(True, _CBS) == ('cbsprod1', 'PROD')


def test_league_prod_without_sink_raises_loudly(monkeypatch):
    league = _FakeLeague('nosink', {'bbcode': False})
    with pytest.raises(RuntimeError, match="'nosink' has no production sheet"):
        sheets_target.resolve_sheets_target(True, league)


def test_league_prod_with_sink_but_unset_var_raises_loudly(monkeypatch):
    with pytest.raises(RuntimeError, match='CBS_SHEETS_OUTPUT_ID'):
        sheets_target.resolve_sheets_target(True, _CBS)


def test_league_prod_ignores_global_prod_override_for_non_legacy_sinks(monkeypatch):
    # SHEETS_PROD_ID is the ESPN pair's migration shim -- it must never
    # leak into a league with its own sink variables.
    monkeypatch.setenv('SHEETS_PROD_ID', 'espnprod')
    monkeypatch.setenv('CBS_SHEETS_OUTPUT_ID', 'cbsprod1')
    assert sheets_target.resolve_sheets_target(True, _CBS) == ('cbsprod1', 'PROD')


def test_espn_league_keeps_legacy_prod_override(monkeypatch):
    espn = _FakeLeague('espn-main', {
        'bbcode': True,
        'sheets_almanac_env': 'SHEETS_OUTPUT_ID',
        'sheets_dev_env': 'SHEETS_DEV_ID',
    })
    monkeypatch.setenv('SHEETS_OUTPUT_ID', 'legacy789')
    monkeypatch.setenv('SHEETS_PROD_ID', 'prod456')
    assert sheets_target.resolve_sheets_target(True, espn) == ('prod456', 'PROD')


def test_espn_league_registry_prod_matches_legacy_chain(monkeypatch):
    espn = _FakeLeague('espn-main', {
        'sheets_almanac_env': 'SHEETS_OUTPUT_ID',
        'sheets_dev_env': 'SHEETS_DEV_ID',
    })
    monkeypatch.setenv('SHEETS_OUTPUT_ID', 'legacy789')
    assert (sheets_target.resolve_sheets_target(True, espn)
            == sheets_target.resolve_sheets_target(True))
    monkeypatch.setenv('SHEETS_DEV_ID', 'dev123')
    assert (sheets_target.resolve_sheets_target(False, espn)
            == sheets_target.resolve_sheets_target(False))
