"""Matchup-period membership, read out of ESPN's own payload (MLB-235).

WHAT THIS EXISTS TO BREAK. The stranger path asks a new ESPN user to
hand-maintain `dbt_league/league_config/matchup_schedule.csv` before the
extract can run at all, and the dependency really is circular:
`load_schedule()` reads that CSV, `get_scoring_periods()` turns its dates into
scoring-period ids, and only THEN does the extract request box scores. So the
`matchup_period` stamped onto every RAW.BOX_SCORES row ORIGINATED IN THE SEED,
and reading the mapping back out of the warehouse proves nothing about the
platform. (MLB-227 briefly claimed BOX_SCORES was evidence; it was withdrawn
for exactly this reason.)

The non-circular input is the `mMatchupScore` view of the league document:

    GET .../seasons/{year}/segments/0/leagues/{league_id}?view=mMatchupScore

    {"status":   {"currentMatchupPeriod": 18, ...},
     "schedule": [{"matchupPeriodId": 1,
                   "home": {"pointsByScoringPeriod": {"1": 0.0, "2": 34.5}},
                   "away": {"pointsByScoringPeriod": {"1": 12.0, "2": 8.25}}},
                  ...]}

THE KEYS of `pointsByScoringPeriod` are the scoring periods belonging to that
matchup period. Both `matchupPeriodId` and those keys are ESPN's own. Nothing
in this module reads a seed, a warehouse row, a calendar date, or any value
this project stamped -- that is the whole point of it, and the reason it takes
a payload rather than fetching one.

WHAT DOES NOT CARRY MEMBERSHIP, recorded here because the shape invites the
assumption and has now cost two rounds of confusion: `scheduleSettings.
matchupPeriods` is a degenerate identity map ({'1': [1], '2': [2], ...}) in
ESPN's OWN payload -- verified against RAW.SCHEDULE_SETTINGS, which is stored
verbatim from `response.json()` with no espn-api in the path. The wrapper is
not flattening anything. See the note in stg_schedule_settings.sql.

WHICH SEASONS TO ASK FOR -- Kyle's ruling, 2026-08-11. The season list comes
from the league registry's explicit `first_season` through `final_season`
(config/leagues.yml), bounded above by the season actually being requested;
when `final_season` is null the league is ongoing and the requested (or
current) season is the upper bound. `seasons_to_request` below is that rule.

ESPN's own `status.previousSeasons` is NOT used. It would be the more
platform-owned answer and it is unverified in this repo -- putting an
unmeasured field on the critical path is how a capture acquires a dependency
nobody has ever seen fail. The registry is explicit, already loaded by every
edge script, and is not the matchup_schedule seed, so it breaks the
circularity just as cleanly.

WHAT THIS DOES NOT SPELL OUT: ISO dates. Membership is scoring-period IDS,
and ESPN serves no start/end calendar in this view. That is not the same as
the calendar being unknowable -- an ESPN scoring period is one day, so
anchoring period 1 to the season's first scoring date turns membership into a
calendar by ordinary day arithmetic. Automating that anchor is rung 4B-2's
job; this module retires the LENGTH and the RECENCY questions, and leaves the
opener to it.

WHY IT IS A SEPARATE MODULE and not a function in extract.py: extract.py reads
LEAGUE_ID at import and raises without it, so a fresh clone cannot import it.
Everything here has to be exercisable with no .env, no credentials and no
network -- same hard constraint, and same reason, as extract/raw_sink.py.

IDENTITY IS NOT A COLUMN HERE. The grain is
(league_key, season_year, matchup_period, scoring_period) and no owner,
member, franchise or team id is read, returned, or named in an error message.
The payload carries `teamId` on every side; this module walks past it.
"""

from collections import Counter
from dataclasses import dataclass


class MatchupMembershipError(RuntimeError):
    """The payload could not be read as matchup-period membership.

    Raised by `parse_matchup_membership`, which is deliberately strict.
    `derive_matchup_periods` catches it and reports `malformed` instead --
    see the two-layer note on that function.
    """


# -- derivation statuses ----------------------------------------------------
# A derived value and an absent one must never be the same answer, which is
# why "we could not tell" has three distinct spellings rather than a None the
# caller has to interpret. Everything except DERIVED means: do not classify
# anything from this report.
DERIVED = "derived"
INSUFFICIENT_EVIDENCE = "insufficient_evidence"
AMBIGUOUS_STANDARD_LENGTH = "ambiguous_standard_length"
UNAVAILABLE = "unavailable"
MALFORMED = "malformed"

DERIVATION_STATUSES = (
    DERIVED,
    INSUFFICIENT_EVIDENCE,
    AMBIGUOUS_STANDARD_LENGTH,
    UNAVAILABLE,
    MALFORMED,
)

# How many closed periods must be in evidence before a modal length is called
# "the league's standard".
#
# The floor exists because of a specific failure: a season observed early
# enough carries ONE closed period, and that period is the long opening week
# in both leagues on file. A mode over a single observation would bless the
# anomaly as the norm and then flag every ordinary week that followed as
# abnormal -- the exact inversion this ticket exists to prevent. Three is a
# judgement call and deliberately conservative; it is one line to move, and
# moving it is a decision, not a tuning knob.
MIN_CLOSED_PERIODS_FOR_STANDARD = 3

# The longest a scoring-period key may be before it is refused as not an id.
#
# Generous by three orders of magnitude -- a scoring period is a day index
# inside one season, so real ids run to the low hundreds. The bound is not
# about plausibility, it is about keeping `derive_matchup_periods`' promise
# never to raise: CPython 3.11+ refuses `int()` on digit strings past 4,300
# characters with a bare ValueError, and a promise that holds for every
# payload except an absurd one is not a promise.
MAX_SCORING_PERIOD_KEY_LENGTH = 6


# ---------------------------------------------------------------------------
# What comes back
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MembershipRow:
    """One (matchup period, scoring period) membership fact.

    league_key and season_year are LOAD METADATA supplied by the caller --
    the registry key from config/leagues.yml and the season the request was
    aimed at. Everything else on this row is ESPN's.
    """

    league_key: str
    season_year: int
    matchup_period: int
    scoring_period: int


@dataclass(frozen=True)
class PeriodMembership:
    """One closed matchup period and the scoring periods inside it."""

    matchup_period: int
    scoring_periods: tuple

    @property
    def scoring_period_count(self):
        return len(self.scoring_periods)


@dataclass(frozen=True)
class MembershipParse:
    """Everything the payload said, before any statistic is computed."""

    league_key: str
    season_year: int
    current_matchup_period: int
    closed: tuple      # PeriodMembership, ascending by matchup_period
    excluded: tuple    # matchup period ids not eligible
    # The period EQUAL to current_matchup_period, when the status block proved
    # the season over and this period proved it was the one that ended it.
    # None on every live season, and on a completed one whose closing period
    # could not back the claim up.
    promoted_period: object = None
    # `status.latestScoringPeriod`, or None when ESPN did not send a usable
    # one. The recency policy's only clock (see `classify_recency`), carried
    # here rather than re-read from the payload so selection and the settled-
    # history guard cannot end up looking at two different documents.
    latest_scoring_period: object = None

    @property
    def closed_periods(self):
        """Just the eligible matchup-period ids, ascending.

        What `--all` extracts, and the set every explicit request is checked
        against. `closed` is already sorted, so this is a projection.
        """
        return tuple(period.matchup_period for period in self.closed)

    def scoring_periods_for(self, matchup_period):
        """This period's scoring-period ids, or None if it is not eligible.

        The replacement for `get_scoring_periods()`, which turned the seed's
        start/end dates into a range. None rather than () for an ineligible
        period, because "ESPN did not serve this as closed" and "this period
        contains no days" must not be the same answer to a caller about to
        decide what to pull.
        """
        for period in self.closed:
            if period.matchup_period == matchup_period:
                return period.scoring_periods
        return None

    @property
    def rows(self):
        """Membership rows, ordered by (matchup_period, scoring_period).

        Deterministic ordering is not cosmetic: these rows are destined for a
        RAW/staging comparison substrate, and a set-ordered walk would make a
        re-parse of the same payload produce a different file.
        """
        return tuple(
            MembershipRow(self.league_key, self.season_year,
                          period.matchup_period, scoring_period)
            for period in self.closed
            for scoring_period in period.scoring_periods
        )


@dataclass(frozen=True)
class DerivedPeriod:
    """A closed period's length and, when derivable, its abnormality.

    `is_abnormal_derived` is None -- never False -- when the standard length
    could not be established. "Not abnormal" and "not known" are different
    facts, and collapsing them is how a fail-closed design quietly becomes a
    fail-open one.
    """

    matchup_period: int
    scoring_period_count: int
    is_abnormal_derived: object = None


@dataclass(frozen=True)
class DerivationReport:
    """The stable seam. What was found, or why it could not be.

    Carries its own `reason` string so a caller never has to reconstruct the
    refusal from the status: MLB-207's job is to SAY what happened, and a
    status alone does not name the period that was missing.
    """

    status: str
    reason: str
    league_key: str
    season_year: int
    current_matchup_period: object     # int, or None when unreadable
    standard_period_length: object     # int, or None unless status is DERIVED
    periods: tuple                     # DerivedPeriod, ascending
    rows: tuple                        # MembershipRow, ascending
    excluded_periods: tuple            # matchup period ids not eligible
    # Provenance for the completion exception: the closing period this season
    # was proven to have finished on, or None when the strict policy applied.
    promoted_final_period: object = None

    @property
    def is_derived(self):
        return self.status == DERIVED

    @property
    def abnormal_periods(self):
        """The closed periods whose length differs from the standard.

        Empty unless the standard length was actually derived -- an
        undetermined period is not an abnormal one.
        """
        return tuple(p.matchup_period for p in self.periods
                     if p.is_abnormal_derived is True)


# ---------------------------------------------------------------------------
# What RAW stores
# ---------------------------------------------------------------------------
# The three top-level keys a captured snapshot must carry -- ALL THREE,
# REQUIRED, and never the schedule array alone (Kyle's ruling, 2026-08-11).
# Stored without any one of them the capture is unusable, and nothing about
# the stored row says so:
#
#   status    holds currentMatchupPeriod, which IS the closed-period policy.
#             Without it a stored snapshot cannot tell a settled period from
#             one that was still filling in when it was captured -- and a
#             short in-flight period is indistinguishable from a real
#             abnormality, which is the one mistake this whole ticket exists
#             to avoid making.
#   seasonId  is ESPN's own answer to which season this document describes,
#             and it is the ONLY independent one on the row. RAW's
#             season_year column is stamped by the loader, so a row filed
#             under the wrong season agrees with itself perfectly. Season
#             identity is part of the storage contract, not a nicety: a 2025
#             document written as 2026 would silently supply 2026's periods
#             from the wrong year's schedule, and the derived flags would be
#             confidently wrong rather than absent.
#   schedule  is the membership.
#
# It is an assembly of three verbatim blocks, not a transformation: nothing
# inside any of them is reshaped, renamed, filtered or sorted.
SNAPSHOT_KEYS = ("seasonId", "status", "schedule")


def matchup_schedule_snapshot(payload, *, season_year):
    """The narrow object RAW stores, out of a full mMatchupScore response.

    Narrow rather than whole because the rest of the league document is
    already captured elsewhere or is not ours to keep -- and because a RAW
    row that is mostly unrelated payload invites the next reader to build on
    the parts nobody verified. Everything the derivation needs is here, and
    the module docstring's traced request is what produces it.

    REFUSES RATHER THAN STORING SOMETHING THAT CANNOT BE DERIVED FROM. All
    three keys are required, their basic shapes are checked, and ESPN's
    `seasonId` must equal `season_year` -- the value the loader is about to
    stamp on the row. Deferring any of these to read time means discovering
    them possibly a season later, with the live payload long gone and no way
    to re-fetch it; ESPN does not re-serve what it has moved on from. The
    checks here are structural only. Whether the CONTENTS make sense is the
    parser's job, and deliberately not duplicated: RAW captures, staging
    interprets.
    """
    if not isinstance(payload, dict):
        raise MatchupMembershipError(
            f"payload is {type(payload).__name__}, not an ESPN league document")

    missing = [key for key in SNAPSHOT_KEYS if payload.get(key) is None]
    if missing:
        raise MatchupMembershipError(
            f"payload carries no {' and no '.join(missing)}; all of "
            f"{', '.join(SNAPSHOT_KEYS)} are required (request the "
            f"mMatchupScore view)")

    declared = payload["seasonId"]
    if isinstance(declared, bool) or not isinstance(declared, int):
        raise MatchupMembershipError(
            f"seasonId is {declared!r} ({type(declared).__name__}), not a "
            f"season year")
    if declared != season_year:
        raise MatchupMembershipError(
            f"ESPN says this document is season {declared} but it is being "
            f"captured as {season_year}; the row's season_year is stamped by "
            f"the loader, so storing it would file the wrong season's "
            f"membership under {season_year} with nothing to contradict it")

    if not isinstance(payload["status"], dict):
        raise MatchupMembershipError(
            f"status is {type(payload['status']).__name__}, not an object; "
            f"the closed-period policy reads currentMatchupPeriod out of it")
    if not isinstance(payload["schedule"], list):
        raise MatchupMembershipError(
            f"schedule is {type(payload['schedule']).__name__}, not a list")

    return {key: payload[key] for key in SNAPSHOT_KEYS}


# ---------------------------------------------------------------------------
# Which seasons a backfill asks for
# ---------------------------------------------------------------------------
def seasons_to_request(first_season, final_season, through_season):
    """The seasons to pull membership for, ascending.

    The registry's own bounds (`first_season` / `final_season` from
    config/leagues.yml) intersected with what can actually be asked for:
    `through_season` is the season being requested, or the current one on a
    default run, and nothing past it exists to fetch. A league still running
    carries `final_season: null`, so `through_season` is its upper bound.

    Deliberately NOT `status.previousSeasons`: see the ruling in the module
    docstring. The registry is explicit and already loaded by every edge
    script, and it is not the matchup_schedule seed -- so the season list is
    non-circular for the same reason the membership is.

    Returns () when the range is empty, which is a real answer (a league
    whose first season is after the one being asked for has no membership to
    pull) and one the caller is expected to report rather than treat as
    success. It is not an error here because there is nothing malformed
    about it.
    """
    if first_season is None:
        raise ValueError(
            "the league registry entry declares no first_season, so there is "
            "no non-circular lower bound for a membership backfill")

    last = through_season if final_season is None else min(final_season,
                                                           through_season)
    return tuple(range(first_season, last + 1))


# ---------------------------------------------------------------------------
# Reading the payload
# ---------------------------------------------------------------------------
def _require_positive_int(value, what):
    """ESPN's ids, checked rather than coerced.

    `bool` is excluded explicitly because it is an `int` subclass in Python
    and `True` would otherwise sail through as the id 1.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise MatchupMembershipError(
            f"{what} is {value!r} ({type(value).__name__}), not an integer")
    if value < 1:
        raise MatchupMembershipError(f"{what} is {value}, which is not a "
                                     f"1-based period id")
    return value


def _optional_scoring_period(value):
    """A completion field, or None -- and NEVER an exception.

    Absent, non-integer, boolean or non-positive all return None, which the
    caller reads as "completion is not proven" and falls back to the strict
    policy. That is deliberate and it is the difference between this and
    `_require_positive_int`: a malformed COMPLETION field must not condemn a
    season whose earlier periods are independently provable. Refusing to
    promote costs one period; refusing the season discards twenty-five.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 1 else None


def _scoring_period_key(key, matchup_period):
    """One `pointsByScoringPeriod` key as a scoring-period id.

    JSON object keys arrive as strings, so this is where "1" becomes 1 -- and
    where "1.0", " 1", "" and "opening-day" are refused instead of being
    coerced into something plausible. A membership set is the thing the whole
    derivation stands on; a silently-dropped or silently-rounded key would
    shorten a period by a day and read as a real abnormality.

    ASCII DIGITS ONLY, and `str.isdigit()` alone does not mean that -- it is
    the obvious test and it is wrong in both directions at once. It is True
    for superscripts like '²', which `int()` then refuses with a bare
    ValueError; and True for full-width digits like '１', which `int()`
    accepts SILENTLY as 1. The first breaks `derive_matchup_periods`' promise
    never to raise, the second is worse -- a key nothing on the wire would
    ever send, admitted as a real scoring period. The length bound closes the
    last raising path (see MAX_SCORING_PERIOD_KEY_LENGTH).
    """
    if isinstance(key, bool):
        raise MatchupMembershipError(
            f"matchup period {matchup_period} has a boolean scoring-period key")
    if isinstance(key, int):
        return _require_positive_int(
            key, f"scoring-period key in matchup period {matchup_period}")
    if isinstance(key, str) and key.isascii() and key.isdigit():
        if len(key) > MAX_SCORING_PERIOD_KEY_LENGTH:
            raise MatchupMembershipError(
                f"matchup period {matchup_period} has a {len(key)}-digit "
                f"scoring-period key; ids are day indexes inside one season, "
                f"so anything past {MAX_SCORING_PERIOD_KEY_LENGTH} digits is "
                f"not one")
        return _require_positive_int(
            int(key), f"scoring-period key in matchup period {matchup_period}")
    raise MatchupMembershipError(
        f"matchup period {matchup_period} has a non-integer scoring-period "
        f"key {key!r}; membership keys must be 1-based scoring-period ids")


def _side_membership(side, matchup_period, position, index):
    """The scoring-period key set one participating side exposes.

    Returns None when the side is not participating at all -- an absent
    `home`/`away` key, or a null one, is a BYE, and this league is not the
    only shape the extract has to survive (see tests/test_extract_bye_week.py).
    A side that IS present but carries no usable `pointsByScoringPeriod` is
    the partial case and refused: that is a period ESPN has not filled in,
    and inferring its length from the other side would invent evidence.

    `position` and `index` locate the problem without naming a team -- the
    identity rule at the top of this module applies to error strings too.
    """
    if side is None:
        return None
    if not isinstance(side, dict):
        raise MatchupMembershipError(
            f"matchup period {matchup_period}, matchup #{index}: the "
            f"{position} side is {type(side).__name__}, not an object")

    points = side.get("pointsByScoringPeriod")
    if points is None:
        raise MatchupMembershipError(
            f"matchup period {matchup_period}, matchup #{index}: the "
            f"{position} side carries no pointsByScoringPeriod, so the "
            f"period's membership is unknown rather than empty")
    if not isinstance(points, dict):
        raise MatchupMembershipError(
            f"matchup period {matchup_period}, matchup #{index}: the "
            f"{position} side's pointsByScoringPeriod is "
            f"{type(points).__name__}, not an object")
    if not points:
        raise MatchupMembershipError(
            f"matchup period {matchup_period}, matchup #{index}: the "
            f"{position} side's pointsByScoringPeriod is empty")

    # KEYS ONLY. Values are never read, so a scoring period on which a team
    # scored exactly zero stays in the membership -- 195 of 3,120 values were
    # 0.0 in the 2025+2026 evidence, and dropping them would shorten real
    # weeks into fake abnormalities.
    return frozenset(_scoring_period_key(k, matchup_period) for k in points)


def parse_matchup_membership(payload, *, league_key, season_year):
    """Platform-owned membership for every CLOSED matchup period.

    Strict on purpose: every way this can be wrong raises
    `MatchupMembershipError` rather than returning a thinner answer. Callers
    that need a verdict instead of an exception use `derive_matchup_periods`.

    THE CLOSED-PERIOD POLICY, and it is structural rather than calendar-based:
    a matchup period is eligible only when its id is strictly less than
    `status.currentMatchupPeriod`. Membership is retrospective -- the period
    in flight is still filling in, and reading it gives a short count that
    looks exactly like a real abnormality. (Measured: 2026's period 19 read 2
    scoring periods against the seed's 7, because it was the current period.
    Restricted to closed periods, membership matched the hand-maintained seed
    on every period of both seasons on file, 43 periods, zero mismatches.)

    THE COMPLETION EXCEPTION, added once it could be measured rather than
    guessed. A finished season pins `currentMatchupPeriod` ON its final period
    rather than past it -- 2025 came back with 26 of 26 -- so the strict rule
    alone discards the last completed week of every finished season. A period
    equal to `currentMatchupPeriod` is therefore promoted, but only on proof
    that the season is over and that this period is the one that ended it:

      * `status.latestScoringPeriod` > `status.finalScoringPeriod`, strictly.
        Equal does NOT prove completion -- the final scoring day can be the
        day in progress. 2025 read 196 > 195; 2026 read 140 < 187.
      * the period is itself well-formed, by the same side-agreement rules as
        any closed period;
      * and its agreed membership ENDS at `finalScoringPeriod`. A period
        claiming to close a season it does not reach the end of is not the
        closing period, whatever the status block says.

    Any of those failing falls back to the strict policy -- the period is
    excluded and the season keeps every earlier period, because those were
    never in question. Completion is never inferred and never invented.

    WHAT IS DELIBERATELY NOT CONSULTED: `isActive`, `isExpired`, `isViewable`,
    calendar dates, the seed, and the wall clock. `isActive` is the tempting
    one and it is measurably wrong for this purpose -- it was True for BOTH
    the finished 2025 season and the live 2026 one, so a policy built on it
    would have promoted the in-flight period every week of the season.

    Periods after the current one are skipped WITHOUT shape validation: they
    are structurally out of scope, and refusing the whole season because an
    unplayed period is shaped like an unplayed period would be a bug.
    """
    if not isinstance(payload, dict):
        raise MatchupMembershipError(
            f"payload is {type(payload).__name__}, not an ESPN league document")

    declared_season = payload.get("seasonId")
    if declared_season is not None and declared_season != season_year:
        # Checked only when ESPN sends it. A document for the wrong season
        # would otherwise be stamped with the season the caller asked for and
        # land, silently, as that season's membership.
        raise MatchupMembershipError(
            f"payload is for season {declared_season!r} but was read as "
            f"{season_year}")

    status = payload.get("status")
    if not isinstance(status, dict):
        raise MatchupMembershipError(
            "payload carries no status block, so the current matchup period "
            "is unknown and no period can be shown to be closed")
    current = _require_positive_int(status.get("currentMatchupPeriod"),
                                    "status.currentMatchupPeriod")

    # Completion evidence. Both must be present, integral and STRICTLY
    # ordered; anything else leaves season_complete False and the strict
    # policy in force.
    latest_scoring_period = _optional_scoring_period(
        status.get("latestScoringPeriod"))
    final_scoring_period = _optional_scoring_period(
        status.get("finalScoringPeriod"))
    season_complete = (latest_scoring_period is not None
                       and final_scoring_period is not None
                       and latest_scoring_period > final_scoring_period)

    schedule = payload.get("schedule")
    if schedule is None:
        raise MatchupMembershipError(
            "payload carries no schedule; request the mMatchupScore view")
    if not isinstance(schedule, list):
        raise MatchupMembershipError(
            f"payload.schedule is {type(schedule).__name__}, not a list")

    # {matchup_period: [key set per participating side]}
    observed = {}
    excluded = set()
    # Entries for the period EQUAL to current, held aside rather than parsed
    # inline: a shape problem there must demote the promotion, not condemn a
    # season whose earlier periods are independently provable.
    candidate_entries = []
    for index, entry in enumerate(schedule, start=1):
        if not isinstance(entry, dict):
            raise MatchupMembershipError(
                f"schedule entry #{index} is {type(entry).__name__}, "
                f"not an object")
        matchup_period = _require_positive_int(
            entry.get("matchupPeriodId"),
            f"schedule entry #{index}: matchupPeriodId")

        if matchup_period > current:
            excluded.add(matchup_period)
            continue

        if matchup_period == current:
            if season_complete:
                candidate_entries.append((entry, index))
            else:
                excluded.add(matchup_period)
            continue

        sides = observed.setdefault(matchup_period, [])
        for position in ("home", "away"):
            membership = _side_membership(
                entry.get(position), matchup_period, position, index)
            if membership is not None:
                sides.append(membership)

    closed = [
        _agree(matchup_period, observed[matchup_period])
        for matchup_period in sorted(observed)
    ]

    promoted = None
    if candidate_entries:
        promoted = _promote_final_period(
            candidate_entries, current, final_scoring_period)
        if promoted is None:
            excluded.add(current)
        else:
            # Appends last, and correctly: every other closed period is
            # strictly below it.
            closed.append(promoted)

    closed = tuple(closed)
    _require_no_gaps(closed, current, promoted is not None)

    return MembershipParse(
        league_key=league_key,
        season_year=season_year,
        current_matchup_period=current,
        closed=closed,
        excluded=tuple(sorted(excluded)),
        promoted_period=promoted.matchup_period if promoted else None,
        latest_scoring_period=latest_scoring_period,
    )


def _promote_final_period(entries, matchup_period, final_scoring_period):
    """The season's closing period, or None if it cannot be shown to be one.

    Returns rather than raises for every failure, which is the whole reason
    this is a function: the caller has already proven the season complete from
    the status block, and a period that does not back that claim up demotes
    itself to excluded. The alternative -- letting the shape error escape --
    would turn "we could not promote the last week" into "this season is
    malformed", discarding twenty-five periods to avoid over-claiming one.
    """
    sides = []
    try:
        for entry, index in entries:
            for position in ("home", "away"):
                membership = _side_membership(
                    entry.get(position), matchup_period, position, index)
                if membership is not None:
                    sides.append(membership)
        agreed = _agree(matchup_period, sides)
    except MatchupMembershipError:
        return None

    # The status block says the season is over; this proves THIS period is
    # where it ended. A period that stops short is not the closing one, and
    # promoting it would publish a short week as a complete one.
    if agreed.scoring_periods[-1] != final_scoring_period:
        return None
    return agreed


def _agree(matchup_period, side_memberships):
    """One period's membership, only if every participating side agrees.

    The adversarial check this encodes: are these keys "the days THIS TEAM
    scored" rather than "the days in the period"? If they were, two teams in
    the same period could differ. Across every period of both seasons on file
    they never do -- so a disagreement here is not a shape to accommodate, it
    is the finding collapsing, and it must stop the derivation rather than
    pick a side. Choosing the longer set would manufacture a normal week;
    choosing the shorter would manufacture an abnormal one.
    """
    if not side_memberships:
        raise MatchupMembershipError(
            f"matchup period {matchup_period} is closed but no side in it "
            f"carries membership")

    distinct = set(side_memberships)
    if len(distinct) > 1:
        rendered = " vs ".join(
            str(sorted(s)) for s in sorted(distinct, key=sorted))
        raise MatchupMembershipError(
            f"matchup period {matchup_period}: participating sides disagree "
            f"about which scoring periods it contains ({rendered}). "
            f"pointsByScoringPeriod is only usable as membership while every "
            f"side in a period exposes the same keys.")

    return PeriodMembership(matchup_period,
                            tuple(sorted(distinct.pop())))


def _require_no_gaps(closed, current, promoted=False):
    """Closed periods must be the unbroken run 1..current-1, or 1..current
    when the season's closing period was promoted.

    A hole means ESPN served a schedule this module does not understand, and
    a mode computed over a gapped set is a statistic about the gap. Refusing
    is the fail-closed answer; the alternative silently narrows the evidence
    and still returns a confident number.

    COSTS WHAT THE PAYLOAD CONTAINS, NOT WHAT IT CLAIMS. `current` is an
    attacker-reachable integer -- it is read straight out of
    `status.currentMatchupPeriod` -- so materialising `range(1, current)` to
    compare against made a six-byte edit to a JSON document into an
    arbitrarily large allocation. A malformed payload has to come back
    `malformed`; exhausting memory on the way to that verdict is not a
    slower version of the same answer. The walk below is O(observed periods)
    and allocates nothing proportional to `current`.

    `closed` arrives sorted ascending with distinct ids (it is built from
    sorted dict keys), so position i holding period i+1 for every entry, plus
    a length of exactly current-1, is the whole contiguity proof.
    """
    expected = current if promoted else current - 1
    for index, period in enumerate(closed, start=1):
        if period.matchup_period != index:
            raise MatchupMembershipError(
                f"matchup period {index} is eligible against the current "
                f"period ({current}) but carries no membership in the "
                f"payload; {len(closed)} eligible period(s) observed where "
                f"{expected} were expected")

    if len(closed) != expected:
        raise MatchupMembershipError(
            f"matchup period {len(closed) + 1} is eligible against the "
            f"current period ({current}) but carries no membership in the "
            f"payload; {len(closed)} eligible period(s) observed where "
            f"{expected} were expected")


# ---------------------------------------------------------------------------
# How recent a closed period is, without a calendar
# ---------------------------------------------------------------------------
# The three answers, and the third is not a decoration. A period the evidence
# cannot place is NOT the same as one placed outside the window: the weekly
# default must not revisit it, and the settled-history guard must not wave it
# through. Collapsing UNKNOWN into either of the other two makes one of those
# two callers wrong, and it is always the destructive one that loses.
RECENT = "recent"
SETTLED = "settled"
UNKNOWN = "unknown"

RECENCY_VERDICTS = (RECENT, SETTLED, UNKNOWN)


def classify_recency(parse, *, window, is_current_season):
    """{matchup_period: RECENT|SETTLED|UNKNOWN} over this parse's closed set.

    THE ONE DEFINITION OF "RECENT", and it has two consumers that must never
    disagree: the weekly default picks the RECENT periods to re-extract, and
    the MLB-188 settled-history guard refuses a rewrite of any loaded period
    that is NOT recent. Two copies of this arithmetic is precisely the
    drifting-twin shape MLB-175 was bitten by, so there is one.

    IT COUNTS SCORING PERIODS, NOT DAYS ON A WALL CLOCK. The old rule read
    each period's calendar end date out of matchup_schedule.csv and compared
    it to `today - LIVE_CAPTURE_WINDOW_DAYS`; the platform payload serves
    scoring-period ids and no dates. The substitution is exact rather than
    approximate BECAUSE an ESPN scoring period is one day: with `latest` being
    the newest scoring period ESPN has scored, `last_sp >= latest - window` is
    the same set the date comparison produced. Measured on the preserved 2026
    payload -- latest 140, window 21 -- both spellings select periods 16, 17
    and 18 and settle 15, because scoring period 119 IS the cutoff date.

    THE BOUNDARY IS INCLUSIVE, matching the `end_date >= cutoff` it replaces:
    a period whose last scoring period is exactly `window` behind `latest` is
    still recent.

    A SEASON EARLIER THAN THE CURRENT ONE IS ENTIRELY SETTLED, however close
    its final period sits to that season's own `latest`. The window is about
    what kona can still answer for TODAY, and last year's last week is as
    unreachable as its first -- an in-season distance says nothing about it.

    FAILS CLOSED ON MISSING EVIDENCE. No usable `latestScoringPeriod`, or a
    period carrying no scoring periods, returns UNKNOWN rather than a guess.

    `window` is required, deliberately: LIVE_CAPTURE_WINDOW_DAYS lives in
    extract.py and a default here would be the second copy of it.
    """
    latest = _optional_scoring_period(parse.latest_scoring_period)

    verdicts = {}
    for period in parse.closed:
        if not is_current_season:
            verdicts[period.matchup_period] = SETTLED
        elif latest is None or not period.scoring_periods:
            verdicts[period.matchup_period] = UNKNOWN
        elif period.scoring_periods[-1] >= latest - window:
            verdicts[period.matchup_period] = RECENT
        else:
            verdicts[period.matchup_period] = SETTLED
    return verdicts


def recent_periods(parse, *, window, is_current_season):
    """The closed periods the weekly default revisits, ascending.

    RECENT only. An UNKNOWN period is not re-extracted on a routine run: the
    guard would have to be satisfied about it anyway, and quietly extracting
    what could not be dated is how a fail-closed guard becomes decorative.
    """
    verdicts = classify_recency(parse, window=window,
                                is_current_season=is_current_season)
    return tuple(sorted(mp for mp, verdict in verdicts.items()
                        if verdict == RECENT))


# ---------------------------------------------------------------------------
# The statistic
# ---------------------------------------------------------------------------
def derive_period_shape(parse):
    """Standard length and per-period abnormality from a parsed payload.

    THE DEPENDENCY THIS INVERTS. mart_team_season_standings.sql derives the
    league's standard period length as `mode(scoring_days)` over periods
    ALREADY FILTERED to `not is_abnormal` -- so today the manual flag is an
    input to the norm that is supposed to explain it. Taking the mode over
    every eligible closed period instead derives the flag. Same statistic,
    dependency reversed, hand-maintained input removed.

    PLAYOFF PERIODS ARE INCLUDED here, deliberately. This payload carries no
    playoff boundary -- that lives in `scheduleSettings.matchupPeriodCount` --
    and inventing one would put a guess inside the derivation. Threading the
    real boundary through is named remaining work, not a default.

    Fails closed twice over: too few closed periods to have a norm, and a
    modal length that ties. Both return a report with no standard length and
    every period's `is_abnormal_derived` left None.
    """
    periods_seen = tuple(
        DerivedPeriod(p.matchup_period, p.scoring_period_count)
        for p in parse.closed
    )
    common = dict(
        league_key=parse.league_key,
        season_year=parse.season_year,
        current_matchup_period=parse.current_matchup_period,
        rows=parse.rows,
        excluded_periods=parse.excluded,
        promoted_final_period=parse.promoted_period,
    )

    if not parse.closed and not parse.excluded:
        return DerivationReport(
            status=UNAVAILABLE,
            reason="ESPN served an empty schedule for this season, so there "
                   "is no membership to derive from",
            standard_period_length=None, periods=(), **common)

    if len(parse.closed) < MIN_CLOSED_PERIODS_FOR_STANDARD:
        return DerivationReport(
            status=INSUFFICIENT_EVIDENCE,
            reason=f"{len(parse.closed)} closed matchup period(s) in evidence, "
                   f"below the {MIN_CLOSED_PERIODS_FOR_STANDARD} needed before "
                   f"a modal length can be called the league's standard "
                   f"(current period is {parse.current_matchup_period})",
            standard_period_length=None, periods=periods_seen, **common)

    tally = Counter(p.scoring_period_count for p in parse.closed)
    top = max(tally.values())
    modal = sorted(length for length, count in tally.items() if count == top)
    if len(modal) > 1:
        return DerivationReport(
            status=AMBIGUOUS_STANDARD_LENGTH,
            reason=f"lengths {modal} each occur {top} time(s) across "
                   f"{len(parse.closed)} closed periods, so no single length "
                   f"is the league's standard",
            standard_period_length=None, periods=periods_seen, **common)

    standard = modal[0]
    return DerivationReport(
        status=DERIVED,
        reason=f"standard length {standard} from {top} of "
               f"{len(parse.closed)} closed matchup periods",
        standard_period_length=standard,
        periods=tuple(
            DerivedPeriod(p.matchup_period, p.scoring_period_count,
                          p.scoring_period_count != standard)
            for p in parse.closed
        ),
        **common)


def derive_matchup_periods(payload, *, league_key, season_year):
    """What ESPN's payload says about this season's periods -- or why not.

    THE SEAM. Everything above raises; this returns. A validator's job is to
    report, and a validator that dies on the payload it was asked to describe
    has reported nothing -- so the strict parser stays strict and this wraps
    it. `status` and `reason` together always answer "what was found, or why
    the derivation is incomplete" without the caller catching anything.

    THE SCOPE OF "NEVER RAISES", stated exactly because a guarantee this
    broad is worth nothing if it is approximate: for any JSON-decodable
    payload -- any nesting of dict, list, str, int, float, bool and None --
    this returns a report. It holds because every refusal inside the parser
    is a MatchupMembershipError by construction, and the two paths that were
    NOT are closed at their source rather than swallowed here (see
    `_scoring_period_key`). Catching bare Exception instead would make the
    same promise while hiding real defects behind a `malformed` verdict, and
    an honest refusal and a masked crash must not look alike.

    Takes an already-fetched payload rather than fetching one: acquisition
    needs a league id and cookies, and everything here has to stay runnable
    on a machine that has neither.
    """
    try:
        parse = parse_matchup_membership(
            payload, league_key=league_key, season_year=season_year)
    except MatchupMembershipError as exc:
        return DerivationReport(
            status=MALFORMED,
            reason=str(exc),
            league_key=league_key,
            season_year=season_year,
            current_matchup_period=None,
            standard_period_length=None,
            periods=(),
            rows=(),
            excluded_periods=(),
        )
    return derive_period_shape(parse)
