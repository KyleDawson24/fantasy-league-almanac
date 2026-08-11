"""The season's first scoring date, measured rather than typed (MLB-235 4B-2).

WHAT THIS EXISTS TO BREAK. Rung 4B-1 took `matchup_schedule.csv` off every
extraction path, but one thing still lived only in that CSV: the CALENDAR. The
seed's earliest `start_date` was the season-opener anchor, and
`output/almanac_data.py::_get_season_opener()` read it to turn a trade's
execution date into an ESPN scoring period. Retiring the required seed means
replacing that anchor, not abandoning dates.

THE CONTRACT, and the project has always relied on it: an ESPN scoring period
is ONE CALENDAR DAY. So the whole calendar follows from a single number --

    scoring period N  ==  opener + (N - 1) days

-- and a matchup period's start/end are just the min and max of the
scoring-period ids in its platform-derived membership. Nothing here needs
ESPN to serve an ISO date, and ESPN does not serve one.

WHERE THE ANCHOR COMES FROM, and why this one. MLB's own public season record:

    GET https://statsapi.mlb.com/api/v1/seasons?sportId=1&season=YYYY
    -> {"seasons": [{"seasonId": "2026",
                     "regularSeasonStartDate": "2026-03-25",
                     "regularSeasonEndDate": "2026-09-27", ...}]}

MEASURED, NOT ASSUMED: 2025 answers 2025-03-18 and 2026 answers 2026-03-25,
which are exactly the openers the hand-maintained seed carries. Restricted to
CLOSED periods -- the only ones with settled membership -- the derived
calendar reproduces the seed on all 44 periods of both seasons, zero
mismatches, including the long opening weeks (13 and 12 days) and both 14-day
All-Star periods. That is the validation, and it is asserted in
tests/test_season_calendar.py rather than described here.

IT IS THE REGULAR-SEASON START, and that is load-bearing rather than
pedantic. MLB routinely opens with a special or standalone game days before
the day most people would call Opening Day, and it does not do it the same
way twice: 2025 began with the Tokyo Series on March 18, while 2026 began
with a single Yankees-Giants Opening Night in San Francisco on March 25. Both
precede the conventional full-slate opener. So "the season starts on the
Thursday everyone plays" is a rule that would have been wrong in both seasons
on file, in two different ways -- which is exactly why the anchor is MLB's own
published `regularSeasonStartDate` rather than any inference about which date
counts. Spring training is excluded for the same reason, and is why the
stored snapshot records WHICH field it used.

WHY NOT `status.latestScoringPeriod` PAIRED WITH THE CAPTURE DATE. It is the
obvious platform-only alternative -- opener = today - (latest - 1) -- and it
is brittle in a way the MLB calendar is not: it silently assumes the capture
happened ON the latest scoring day, so a run before ESPN rolls the day over,
or a run during a gap in play, lands the whole season one or more days off and
nothing about the answer says so. MLB's regular-season start is a published
fact about the season rather than an inference from when someone ran a script.

ALL-STAR DAYS ARE STILL DAYS. The break is three no-game days inside a
14-scoring-period matchup period (2025: ids 112..125, July 7-20, with the
official break July 15-17; 2026: ids 104..117, July 6-19). ESPN may return no
player-stat content on those days, but the membership ids do not disappear, so
the mapping must NOT compress them -- doing so would shift every date after
the break by three days. There is deliberately no break-handling code here:
the arithmetic is a plain day offset and that is exactly why it is correct.

POSTPONEMENTS, SUSPENDED GAMES, DOUBLEHEADERS AND STAT CORRECTIONS do not move
anything either. ESPN's scoring-period id remains authoritative for WHERE
fantasy points landed; the calendar only supplies the corresponding date.

WHY IT IS A SEPARATE MODULE, same reason as matchup_membership.py: extract.py
reads LEAGUE_ID at import and raises without it, so a fresh clone cannot
import it. Everything here has to be exercisable with no .env, no credentials
and no network -- which is why it takes an already-fetched payload rather than
fetching one.

NO IDENTITY. The grain is (season_year) and nothing else. MLB's calendar is a
fact about baseball, not about a league, an owner or a team.
"""

from datetime import date, timedelta

# The public, key-free MLB Stats API. Same host the project's baseball layer
# already sources player production from (extract/mlb_stats.py), so this adds
# no new dependency, no credential and no vendor.
SEASON_API = "https://statsapi.mlb.com/api/v1/seasons"

# The field that IS the anchor, named as a constant because the stored
# snapshot records which field it used -- a later reader must be able to tell
# `regularSeasonStartDate` from `seasonStartDate` (which is spring training,
# and is 34 days earlier).
ANCHOR_FIELD = "regularSeasonStartDate"

# What the narrow snapshot keeps. Two measured dates and nothing else: the
# endpoint also serves All-Star, postseason and offseason boundaries, and
# storing fields no model reads invites the next reader to build on the parts
# nobody verified.
CALENDAR_FIELDS = (ANCHOR_FIELD, "regularSeasonEndDate")


class SeasonCalendarError(RuntimeError):
    """The payload could not be read as a season calendar."""


def season_calendar_url(season_year):
    """The one request, spelled once so the stored provenance matches it."""
    return f"{SEASON_API}?sportId=1&season={season_year}"


def _iso_date(value, field, season_year):
    """One ISO date field, checked rather than coerced.

    The year check is not decoration. A wrong-season anchor is the dangerous
    failure here: every date in the season would be internally consistent and
    uniformly wrong, and nothing on the row would contradict it -- the same
    shape of trap `matchup_schedule_snapshot` guards with `seasonId`.
    """
    if not isinstance(value, str):
        raise SeasonCalendarError(
            f"{field} is {value!r} ({type(value).__name__}), not an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise SeasonCalendarError(
            f"{field} is {value!r}, which is not an ISO (YYYY-MM-DD) date"
        ) from None
    if parsed.year != season_year:
        raise SeasonCalendarError(
            f"{field} is {value}, which falls in {parsed.year} rather than "
            f"the season being captured ({season_year}); an anchor from the "
            f"wrong season would date every scoring period consistently and "
            f"wrongly")
    return parsed


def season_calendar_snapshot(payload, *, season_year):
    """The narrow, provenance-bearing object RAW stores.

    REFUSES RATHER THAN STORING AN ANCHOR THAT CANNOT BE TRUSTED, on the same
    reasoning as the mMatchupScore snapshot: a bad calendar does not produce
    missing dates, it produces confident wrong ones.

    `seasonId` arrives as a STRING from this endpoint ('2026', not 2026) --
    measured, not assumed -- so it is compared as text against the season
    being captured rather than as an integer.

    The stored object carries `source` and `anchor_field` so the row explains
    itself: which URL answered, and which of the several date fields on that
    response was taken as scoring period 1.
    """
    if not isinstance(payload, dict):
        raise SeasonCalendarError(
            f"payload is {type(payload).__name__}, not an MLB seasons document")

    seasons = payload.get("seasons")
    if not isinstance(seasons, list) or not seasons:
        raise SeasonCalendarError(
            "payload carries no non-empty seasons array; request "
            f"{season_calendar_url(season_year)}")
    entry = seasons[0]
    if not isinstance(entry, dict):
        raise SeasonCalendarError(
            f"seasons[0] is {type(entry).__name__}, not an object")

    declared = entry.get("seasonId")
    if str(declared) != str(season_year):
        raise SeasonCalendarError(
            f"MLB says this calendar is season {declared!r} but it is being "
            f"captured as {season_year}")

    snapshot = {"seasonId": season_year,
                "source": season_calendar_url(season_year),
                "anchor_field": ANCHOR_FIELD}
    for field in CALENDAR_FIELDS:
        # Validated on the way in, stored as the ISO text MLB sent. Text
        # rather than a date object because this lands in a JSON column and a
        # round-trip through json.dumps has to be lossless.
        snapshot[field] = _iso_date(entry.get(field), field, season_year).isoformat()
    return snapshot


def season_opener(snapshot):
    """The date of scoring period 1, out of a stored snapshot."""
    if not isinstance(snapshot, dict):
        raise SeasonCalendarError(
            f"snapshot is {type(snapshot).__name__}, not a season calendar")
    declared = snapshot.get("seasonId")
    if not isinstance(declared, int) or isinstance(declared, bool):
        raise SeasonCalendarError(
            f"snapshot carries no integer seasonId ({declared!r})")
    return _iso_date(snapshot.get(ANCHOR_FIELD), ANCHOR_FIELD, declared)


# ---------------------------------------------------------------------------
# The arithmetic
# ---------------------------------------------------------------------------
def scoring_period_date(opener, scoring_period):
    """The calendar day ESPN scoring period `scoring_period` fell on.

    Period 1 IS the opener, so the offset is N-1 rather than N. Off-by-one
    here would shift a whole season, which is why it is one function with one
    test rather than an expression repeated at three call sites.
    """
    if isinstance(scoring_period, bool) or not isinstance(scoring_period, int):
        raise SeasonCalendarError(
            f"scoring period is {scoring_period!r} "
            f"({type(scoring_period).__name__}), not an integer")
    if scoring_period < 1:
        raise SeasonCalendarError(
            f"scoring period {scoring_period} is not a 1-based id")
    return opener + timedelta(days=scoring_period - 1)


def matchup_period_dates(opener, scoring_periods):
    """(start_date, end_date) for one matchup period's membership.

    THE BOUNDS, NOT THE COUNT. Taking min and max rather than
    `opener + first .. + len(members)` is what makes a non-contiguous
    membership fail visibly instead of silently sliding: the dates describe
    the ids that are actually there.
    """
    members = sorted(scoring_periods)
    if not members:
        raise SeasonCalendarError(
            "a matchup period with no scoring periods has no dates")
    return (scoring_period_date(opener, members[0]),
            scoring_period_date(opener, members[-1]))
