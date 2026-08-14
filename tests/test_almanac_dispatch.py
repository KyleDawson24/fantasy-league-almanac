"""The points-league render dispatch (MLB-201).

`_run_points_league_almanac` has four reachable outcomes, decided by two
flags and whether a dev Sheet is configured. Three of them were exercised.
The fourth -- no `--no-sheets`, no `--preview-dir`, and no dev Sheet -- fell
into the documented preview fallback and died on an unbound `preview_tabs`,
because tabs were only built when one of the flags was present while the
fallback could be reached without either.

That combination is not exotic. It is a stranger's literal first render: a
fresh clone with no SHEETS_DEV_ID in .env, running the command the docs
give them. Every branch is now covered, and the no-flags one is asserted to
produce the SAME tabs an explicit preview request does -- "it didn't crash"
would pass just as well on a run that quietly emitted nothing.

Pure: the tab builder and the Sheets writer are both stubbed, so nothing
here touches the warehouse or the network.
"""
from __future__ import annotations

import pytest

import generate_almanac_sheet as gas


FAKE_TABS = [
    ("Home", [["a", "b"]], None),
    ("Records", [["c", "d"]], None),
]
EXPECTED_PREVIEW = [("Home", [["a", "b"]]), ("Records", [["c", "d"]])]


class _Args:
    """The subset of the parsed namespace this path reads."""

    def __init__(self, no_sheets=False, preview_dir=None, prod=False,
                 print_all=False, new_public_workbook=False,
                 include_trades=False, season_year=None, matchup_period=None):
        self.no_sheets = no_sheets
        self.preview_dir = preview_dir
        self.prod = prod
        self.print_all = print_all
        self.new_public_workbook = new_public_workbook
        self.include_trades = include_trades
        self.season_year = season_year
        self.matchup_period = matchup_period


class _CbsLeague:
    """Stand-in for the registry League object. The points path now picks
    its DATA ADAPTER by platform (the shape was already settled by format),
    so these CBS-path tests have to say which platform they are."""

    key = 'cbs-bsb'
    platform = 'cbs'
    display_name = 'Box Score Baseball (CBS)'


class _Parser:
    def error(self, message):          # argparse's contract: never returns
        raise SystemExit(f"parser.error: {message}")


@pytest.fixture
def stubs(monkeypatch):
    """Stub the tab builder, the Sheets writer, and the target resolver."""
    calls = {"built": 0, "written": [], "preview_dirs": []}

    def _build_all_tabs():
        calls["built"] += 1
        return FAKE_TABS, None, None

    monkeypatch.setattr(gas.cbs_almanac_sheets, "build_all_tabs", _build_all_tabs)
    monkeypatch.setattr(gas.cbs_almanac_sheets, "write_cbs_almanac",
                        lambda sheet_id: calls["written"].append(sheet_id))
    monkeypatch.setattr(gas, "_write_preview_dir",
                        lambda tabs, d: calls["preview_dirs"].append((tabs, d)))
    monkeypatch.setattr(gas.db, "league", lambda: _CbsLeague())
    monkeypatch.setattr(gas.db, "league_key", lambda: "cbs-bsb")
    return calls


def _no_dev_sheet(monkeypatch):
    monkeypatch.setattr(gas.sheets_target, "resolve_sheets_target",
                        lambda prod, league: (None, "dev"))


def _dev_sheet(monkeypatch, sheet_id="dev123"):
    monkeypatch.setattr(gas.sheets_target, "resolve_sheets_target",
                        lambda prod, league: (sheet_id, "dev"))


# --------------------------------------------------------------------------
# The branch that crashed.
# --------------------------------------------------------------------------

def test_no_flags_and_no_dev_sheet_previews_instead_of_crashing(stubs, monkeypatch, capsys):
    """THE regression: neither flag, no dev Sheet configured."""
    _no_dev_sheet(monkeypatch)
    gas._run_points_league_almanac(_Args(), _Parser())

    out = capsys.readouterr().out
    assert "preview only" in out
    assert stubs["built"] == 1, (
        "the preview fallback was reached without building any tabs -- this "
        "is the unbound preview_tabs path"
    )
    assert stubs["written"] == [], "nothing should be written to Sheets"
    # The fallback prints the first two tabs by default.
    assert "Home" in out and "Records" in out


def test_no_flags_fallback_emits_the_same_tabs_as_an_explicit_preview(
        stubs, monkeypatch, capsys):
    """Not crashing is not the bar: the fallback must produce a real preview."""
    _no_dev_sheet(monkeypatch)
    gas._run_points_league_almanac(_Args(print_all=True), _Parser())
    fallback = capsys.readouterr().out

    _no_dev_sheet(monkeypatch)
    gas._run_points_league_almanac(_Args(no_sheets=True, print_all=True), _Parser())
    explicit = capsys.readouterr().out

    assert fallback == explicit, (
        "the no-flags fallback and an explicit --no-sheets preview render "
        "different output"
    )


# --------------------------------------------------------------------------
# The three branches that already worked, so the fix cannot regress them.
# --------------------------------------------------------------------------

def test_no_sheets_previews_without_resolving_a_target(stubs, monkeypatch, capsys):
    def _explode(prod, league):
        raise AssertionError("--no-sheets must not resolve a Sheets target")

    monkeypatch.setattr(gas.sheets_target, "resolve_sheets_target", _explode)
    gas._run_points_league_almanac(_Args(no_sheets=True), _Parser())

    assert stubs["built"] == 1
    assert stubs["written"] == []
    assert "preview only" in capsys.readouterr().out


def test_preview_dir_writes_tsvs_and_still_writes_the_sheet(stubs, monkeypatch, tmp_path):
    """--preview-dir alongside a configured dev Sheet does both."""
    _dev_sheet(monkeypatch)
    gas._run_points_league_almanac(_Args(preview_dir=str(tmp_path)), _Parser())

    assert stubs["preview_dirs"] == [(EXPECTED_PREVIEW, str(tmp_path))]
    assert stubs["written"] == ["dev123"]


def test_dev_sheet_without_flags_writes_and_builds_no_preview(stubs, monkeypatch):
    """write_cbs_almanac builds its own tabs -- the two-pass nav-link write
    needs real sheet gids -- so the plain write path must not build them
    twice."""
    _dev_sheet(monkeypatch)
    gas._run_points_league_almanac(_Args(), _Parser())

    assert stubs["written"] == ["dev123"]
    assert stubs["built"] == 0, "tabs were built for a run that needs no preview"


def test_prod_target_is_announced(stubs, monkeypatch, capsys):
    monkeypatch.setattr(gas.sheets_target, "resolve_sheets_target",
                        lambda prod, league: ("prod456", "PROD"))
    gas._run_points_league_almanac(_Args(prod=True), _Parser())

    assert ">>> writing to PRODUCTION sheet" in capsys.readouterr().out
    assert stubs["written"] == ["prod456"]


def test_unresolvable_target_still_goes_through_parser_error(stubs, monkeypatch):
    def _raise(prod, league):
        raise RuntimeError("SHEETS_PROD_ID is not set")

    monkeypatch.setattr(gas.sheets_target, "resolve_sheets_target", _raise)
    with pytest.raises(SystemExit, match="SHEETS_PROD_ID"):
        gas._run_points_league_almanac(_Args(prod=True), _Parser())
