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
