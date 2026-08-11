"""The automatic season opener and the calendar it produces (MLB-235 4B-2).

Pure: no credentials, no network, no warehouse, no seed. `extract/
season_calendar.py` is its own module precisely so this file can reach it on a
fresh clone with an empty environment.

WHAT IS BEING PROVEN, and it is three independent facts agreeing rather than
one implementation agreeing with itself:

  * MLB says when the regular season starts (2025-03-18, 2026-03-25) --
    measured from statsapi.mlb.com and pinned here as fixtures;
  * ESPN says how many scoring periods each matchup period holds -- measured
    from the preserved mMatchupScore payloads and pinned here as period
    lengths;
  * the hand-maintained `demo/league_config/matchup_schedule.csv` says what
    the calendar actually was -- committed, publishable, and written by a
    human years before any of this was derived.

If the opener were wrong, or the day arithmetic off by one, or the All-Star
break compressed, the third would disagree with the first two. That is the
test. Nothing here reads the private league_config seed or the scratchpad
rehearsal, so it runs identically on a fresh clone.
"""

import csv
import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "extract"))

from season_calendar import (  # noqa: E402
    ANCHOR_FIELD, SeasonCalendarError, matchup_period_dates,
    scoring_period_date, season_calendar_snapshot, season_calendar_url,
    season_opener,
)

# ---------------------------------------------------------------------------
# Measured fixtures
# ---------------------------------------------------------------------------
# MLB's own published regular-season start, as statsapi.mlb.com answered on
# 2026-08-11. Both are SPECIAL openers days before the conventional full-slate
# Opening Day, and they are not the same kind of special: 2025-03-18 was the
# Tokyo Series, 2026-03-25 a single Yankees-Giants Opening Night in San
# Francisco. A rule like "the season starts on the day everyone plays" would
# therefore have been wrong in both seasons on file, in two different ways --
# which is why the anchor is MLB's published field rather than an inference.
MLB_OPENER = {2025: date(2025, 3, 18), 2026: date(2026, 3, 25)}

# ESPN's own membership sizes, from the preserved mMatchupScore payloads: how
# many scoring periods each matchup period holds, in order. The periods are
# contiguous and abut, so period N's ids follow directly from these.
#
# 2025 is a COMPLETED season (26 periods). 2026 was live at capture, so its
# last entry (period 19, two ids) is the in-flight period and is excluded from
# the date comparison below -- an unfinished period's membership is still
# filling in, which is precisely why derived dates are gated on is_closed.
ESPN_PERIOD_LENGTHS = {
    2025: [13] + [7] * 14 + [14] + [7] * 10,
    2026: [12] + [7] * 13 + [14] + [7] * 3 + [2],
}
LIVE_PERIOD = {2025: None, 2026: 19}

# The two All-Star periods, called out because they are the case a naive
# implementation gets wrong: MLB's official break is three no-game days INSIDE
# them (2025-07-15..17, 2026-07-14..16 by MLB's own lastDate1stHalf /
# firstDate2ndHalf), and ESPN returns no player-stat content on those days.
# The membership ids do not disappear, so compressing them would shift every
# date after the break.
ALL_STAR = {2025: (16, 112, 125), 2026: (15, 104, 117)}

DEMO_SEED = REPO_ROOT / "demo" / "league_config" / "matchup_schedule.csv"


def _payload(season_year, start, end="2026-09-27", season_id=None):
    """An MLB /seasons response, shaped as measured."""
    return {"seasons": [{
        "seasonId": season_id if season_id is not None else str(season_year),
        "seasonStartDate": "2026-02-20",       # spring training -- a decoy
        ANCHOR_FIELD: start,
        "regularSeasonEndDate": end,
        "allStarDate": "2026-07-14",
    }]}


def _legacy_calendar():
    """The committed demo fixture's calendar: {(season, period): (start, end)}.

    The demo fixture rather than dbt_league/league_config/: the latter ships
    as a header-only template and holds real league data only on the
    maintainer's disk, so a test reading it would pass for one person and
    fail for everyone. The demo twin carries the same dates -- names are
    anonymized, calendars are not.
    """
    out = {}
    with DEMO_SEED.open(newline="") as handle:
        for row in csv.DictReader(handle):
            out[(int(row["season_year"]), int(row["matchup_period"]))] = (
                date.fromisoformat(row["start_date"]),
                date.fromisoformat(row["end_date"]))
    return out


def _membership(season_year):
    """{matchup_period: [scoring period ids]} from the measured lengths."""
    periods, scoring_period = {}, 1
    for index, length in enumerate(ESPN_PERIOD_LENGTHS[season_year], start=1):
        periods[index] = list(range(scoring_period, scoring_period + length))
        scoring_period += length
    return periods


# ===========================================================================
# 19. The day arithmetic
# ===========================================================================
def test_scoring_period_one_is_the_opener_itself():
    """The off-by-one that would shift an entire season, pinned."""
    for year, opener in MLB_OPENER.items():
        assert scoring_period_date(opener, 1) == opener, year


def test_scoring_period_n_is_the_opener_plus_n_minus_one_days():
    opener = MLB_OPENER[2026]

    assert scoring_period_date(opener, 2) == date(2026, 3, 26)
    assert scoring_period_date(opener, 8) == date(2026, 4, 1)
    assert scoring_period_date(opener, 104) == date(2026, 7, 6)
    assert scoring_period_date(opener, 187) == date(2026, 9, 27)


def test_matchup_dates_come_from_the_membership_bounds():
    """Min and max, not first-and-count: the dates describe the ids that are
    actually there, so a hole in a membership shows up instead of sliding."""
    opener = MLB_OPENER[2026]

    assert matchup_period_dates(opener, [1, 2, 3, 4, 5, 6, 7]) == (
        date(2026, 3, 25), date(2026, 3, 31))
    # Deliberately unordered and gapped: bounds, not sequence.
    assert matchup_period_dates(opener, [9, 3, 5]) == (
        date(2026, 3, 27), date(2026, 4, 2))


def test_a_scoring_period_that_is_not_a_positive_integer_refuses():
    opener = MLB_OPENER[2026]
    for bad in (0, -1, True, "5", 5.0, None):
        with pytest.raises(SeasonCalendarError):
            scoring_period_date(opener, bad)


def test_a_period_with_no_membership_has_no_dates():
    with pytest.raises(SeasonCalendarError, match="no scoring periods"):
        matchup_period_dates(MLB_OPENER[2026], [])


# ===========================================================================
# 20. The derived calendar equals the hand-maintained one
# ===========================================================================
def test_the_derived_calendar_reproduces_every_closed_period_of_both_seasons():
    """THE HEADLINE, and the reason this rung can retire the seed.

    MLB's opener plus ESPN's membership sizes reproduce a calendar a human
    maintained by hand, period for period, across two seasons. Any error in
    the anchor, the arithmetic or the membership shows up as a mismatch here.
    """
    legacy = _legacy_calendar()
    compared = []

    for season_year, opener in MLB_OPENER.items():
        for period, scoring_periods in _membership(season_year).items():
            if period == LIVE_PERIOD[season_year]:
                continue                     # in flight; membership incomplete
            expected = legacy.get((season_year, period))
            assert expected is not None, (season_year, period)
            assert matchup_period_dates(opener, scoring_periods) == expected, (
                f"{season_year} matchup period {period}")
            compared.append((season_year, period))

    assert len(compared) == 44, (
        "the comparison silently shrank; 2025 contributes 26 closed periods "
        "and 2026 eighteen")


def test_the_long_opening_weeks_are_reproduced_not_rounded_to_seven():
    """Both seasons open with an oversized period (13 days and 12), which a
    seven-day assumption anywhere would flatten and then shift the rest."""
    legacy = _legacy_calendar()

    for season_year, length in ((2025, 13), (2026, 12)):
        members = _membership(season_year)[1]
        assert len(members) == length
        assert matchup_period_dates(MLB_OPENER[season_year], members) == \
            legacy[(season_year, 1)]


def test_a_wrong_year_anchor_refuses_rather_than_shifting_the_calendar():
    """The dangerous failure: an anchor that is internally consistent and
    uniformly wrong. Every date would agree with every other date and the
    whole season would be off."""
    with pytest.raises(SeasonCalendarError, match="wrong season"):
        season_calendar_snapshot(_payload(2026, "2025-03-18"),
                                 season_year=2026)


def test_a_disagreeing_season_id_refuses():
    with pytest.raises(SeasonCalendarError, match="season "):
        season_calendar_snapshot(_payload(2026, "2026-03-25", season_id="2025"),
                                 season_year=2026)


# ===========================================================================
# 22. The All-Star break is days, not a gap
# ===========================================================================
@pytest.mark.parametrize("season_year", sorted(ALL_STAR))
def test_the_all_star_period_maps_all_fourteen_ids_to_all_fourteen_days(
        season_year):
    """Fourteen consecutive scoring-period ids become fourteen consecutive
    calendar days. ESPN returns no player-stat content on the three official
    break days inside them, and that must change nothing -- a zero-stat day
    is still a day."""
    period, first, last = ALL_STAR[season_year]
    opener = MLB_OPENER[season_year]
    members = _membership(season_year)[period]

    assert members == list(range(first, last + 1))
    assert len(members) == 14

    days = [scoring_period_date(opener, sp) for sp in members]
    assert len(set(days)) == 14
    assert (days[-1] - days[0]).days == 13, "the mapping compressed the break"
    assert matchup_period_dates(opener, members) == \
        _legacy_calendar()[(season_year, period)]


@pytest.mark.parametrize("season_year", sorted(ALL_STAR))
def test_a_zero_stat_break_day_does_not_shift_the_dates_after_it(season_year):
    """The regression a break-aware implementation would introduce: skipping
    three no-game days would pull every later period three days earlier."""
    period, _first, last = ALL_STAR[season_year]
    opener = MLB_OPENER[season_year]
    legacy = _legacy_calendar()

    following = _membership(season_year)[period + 1]
    assert following[0] == last + 1, "the next period does not abut this one"
    assert matchup_period_dates(opener, following) == \
        legacy[(season_year, period + 1)]

    # And the season's LAST closed period too, so a three-day drift anywhere
    # in between is caught rather than only at the seam.
    live = LIVE_PERIOD[season_year]
    final = max(p for p in _membership(season_year) if p != live)
    assert matchup_period_dates(opener, _membership(season_year)[final]) == \
        legacy[(season_year, final)]


# ===========================================================================
# The snapshot contract
# ===========================================================================
def test_the_snapshot_is_narrow_and_provenance_bearing():
    snapshot = season_calendar_snapshot(_payload(2026, "2026-03-25"),
                                        season_year=2026)

    assert set(snapshot) == {"seasonId", "source", "anchor_field",
                             "regularSeasonStartDate", "regularSeasonEndDate"}
    assert snapshot["seasonId"] == 2026
    assert snapshot["anchor_field"] == ANCHOR_FIELD
    assert snapshot["source"] == season_calendar_url(2026)
    assert "sportId=1" in snapshot["source"] and "season=2026" in snapshot["source"]


def test_the_snapshot_keeps_no_field_no_model_reads():
    """The endpoint also serves All-Star, postseason and spring boundaries.
    Storing them would invite the next reader to build on parts nobody
    verified -- and `seasonStartDate` in particular is spring training, i.e.
    a plausible-looking wrong anchor sitting right next to the real one."""
    snapshot = season_calendar_snapshot(_payload(2026, "2026-03-25"),
                                        season_year=2026)

    assert "seasonStartDate" not in snapshot
    assert "allStarDate" not in snapshot


def test_the_opener_round_trips_out_of_a_stored_snapshot():
    for year, expected in MLB_OPENER.items():
        stored = season_calendar_snapshot(
            _payload(year, expected.isoformat(), end=f"{year}-09-27"),
            season_year=year)
        assert season_opener(stored) == expected


@pytest.mark.parametrize("payload, match", [
    ({}, "no non-empty seasons array"),
    ({"seasons": []}, "no non-empty seasons array"),
    ({"seasons": [None]}, "not an object"),
    ("2026-03-25", "not an MLB seasons document"),
])
def test_a_malformed_seasons_document_refuses(payload, match):
    with pytest.raises(SeasonCalendarError, match=match):
        season_calendar_snapshot(payload, season_year=2026)


@pytest.mark.parametrize("bad", [None, "", "March 25, 2026", "2026-13-40", 20260325])
def test_an_unparseable_anchor_refuses_rather_than_landing_a_guess(bad):
    payload = _payload(2026, "2026-03-25")
    payload["seasons"][0][ANCHOR_FIELD] = bad

    with pytest.raises(SeasonCalendarError):
        season_calendar_snapshot(payload, season_year=2026)


def test_the_only_file_this_reads_is_the_committed_demo_twin():
    """Everything else here is a measured constant.

    The private league_config seed ships as a header-only template and holds
    real data only on the maintainer's disk, so a test reading it would pass
    for one person and fail for everyone; the scratchpad rehearsal does not
    survive the session. The demo twin is tracked, publishable, and carries
    the same calendar -- names are anonymized, dates are not.
    """
    assert DEMO_SEED.exists(), "the demo fixture calendar is missing"
    assert DEMO_SEED.relative_to(REPO_ROOT).parts[0] == "demo"
    assert len(_legacy_calendar()) >= 44


# ===========================================================================
# The request itself is bounded
# ===========================================================================
def test_the_calendar_fetch_is_bounded_and_identifies_itself():
    """It rides EVERY ordinary box-score run now, so an unbounded request is
    not a small thing: requests' default is no timeout at all, and a host
    that accepts the connection then stalls would hang the weekly extract
    forever -- on a call whose entire failure plan is "warn and carry on".

    Loaded here rather than in the extract's own test module because this is
    about the shape of one outbound request, not about selection.
    """
    import importlib.util
    import os

    from dotenv import load_dotenv

    load_dotenv()
    os.environ.setdefault("LEAGUE_ID", "0")
    spec = importlib.util.spec_from_file_location(
        "extract_calendar_request_under_test",
        REPO_ROOT / "extract" / "extract.py")
    extract = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(extract)

    calls = []

    class _Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"seasons": [{"seasonId": "2026",
                                 "regularSeasonStartDate": "2026-03-25",
                                 "regularSeasonEndDate": "2026-09-27"}]}

    def _get(url, **kwargs):
        calls.append((url, kwargs))
        return _Response()

    original = extract.requests.get
    extract.requests.get = _get
    try:
        extract.fetch_season_calendar(2026)
    finally:
        extract.requests.get = original

    assert len(calls) == 1, "one request per season, still"
    url, kwargs = calls[0]

    assert url == season_calendar_url(2026)
    timeout = kwargs.get("timeout")
    assert timeout is not None, "an unbounded request can hang the weekly run"
    assert 0 < timeout <= 60, timeout
    assert timeout == extract.SEASON_CALENDAR_TIMEOUT_SECONDS

    agent = (kwargs.get("headers") or {}).get("User-Agent")
    assert agent == extract.PUBLIC_API_USER_AGENT
    assert "espn-league-manager" in agent, (
        "a free public API deserves to know who is calling it"
    )
    # No credentials go to a third party.
    assert "cookies" not in kwargs
