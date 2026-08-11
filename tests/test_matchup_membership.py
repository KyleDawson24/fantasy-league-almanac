"""Matchup-period membership derived from ESPN's payload (MLB-235, rung 1).

Pure: no credentials, no network, no warehouse, no seed. `extract/
matchup_membership.py` exists as its own module precisely so this file can
reach it on a fresh clone with an empty environment -- extract/extract.py
raises on import without LEAGUE_ID.

EVERY PAYLOAD HERE IS SYNTHETIC, and the fixtures are built to the shape
MLB-235 recorded on the wire rather than to whatever would be convenient:
`schedule[]` entries keyed by `matchupPeriodId`, sides under `home`/`away`,
membership as the KEYS of `pointsByScoringPeriod`, and the current period read
from `status.currentMatchupPeriod`. Nothing is asserted about a status field
MLB-235 did not record -- a green test over a guessed field name would be
worse than no test, because it would read as verification.

The real numbers the fixtures echo, for anyone checking the shapes are not
invented: 2025 carried 26 matchup periods and 2026 eighteen at capture time,
every season has one long opening period and one short All-Star period, and
195 of 3,120 point values were exactly 0.0 with their keys intact.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Sibling import by bare name, for the reason spelled out at length in
# tests/test_local_raw_writer.py: once any test has imported extract/extract.py
# the dotted name `extract` resolves to that module FILE, so `from
# extract.matchup_membership import ...` passes alone and fails in the full
# suite depending on collection order.
sys.path.insert(0, str(REPO_ROOT / "extract"))

from matchup_membership import (  # noqa: E402
    AMBIGUOUS_STANDARD_LENGTH, DERIVED, INSUFFICIENT_EVIDENCE, MALFORMED,
    MAX_SCORING_PERIOD_KEY_LENGTH, MIN_CLOSED_PERIODS_FOR_STANDARD, UNAVAILABLE,
    MatchupMembershipError, derive_matchup_periods, derive_period_shape,
    matchup_schedule_snapshot, parse_matchup_membership, seasons_to_request,
)

LEAGUE = "espn-main"
SEASON = 2026

# Identity rides every fixture side, and is here to be IGNORED: the grain this
# module returns must never acquire it, and a fixture with no identity in it
# could not show that. The values are deliberately unmistakable -- a team id of
# 4 would collide with scoring period 4 and make the assertion meaningless.
TEAM_ID = 987654
OWNER_TAG = "SYNTHETIC-OWNER-TAG-NOT-A-GUID"
IDENTITY_NOISE = {"teamId": TEAM_ID, "adjustment": 0.0, "totalPoints": 123.45,
                  "owner": OWNER_TAG}


# ---------------------------------------------------------------------------
# Payload construction
# ---------------------------------------------------------------------------
def _side(scoring_periods, points=0.0):
    """One participating side. Keys are strings, as JSON delivers them."""
    side = dict(IDENTITY_NOISE)
    side["pointsByScoringPeriod"] = {str(sp): points for sp in scoring_periods}
    return side


def _matchup(matchup_period, scoring_periods, *, bye=False, away=None):
    entry = {"id": matchup_period * 100,
             "matchupPeriodId": matchup_period,
             "winner": "HOME",
             "home": _side(scoring_periods)}
    if not bye:
        entry["away"] = _side(away if away is not None else scoring_periods)
    return entry


def _season(period_lengths, *, current=None, season_year=SEASON,
            matchups_per_period=2, first_scoring_period=1):
    """Periods 1..N with consecutive scoring periods and N matchups each.

    `current` defaults to one past the last period, i.e. every period built is
    closed. Pass a lower value to model a season still in flight.
    """
    schedule = []
    scoring_period = first_scoring_period
    for index, length in enumerate(period_lengths, start=1):
        members = list(range(scoring_period, scoring_period + length))
        scoring_period += length
        for _ in range(matchups_per_period):
            schedule.append(_matchup(index, members))
    return {
        "id": 999,
        "seasonId": season_year,
        "status": {"currentMatchupPeriod": current or len(period_lengths) + 1},
        "schedule": schedule,
    }


def _parse(payload, season_year=SEASON):
    return parse_matchup_membership(
        payload, league_key=LEAGUE, season_year=season_year)


def _derive(payload, season_year=SEASON):
    return derive_matchup_periods(
        payload, league_key=LEAGUE, season_year=season_year)


# The shape both leagues on file actually have: a long opening period, an
# All-Star break period shorter than the rest, sevens everywhere else.
ORDINARY_SEASON = [10] + [7] * 12 + [4] + [7] * 3


# ---------------------------------------------------------------------------
# The ordinary case
# ---------------------------------------------------------------------------
def test_seven_day_membership_comes_off_the_keys():
    parse = _parse(_season([7, 7, 7, 7]))

    assert [p.matchup_period for p in parse.closed] == [1, 2, 3, 4]
    assert parse.closed[0].scoring_periods == (1, 2, 3, 4, 5, 6, 7)
    assert parse.closed[1].scoring_periods == (8, 9, 10, 11, 12, 13, 14)
    assert all(p.scoring_period_count == 7 for p in parse.closed)


def test_rows_carry_the_declared_grain_and_nothing_else():
    """(league_key, season_year, matchup_period, scoring_period). No identity.

    The fixtures put a teamId on every side; if it ever reached a row this
    fails, which is the point of it being there.
    """
    row = _parse(_season([7, 7, 7])).rows[0]

    assert (row.league_key, row.season_year) == (LEAGUE, SEASON)
    assert (row.matchup_period, row.scoring_period) == (1, 1)
    assert set(vars(row)) == {
        "league_key", "season_year", "matchup_period", "scoring_period"}


def test_no_team_or_owner_identity_survives_the_parse():
    report = _derive(_season([7, 7, 7]))
    rendered = repr(report)

    assert str(TEAM_ID) not in rendered
    assert OWNER_TAG not in rendered
    assert "teamId" not in rendered and "totalPoints" not in rendered


def test_ordering_is_deterministic():
    """Same payload in, same row order out -- these rows are a comparison
    substrate, and a set-ordered walk would reorder them between reads."""
    payload = _season(ORDINARY_SEASON)
    first = [(r.matchup_period, r.scoring_period) for r in _parse(payload).rows]
    second = [(r.matchup_period, r.scoring_period) for r in _parse(payload).rows]

    assert first == second
    assert first == sorted(first)


def test_a_long_opening_and_a_short_all_star_period_are_both_abnormal():
    """The real shape. 7 is the norm; the opening 10 and the All-Star 4 are
    the two exceptions the hand-maintained seed flags today."""
    report = _derive(_season(ORDINARY_SEASON))

    assert report.status == DERIVED
    assert report.standard_period_length == 7
    assert report.abnormal_periods == (1, 14)
    assert [p.scoring_period_count for p in report.periods][:2] == [10, 7]


def test_every_closed_period_gets_an_explicit_verdict():
    report = _derive(_season(ORDINARY_SEASON))

    assert len(report.periods) == len(ORDINARY_SEASON)
    assert all(isinstance(p.is_abnormal_derived, bool) for p in report.periods)


# ---------------------------------------------------------------------------
# Zero-point days
# ---------------------------------------------------------------------------
def test_zero_point_scoring_periods_stay_in_the_membership():
    """195 of 3,120 values were exactly 0.0 on the wire. Values are never
    read here -- dropping a zero would shorten a real week into a fake
    abnormality."""
    payload = _season([7, 7, 7])
    payload["schedule"][0]["home"]["pointsByScoringPeriod"] = {
        "1": 0.0, "2": 0.0, "3": 0.0, "4": 0.0, "5": 0.0, "6": 0.0, "7": 0.0}
    payload["schedule"][0]["away"]["pointsByScoringPeriod"] = {
        "1": 0.0, "2": 12.5, "3": 0, "4": 0.0, "5": 8.0, "6": 0.0, "7": 0.0}

    report = _derive(payload)

    assert report.status == DERIVED
    assert report.periods[0].scoring_period_count == 7
    assert report.abnormal_periods == ()


# ---------------------------------------------------------------------------
# The in-progress period
# ---------------------------------------------------------------------------
def test_the_current_period_is_excluded_structurally():
    """2026's period 18 read 6 scoring periods against the seed's 7, because
    it was still filling in. A short read from a live period is
    indistinguishable from a real abnormality, so it never reaches the
    statistic."""
    payload = _season([7] * 17 + [3], current=18)
    report = _derive(payload)

    assert report.current_matchup_period == 18
    assert report.excluded_periods == (18,)
    assert [p.matchup_period for p in report.periods] == list(range(1, 18))
    assert 18 not in {r.matchup_period for r in report.rows}
    assert report.standard_period_length == 7
    assert report.abnormal_periods == ()


def test_future_periods_are_skipped_without_being_validated():
    """Periods ESPN has not played are shaped like periods ESPN has not
    played. Refusing the season over one would be a bug, not a guard."""
    payload = _season([7] * 5, current=4)
    payload["schedule"].append(
        {"matchupPeriodId": 6, "home": {}, "away": {}})

    report = _derive(payload)

    assert report.status == DERIVED
    assert report.excluded_periods == (4, 5, 6)
    assert [p.matchup_period for p in report.periods] == [1, 2, 3]


def test_a_completed_season_classifies_every_period_it_carries():
    """26 periods with the current pointer past the end -- the shape a closed
    season takes when ESPN has moved on from it."""
    lengths = [10] + [7] * 24 + [4]
    report = _derive(_season(lengths, current=27, season_year=2025),
                     season_year=2025)

    assert report.status == DERIVED
    assert report.season_year == 2025
    assert len(report.periods) == 26
    assert report.excluded_periods == ()
    assert report.abnormal_periods == (1, 26)


def test_a_season_pointing_at_its_own_final_period_leaves_it_unclassified():
    """The honest limit of the recorded evidence, pinned rather than papered
    over: `currentMatchupPeriod` is the only status field MLB-235 measured, so
    a completed season whose pointer rests ON the final period loses that
    period from the derivation. Fail-closed -- unclassified, never guessed --
    and one read-only probe of a closed season's status block resolves it."""
    report = _derive(_season([7] * 26, current=26))

    assert report.status == DERIVED
    assert report.excluded_periods == (26,)
    assert 26 not in {p.matchup_period for p in report.periods}


# ---------------------------------------------------------------------------
# Byes
# ---------------------------------------------------------------------------
def test_a_bye_side_is_not_a_missing_side():
    """An odd-numbered league pairs someone with nobody (MLB-222 C-1). The
    unpaired team's own key set is the period's membership."""
    payload = _season([7, 7, 7])
    payload["schedule"][0] = _matchup(1, [1, 2, 3, 4, 5, 6, 7], bye=True)

    report = _derive(payload)

    assert report.status == DERIVED
    assert report.periods[0].scoring_period_count == 7


def test_a_null_side_is_treated_as_a_bye_rather_than_a_shape_error():
    payload = _season([7, 7, 7])
    payload["schedule"][0]["away"] = None

    assert _derive(payload).status == DERIVED


# ---------------------------------------------------------------------------
# Disagreement -- the finding collapsing
# ---------------------------------------------------------------------------
def test_home_and_away_disagreement_stops_the_derivation():
    """If these keys were 'days THIS TEAM scored' rather than 'days in the
    period', two sides could differ. They never do in the evidence -- so a
    disagreement is not a shape to accommodate, and picking a side would
    manufacture either a normal week or an abnormal one."""
    payload = _season([7, 7, 7])
    payload["schedule"][0]["away"] = _side([1, 2, 3, 4, 5, 6])

    with pytest.raises(MatchupMembershipError) as excinfo:
        _parse(payload)

    assert "disagree" in str(excinfo.value)
    assert "matchup period 1" in str(excinfo.value)


def test_disagreement_across_matchups_in_the_same_period_is_caught_too():
    """Two teams in the SAME period, in different matchups. The check is
    per-period, not per-matchup, because that is the claim being tested."""
    payload = _season([7, 7, 7], matchups_per_period=2)
    payload["schedule"][1] = _matchup(1, [1, 2, 3, 4, 5, 6, 7, 8])

    with pytest.raises(MatchupMembershipError, match="disagree"):
        _parse(payload)


def test_a_disagreement_message_names_the_keys_not_the_teams():
    payload = _season([7, 7, 7])
    payload["schedule"][0]["away"] = _side([1, 2, 3, 4, 5, 6])

    with pytest.raises(MatchupMembershipError) as excinfo:
        _parse(payload)

    assert str(TEAM_ID) not in str(excinfo.value)
    assert OWNER_TAG not in str(excinfo.value)
    assert "[1, 2, 3, 4, 5, 6]" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Missing, empty, malformed
# ---------------------------------------------------------------------------
def test_a_closed_side_with_no_points_block_is_refused():
    """Unknown is not empty. Inferring the length from the other side would
    invent evidence for a period ESPN has not filled in."""
    payload = _season([7, 7, 7])
    del payload["schedule"][0]["away"]["pointsByScoringPeriod"]

    with pytest.raises(MatchupMembershipError, match="no pointsByScoringPeriod"):
        _parse(payload)


def test_an_empty_points_block_is_refused():
    payload = _season([7, 7, 7])
    payload["schedule"][0]["home"]["pointsByScoringPeriod"] = {}

    with pytest.raises(MatchupMembershipError, match="empty"):
        _parse(payload)


@pytest.mark.parametrize("bad_key", ["1.0", " 1", "", "opening-day", "-1"])
def test_non_integer_scoring_period_keys_are_refused(bad_key):
    payload = _season([7, 7, 7])
    payload["schedule"][0]["home"]["pointsByScoringPeriod"] = {bad_key: 1.0}

    with pytest.raises(MatchupMembershipError):
        _parse(payload)


@pytest.mark.parametrize("unicode_key, why", [
    ("²", "int() refuses a superscript with a bare ValueError"),
    ("１", "int() accepts a full-width digit SILENTLY as 1"),
])
def test_unicode_digits_that_pass_isdigit_are_refused(unicode_key, why):
    """`str.isdigit()` is wrong in both directions and the seam promised
    otherwise. '²' escaped the parser's own error type entirely; '１'
    was worse -- admitted as scoring period 1, a key nothing on the wire
    would ever send."""
    payload = _season([7, 7, 7])
    payload["schedule"][0]["home"]["pointsByScoringPeriod"] = {unicode_key: 1.0}

    with pytest.raises(MatchupMembershipError, match="non-integer"):
        _parse(payload)

    assert _derive(payload).status == MALFORMED, why


def test_an_absurdly_long_digit_key_is_refused_rather_than_raising():
    """The last raising path: CPython 3.11+ refuses int() on digit strings
    past 4,300 characters, so a promise that held for every payload except an
    absurd one was not a promise."""
    payload = _season([7, 7, 7])
    payload["schedule"][0]["home"]["pointsByScoringPeriod"] = {"1" * 5000: 1.0}

    with pytest.raises(MatchupMembershipError,
                       match=f"past {MAX_SCORING_PERIOD_KEY_LENGTH} digits"):
        _parse(payload)

    assert _derive(payload).status == MALFORMED


def test_a_zero_scoring_period_id_is_refused():
    payload = _season([7, 7, 7])
    payload["schedule"][0]["home"]["pointsByScoringPeriod"] = {"0": 1.0}

    with pytest.raises(MatchupMembershipError, match="1-based"):
        _parse(payload)


def test_a_missing_status_block_is_refused():
    payload = _season([7, 7, 7])
    del payload["status"]

    with pytest.raises(MatchupMembershipError, match="status"):
        _parse(payload)


def test_a_missing_schedule_names_the_view_to_request():
    payload = _season([7, 7, 7])
    del payload["schedule"]

    with pytest.raises(MatchupMembershipError, match="mMatchupScore"):
        _parse(payload)


def test_a_payload_for_another_season_is_refused():
    """Stamping the caller's season onto another season's document would land
    it, silently, as that season's membership."""
    with pytest.raises(MatchupMembershipError, match="2025"):
        _parse(_season([7, 7, 7], season_year=2025), season_year=SEASON)


def test_a_season_id_absent_from_the_payload_is_not_invented():
    payload = _season([7, 7, 7])
    del payload["seasonId"]

    assert _parse(payload).season_year == SEASON


def test_a_gap_in_the_closed_run_is_refused():
    """A mode over a gapped set is a statistic about the gap."""
    payload = _season([7] * 6, current=7)
    payload["schedule"] = [e for e in payload["schedule"]
                           if e["matchupPeriodId"] != 3]

    with pytest.raises(MatchupMembershipError, match="matchup period 3"):
        _parse(payload)


def test_a_missing_tail_of_closed_periods_is_refused():
    """The other side of the same check: everything present is contiguous,
    but the run stops short of the current period."""
    payload = _season([7] * 3, current=9)

    with pytest.raises(MatchupMembershipError, match="matchup period 4"):
        _parse(payload)


def test_a_huge_current_period_is_refused_without_allocating_for_it():
    """`status.currentMatchupPeriod` is an attacker-reachable integer, so the
    gap check must cost what the payload CONTAINS, not what it claims.

    Comparing against a materialised `range(1, current)` turned a six-byte
    edit into a terabyte-scale allocation -- and exhausting memory on the way
    to a verdict is not a slower version of returning it. A regression here
    surfaces as MemoryError rather than a wrong answer, which is why the
    number is absurd rather than merely large.
    """
    payload = _season([7] * 3, current=10 ** 12)

    report = _derive(payload)

    assert report.status == MALFORMED
    assert "matchup period 4" in report.reason
    assert str(10 ** 12) in report.reason


@pytest.mark.parametrize("bad", [None, [], "schedule", 42])
def test_a_non_document_payload_is_refused(bad):
    with pytest.raises(MatchupMembershipError):
        _parse(bad)


# ---------------------------------------------------------------------------
# Fail-closed statistics
# ---------------------------------------------------------------------------
def test_one_closed_period_never_becomes_the_standard():
    """The failure this floor exists for: the only closed period early in a
    season is the long opening one, and a mode over it would bless the
    anomaly as the norm and flag every ordinary week after it."""
    report = _derive(_season([10] + [7] * 5, current=2))

    assert report.status == INSUFFICIENT_EVIDENCE
    assert report.standard_period_length is None
    assert report.periods[0].scoring_period_count == 10
    assert report.periods[0].is_abnormal_derived is None
    assert str(MIN_CLOSED_PERIODS_FOR_STANDARD) in report.reason


def test_an_undetermined_period_is_not_reported_as_normal():
    """None, never False. 'Not abnormal' and 'not known' are different facts,
    and collapsing them turns a fail-closed design into a fail-open one."""
    report = _derive(_season([10] + [7] * 5, current=2))

    assert all(p.is_abnormal_derived is None for p in report.periods)
    assert report.abnormal_periods == ()


def test_a_tied_mode_refuses_rather_than_picking_the_lower():
    report = _derive(_season([7, 7, 10, 10], current=5))

    assert report.status == AMBIGUOUS_STANDARD_LENGTH
    assert report.standard_period_length is None
    assert "[7, 10]" in report.reason


def test_all_distinct_lengths_are_a_tie_and_refuse():
    report = _derive(_season([6, 7, 8], current=4))

    assert report.status == AMBIGUOUS_STANDARD_LENGTH
    assert report.standard_period_length is None


def test_a_bare_majority_is_enough_once_the_floor_is_met():
    report = _derive(_season([7, 7, 10], current=4))

    assert report.status == DERIVED
    assert report.standard_period_length == 7
    assert report.abnormal_periods == (3,)


def test_an_empty_schedule_is_unavailable_not_derived():
    payload = _season([7, 7, 7])
    payload["schedule"] = []
    payload["status"]["currentMatchupPeriod"] = 1

    report = _derive(payload)

    assert report.status == UNAVAILABLE
    assert report.rows == ()
    assert report.standard_period_length is None


def test_a_season_that_has_not_started_is_insufficient_not_unavailable():
    """Nothing closed yet, but ESPN did serve a schedule -- a different fact
    from 'no membership exists', and the reason string has to say which."""
    report = _derive(_season([7] * 20, current=1))

    assert report.status == INSUFFICIENT_EVIDENCE
    assert report.excluded_periods == tuple(range(1, 21))


# ---------------------------------------------------------------------------
# The seam MLB-207 consumes
# ---------------------------------------------------------------------------
def test_the_seam_reports_malformed_instead_of_raising():
    """A validator that dies on the payload it was asked to describe has
    reported nothing. The strict parser stays strict; this wraps it."""
    payload = _season([7, 7, 7])
    payload["schedule"][0]["away"] = _side([1, 2, 3])

    report = _derive(payload)

    assert report.status == MALFORMED
    assert "disagree" in report.reason
    assert report.rows == () and report.periods == ()
    assert report.current_matchup_period is None


@pytest.mark.parametrize("payload", [
    None, {}, {"status": {}}, {"status": {"currentMatchupPeriod": 3}},
    {"seasonId": 1999, "status": {"currentMatchupPeriod": 1}, "schedule": []},
])
def test_the_seam_never_raises_on_any_shape(payload):
    report = _derive(payload)

    assert report.status in {MALFORMED, UNAVAILABLE, INSUFFICIENT_EVIDENCE}
    assert report.reason
    assert report.standard_period_length is None


def test_the_seam_always_names_the_league_and_season_it_was_asked_about():
    """MLB-207 reports per (league, season); a refusal that cannot say which
    one it refused is not actionable."""
    report = _derive({"status": {"currentMatchupPeriod": "soon"}})

    assert (report.league_key, report.season_year) == (LEAGUE, SEASON)
    assert report.status == MALFORMED


def test_the_seam_survives_every_json_shape_a_payload_can_take():
    """The guarantee, exercised rather than asserted in prose. Every value
    JSON can decode to, in the places the parser reaches for one."""
    shapes = [None, True, 0, 1.5, "", [], {}, [[{}]], {"a": {"b": [None]}}]
    payloads = [{"status": s, "schedule": s, "seasonId": s} for s in shapes]
    payloads += [{"status": {"currentMatchupPeriod": s}, "schedule": []}
                 for s in shapes]
    payloads += [{"status": {"currentMatchupPeriod": 3},
                  "schedule": [{"matchupPeriodId": 1, "home": s, "away": s}]}
                 for s in shapes]
    payloads += shapes

    for payload in payloads:
        report = _derive(payload)
        assert report.status in {MALFORMED, UNAVAILABLE, INSUFFICIENT_EVIDENCE}
        assert report.reason


def test_derive_period_shape_can_be_called_on_a_parse_directly():
    """The two halves compose: parse once, derive from it, no re-read."""
    parse = _parse(_season(ORDINARY_SEASON))
    report = derive_period_shape(parse)

    assert report.status == DERIVED
    assert report.rows == parse.rows


# ---------------------------------------------------------------------------
# What RAW stores
# ---------------------------------------------------------------------------
def _snapshot(payload, season_year=SEASON):
    return matchup_schedule_snapshot(payload, season_year=season_year)


def test_the_snapshot_keeps_the_three_keys_a_derivation_needs():
    payload = _season([7, 7, 7])
    payload["draftDetail"] = {"drafted": True}

    snapshot = _snapshot(payload)

    assert set(snapshot) == {"seasonId", "status", "schedule"}
    assert snapshot["schedule"] is payload["schedule"]
    assert snapshot["status"] is payload["status"]


def test_a_stored_snapshot_round_trips_through_the_parser():
    """The load-bearing property: what RAW keeps is enough to derive from,
    with no access to the original response. If this ever fails, every row
    already captured is undecodable and ESPN will not re-serve the payload."""
    report = derive_matchup_periods(
        _snapshot(_season(ORDINARY_SEASON)),
        league_key=LEAGUE, season_year=SEASON)

    assert report.status == DERIVED
    assert report.standard_period_length == 7
    assert report.abnormal_periods == (1, 14)


def test_the_snapshot_survives_a_json_round_trip():
    """RAW stores it as JSON text in a VARIANT, so the object that comes back
    is not the object that went in -- integer keys would become strings and
    tuples would become lists. Derivation has to hold across that."""
    import json

    snapshot = json.loads(json.dumps(_snapshot(_season(ORDINARY_SEASON))))
    report = derive_matchup_periods(
        snapshot, league_key=LEAGUE, season_year=SEASON)

    assert report.status == DERIVED
    assert report.standard_period_length == 7


@pytest.mark.parametrize("drop", ["seasonId", "status", "schedule"])
def test_a_snapshot_missing_any_required_block_is_refused(drop):
    """All three are required. Finding this out at read time -- possibly a
    season later, with the live payload long gone -- is strictly worse than
    finding it out now."""
    payload = _season([7, 7, 7])
    del payload[drop]

    with pytest.raises(MatchupMembershipError, match=drop):
        _snapshot(payload)


def test_the_schedule_array_alone_is_not_a_snapshot():
    """Named explicitly because it is the shape someone would reach for
    first: it is the membership, and it is still not enough."""
    payload = _season([7, 7, 7])

    with pytest.raises(MatchupMembershipError, match="status"):
        _snapshot({"schedule": payload["schedule"]})


def test_a_season_id_that_disagrees_with_the_stamp_is_refused():
    """The row's season_year comes from the loader, so a document filed
    under the wrong season agrees with itself perfectly -- ESPN's own
    seasonId is the only thing that can contradict it, and only if it is
    checked before the write."""
    payload = _season([7, 7, 7], season_year=2025)

    with pytest.raises(MatchupMembershipError, match="season 2025"):
        _snapshot(payload, season_year=SEASON)


@pytest.mark.parametrize("bad_season", ["2026", 2026.0, True, [2026]])
def test_a_non_integer_season_id_is_refused(bad_season):
    """'2026' == 2026 is False, so a string season would fail the equality
    check with a confusing message about a mismatch rather than a shape."""
    payload = _season([7, 7, 7])
    payload["seasonId"] = bad_season

    with pytest.raises(MatchupMembershipError, match="season year|season 2026"):
        _snapshot(payload)


@pytest.mark.parametrize("bad_status", ["18", 18, ["currentMatchupPeriod"], 1.5])
def test_a_non_object_status_is_refused(bad_status):
    payload = _season([7, 7, 7])
    payload["status"] = bad_status

    with pytest.raises(MatchupMembershipError, match="status is"):
        _snapshot(payload)


@pytest.mark.parametrize("bad_schedule", [{}, "schedule", 7, {"1": []}])
def test_a_non_list_schedule_is_refused(bad_schedule):
    payload = _season([7, 7, 7])
    payload["schedule"] = bad_schedule

    with pytest.raises(MatchupMembershipError, match="schedule is"):
        _snapshot(payload)


def test_an_empty_schedule_list_is_still_a_valid_capture():
    """Structural checks only. A league ESPN has not scheduled yet returns
    an empty list, which is a real answer about the league -- refusing it
    would confuse 'nothing scheduled' with 'document not understood'."""
    payload = _season([7, 7, 7])
    payload["schedule"] = []

    assert _snapshot(payload)["schedule"] == []


# ---------------------------------------------------------------------------
# Which seasons a backfill asks for (Kyle's ruling, 2026-08-11)
# ---------------------------------------------------------------------------
def test_an_ongoing_league_is_bounded_by_the_season_being_asked_for():
    """final_season null means still running, so the request bounds it --
    nothing past the current season exists to fetch."""
    assert seasons_to_request(2025, None, 2026) == (2025, 2026)


def test_a_closed_league_stops_at_its_final_season():
    assert seasons_to_request(2019, 2021, 2026) == (2019, 2020, 2021)


def test_the_request_still_bounds_a_league_declared_final_in_the_future():
    assert seasons_to_request(2025, 2030, 2026) == (2025, 2026)


def test_a_single_season_league_asks_once():
    assert seasons_to_request(2026, None, 2026) == (2026,)


def test_a_range_that_has_not_started_is_empty_rather_than_wrong():
    """A real answer, and one the caller reports rather than treats as
    success."""
    assert seasons_to_request(2027, None, 2026) == ()


def test_a_registry_entry_with_no_first_season_is_refused():
    """There is no non-circular lower bound to fall back on -- guessing one
    from the seed is the dependency this whole module exists to remove."""
    with pytest.raises(ValueError, match="first_season"):
        seasons_to_request(None, None, 2026)
