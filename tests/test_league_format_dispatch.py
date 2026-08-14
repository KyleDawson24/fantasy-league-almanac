"""The canonical format contract and what it dispatches (MLB-243).

THE BUG THIS PINS. The renderer chose the head-to-head almanac unless
`mart_period_standings` had rows -- a CBS-shaped feed standing in for "is
this a points league". An ESPN season-long points league
(`currentLeagueType = 5`) delivers no period standings, so the first real
stranger rehearsal was handed a matchup workbook over a league that has
never played a matchup, and nothing failed loudly enough to notice.

So the tests here are about the DECISION, not the drawing:

  * format comes from `dim_league_format` and nowhere else;
  * a platform name cannot override it, in either direction;
  * `unknown` raises instead of quietly meaning H2H.

Pure: `db.query_snowflake` is stubbed, so nothing here reaches a
warehouse. Every league in this file is synthetic.
"""
from __future__ import annotations

import pytest

import db
import generate_almanac_sheet as gas
import league_format


class _Parser:
    def error(self, message):          # argparse's contract: never returns
        raise SystemExit(f'parser.error: {message}')


class _Args:
    def __init__(self, **kw):
        defaults = dict(no_sheets=True, preview_dir=None, prod=False,
                        print_all=False, new_public_workbook=False,
                        include_trades=False, season_year=None,
                        matchup_period=None)
        defaults.update(kw)
        for key, value in defaults.items():
            setattr(self, key, value)


def _dimension(monkeypatch, rows, key='espn-main'):
    """Stub the one query league_format makes."""
    monkeypatch.setattr(db, 'league_key', lambda: key)
    monkeypatch.setattr(league_format.db, 'league_key', lambda: key)
    monkeypatch.setattr(league_format.db, 'query_snowflake',
                        lambda sql, params=None: rows)


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------

def test_espn_league_type_5_resolves_to_points(monkeypatch):
    """THE REHEARSAL CASE. No period standings, no matchups, a season-points
    schedule -- the dimension says points, and so must we."""
    _dimension(monkeypatch, [{'league_format': 'points'}])
    assert league_format.resolve() == league_format.POINTS
    assert league_format.is_points() is True


def test_h2h_still_resolves_to_h2h(monkeypatch):
    _dimension(monkeypatch, [{'league_format': 'h2h'}], key='espn-main')
    assert league_format.resolve() == league_format.H2H
    assert league_format.is_points() is False


@pytest.mark.parametrize('platform_key,fmt', [
    # An ESPN league that is a points league.
    ('espn-main', 'points'),
    # A CBS league that is head-to-head. Hypothetical today and precisely
    # the case a platform check gets wrong.
    ('cbs-bsb', 'h2h'),
])
def test_platform_name_cannot_override_format(monkeypatch, platform_key, fmt):
    """FORMAT decides the workbook; PLATFORM decides the data.

    Both directions matter. 'ESPN therefore H2H' is the bug that shipped;
    'CBS therefore points' is the same mistake wearing the other hat, and
    it would misfile a CBS head-to-head league the day one appears.
    """
    _dimension(monkeypatch, [{'league_format': fmt}], key=platform_key)
    assert league_format.resolve() == fmt


def test_unknown_format_raises_rather_than_defaulting_to_h2h(monkeypatch):
    """The silent default IS the defect. A league that has told us nothing
    must produce a message, not a workbook."""
    _dimension(monkeypatch, [{'league_format': 'unknown'}])
    with pytest.raises(league_format.LeagueFormatError) as exc:
        league_format.resolve()
    assert 'unknown' in str(exc.value).lower()
    # The refusal has to be legible, not just correct.
    assert 'refusing' in str(exc.value).lower()


def test_a_league_absent_from_the_dimension_raises(monkeypatch):
    _dimension(monkeypatch, [])
    with pytest.raises(league_format.LeagueFormatError, match='no row'):
        league_format.resolve()


def test_a_missing_dimension_names_the_remedy(monkeypatch):
    """A warehouse built before the model existed is a fixable state, so
    the error says how to fix it rather than reading as a crash."""
    def _explode(sql, params=None):
        raise RuntimeError('Catalog Error: Table with name '
                           'dim_league_format does not exist!')

    monkeypatch.setattr(league_format.db, 'league_key', lambda: 'espn-main')
    monkeypatch.setattr(league_format.db, 'query_snowflake', _explode)
    with pytest.raises(league_format.LeagueFormatError, match='dbt build'):
        league_format.resolve()


def test_a_real_database_error_is_not_swallowed(monkeypatch):
    """Only "the relation is missing" becomes a format error. Everything
    else is a genuine failure and must keep its own traceback."""
    def _explode(sql, params=None):
        raise RuntimeError('connection reset by peer')

    monkeypatch.setattr(league_format.db, 'league_key', lambda: 'espn-main')
    monkeypatch.setattr(league_format.db, 'query_snowflake', _explode)
    with pytest.raises(RuntimeError, match='connection reset'):
        league_format.resolve()


def test_is_points_never_reports_a_failure_as_false(monkeypatch):
    """`is_points()` returning False on an error would restore the exact
    silent-H2H default this module exists to remove."""
    _dimension(monkeypatch, [{'league_format': 'unknown'}])
    with pytest.raises(league_format.LeagueFormatError):
        league_format.is_points()


# ---------------------------------------------------------------------------
# What the contract dispatches
# ---------------------------------------------------------------------------

def test_a_points_league_takes_the_points_path(monkeypatch):
    monkeypatch.setattr(gas.league_format, 'resolve',
                        lambda *a, **kw: league_format.POINTS)
    taken = []
    monkeypatch.setattr(gas, '_run_points_league_almanac',
                        lambda args, parser: taken.append('points'))
    gas._generate(_Args(), _Parser())
    assert taken == ['points']


def test_an_h2h_league_does_not_take_the_points_path(monkeypatch):
    monkeypatch.setattr(gas.league_format, 'resolve',
                        lambda *a, **kw: league_format.H2H)

    def _explode(args, parser):
        raise AssertionError('an H2H league was routed to the points almanac')

    monkeypatch.setattr(gas, '_run_points_league_almanac', _explode)
    monkeypatch.setattr(gas.almanac_sheets, 'get_latest_matchup_period',
                        lambda: (_ for _ in ()).throw(
                            _ReachedH2H('the H2H path ran')))
    with pytest.raises(_ReachedH2H):
        gas._generate(_Args(), _Parser())


def test_an_unknown_format_stops_the_run_with_the_diagnostic(monkeypatch):
    """It must not fall through to either renderer."""
    def _raise(*a, **kw):
        raise league_format.LeagueFormatError('I cannot tell what this is')

    monkeypatch.setattr(gas.league_format, 'resolve', _raise)
    monkeypatch.setattr(gas, '_run_points_league_almanac',
                        lambda *a: (_ for _ in ()).throw(
                            AssertionError('unknown routed to points')))
    with pytest.raises(SystemExit, match='I cannot tell what this is'):
        gas._generate(_Args(), _Parser())


# ---------------------------------------------------------------------------
# Adapter selection -- the ONE thing platform is allowed to decide
# ---------------------------------------------------------------------------

class _League:
    def __init__(self, platform, key='x'):
        self.platform = platform
        self.key = key
        self.display_name = key


def test_the_data_adapter_is_chosen_by_platform(monkeypatch):
    """Platform picks WHERE THE NUMBERS COME FROM -- and only that. Both
    adapters render the same points-format product."""
    monkeypatch.setattr(gas.db, 'league', lambda: _League('cbs', 'cbs-bsb'))
    _build, write, public = gas._points_adapter(_Parser())
    assert write is gas.cbs_almanac_sheets.write_cbs_almanac
    assert public is None, (
        'the CBS writer authorizes its own maintainer client, so it cannot '
        'render into a drive.file workbook'
    )

    monkeypatch.setattr(gas.db, 'league', lambda: _League('espn', 'espn-main'))
    _build, write, public = gas._points_adapter(_Parser())
    assert write is gas.points_almanac.write_points_almanac
    assert public is gas.points_almanac.write_points_almanac


def test_an_adapter_without_a_public_renderer_refuses_cleanly(monkeypatch):
    """Removing the blanket refusal must not turn CBS into a TypeError.

    Under `drive.file` only the client that CREATED a workbook may open it,
    so a writer that authorizes its own client cannot render into one. That
    is a capability fact about the adapter, and it gets a sentence rather
    than a traceback.
    """
    monkeypatch.setattr(gas.db, 'league', lambda: _League('cbs', 'cbs-bsb'))
    monkeypatch.setattr(gas.db, 'league_key', lambda: 'cbs-bsb')
    monkeypatch.setattr(gas, 'publish_new_public_workbook',
                        lambda *a, **kw: (_ for _ in ()).throw(
                            AssertionError('a publish was started anyway')))
    with pytest.raises(SystemExit, match='cannot write into an app-created'):
        gas._run_points_league_almanac(
            _Args(new_public_workbook=True, no_sheets=False), _Parser())


def test_a_points_league_on_an_unadapted_platform_refuses(monkeypatch):
    """The honest failure. Rendering another platform's queries against it
    would be the mirror image of the bug this ticket fixes."""
    monkeypatch.setattr(gas.db, 'league', lambda: _League('yahoo', 'yahoo-x'))
    monkeypatch.setattr(gas.db, 'league_key', lambda: 'yahoo-x')
    with pytest.raises(SystemExit, match='no points-format data adapter'):
        gas._points_adapter(_Parser())


class _ReachedH2H(Exception):
    """Sentinel proving the H2H branch was entered."""
