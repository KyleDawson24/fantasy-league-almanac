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
#
# `canonical_key` rides every row because the seed has it and Python now
# READS it (MLB-243): slot_catalog.canonical_lineup_slot resolves U/UTIL to
# one key from this column rather than from a Python alias table. A stub
# without it would leave that function silently un-normalizing.
_SLOT_CATALOG_ROWS = (
    {'platform': 'espn', 'lineup_slot': 'C', 'slot_category': 'hitting',
     'is_starting_slot': True, 'sort_order': 10, 'notes': None,
     'canonical_key': 'catcher'},
    {'platform': 'espn', 'lineup_slot': 'UTIL', 'slot_category': 'hitting',
     'is_starting_slot': True, 'sort_order': 120, 'notes': None,
     'canonical_key': 'utility'},
    {'platform': 'espn', 'lineup_slot': 'P', 'slot_category': 'pitching',
     'is_starting_slot': True, 'sort_order': 130, 'notes': None,
     'canonical_key': 'pitcher'},
    {'platform': 'espn', 'lineup_slot': 'SP', 'slot_category': 'pitching',
     'is_starting_slot': True, 'sort_order': 140, 'notes': None,
     'canonical_key': 'starting_pitcher'},
    {'platform': 'espn', 'lineup_slot': 'RP', 'slot_category': 'pitching',
     'is_starting_slot': True, 'sort_order': 150, 'notes': None,
     'canonical_key': 'relief_pitcher'},
    {'platform': 'espn', 'lineup_slot': 'BE', 'slot_category': 'inactive',
     'is_starting_slot': False, 'sort_order': 900, 'notes': None,
     'canonical_key': 'bench'},
    {'platform': 'espn', 'lineup_slot': 'IL', 'slot_category': 'inactive',
     'is_starting_slot': False, 'sort_order': 910, 'notes': None,
     'canonical_key': 'injured'},
    {'platform': 'espn', 'lineup_slot': 'FA', 'slot_category': 'inactive',
     'is_starting_slot': False, 'sort_order': 920, 'notes': 'SYNTHETIC.',
     'canonical_key': 'free_agent'},
    {'platform': 'cbs', 'lineup_slot': 'RS', 'slot_category': 'inactive',
     'is_starting_slot': False, 'sort_order': 900, 'notes': None,
     'canonical_key': 'bench'},
    # The CBS spelling of the ESPN UTIL row above -- the pair whose shared
    # canonical_key is the whole reason Python reads this column.
    {'platform': 'cbs', 'lineup_slot': 'U', 'slot_category': 'hitting',
     'is_starting_slot': True, 'sort_order': 120, 'notes': None,
     'canonical_key': 'utility'},
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


# ---------------------------------------------------------------------------
# The stat catalog, stubbed
# ---------------------------------------------------------------------------
#
# The exact twin of the slot-catalog stub above, for the same reason and
# with the same failure mode -- found the hard way when the v1.9.0 release
# bundle was tested as a stranger would test it (2026-08-16).
#
# `stat_catalog` does `from db import query_snowflake`, which BINDS THE
# NAME IN THAT MODULE, so patching some other module's `query_snowflake`
# never reaches it. And `almanac_logic.build_records_tab_rows` defaults
# `display_map` to `stat_catalog.get_display_map()` and always reads
# `get_rate_qualifiers()` -- so a "pure" records test opens a real
# Snowflake connection unless this fixture is applied.
#
# It passed on a maintainer's machine only because `.env` supplies
# credentials and every accessor is lru_cached: the first caller in the
# session connects for real and the rest read a warm cache. Extract the
# release ZIP, where there is no `.env`, and ten tests die in
# `snowflake.connector.util_text.construct_hostname` on a None account --
# a connection attempt, not an assertion. Same order-and-environment
# dependence the slot stub was written to kill.
#
# Caches are cleared on the way IN so a cache warmed by an earlier test
# cannot mask a missing stub, and on the way OUT so this stub cannot leak
# into a warehouse-marked test that wants the real `dim_stat`. Cleared by
# scanning the module for anything carrying `cache_clear`, so an accessor
# added to stat_catalog later is covered without editing this file.

# Rows shaped exactly like `dim_stat` -- every column `_load_catalog`
# selects -- and carrying the REAL qualifier gates (hitting rates on
# ab >= 225, pitching rates on outs >= 150, so IP >= 50). Faithful values
# rather than invented ones, because the records caption prints them: a
# stub that drifted from the seed would make the test assert a sentence
# the product never shows.
def _stat_row(leaderboard_name, display_name, abbrev, category, polarity,
              qualifier_stat=None, qualifier_min=None, auto_tracked=False,
              is_counting=True, derivation_expr=None):
    return {
        'stat_name': leaderboard_name,
        'leaderboard_name': leaderboard_name,
        'espn_stat_id': None,
        'stat_category': category,
        'espn_stat_label': abbrev,
        'display_name': display_name,
        'abbrev': abbrev,
        'is_counting': is_counting,
        'is_derived': derivation_expr is not None,
        'derivation_expr': derivation_expr,
        'auto_tracked': auto_tracked,
        'is_record_candidate': True,
        'polarity': polarity,
        'qualifier_stat': qualifier_stat,
        'qualifier_min': qualifier_min,
        'notes': None,
    }


_STAT_CATALOG_ROWS = (
    # Points -- what the record book's Score Records section reads.
    _stat_row('CALCULATED_POINTS', 'Total Points', 'CALCULATED_POINTS',
              'total', 'positive'),
    _stat_row('CALCULATED_HITTING_PTS', 'Hitting Points',
              'CALCULATED_HITTING_PTS', 'hitting', 'positive'),
    _stat_row('CALCULATED_PITCHING_PTS', 'Pitching Points',
              'CALCULATED_PITCHING_PTS', 'pitching', 'positive'),
    _stat_row('WASTED_POINTS', 'Wasted Points', 'Wasted', 'total',
              'negative'),
    # Counting stats the synthetic record rows in these tests name.
    _stat_row('HR', 'Home Runs', 'HR', 'hitting', 'positive',
              auto_tracked=True),
    _stat_row('K', 'Strikeouts (Pitcher)', 'K', 'pitching', 'positive'),
    # Hitting rates -- the AB gate the caption quotes.
    _stat_row('AVG', 'Batting Average', 'AVG', 'hitting', 'neutral',
              'ab', 225, is_counting=False),
    _stat_row('OBP', 'On Base Percentage', 'OBP', 'hitting', 'neutral',
              'ab', 225, is_counting=False),
    _stat_row('SLG', 'Slugging Percentage', 'SLG', 'hitting', 'neutral',
              'ab', 225, is_counting=False),
    _stat_row('OPS', 'On Base Plus Slugging', 'OPS', 'hitting', 'neutral',
              'ab', 225, is_counting=False),
    # Pitching rates -- the OUTS gate, which the caption divides by 3 for IP.
    _stat_row('ERA', 'ERA', 'ERA', 'pitching', 'negative',
              'outs', 150, is_counting=False),
    _stat_row('WHIP', 'WHIP', 'WHIP', 'pitching', 'negative',
              'outs', 150, is_counting=False),
    _stat_row('K_PER_9', 'K/9', 'K/9', 'pitching', 'positive',
              'outs', 150, is_counting=False),
    _stat_row('BB_PER_9', 'BB/9', 'BB/9', 'pitching', 'negative',
              'outs', 150, is_counting=False),
    _stat_row('K_PER_BB', 'K/BB', 'K/BB', 'pitching', 'positive',
              'outs', 150, is_counting=False),
    _stat_row('HR_PER_9', 'HR/9', 'HR/9', 'pitching', 'negative',
              'outs', 150, is_counting=False),
)


def _clear_stat_catalog_caches(stat_catalog):
    for name in dir(stat_catalog):
        clear = getattr(getattr(stat_catalog, name, None), 'cache_clear', None)
        if clear is not None:
            clear()


@pytest.fixture
def stub_stat_catalog(monkeypatch):
    """Serve the stat vocabulary from a literal, with no warehouse.

    Patch this ALONGSIDE the query_snowflake your test already patches --
    patching one module's name never reaches the other's.
    """
    import stat_catalog

    _clear_stat_catalog_caches(stat_catalog)
    monkeypatch.setattr(
        stat_catalog, 'query_snowflake',
        lambda sql, params=None: list(_STAT_CATALOG_ROWS))
    yield
    _clear_stat_catalog_caches(stat_catalog)
