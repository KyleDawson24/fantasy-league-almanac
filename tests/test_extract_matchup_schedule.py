"""The mMatchupScore capture, both engines (MLB-235, rung 2).

Pure: the one network call in the path is substituted, and no warehouse is
opened -- `SnowflakeSink` runs over a fake connection so the cursor-level SQL
is the thing under test, the same shape as
tests/test_extract_raw_contract_tables.py. Faking the sink would skip exactly
the write this ticket adds.

WHAT THESE ASSERT THAT THE SHARED CODE PATH DOES NOT GIVE FOR FREE. The
snapshot machinery is shared, so "it writes a row" is not the risk. The risks
are that the capture stores something a later reader cannot derive from --
the schedule array without the status block beside it -- and that it acquires
a dependency on the seed it exists to replace. Both are asserted directly.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[1]

# load_dotenv() first, then setdefault, so a fresh clone imports and Kyle's
# real id never leaks into the session -- see test_extract_club_of_game.py.
load_dotenv()
os.environ.setdefault("LEAGUE_ID", "0")

_spec = importlib.util.spec_from_file_location(
    "extract_matchup_schedule_under_test", _REPO_ROOT / "extract" / "extract.py")
extract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(extract)

sys.path.insert(0, str(_REPO_ROOT / "extract"))
from matchup_membership import MatchupMembershipError  # noqa: E402

LEAGUE = "espn-main"


def _payload(current=4, periods=(1, 2, 3), season_year=2026, extra=True):
    """An mMatchupScore document, with the surrounding league fields ESPN
    also returns -- the narrowing has to be visible, so the payload has to
    carry things that must NOT survive it."""
    payload = {
        "seasonId": season_year,
        "status": {"currentMatchupPeriod": current, "isActive": True},
        "schedule": [{"matchupPeriodId": p,
                      "home": {"teamId": 987654,
                               "pointsByScoringPeriod": {"1": 0.0}},
                      "away": {"teamId": 987655,
                               "pointsByScoringPeriod": {"1": 0.0}}}
                     for p in periods],
    }
    if extra:
        payload.update({"gameId": 1, "segmentId": 0, "scoringPeriodId": 22,
                        "draftDetail": {"drafted": True}})
    return payload


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class _FakeCursor:
    def __init__(self):
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))
        return self

    def fetchone(self):
        # ensure_league_key_column's catalog probe: the column is present.
        return (1,)

    def close(self):
        pass


class _FakeConn:
    def __init__(self):
        self.cursors = []
        self.commits = 0

    def cursor(self):
        c = _FakeCursor()
        self.cursors.append(c)
        return c

    def commit(self):
        self.commits += 1


class _RecordingSink:
    """Only the method the capture is allowed to call."""

    def __init__(self):
        self.written = []

    def write_matchup_schedule(self, payload, year, league_key):
        self.written.append((payload, year, league_key))


@pytest.fixture
def no_network(monkeypatch):
    """The one request in the path. Records what view was asked for."""
    asked = []

    def _fetch(year, views):
        asked.append((year, tuple(views)))
        return _payload()

    monkeypatch.setattr(extract, "fetch_league_payload", _fetch)
    return asked


# ---------------------------------------------------------------------------
# The request
# ---------------------------------------------------------------------------
def test_the_capture_asks_for_exactly_one_view(no_network):
    extract.fetch_matchup_schedule(2026)

    assert no_network == [(2026, ("mMatchupScore",))]


def test_the_capture_never_reads_the_schedule_seed(no_network, monkeypatch):
    """The circularity, asserted rather than described. load_schedule() and
    get_scoring_periods() are the seed-reading path this ticket exists to
    break; touching either would make the capture prove nothing."""
    def _boom(*args, **kwargs):
        raise AssertionError(
            "the membership capture read the matchup_schedule seed, which is "
            "the circular dependency it exists to remove")

    monkeypatch.setattr(extract, "load_schedule", _boom)
    monkeypatch.setattr(extract, "get_scoring_periods", _boom)

    extract.extract_matchup_schedule(_RecordingSink(), 2026, LEAGUE)


# ---------------------------------------------------------------------------
# What gets stored
# ---------------------------------------------------------------------------
def test_the_stored_snapshot_carries_status_and_season_not_just_schedule(no_network):
    """Kyle's ruling: never the schedule array alone. Without status the
    stored row cannot tell a closed period from one that was still filling
    in when it was captured, and without seasonId the only season label is
    the one the loader stamped itself."""
    sink = _RecordingSink()
    extract.extract_matchup_schedule(sink, 2026, LEAGUE)

    stored, year, league_key = sink.written[0]
    assert set(stored) == {"seasonId", "status", "schedule"}
    assert stored["status"]["currentMatchupPeriod"] == 4
    assert stored["seasonId"] == 2026
    assert (year, league_key) == (2026, LEAGUE)


def test_the_blocks_are_stored_verbatim(no_network):
    """An assembly of three keys, not a transformation: nothing inside any
    block is reshaped, filtered or sorted."""
    sink = _RecordingSink()
    extract.extract_matchup_schedule(sink, 2026, LEAGUE)
    stored = sink.written[0][0]

    original = _payload()
    assert stored["schedule"] == original["schedule"]
    assert stored["status"] == original["status"]


def test_unrelated_league_document_fields_do_not_ride_along(no_network):
    sink = _RecordingSink()
    extract.extract_matchup_schedule(sink, 2026, LEAGUE)

    assert "draftDetail" not in sink.written[0][0]
    assert "scoringPeriodId" not in sink.written[0][0]


def test_the_valid_three_block_capture_writes_unchanged(no_network):
    """The control for every refusal below: a good document still writes,
    once, with all three blocks intact."""
    sink = _RecordingSink()
    extract.extract_matchup_schedule(sink, 2026, LEAGUE)

    assert len(sink.written) == 1
    stored, year, league_key = sink.written[0]
    assert set(stored) == {"seasonId", "status", "schedule"}
    assert (year, league_key) == (2026, LEAGUE)
    assert len(stored["schedule"]) == 3


# ---------------------------------------------------------------------------
# Refusals -- every one of these must write NOTHING
# ---------------------------------------------------------------------------
def _refuses(monkeypatch, payload, year=2026):
    """Point the capture at `payload` and return the refusal message."""
    monkeypatch.setattr(extract, "fetch_league_payload",
                        lambda y, views: payload)
    sink = _RecordingSink()

    with pytest.raises(SystemExit) as excinfo:
        extract.extract_matchup_schedule(sink, year, LEAGUE)

    assert sink.written == [], "a refused capture wrote to the sink anyway"
    return str(excinfo.value)


def test_a_missing_season_id_refuses_and_writes_nothing(monkeypatch):
    """Season identity is part of the storage contract. Without ESPN's own
    answer the row carries only the season_year the loader stamped, which
    agrees with itself no matter which season the document was."""
    payload = _payload()
    del payload["seasonId"]

    message = _refuses(monkeypatch, payload)

    assert "seasonId" in message
    assert "REFUSING to capture 2026" in message


def test_a_season_id_mismatch_refuses_and_writes_nothing(monkeypatch):
    """The dangerous one: a perfectly well-formed document for the WRONG
    season. Stored, it would supply one season's periods under another's
    label, and every derived flag downstream would be confidently wrong
    rather than absent."""
    message = _refuses(monkeypatch, _payload(season_year=2025), year=2026)

    assert "season 2025" in message and "2026" in message


def test_a_non_object_status_refuses_and_writes_nothing(monkeypatch):
    payload = _payload()
    payload["status"] = "currentMatchupPeriod=4"

    assert "status is str" in _refuses(monkeypatch, payload)


def test_a_non_list_schedule_refuses_and_writes_nothing(monkeypatch):
    payload = _payload()
    payload["schedule"] = {"1": []}

    assert "schedule is dict" in _refuses(monkeypatch, payload)


def test_the_refusal_says_what_the_capture_needs_and_what_to_check(monkeypatch):
    """A stranger hitting this has to learn what the contract is and what to
    do next from the message alone."""
    payload = _payload()
    del payload["status"]

    message = _refuses(monkeypatch, payload)

    assert "Nothing was written" in message
    assert "seasonId" in message and "status" in message and "schedule" in message
    assert "--year" in message


# ---------------------------------------------------------------------------
# The warehouse write
# ---------------------------------------------------------------------------
def test_the_warehouse_write_appends_to_its_own_table():
    conn = _FakeConn()
    extract.SnowflakeSink(conn).write_matchup_schedule(
        _payload(), 2026, LEAGUE)

    statements = [sql for sql, _ in conn.cursors[0].executed]
    assert any("CREATE TABLE IF NOT EXISTS MATCHUP_SCHEDULE" in s
               for s in statements)
    assert any(s.startswith("INSERT INTO MATCHUP_SCHEDULE") for s in statements)
    # Append-only: no DELETE anywhere near it. Membership is retrospective,
    # so each capture closes one more period than the last and the history
    # is the audit trail for a derived flag.
    assert not any("DELETE" in s for s in statements)
    assert conn.commits == 1


def test_the_warehouse_write_stamps_season_and_league_key():
    conn = _FakeConn()
    extract.SnowflakeSink(conn).write_matchup_schedule(
        _payload(), 2026, LEAGUE)

    insert = [params for sql, params in conn.cursors[0].executed
              if sql.startswith("INSERT INTO MATCHUP_SCHEDULE")][0]
    year, raw_json, league_key = insert
    assert (year, league_key) == (2026, LEAGUE)
    assert json.loads(raw_json)["status"]["currentMatchupPeriod"] == 4


def test_the_table_is_registered_everywhere_the_snapshot_machinery_looks():
    """One list governs the DDL, one governs the local writer. A table in
    only one of them creates a relation on one engine and a missing one on
    the other -- which is the failure mode MLB-222 C-5 was."""
    from raw_sink import ESPN_RAW_TABLES

    assert "MATCHUP_SCHEDULE" in extract.SNAPSHOT_TABLES
    assert "MATCHUP_SCHEDULE" in extract.CONDITIONAL_RAW_TABLES
    assert "MATCHUP_SCHEDULE" in ESPN_RAW_TABLES


def test_an_unregistered_table_name_is_still_refused():
    """The guard on load_snapshot_to_snowflake stays honest now that the
    tuple has grown -- the name lands in DDL by interpolation."""
    with pytest.raises(ValueError, match="MATCHUP_SCHEDULES"):
        extract.load_snapshot_to_snowflake(
            _FakeConn(), "MATCHUP_SCHEDULES", {}, 2026, LEAGUE, "typo")


# ---------------------------------------------------------------------------
# The CLI contract
# ---------------------------------------------------------------------------
def test_the_capture_is_opt_in():
    """Nothing consumes it yet, so it must not add a request to the weekly
    runbook. This is the assertion that would fail if it were made default
    by accident."""
    source = (_REPO_ROOT / "extract" / "extract.py").read_text()

    assert '"--include-matchup-schedule", action="store_true"' in source
    assert "do_matchup_schedule = (args.matchup_schedule_only" in source


def test_the_narrowing_helper_is_the_pure_one():
    """extract.py holds the fetch; the shape rule lives in the credential-free
    module so it is testable without a network."""
    assert extract.matchup_schedule_snapshot.__module__ == "matchup_membership"

    with pytest.raises(MatchupMembershipError):
        extract.matchup_schedule_snapshot({"schedule": []}, season_year=2026)
