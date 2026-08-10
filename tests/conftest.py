"""Shared pytest scaffolding.

Adds output/ to sys.path so test files can `import records`, `import
formatters`, etc. — matching how the output scripts import each other
as siblings.

Phase 7 test scope is pure-function: every test in this directory must
run without a Snowflake connection. Functions that hit the warehouse
(query_snowflake, anything that calls it directly) are out of scope
here; covering those would need a fixture warehouse (DuckDB POC,
deferred to v1.x).
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OUTPUT_DIR = _REPO_ROOT / 'output'

if str(_OUTPUT_DIR) not in sys.path:
    sys.path.insert(0, str(_OUTPUT_DIR))

# Repo root on the path too, so tests can import the league registry the
# same way the edge scripts do: `from config.league_registry import ...`
# (config/ is a namespace package; no __init__.py needed).
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# The slot catalog, stubbed
# ---------------------------------------------------------------------------
#
# MLB-222 F-1 gave the Python layer a slot vocabulary read from the
# `slot_classification` seed, and any query builder that renders a slot list
# now reaches the warehouse to get it. That broke this module's opening
# promise -- every test here runs without a Snowflake connection -- in a way
# no local run could show:
#
#   `slot_catalog` does `from db import query_snowflake`, which BINDS THE
#   NAME IN THAT MODULE. A test that patches `almanac_data.query_snowflake`
#   rebinds a different module's name, so the catalog's own call goes
#   straight through to a real connection.
#
# It passed locally only because `_load_catalog` is lru_cached: on a machine
# with credentials the first caller in the session connects for real and
# every later test reads a warm cache. On CI, where there are no
# credentials, the first attempt is the failure. Order-dependent and
# environment-dependent at once, which is why "green on my machine" was
# worth nothing here.
#
# Caches are cleared on the way IN so a cache warmed by an earlier test
# cannot mask a missing stub, and on the way OUT so this stub cannot leak
# into a warehouse-marked test that wants the real seed. Cleared by scanning
# the module for anything carrying `cache_clear`, so a helper added to
# slot_catalog later is covered without editing this file -- the caches are
# a set, and naming three of them here would rot the first time a fourth
# appeared.

import pytest

# The ESPN rows the query builders actually ask about, verbatim from
# dbt_league/seeds/slot_classification.csv, plus one CBS row. The CBS row is
# load-bearing rather than decorative: it is `inactive` too, so a stub
# without it would still pass if the platform filter broke, and with it the
# ESPN answer stays {BE, IL, FA} only while that filter works.
_SLOT_CATALOG_ROWS = (
    {'platform': 'espn', 'lineup_slot': 'C', 'slot_category': 'hitting',
     'is_starting_slot': True, 'sort_order': 10, 'notes': None},
    {'platform': 'espn', 'lineup_slot': 'UTIL', 'slot_category': 'hitting',
     'is_starting_slot': True, 'sort_order': 120, 'notes': None},
    {'platform': 'espn', 'lineup_slot': 'P', 'slot_category': 'pitching',
     'is_starting_slot': True, 'sort_order': 130, 'notes': None},
    {'platform': 'espn', 'lineup_slot': 'SP', 'slot_category': 'pitching',
     'is_starting_slot': True, 'sort_order': 140, 'notes': None},
    {'platform': 'espn', 'lineup_slot': 'RP', 'slot_category': 'pitching',
     'is_starting_slot': True, 'sort_order': 150, 'notes': None},
    {'platform': 'espn', 'lineup_slot': 'BE', 'slot_category': 'inactive',
     'is_starting_slot': False, 'sort_order': 900, 'notes': None},
    {'platform': 'espn', 'lineup_slot': 'IL', 'slot_category': 'inactive',
     'is_starting_slot': False, 'sort_order': 910, 'notes': None},
    {'platform': 'espn', 'lineup_slot': 'FA', 'slot_category': 'inactive',
     'is_starting_slot': False, 'sort_order': 920, 'notes': 'SYNTHETIC.'},
    {'platform': 'cbs', 'lineup_slot': 'RS', 'slot_category': 'inactive',
     'is_starting_slot': False, 'sort_order': 900, 'notes': None},
)


def _clear_slot_catalog_caches(slot_catalog):
    for name in dir(slot_catalog):
        clear = getattr(getattr(slot_catalog, name, None), 'cache_clear', None)
        if clear is not None:
            clear()


@pytest.fixture
def stub_slot_catalog(monkeypatch):
    """Serve the slot vocabulary from a literal, with no warehouse.

    Patch this ALONGSIDE the query_snowflake your test already patches --
    patching one module's name never reaches the other's.
    """
    import slot_catalog

    _clear_slot_catalog_caches(slot_catalog)
    monkeypatch.setattr(
        slot_catalog, 'query_snowflake',
        lambda sql, params=None: list(_SLOT_CATALOG_ROWS))
    yield
    _clear_slot_catalog_caches(slot_catalog)
