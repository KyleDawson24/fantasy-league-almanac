"""
Pure tests for the club-of-game lift and the MLB-188 overwrite guard.

Both live in extract/extract.py, which no other test imports. It is loaded
by path here rather than added to conftest's sys.path, so the rest of the
suite keeps its current import surface.

Nothing in this file opens a connection. `settled_loaded_periods` takes its
warehouse read through a cursor and its calendar through `load_schedule`
(a seed CSV), so both are substitutable and the guard's decision logic is
testable without Snowflake.
"""

import importlib.util
import os
from datetime import date, datetime
from pathlib import Path

import pytest
from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[1]

# extract.py reads LEAGUE_ID at import time. Present in Kyle's .env, absent
# in a fresh clone -- setdefault covers the clone without overriding a real
# value. Nothing under test consumes it.
#
# load_dotenv() FIRST, and that is not belt-and-braces. The setdefault below
# protects a "real value" that is never present at this point: pytest does
# not call load_dotenv(), so LEAGUE_ID is unset in the pytest process no
# matter what .env says, and setdefault therefore always fired. That value
# then leaked out of this module and into the whole session, because
# collection imports every test module and test_almanac_byte_diff spawns its
# render with env=dict(os.environ, ...). The child's own load_dotenv() could
# not undo it -- load_dotenv does not override an existing variable -- so
# every ESPN box-score hyperlink in that render came out as leagueId=0
# against a fixture holding the real id.
#
# The teeth: the documented re-anchor command is
# `REGENERATE_BASELINES=1 pytest tests/ -m warehouse`, i.e. exactly the
# invocation that leaks. Re-anchoring through it would have written
# leagueId=0 into the golden corpus permanently.
load_dotenv()
os.environ.setdefault("LEAGUE_ID", "0")

_spec = importlib.util.spec_from_file_location(
    "extract_under_test", _REPO_ROOT / "extract" / "extract.py")
extract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(extract)


# ---------------------------------------------------------------------------
# The collapse rule
# ---------------------------------------------------------------------------
def test_no_split_carried_a_club_is_unattributed_not_fa():
    """None and 'FA' are different words in MLB-159's vocabulary. A player
    whose splits carried no club is Unattributed; returning 'FA' would file
    him with the genuinely unrostered."""
    assert extract._resolve_club_of_game({}) is None


def test_single_club_is_that_club():
    assert extract._resolve_club_of_game({"Bos": 1}) == "Bos"


def test_most_splits_wins():
    """A doubleheader split across a trade: the club he played more of that
    day's games for takes the period."""
    assert extract._resolve_club_of_game({"Bos": 1, "SF": 2}) == "SF"
    assert extract._resolve_club_of_game({"Bos": 2, "SF": 1}) == "Bos"


def test_tie_breaks_to_first_in_payload_order_both_ways():
    """The tie-break is a rule, not an artifact of dict iteration luck --
    so it has to be observably order-sensitive and stable (MLB-128)."""
    assert extract._resolve_club_of_game({"Bos": 1, "SF": 1}) == "Bos"
    assert extract._resolve_club_of_game({"SF": 1, "Bos": 1}) == "SF"


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------
TODAY = date(2026, 8, 3)
LOADED_AT = datetime(2026, 5, 3, 20, 24, 13)


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *args, **kwargs):
        pass

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _FakeConn:
    """Stands in for a Snowflake connection carrying `loaded` periods."""

    def __init__(self, loaded_periods):
        self._rows = [(mp, LOADED_AT) for mp in loaded_periods]

    def cursor(self):
        return _FakeCursor(self._rows)


@pytest.fixture
def schedule(monkeypatch):
    """Two settled periods, two inside the 21-day window, one with no
    schedule row at all."""
    matchups = [
        (1, date(2026, 3, 30), date(2026, 4, 5)),    # settled, 120d old
        (14, date(2026, 6, 29), date(2026, 7, 5)),   # settled, 29d old
        (16, date(2026, 7, 20), date(2026, 7, 26)),  # 8d old, in window
        (17, date(2026, 7, 27), date(2026, 8, 2)),   # 1d old, in window
    ]
    monkeypatch.setattr(extract, "load_schedule",
                        lambda year: (date(2026, 3, 30), matchups))
    return matchups


def _settled(conn, periods):
    return extract.settled_loaded_periods(
        conn, 2026, "espn-main", periods, today=TODAY)


def test_never_loaded_period_is_allowed(schedule):
    """A first extract invents no history, so a genuinely new period is
    unaffected however old it is."""
    assert _settled(_FakeConn(loaded_periods=[]), [1, 14]) == []


def test_loaded_period_inside_the_window_is_allowed(schedule):
    """The weekly run revisits these deliberately -- it is the mechanism
    that captures the stamps at all. A guard the routine path had to bypass
    would be bypassed permanently by the second week."""
    conn = _FakeConn(loaded_periods=[16, 17])
    assert _settled(conn, [16, 17]) == []


def test_loaded_and_settled_period_is_refused(schedule):
    conn = _FakeConn(loaded_periods=[1, 14, 16, 17])
    assert [mp for mp, _end, _at in _settled(conn, [14])] == [14]


def test_refusal_names_only_the_offenders(schedule):
    """Mixed sets refuse as a whole, but the report has to be precise about
    which periods are the problem."""
    conn = _FakeConn(loaded_periods=[1, 14, 16, 17])
    got = _settled(conn, [1, 14, 16, 17, 99])
    assert [mp for mp, _end, _at in got] == [1, 14]


def test_period_with_no_schedule_row_fails_closed(schedule):
    """No schedule row means no knowable age. A guard that waved those
    through would be defeated by a schedule gap."""
    conn = _FakeConn(loaded_periods=[42])
    assert [mp for mp, _end, _at in _settled(conn, [42])] == [42]


# ---------------------------------------------------------------------------
# The riders
# ---------------------------------------------------------------------------
def test_lookback_and_guard_window_are_the_same_constant():
    """The weekly lookback and the guard's exemption must not drift apart:
    a second hardcoded 21 would silently widen or narrow the guard the day
    someone tuned one of them (MLB-175)."""
    import inspect
    default = inspect.signature(
        extract.get_recent_matchup_periods).parameters["lookback_days"].default
    assert default == extract.LIVE_CAPTURE_WINDOW_DAYS


def test_refusal_message_says_what_to_do_next():
    """A refusal that does not name the way forward gets pattern-matched
    into --force by the next person in a hurry."""
    msg = extract.refuse_settled_overwrite(
        [(14, date(2026, 7, 5), LOADED_AT)], 2026,
        "--overwrite-day-accurate-history")
    assert "--overwrite-day-accurate-history" in msg
    assert "--backfill-club-of-game" in msg
    assert "snapshot" in msg.lower()
    assert "Nothing was written" in msg
    assert "14" in msg and "2026-07-05" in msg


def test_refusal_message_is_ascii():
    """It prints to a Windows console; a mangled safety message is a
    weakened one."""
    msg = extract.refuse_settled_overwrite(
        [(14, date(2026, 7, 5), LOADED_AT)], 2026, "--flag")
    msg.encode("ascii")
