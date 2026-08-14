"""The canonical league-format contract (MLB-243).

THE PRODUCT RULE THIS FILE ENFORCES:

    FORMAT decides what the workbook SHOWS.
    PLATFORM decides which data is AVAILABLE to build it.

Those are independent axes and the output layer had them fused. The
renderer chose the head-to-head almanac unless `mart_period_standings`
had rows -- a CBS-shaped feed (the F7 seam) standing in for "is this a
points league". That test is right for CBS and wrong for everybody else:
an ESPN season-long points league (`currentLeagueType = 5`) delivers no
period standings at all, so it read as H2H and was handed a matchup
almanac over a league that has never played a matchup. The first real
stranger rehearsal produced exactly that workbook.

The warehouse already answers the question properly. `dim_league_format`
classifies one row per league_key from what the data DOES -- delivered
period standings, matchup pairings, a season-points schedule -- and never
from the platform's name. This module is the single Python seam that
reads that answer, so no second inference can drift from it.

WHY THIS FAILS CLOSED. `unknown` is a real value in that dimension: a
league with neither period standings nor matchup pairings genuinely has
not told us what it is yet, and an empty install produces it. The old
code's `else` branch made unknown mean H2H, which is how a wrong answer
became silent. Here it raises. A stranger who sees "I cannot tell what
format this league is, and here is what to run" has a bug report; a
stranger who silently receives the wrong workbook does not know to file
one.

The same reasoning covers a missing dimension entirely: a warehouse built
before this model existed raises the same error rather than guessing.
"""

import re

import db


POINTS = 'points'
H2H = 'h2h'
UNKNOWN = 'unknown'

# The formats this codebase can actually render. `unknown` is deliberately
# absent: it is a legitimate warehouse answer and an illegitimate render
# instruction.
RENDERABLE = frozenset({POINTS, H2H})


class LeagueFormatError(RuntimeError):
    """The league's format could not be established, so no workbook shape
    can be chosen. Always carries the remediation, because every way of
    reaching it is a state the user can fix."""


_MISSING_RELATION = re.compile(
    r"(does not exist|not found|unknown table|invalid identifier"
    r"|object .* does not exist)",
    re.IGNORECASE,
)

_REBUILD_HINT = (
    "Run a dbt build so the league-format dimension exists, then re-run "
    "this command."
)


def resolve(league_key=None):
    """Return the canonical format for a league: `points` or `h2h`.

    Reads `dim_league_format`, the one place the warehouse decides this.
    Raises LeagueFormatError -- never guesses, never defaults -- when the
    league is absent from the dimension, classified `unknown`, or the
    dimension itself has not been built.
    """
    key = league_key or db.league_key()
    try:
        rows = db.query_snowflake(
            "SELECT league_format FROM dim_league_format "
            f"WHERE league_key = '{_safe_key(key)}'"
        )
    except Exception as exc:                       # noqa: BLE001 -- rethrown
        if _MISSING_RELATION.search(str(exc)):
            raise LeagueFormatError(
                f"dim_league_format is not present in this warehouse, so the "
                f"format of league '{key}' cannot be established. "
                f"{_REBUILD_HINT}"
            ) from exc
        raise

    if not rows:
        raise LeagueFormatError(
            f"League '{key}' has no row in dim_league_format, so its format "
            f"is unknown. That dimension carries a row once a league "
            f"delivers period standings, matchup pairings, or a "
            f"season-points schedule -- an extraction that landed none of "
            f"those has nothing to classify. Check that the extract ran for "
            f"this league, then rebuild. Refusing to guess a workbook shape."
        )

    fmt = (rows[0].get('league_format') or '').strip().lower()
    if fmt in RENDERABLE:
        return fmt

    raise LeagueFormatError(
        f"League '{key}' is classified '{fmt or UNKNOWN}' in "
        f"dim_league_format, which names no workbook shape. A league reads "
        f"'{UNKNOWN}' when it delivers neither period standings, nor matchup "
        f"pairings, nor a season-points schedule. Refusing to fall back to "
        f"the head-to-head almanac: the wrong workbook rendered silently is "
        f"worse than this message. {_REBUILD_HINT}"
    )


def is_points(league_key=None):
    """True for a points-format league. Propagates LeagueFormatError --
    callers must not be able to read a failure as `False`, which is the
    silent-H2H default this module exists to remove."""
    return resolve(league_key) == POINTS


def describe(league_key=None):
    """The full dimension row, for diagnostics that want the evidence
    columns alongside the verdict. Same failure semantics as resolve()."""
    key = league_key or db.league_key()
    resolve(key)                                   # raise first, report second
    rows = db.query_snowflake(
        "SELECT league_key, has_period_standings, has_matchups, "
        "has_season_points_schedule, league_format FROM dim_league_format "
        f"WHERE league_key = '{_safe_key(key)}'"
    )
    return rows[0]


def _safe_key(key):
    """League keys are spliced as SQL literals throughout the output layer
    (db.league_predicate does the same); constrain the alphabet rather
    than trusting an arbitrary caller-supplied string."""
    if not re.fullmatch(r"[a-z0-9_-]+", str(key or '')):
        raise ValueError(
            f"league_key {key!r} contains characters outside [a-z0-9_-]"
        )
    return key
