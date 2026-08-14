"""
Pure tests for the club-of-game lift and the MLB-188 overwrite guard.

Both live in extract/extract.py, which no other test imports. It is loaded
by path here rather than added to conftest's sys.path, so the rest of the
suite keeps its current import surface.

Nothing in this file opens a connection. `settled_loaded_periods` takes its
warehouse read through a SINK (MLB-208) and, since MLB-235 rung 4B-1, its
AGES through a parsed `mMatchupScore` payload rather than through
`load_schedule` (a seed CSV) -- so both are substitutable and the guard's
decision logic is testable without Snowflake and without a calendar. The sink
here is the real `SnowflakeSink` over a fake connection, so the cursor-level
behaviour these tests were written for is still the thing being exercised.

WHAT CHANGED UNDERNEATH THESE TESTS. "Ended more than
LIVE_CAPTURE_WINDOW_DAYS ago" used to be an end DATE out of
matchup_schedule.csv compared to `today`. It is now the last scoring-period
ID of the period compared to that season's `status.latestScoringPeriod`. The
substitution is exact because an ESPN scoring period is one day, so the
fixture below expresses the same four periods in the platform's units and
every verdict these tests assert is unchanged.

The LOCAL sink's half of the same guard is covered in
tests/test_local_raw_writer.py, which needs no connection at all. The shared
recency policy itself, and the selection half that consumes it, are in
tests/test_extract_membership_selection.py.
"""

import importlib.util
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

import pytest
import requests
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

sys.path.insert(0, str(_REPO_ROOT / "extract"))
from matchup_membership import parse_matchup_membership  # noqa: E402


def _http_response(status_code):
    response = requests.Response()
    response.status_code = status_code
    response.url = "https://example.invalid/communication/"
    return response


def test_transaction_feed_refusal_is_unavailable_not_empty(monkeypatch, capsys):
    """Unchanged behaviour, richer return value (MLB-243). A stable refusal
    is still unavailable and still carries NO topic list -- what changed is
    that the verdict is now named, and that it takes a bounded retry to
    reach, because this endpoint flaps."""
    response = _http_response(401)
    monkeypatch.setattr(extract.requests, "get", lambda *a, **k: response)
    monkeypatch.setattr(extract.time, "sleep", lambda s: None)

    capture = extract.fetch_transactions(2026)
    assert capture.outcome == extract.UNAUTHORIZED
    assert capture.served is False
    assert capture.topics is None, (
        'an unavailable feed must not carry [], which downstream reads as '
        'proven-zero activity'
    )
    assert "ransaction-dependent output will be omitted" in capsys.readouterr().out


def test_unavailable_transaction_feed_writes_nothing(monkeypatch):
    """The snapshot is still withheld so a prior good capture survives. The
    COVERAGE row is written, which is the new part: an absence of topics is
    no longer the only trace of what happened."""
    class Sink:
        def __init__(self):
            self.writes = []
            self.coverage = []

        def write_transactions(self, *args):
            self.writes.append(args)

        def write_transaction_coverage(self, *args):
            self.coverage.append(args)

    sink = Sink()
    monkeypatch.setattr(
        extract, "fetch_transactions",
        lambda year: extract.TransactionCapture(extract.UNAUTHORIZED, None,
                                                401, 5))

    extract.extract_transactions(sink, 2026, "espn-test")

    assert sink.writes == []
    assert len(sink.coverage) == 1
    assert sink.coverage[0][0]['outcome'] == extract.UNAUTHORIZED


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
    """Answers the guard's three questions, routed on SQL shape.

    The guard asks, in order: does BOX_SCORES exist, does it carry
    league_key, and what has been loaded. It used to ask only the third and
    read ANY failure as "no table" -- so a fake that answered only the third
    is precisely what made the broken version look tested (MLB-199).
    """

    def __init__(self, rows, table_exists=True, has_league_key=True,
                 raise_on=None):
        self._rows = rows
        self._table_exists = table_exists
        self._has_league_key = has_league_key
        self._raise_on = raise_on or {}
        self._last = ""
        self.altered = []
        self.executed = []

    def execute(self, sql, params=None):
        self._last = sql
        self.executed.append(sql)
        for needle, exc in self._raise_on.items():
            if needle in sql:
                raise exc
        if sql.strip().upper().startswith("ALTER TABLE"):
            self.altered.append(sql)
            self._has_league_key = True

    def fetchone(self):
        if "INFORMATION_SCHEMA.TABLES" in self._last:
            return (1 if self._table_exists else 0,)
        if "INFORMATION_SCHEMA.COLUMNS" in self._last:
            return (1 if self._has_league_key else 0,)
        return None

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _FakeConn:
    """Stands in for a Snowflake connection carrying `loaded` periods."""

    def __init__(self, loaded_periods, **cursor_kwargs):
        self._rows = [(mp, LOADED_AT) for mp in loaded_periods]
        self._cursor_kwargs = cursor_kwargs
        self.last_cursor = None

    def cursor(self):
        self.last_cursor = _FakeCursor(self._rows, **self._cursor_kwargs)
        return self.last_cursor

    def commit(self):
        pass


def _programming_error(msg):
    return extract.snowflake.connector.errors.ProgrammingError(msg=msg)


@pytest.fixture
def schedule(monkeypatch):
    """The same four verdicts as the old seed fixture, in ESPN's own units.

    Seventeen closed 7-day periods (period N ends at scoring period 7N),
    period 18 in flight, `latestScoringPeriod` 120. The window is 21 scoring
    periods, so the cutoff is 99:

        period  1  ends at   7  -- settled, and long settled
        period 14  ends at  98  -- settled by ONE scoring period
        period 16  ends at 112  -- inside the window
        period 17  ends at 119  -- inside the window

    Contiguous rather than sparse because the membership parser refuses a
    gapped closed run -- a hole means ESPN served a schedule it does not
    understand. The old fixture could be sparse; a real payload cannot.

    The seed is BOOBY-TRAPPED rather than merely unused: if the guard ever
    reaches for a calendar again, these tests say so.
    """
    def _seed_is_gone(*args, **kwargs):
        raise AssertionError(
            "the settled-history guard read matchup_schedule.csv, which "
            "MLB-235 rung 4B-1 removed from every extraction path")

    monkeypatch.setattr(extract, "load_schedule", _seed_is_gone)

    def _side(scoring_periods):
        return {"teamId": 987654,
                "pointsByScoringPeriod": {str(sp): 0.0 for sp in scoring_periods}}

    payload = {
        "seasonId": 2026,
        "status": {"currentMatchupPeriod": 18, "latestScoringPeriod": 120},
        "schedule": [{"matchupPeriodId": mp,
                      "home": _side(range(7 * mp - 6, 7 * mp + 1)),
                      "away": _side(range(7 * mp - 6, 7 * mp + 1))}
                     for mp in range(1, 18)],
    }
    return parse_matchup_membership(
        payload, league_key="espn-main", season_year=2026)


def _settled(conn, periods, parse):
    """Drive the guard through the REAL SnowflakeSink over a fake connection.

    MLB-208 split the guard: `settled_loaded_periods` takes a sink and asks
    it what is loaded, so the fake connection goes in one layer lower.
    Wrapping rather than faking the sink keeps the adapter's own catalog
    probe and legacy-column self-heal under test -- those are the parts
    MLB-199/W-02 was about, and a fake sink would have skipped straight past
    them.

    MLB-235 rung 4B-1 added the third input: the parsed membership the run
    already captured, which supplies the ages the seed's dates used to.
    """
    return extract.settled_loaded_periods(
        extract.SnowflakeSink(conn), 2026, "espn-main", periods, parse,
        today=TODAY)


def test_never_loaded_period_is_allowed(schedule):
    """A first extract invents no history, so a genuinely new period is
    unaffected however old it is."""
    assert _settled(_FakeConn(loaded_periods=[]), [1, 14], schedule) == []


def test_loaded_period_inside_the_window_is_allowed(schedule):
    """The weekly run revisits these deliberately -- it is the mechanism
    that captures the stamps at all. A guard the routine path had to bypass
    would be bypassed permanently by the second week."""
    conn = _FakeConn(loaded_periods=[16, 17])
    assert _settled(conn, [16, 17], schedule) == []


def test_loaded_and_settled_period_is_refused(schedule):
    conn = _FakeConn(loaded_periods=[1, 14, 16, 17])
    assert [mp for mp, _end, _at in _settled(conn, [14], schedule)] == [14]


def test_refusal_names_only_the_offenders(schedule):
    """Mixed sets refuse as a whole, but the report has to be precise about
    which periods are the problem."""
    conn = _FakeConn(loaded_periods=[1, 14, 16, 17])
    got = _settled(conn, [1, 14, 16, 17, 99], schedule)
    assert [mp for mp, _end, _at in got] == [1, 14]


def test_period_with_no_schedule_row_fails_closed(schedule):
    """No schedule row means no knowable age. A guard that waved those
    through would be defeated by a schedule gap."""
    conn = _FakeConn(loaded_periods=[42])
    assert [mp for mp, _end, _at in _settled(conn, [42], schedule)] == [42]


# ---------------------------------------------------------------------------
# MLB-199 (1): the guard must not read an unanswerable question as "safe".
#
# The old version wrapped its own query in `except ProgrammingError: return
# []`. Snowflake raises ProgrammingError for a missing table, a missing
# column, a permission failure and a typo alike -- and it deliberately
# reports "does not exist OR not authorized" as ONE error, so the exception
# could never have distinguished them. Every one of those bypassed the guard
# immediately before the loader deleted settled rows.
# ---------------------------------------------------------------------------

def test_absent_table_is_still_allowed(schedule):
    """A first install has nothing to overwrite. Still true, now asked of
    the catalog rather than inferred from a failure."""
    conn = _FakeConn(loaded_periods=[1, 14], table_exists=False)
    assert _settled(conn, [1, 14], schedule) == []


def test_legacy_table_without_league_key_is_migrated_not_bypassed(schedule):
    """THE regression. A pre-registry table has no league_key, so the
    guard's own query referenced a column that did not exist -- and the
    result was read as 'no table, nothing to protect'.

    The column is now added BEFORE the decision, so the legacy shape is
    upgraded and then guarded rather than used as an escape hatch.
    """
    conn = _FakeConn(loaded_periods=[1, 14], has_league_key=False)
    got = _settled(conn, [14], schedule)
    assert [mp for mp, _end, _at in got] == [14], (
        "a legacy-shaped table bypassed the settled-history guard"
    )
    assert conn.last_cursor.altered, (
        "league_key was never added; the guard queried the legacy shape"
    )


@pytest.mark.parametrize("failing_sql, message", [
    ("SELECT matchup_period", "SQL compilation error: invalid identifier 'LEAGUE_KEY'"),
    ("SELECT matchup_period", "Insufficient privileges to operate on table 'BOX_SCORES'"),
    ("SELECT matchup_period", "something nobody has seen before"),
    ("INFORMATION_SCHEMA.TABLES", "Insufficient privileges on INFORMATION_SCHEMA"),
])
def test_any_unanswerable_guard_refuses(schedule, failing_sql, message):
    """Permission, identifier, schema drift, generic: all refuse.

    'We could not check' is not 'there is nothing to lose'.
    """
    conn = _FakeConn(loaded_periods=[1, 14],
                     raise_on={failing_sql: _programming_error(message)})
    with pytest.raises(SystemExit) as excinfo:
        _settled(conn, [14], schedule)
    assert "REFUSING TO EXTRACT" in str(excinfo.value)
    assert message in str(excinfo.value), "the underlying error must be surfaced"


def test_unverifiable_guard_refusal_is_ascii_and_actionable():
    msg = extract.refuse_unverifiable_guard(
        _programming_error("Insufficient privileges"), 2026)
    msg.encode("ascii")
    assert "cannot verify settled history" in msg
    assert "Insufficient privileges" in msg


# ---------------------------------------------------------------------------
# MLB-199 (2): a failed fetch is not a valid empty response.
#
# `fetch_all_player_stats` returned {} on any failure -- the same value a
# genuine no-games day produces. On the backfill path that resolved every
# player to null and COMMITTED it, so one outage could erase the enrichment
# across a whole requested range. Every test below asserts the stored bytes
# are untouched, because "it refused" is not the property that matters here.
# ---------------------------------------------------------------------------

def _kona_response(monkeypatch, *, json_value=None, exc=None, status_exc=None):
    """Point extract.requests.get at a canned response."""
    class _Response:
        def raise_for_status(self):
            if status_exc:
                raise status_exc

        def json(self):
            if isinstance(json_value, Exception):
                raise json_value
            return json_value

    def _get(*args, **kwargs):
        if exc:
            raise exc
        return _Response()

    monkeypatch.setattr(extract.requests, "get", _get)


def test_network_failure_raises_rather_than_returning_empty(monkeypatch):
    _kona_response(monkeypatch,
                   exc=extract.requests.RequestException("connection reset"))
    with pytest.raises(extract.KonaUnavailable):
        extract.fetch_all_player_stats(2026, 100)


def test_transient_connection_reset_retries_then_returns_players(monkeypatch):
    class _Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"players": []}

    calls = []
    sleeps = []

    def _get(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            raise requests.ConnectionError("connection reset")
        return _Response()

    monkeypatch.setattr(extract.requests, "get", _get)
    monkeypatch.setattr(extract.time, "sleep", sleeps.append)

    assert extract.fetch_all_player_stats(2026, 100) == {}
    assert len(calls) == 2
    assert sleeps == [1]


def test_auth_failure_is_not_retried(monkeypatch):
    calls = []
    response = requests.Response()
    response.status_code = 401

    def _get(*args, **kwargs):
        calls.append((args, kwargs))
        raise requests.HTTPError("401 Unauthorized", response=response)

    monkeypatch.setattr(extract.requests, "get", _get)

    with pytest.raises(extract.KonaUnavailable, match="401 Unauthorized"):
        extract.fetch_all_player_stats(2026, 100)
    assert len(calls) == 1


def test_transient_retries_are_bounded_then_refuse(monkeypatch):
    calls = []
    sleeps = []

    def _get(*args, **kwargs):
        calls.append((args, kwargs))
        raise requests.ConnectionError("connection reset")

    monkeypatch.setattr(extract.requests, "get", _get)
    monkeypatch.setattr(extract.time, "sleep", sleeps.append)

    with pytest.raises(extract.KonaUnavailable, match="connection reset"):
        extract.fetch_all_player_stats(2026, 100)
    assert len(calls) == 4
    assert sleeps == [1, 3, 7]


def test_bad_json_raises_rather_than_returning_empty(monkeypatch):
    _kona_response(monkeypatch, json_value=ValueError("not json"))
    with pytest.raises(extract.KonaUnavailable):
        extract.fetch_all_player_stats(2026, 100)


def test_http_error_raises_rather_than_returning_empty(monkeypatch):
    _kona_response(
        monkeypatch,
        status_exc=extract.requests.RequestException("401 Unauthorized"))
    with pytest.raises(extract.KonaUnavailable):
        extract.fetch_all_player_stats(2026, 100)


def test_success_shaped_non_payload_raises(monkeypatch):
    """A 200 carrying an error document parses as JSON perfectly well."""
    _kona_response(monkeypatch, json_value={"error": "rate limited"})
    with pytest.raises(extract.KonaUnavailable):
        extract.fetch_all_player_stats(2026, 100)


def test_valid_empty_response_is_not_a_failure(monkeypatch):
    """ESPN saying 'nobody played' is an answer, and must stay distinct."""
    _kona_response(monkeypatch, json_value={"players": []})
    assert extract.fetch_all_player_stats(2026, 100) == {}


# --- the backfill write path ------------------------------------------------

def _blob(players):
    """A stored box-score blob with one matchup and the given players."""
    return {
        "matchups": [{"home_lineup": list(players), "away_lineup": []}],
        "free_agents": [],
    }


class _BackfillCursor:
    """Serves one stored period and records every UPDATE."""

    def __init__(self, stored_json):
        self.stored_json = stored_json
        self.updates = []
        self._last = ""

    def execute(self, sql, params=None):
        self._last = sql
        if sql.strip().upper().startswith("UPDATE"):
            self.updates.append(params[0])

    def fetchall(self):
        return [(100, self.stored_json)]

    def close(self):
        pass


class _BackfillConn:
    def __init__(self, stored_json):
        self.cur = _BackfillCursor(stored_json)

    def cursor(self):
        return self.cur

    def commit(self):
        pass


ENRICHED = [
    {"playerId": 1, "clubOfGame": "LAD", "games_played": 1},
    {"playerId": 2, "clubOfGame": "NYY", "games_played": 1},
]


@pytest.mark.parametrize("failure", [
    {"exc": extract.requests.RequestException("connection reset")},
    {"json_value": ValueError("not json")},
    {"json_value": {"error": "rate limited"}},
    {"status_exc": extract.requests.RequestException("503")},
])
def test_backfill_writes_nothing_when_the_fetch_fails(monkeypatch, failure):
    """The erasure path. A failed fetch used to null every stored club."""
    stored = json.dumps(_blob(ENRICHED))
    conn = _BackfillConn(stored)
    _kona_response(monkeypatch, **failure)

    with pytest.raises(SystemExit) as excinfo:
        extract.backfill_club_of_game(conn, 2026, "espn-main", [7])

    assert "REFUSING TO BACKFILL" in str(excinfo.value)
    assert conn.cur.updates == [], "a failed fetch reached the write path"
    assert conn.cur.stored_json == stored, "settled RAW was not byte-equivalent"


def test_backfill_refuses_an_empty_universe_when_the_period_records_play(monkeypatch):
    """A valid but empty response is only believable if the stored period
    agrees nobody played. Otherwise it is an outage answering 200."""
    stored = json.dumps(_blob(ENRICHED))
    conn = _BackfillConn(stored)
    _kona_response(monkeypatch, json_value={"players": []})

    with pytest.raises(SystemExit) as excinfo:
        extract.backfill_club_of_game(conn, 2026, "espn-main", [7])

    assert "empty player universe" in str(excinfo.value)
    assert conn.cur.updates == []
    assert conn.cur.stored_json == stored


def test_backfill_never_nulls_an_existing_club_on_partial_coverage(monkeypatch):
    """Absence of evidence is not evidence of absence.

    Player 1 is covered by this fetch, player 2 is not. Player 2 already has
    a stored club, and a partial response must not take it away.
    """
    stored = json.dumps(_blob(ENRICHED))
    conn = _BackfillConn(stored)
    _kona_response(monkeypatch, json_value={"players": [{
        "player": {
            "id": 1, "fullName": "Covered Player", "proTeamId": 19,
            "defaultPositionId": 1, "eligibleSlots": [],
            "stats": [{
                "statSplitTypeId": 5, "scoringPeriodId": 100,
                "proTeamId": 19, "appliedTotal": 5.0, "stats": {"0": 1},
            }],
        }
    }]})

    extract.backfill_club_of_game(conn, 2026, "espn-main", [7])

    assert len(conn.cur.updates) == 1
    written = json.loads(conn.cur.updates[0])
    by_id = {p["playerId"]: p for p in written["matchups"][0]["home_lineup"]}
    assert by_id[2]["clubOfGame"] == "NYY", (
        "an uncovered player's stored club was overwritten with null"
    )
    assert by_id[1]["clubOfGame"], "the covered player was not attributed"


def test_backfill_records_null_for_a_player_with_no_stored_club(monkeypatch):
    """First pass over a period that has never been enriched still writes
    the key -- there is nothing to preserve and null is the honest answer."""
    fresh = [{"playerId": 3, "games_played": 0}]
    conn = _BackfillConn(json.dumps(_blob(fresh)))
    _kona_response(monkeypatch, json_value={"players": []})

    extract.backfill_club_of_game(conn, 2026, "espn-main", [7])

    written = json.loads(conn.cur.updates[0])
    assert written["matchups"][0]["home_lineup"][0]["clubOfGame"] is None


def test_backfill_refusal_is_ascii_and_says_nothing_was_written():
    msg = extract.refuse_backfill_without_evidence(2026, 7, 100, "boom")
    msg.encode("ascii")
    assert "Nothing was written" in msg
    assert "boom" in msg


# --- the normal extract write path -----------------------------------------

def test_serialize_refuses_rather_than_building_a_period_without_kona(monkeypatch):
    """The loader is delete-then-insert and kona is the ONLY source of FA
    production and club labels, so a period serialized without it would
    replace a good period with one that has neither."""
    _kona_response(monkeypatch,
                   exc=extract.requests.RequestException("connection reset"))

    class _League:
        year = 2026

        def box_scores(self, **kwargs):
            return []

    # It raises rather than returning a period built from wrapper data
    # alone. extract_matchup_period serializes every scoring period BEFORE
    # calling the loader, so raising here is raising before any write.
    with pytest.raises(extract.KonaUnavailable):
        extract.serialize_box_scores(_League(), 100, 7)


def test_extract_refusal_is_ascii_and_explains_the_stakes():
    msg = extract.refuse_extract_without_stats(2026, 7, "connection reset")
    msg.encode("ascii")
    assert "Nothing was written" in msg
    assert "free-agent" in msg


# ---------------------------------------------------------------------------
# The riders
# ---------------------------------------------------------------------------
def test_lookback_and_guard_window_are_the_same_policy():
    """The weekly lookback and the guard's exemption must not drift apart:
    a second hardcoded 21 would silently widen or narrow the guard the day
    someone tuned one of them (MLB-175).

    Rung 4B-1 raised this from "the same constant" to "the same function".
    There used to be two calendar walks reading one number; there is now one
    `classify_recency`, and both callers pass LIVE_CAPTURE_WINDOW_DAYS into
    it. Asserted period-by-period rather than by inspecting a default,
    because agreeing on the constant while disagreeing on the comparison was
    always the failure mode worth catching.
    """
    from matchup_membership import RECENT, classify_recency

    payload = {
        "seasonId": 2026,
        "status": {"currentMatchupPeriod": 18, "latestScoringPeriod": 120},
        "schedule": [{"matchupPeriodId": mp,
                      "home": {"pointsByScoringPeriod": {
                          str(sp): 0.0
                          for sp in range(7 * mp - 6, 7 * mp + 1)}}}
                     for mp in range(1, 18)],
    }
    parse = parse_matchup_membership(
        payload, league_key="espn-main", season_year=2026)

    selected = set(extract.select_matchup_periods(
        parse, requested=[], want_all=False, year=2026, today=TODAY)[0])
    exempt = {
        mp for mp in parse.closed_periods
        if not _settled(_FakeConn(loaded_periods=[mp]), [mp], parse)
    }
    expected = {mp for mp, verdict in classify_recency(
        parse, window=extract.LIVE_CAPTURE_WINDOW_DAYS,
        is_current_season=True).items() if verdict == RECENT}

    assert selected == exempt == expected
    assert selected == {15, 16, 17}, (
        "the window moved; if that was deliberate, move it here too"
    )


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
