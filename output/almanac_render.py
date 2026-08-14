"""output/almanac_render.py

Tier 2c.2 (v1.1.1): cell-level rendering and display constants for the
league almanac. Pure functions: take rows + specs, return display values
or row dicts. No SQL, no Sheets API.

Imports flow: almanac_data -> almanac_render. (Logic and write build on
top of render.) Render reads small helpers from almanac_data --
_fact_stat_column_name, HITTING_RECORD_LABELS, etc. -- because data is
where the seed-driven stat metadata lives.
"""

import math
import os
import statistics
import urllib.parse

import almanac_data
from almanac_data import (
    _fact_stat_column_name,
    HITTING_RECORD_LABELS,
    slot_label,
)
from formatters import (
    fmt_avg, fmt_ip, fmt_value, fmt_record_value,
    format_top_scorer_stats_line,
    TOP_SCORER_STAT_DISPLAY, _SCORE_STAT_KEYS, _top_n_stats,
)
import records
import stat_catalog


HOME_TAB = 'Home'


RECORDS_TAB = 'Records'


TEAM_WEEKS_TAB = 'Matchup History'


DRAFT_TAB = 'Draft Recap'


ADVANCED_STANDINGS_TAB = 'Advanced Standings'


TRADES_TAB = 'Trades'


TRADES_BLOCK_LABEL = 'Trading Block'


TRADE_RECORD_LABEL = 'Trade Record'


# ---------------------------------------------------------------------
# The house "explainer" text style (MLB-170).
#
# Every explainer / caption / footnote-class surface on BOTH books takes
# its text style from here: acquisition lens captions, banner scope
# captions, section notes, provenance notes, glossary footnote text. One
# decision in one place, so "wrong font on an explainer" stops being a
# thing that can be filed.
#
# Kyle 07-30, latest-wins: the token is size 9. That supersedes MLB-142,
# which had standardized banner scope captions at 10 -- those normalize
# DOWN to 9 here, and the records tab's 9 was right all along.
#
# DEVIATIONS: a surface that genuinely must differ passes an override AND
# carries a comment at the call site saying why. An undocumented
# deviation is a bug, not a style choice.
#
# On bold=False: both writers mask formats as
# `userEnteredFormat(textFormat)` -- TOP-LEVEL keys only -- so supplying
# textFormat replaces it wholesale and every sub-field left out resets to
# its default. bold=False is therefore belt-and-braces rather than
# load-bearing, but it keeps the intent legible at call sites that style
# a row which would otherwise read as a header.
EXPLAINER_FONT_SIZE = 9


def explainer_text_format(**overrides):
    """The house explainer `textFormat` mapping; kwargs override/extend.

    Returns the INNER textFormat dict, so callers wrap it themselves:

        {'range': ..., 'format': {'textFormat': explainer_text_format()}}

    Pass overrides both for documented deviations and for the extra keys
    a surface needs -- e.g. ``foregroundColor=`` on captions sitting on
    the navy band. Because the field mask replaces textFormat wholesale,
    anything the surface needs must be IN this dict: a foregroundColor
    set by an earlier format entry on the same cell will NOT survive.
    (A sibling ``backgroundColor`` will -- that is its own top-level key.)
    """
    return {'bold': False, 'italic': True,
            'fontSize': EXPLAINER_FONT_SIZE, **overrides}


# MLB-103 Trading Block header -- the ticket's column spec plus the season
# Total / Active points columns from the 2026-07-20 dev-render feedback.
TRADES_HEADER = ['Fantasy Team', 'MLB', 'Pos Eligibility', 'Player Name',
                 'Trade Availability', 'Interest Count', 'Total Points',
                 'Active Points']


# Trade Record header, from Kyle's mock verbatim. One row per received
# player; the two Sum columns hold per-receiving-side sums (merged down
# the side's rows by the write layer) and Date Executed merges down the
# whole trade. Points here are since-the-trade, for the receiving team.
TRADE_RECORD_HEADER = ['Receiving Fantasy Team', 'MLB', 'Pos Eligibility',
                       'Player Name', 'Sending Fantasy Team', '',
                       'Total Points', 'Active Points', 'Total Points Gained',
                       'Active Points Gained', 'Date Executed']


# ESPN tradeBlock statuses -> the UI-facing availability labels. A player
# who qualifies on interest alone (no mark set) renders an empty cell.
TRADE_AVAILABILITY_LABELS = {
    'ON_THE_BLOCK': 'On the Block',
    'UNTOUCHABLE': 'Untouchable',
}


# ---------------------------------------------------------------------------
# Podium marks (MLB-230)
# ---------------------------------------------------------------------------
#
# Both books mark the top three of a closed season in their finish grids.
# The vocabulary lives here because the three modules that need it -- the
# ESPN builder, the ESPN writer and the CBS module -- all already import
# render, and a glyph that the builder emits but the writer does not
# recognise renders as an unpainted hole.
#
# WHAT COUNTS AS SECOND AND THIRD IS PER-BOOK, and deliberately so:
#   ESPN is head-to-head with a bracket, so the medals key on the
#   POST-PLAYOFF finish -- the finals loser and the third-place-game winner.
#   CBS is a season-long points league with no playoffs at all, so there is
#   no such thing to read; second and third are the season standings.
# Callers pass whichever rank their book means. This module does not know
# which is which, and must not guess.
FINISH_MEDALS = {1: '🏆', 2: '🥈', 3: '🥉'}


def finish_medal(rank):
    """The podium glyph for `rank`, or None. Tolerates a NULL/blank rank
    (an in-flight season has no finish yet) and the string-ish numbers the
    warehouse hands back."""
    if rank is None or rank == '':
        return None
    return FINISH_MEDALS.get(int(rank))


# The finish scale, shared by both books: MIN green -> median yellow -> MAX
# red, auto-scaled per year column.
FINISH_GREEN = {'red': 0.341, 'green': 0.733, 'blue': 0.541}    # #57BB8A
FINISH_YELLOW = {'red': 1.0, 'green': 0.839, 'blue': 0.4}       # #FFD666
FINISH_RED = {'red': 0.902, 'green': 0.486, 'blue': 0.451}      # #E67C73

# The champion's cell is the ONE deliberate override of that scale: green
# for the title regardless of where the team finished the regular season,
# which is what lets a trophy read as a trophy from across the tab. It has
# been that way since the grid shipped and MLB-230 did not change it.
CHAMPION_FILL = dict(FINISH_GREEN)


def finish_cell_rank(cell):
    """The numeric rank behind a rendered finish cell, or None.

    Reads the bare `12`, the CBS matrix's bare `🥈`, and the ESPN table's
    `🥈 1` alike. Deliberately does NOT match a medal glyph appearing
    anywhere other than the front: team and owner names are user data and
    an emoji name is a real name.
    """
    if isinstance(cell, bool):
        return None
    if isinstance(cell, (int, float)):
        return int(cell)
    if not isinstance(cell, str):
        return None
    text = cell.strip()
    for rank, glyph in FINISH_MEDALS.items():
        if text == glyph:
            return rank
        if text.startswith(glyph):
            tail = text[len(glyph):].strip()
            return int(tail) if tail.isdigit() else rank
    return int(text) if text.isdigit() else None


def is_medal_cell(cell):
    """Whether a rendered finish cell leads with a podium glyph."""
    return isinstance(cell, str) and cell.startswith(tuple(FINISH_MEDALS.values()))


def finish_column_scale(cells):
    """The ranks a year column's gradient actually scales over.

    NUMERIC CELLS ONLY, because that is what Sheets sees: a conditional
    gradient ignores text, so every medal cell drops out of its own
    column's MIN / PERCENTILE / MAX. Matching that exactly is the point --
    a medal interpolated against the full set lands a shade off from the
    painted cells either side of it, which is the tell that its colour was
    computed somewhere else.

    Falls back to the full set only when a column is nothing but medals,
    where there is no gradient to agree with anyway.
    """
    numeric = [finish_cell_rank(c) for c in cells if not is_medal_cell(c)]
    numeric = [v for v in numeric if v is not None]
    if numeric:
        return numeric
    return [v for v in (finish_cell_rank(c) for c in cells) if v is not None]


def finish_scale_fill(rank, column_ranks):
    """The colour the finish gradient would paint for `rank`, interpolated
    over `column_ranks` on the same MIN / median / MAX stops Sheets uses.

    A medal cell is TEXT, and a conditional gradient paints numeric cells
    only -- so a medal has to carry a static fill or it reads as a white
    hole. Computing that fill from the column's own spread is what keeps
    the medal IN the colour run instead of overriding it (Kyle 2026-08-09:
    the medals "sort of messed up the color grading ... that should
    continue as it had been"). A flat medal-coloured swatch put the best
    finish in the grid on a grey cell surrounded by greens.

    `rank` is CLAMPED to the scale rather than extrapolated: a medal is
    usually the very value its column's gradient no longer sees, so 1st
    place routinely sits below a MIN of 2. Clamping paints it the green end
    exactly, which is where it belongs.

    Returns None when the column has no spread to interpolate over, which
    leaves the caller to fall back rather than invent a colour.
    """
    values = sorted(v for v in column_ranks if v is not None)
    if not values or rank is None:
        return None
    lo, hi = values[0], values[-1]
    mid = statistics.median(values)
    if hi == lo:
        return dict(FINISH_GREEN)
    if rank <= mid:
        span = mid - lo
        t = 0.0 if span == 0 else (rank - lo) / span
        start, end = FINISH_GREEN, FINISH_YELLOW
    else:
        span = hi - mid
        t = 1.0 if span == 0 else (rank - mid) / span
        start, end = FINISH_YELLOW, FINISH_RED
    t = min(1.0, max(0.0, t))
    return {k: start[k] + (end[k] - start[k]) * t
            for k in ('red', 'green', 'blue')}


def medal_fill_for_cell(cell, column_ranks=()):
    """The static fill a rendered finish cell needs, or None if it is not a
    medal cell.

    The champion takes its own green. Silver and bronze take the scale
    colour for the rank they represent, so they sit in the gradient run
    rather than punching a flat swatch through it. Pass `column_ranks` from
    finish_column_scale() so the scale matches the one Sheets will use.
    """
    if not isinstance(cell, str):
        return None
    if cell.startswith(FINISH_MEDALS[1]):
        return dict(CHAMPION_FILL)
    if not cell.startswith((FINISH_MEDALS[2], FINISH_MEDALS[3])):
        return None
    rank = finish_cell_rank(cell)
    # Falling back to the scale's best-finish end rather than to the
    # champion's fill: they are the same colour, but only one of those is a
    # true statement about a runner-up.
    return finish_scale_fill(rank, column_ranks) or dict(FINISH_GREEN)


def upright_emoji_runs(text, base_format=None):
    """textFormatRuns keeping every emoji in `text` upright inside an italic
    explainer (Kyle round 12: an italic 🏆 'looks quite bad').

    The single hardcoded pair this replaces -- de-italicize at 0, resume at
    2 -- only ever worked because the trophy led the string and nothing else
    followed it. A legend naming three medals has emoji in the middle too,
    so the runs are computed from the text. Sheets indexes runs in UTF-16
    code units, not codepoints, so offsets are measured that way.
    """
    base = dict(base_format or {'italic': True})
    upright = {**base, 'italic': False}
    runs, index, pending_resume = [], 0, False
    for ch in text:
        width = len(ch.encode('utf-16-le')) // 2
        if ord(ch) >= 0x1F000:                       # pictographic planes
            runs.append({'startIndex': index, 'format': dict(upright)})
            pending_resume = True
        elif pending_resume:
            runs.append({'startIndex': index, 'format': dict(base)})
            pending_resume = False
        index += width
    if not runs:
        return []
    # A leading run must start at 0 or Sheets rejects the request; text that
    # opens with a non-emoji needs the base run stated first.
    if runs[0]['startIndex'] != 0:
        runs.insert(0, {'startIndex': 0, 'format': dict(base)})
    return runs


# v1.2 draft tab: Best Value / Biggest Bust leaderboard columns.
DRAFT_VALUE_HEADER = ['Pts', 'Tm', 'Player', '(Rd) #Pick', 'Δ Rank']
DRAFT_ALLTIME_CELLS_LABEL = "Each Round × Pick's Historical Median Value"


# v2.0 Advanced Standings, Table A: the identity columns ahead of the
# per-stat standings grid. The full header is spec-driven -- see
# standings_header().
STANDINGS_FIXED_HEADER = ['Rank', 'Team', 'Owner', 'W-L']


def col_letter(n):
    """1-based column index -> A1 letters, dependency-free (both leagues'
    builders lay out wide blocks by arithmetic)."""
    letters = ''
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def standings_header(hitting_specs, pitching_specs):
    """Advanced Standings (Table A) header: identity columns, each scored
    hitting stat, the offense total, each scored pitching stat, the
    pitching total, then total points for / against -- with a buffer
    column between the groups. Stat set and order come from the scored
    stat specs (the Matchup History set), so a scoring change flows
    through with no code edit."""
    return [
        *STANDINGS_FIXED_HEADER,
        *_team_week_stat_headers(hitting_specs),
        'Offense',
        '',
        *_team_week_stat_headers(pitching_specs),
        'Defense',
        # Fielding stats would slot into this buffer for leagues that
        # score fielding categories; this league doesn't.
        '',
        'Total',
        'Against',
    ]


HOME_HEADER = [
    'Slot', 'Team', 'Player', 'Fantasy Team', 'Owner',
    'Points', 'Slash', 'Stat Line',
]


# v1.2 (#23): single group label spanning the two Total-Pts deviation
# columns appended to the right-band All-League rows (alt player + total pts).
HOME_DEVIATION_LABEL = 'Total-Pts Best (incl. bench & FA)'


# v1.2 (#22/#23): thin left-band All-League Team (all-time) header.
HOME_ALLTIME_HEADER = ['Slot', 'Player', 'Points', 'ppg']


RECORDS_HEADER = [
    'Scope', 'Record', 'Holder', 'Fantasy Team', 'Owner',
    'Value', 'Season', 'Period', 'Details', 'Boxscore',
]


RECORDS_MATRIX_WIDTH = 12


RECORDS_MATRIX_DETAIL_HEADER = [
    'Record',
    'Holder', 'Owner', 'Value', 'Period', 'Details',
    '',
    'Holder', 'Owner', 'Value', 'Period', 'Details',
]



# ---------------------------------------------------------------------
# MLB-164: the two Halls, appended below the record matrix.
#
# Layout mirrors CBS exactly: Franchise Hall of Fame in A-F, the G buffer,
# then the Wasted Hall of Shame's two boards side by side -- Pitchers in
# H-K, Hitters in L-O. Both books' Halls of Shame now start in column H
# after a G buffer, at the same widths.
#
# That makes the TAB 15 columns wide where the record matrix is still 12.
# The two numbers are kept separate on purpose: RECORDS_MATRIX_WIDTH is
# the shape of a matrix ROW (and the thing the row-scanners key on),
# RECORDS_TAB_WIDTH is the geometry of the SHEET. Widening the sheet
# without widening the matrix rows is what lets the matrix's all-time
# Details cell at L keep overflowing rightward across M-O.
#
# Both blocks are ALL-TIME career views, as on CBS.
RECORDS_TAB_WIDTH = 15


RECORDS_HALL_BANNER = [
    'Franchise Hall of Fame', '', '', '', '', '',
    '',
    'Wasted Hall of Shame', '', '', '', '', '', '', '',
]


# The scope captions ride the banner row beside each label -- the
# bold-banner-plus-explainer-token idiom MLB-170 settled (the same shape
# as the Advanced Standings _section captions).
#
# Column choice is about overflow, not taste. Each label sits in a cell
# narrower than its own text and overflows rightward (the tab sets
# OVERFLOW_CELL across A:O), so the cell immediately after a label must
# stay EMPTY or the label gets clipped: hence C rather than B on the
# left, and J rather than I on the right.
RECORDS_HALL_OF_FAME_CAPTION_COL = 2
RECORDS_HALL_OF_SHAME_CAPTION_COL = 9
RECORDS_HALL_OF_FAME_CAPTION = (
    'top {n} careers with one franchise, by points scored in the lineup'
)
RECORDS_HALL_OF_SHAME_CAPTION = (
    'top {n} careers each side, by wasted points of that type '
    '(unrostered + benched + negative)'
)


RECORDS_HALL_DETAIL_HEADER = [
    'Rank', 'Player', 'Franchise', 'Active Points', 'Years of Service',
    'Slash | Stat Line (While Active for Listed Team)',
    '',
    'Pitchers', 'Benched Most By', 'Wasted Points', 'Breakdown',
    'Hitters', 'Benched Most By', 'Wasted Points', 'Breakdown',
]


# 0-based columns carrying each board's Breakdown text. Rendered small and
# centered, the CBS treatment -- the string is long and the column has a
# populated neighbour, so it cannot overflow its way out of trouble.
RECORDS_HALL_BREAKDOWN_COLS = (10, 14)


def _is_records_hall_banner(row):
    """True for the Halls' banner row -- the marker the write layer uses to
    stop the record matrix's row-scanners running on past the matrix."""
    return (
        len(row) == RECORDS_TAB_WIDTH
        and str(row[0] or '').startswith('Franchise Hall of Fame')
        and str(row[7] or '').startswith('Wasted Hall of Shame')
    )


def _hall_slash_line(row):
    """Career slash for a Hall of Fame row, through the shared All-League
    helper so the Hall reads identically to the Home tab and the per-team
    pages: hitters get AVG/OBP/SLG, pitchers W-L-Sv/ERA/WHIP.

    Discipline is decided by outs vs at-bats -- the CBS Hall's test, and
    the only one available here, since a career row spans many lineup
    slots and has no single one to key off. Two-way players resolve to
    whichever side they did more of; Ohtani's line reads as a hitter's
    because that is where the bulk of his plate time is."""
    pitcher = float(row.get('outs') or 0) > float(row.get('ab') or 0)
    return _all_league_slash_line({**row, 'slot_label': 'SP' if pitcher else 'DH'})


def format_hall_of_fame_cells(entry, rank):
    """Left six cells of one Hall of Fame row."""
    return [
        rank,
        _bref_player_cell(entry),
        entry.get('team_abbrev') or entry.get('team_name') or '',
        # Whole points (Kyle 2026-08-03): over a span of a season or more,
        # the decimal is noise. Same ,.0f as the CBS book -- USER_ENTERED
        # parses the grouped string straight back to a number, so the cell
        # stays numeric and right-aligned.
        f"{float(entry.get('active_points') or 0):,.0f}",
        int(entry.get('service_years') or 0),
        ' || '.join(part for part in (
            _hall_slash_line(entry),
            format_top_scorer_stats_line(entry),
        ) if part),
    ]


def hall_of_shame_wasted(entry, discipline):
    """Career wasted points of one production type -- the three canonical
    terms of that type, summed. The ranking key for one board."""
    return sum(float(entry.get(f'{term}_{discipline}') or 0)
               for term in ('unrostered', 'benched', 'negative'))


def format_hall_of_shame_cells(entry, discipline):
    """Four cells of one Hall of Shame row, for one board.

    `discipline` is 'pitching' or 'hitting' and selects which half of the
    player's career waste this board is showing. The same player can be
    formatted for both boards with different numbers -- that is the point
    of the split, not a duplicate.

    'Benched Most By' carries how much that franchise sat, as CBS's does --
    "AAA (233)". It is blank when the whole total is unrostered: nobody sat
    him, so naming a franchise would invent a benching, and the breakdown
    says so by putting the number under 'unrostered'.

    Points are whole here (Kyle 2026-08-03): across a span of a season or
    more, the decimal is noise. Same ,.0f as the CBS book.

    The trailing '% of career wasted' is wasted over everything the player
    produced in this discipline -- active plus what was left unused. Kyle
    2026-08-03 overrides the earlier ruling that kept the percentage keyed
    to unrostered+benched only: the full three-term numerator can exceed
    100% for someone who gave back more than he ever banked, and that is a
    true number worth printing rather than a case to design around. It is
    labelled 'wasted' rather than CBS's old 'unused' precisely so the label
    matches what is in the numerator.
    """
    def _pts(key):
        return float(entry.get(f'{key}_{discipline}') or 0)

    wasted = hall_of_shame_wasted(entry, discipline)
    produced = _pts('active') + _pts('unrostered') + _pts('benched')
    bench_team = entry.get(f'bench_team_{discipline}') or ''
    bench_points = entry.get(f'bench_points_{discipline}')
    # The three WASTED terms, in canonical order. 'benched', not 'bench/IL'
    # (Kyle 2026-08-03) -- the two books' breakdown lines now read
    # identically. This is NOT a reversal of MLB-169: that ruling was about
    # the ACQUISITION LENS captions on Advanced Standings, which keep
    # 'bench/IL' because a caption read mid-scan needs the concrete words.
    # Here the term sits inside a breakdown whose other three parts are
    # already one word each, and it has a CBS twin to match.
    wasted_terms = (('unrostered', 'unrostered'), ('benched', 'benched'),
                    ('negative', 'negative'))
    # Kyle 2026-08-04: the wasted terms order by DESCENDING value, so the
    # term that actually drove the total leads the line instead of the
    # reader having to scan three numbers to find it. Only those three
    # reorder -- 'active' is not a wasted term but the denominator's
    # context, so it stays pinned in fourth, directly ahead of the
    # percentage it feeds.
    #
    # Sorted on the underlying value rather than the rounded display, and
    # `sorted` is stable, so two terms that round to the same string keep
    # canonical order and the line stays deterministic.
    breakdown = [
        f"{_pts(key):,.0f} {label}"
        for key, label in sorted(wasted_terms, key=lambda kl: -_pts(kl[0]))
    ]
    breakdown.append(f"{_pts('active'):,.0f} active")
    # A non-positive denominator has no readable percentage -- it happens
    # only for a player whose production in this discipline nets to zero
    # or below, where "x% of career" would be meaningless or sign-flipped.
    if produced > 0:
        breakdown.append(f"{wasted / produced * 100:,.0f}% of career wasted")
    return [
        _bref_player_cell(entry),
        f"{bench_team} ({float(bench_points):,.0f})" if bench_team else '',
        f"{wasted:,.0f}",
        ' · '.join(breakdown),
    ]

# Row-5 column headers, one side. Kyle 2026-07-17 restructure: a Total
# column between Games and Active (= Active + Inactive under the tab's
# filters), 'Active Points'/'Bench/IL Points' renamed Active/Inactive
# with a merged, centered 'Points' banner above the trio (row 4), and
# 'Roster Days' living in row 4 too (vertically merged over its empty
# row-5 cell -- "janky but gets things looking right"). The row-4 text
# comes from _team_history_scope_header; the merges are writer-side.
TEAM_HISTORY_DETAIL_HEADER = [
    'Tm', 'Slot', 'Player', 'Team',
    '', 'Games', 'Total', 'Active', 'Inactive', 'ppg',
    'Avg|W-L-Sv', 'OBP|ERA', 'Slg|WHIP', 'HR|K', 'SB|BB',
]


# The all-time side carries one extra trailing column -- Years of Service --
# for leagues with enough history (Kyle 2026-07-16). Kept as a tail so the
# current-vs-all-time asymmetry reads as an addition, not a mid-row jog.
TEAM_HISTORY_ALLTIME_DETAIL_HEADER = [
    *TEAM_HISTORY_DETAIL_HEADER,
    'Years of Service',
]


# Sentinel row labels for the Other section's tail (Kyle 2026-07-17):
# the "...N more under the bar" summary line and the franchise futility
# chair (worst-ever player by rostered_days - total_points), pinned last.
TEAM_HISTORY_OTHER_MORE = '__other_more__'
TEAM_HISTORY_OTHER_WORST = '__other_worst__'

# Also-rans rendered per side before the summary line collapses the rest
# (Kyle 2026-07-17 -- the uncapped register ran 1,000+ rows on the
# long-tenured CBS franchises).
TEAM_HISTORY_OTHER_CAP = 100

# The left-side block under the Current Season readout: the franchise's
# optimal lineup drawn from PLAYER-SEASONS (same player may recur across
# slots via different seasons; a player-season is used once). Kyle
# 2026-07-17. The banner text doubles as the format/merge marker.
TEAM_HISTORY_BEST_SEASON_BANNER = 'Best Individual Seasons by Lineup Slot'

# Row 1-3 gloss lines (col H), verbatim from Kyle's gold-standard mock
# (2026-07-17). The old Q1:Q3 glossary is retired.
TEAM_HISTORY_GLOSS_LINES = (
    'Active Points -- produced while in an active lineup slot. '
    'Starting Lineups are optimized by Active Points',
    'Inactive Points -- produced while on this team\'s bench or IL.',
    'Total Points -- Active + Inactive Points. Bench & "Other" slots '
    'are ranked by total points.',
)


TEAM_ROSTER_HEADER = [
    *TEAM_HISTORY_DETAIL_HEADER,
    '',
    *TEAM_HISTORY_DETAIL_HEADER,
]


TEAM_ROSTER_MATRIX_WIDTH = len(TEAM_ROSTER_HEADER)


TEAM_HISTORY_HITTER_HEADER = '__hitter_header__'


TEAM_HISTORY_PITCHER_HEADER = '__pitcher_header__'


TEAM_HISTORY_MIXED_HEADER = '__mixed_header__'


TEAM_HISTORY_HITTER_STATS = ['Avg', 'OBP', 'Slg', 'HR', 'SB']


TEAM_HISTORY_PITCHER_STATS = ['W-L (Sv)', 'ERA', 'WHIP', 'K', 'BB']


TEAM_HISTORY_MIXED_STATS = ['Avg|W-L-Sv', 'OBP|ERA', 'Slg|WHIP', 'HR|K', 'SB|BB']


TEAM_WEEKS_BASE_HEADER = [
    'Sort Key', 'Season', 'Matchup', 'Team',
]


TEAM_WEEKS_SCORE_HEADER = [
    'Hitting Points', 'Pitching Points', 'Total Points', 'Margin', 'W', 'L',
    '', 'Matchup Hit', 'Matchup Pitch', 'Matchup Total',
    '', 'Lg Avg Hit', 'Lg Avg Pitch', 'Lg Avg Total',
]


TEAM_WEEKS_RARE_STATS = {'CYC', 'NH', 'PG', 'SHO'}


TEAM_WEEKS_WHITE_TO_GREEN_STATS = {'TRIPLES', 'B_IBB', 'CYC', 'CG', 'SHO', 'PG', 'PK'}


TEAM_WEEKS_WHITE_TO_RED_STATS = {'BLK'}


SLOT_ORDER = {
    'C': 10,
    '1B': 20,
    '2B': 30,
    '3B': 40,
    'SS': 50,
    'IF': 60,
    'LF': 70,
    'CF': 80,
    'RF': 90,
    'OF': 100,
    'DH': 110,
    'U': 115,   # CBS's utility slot (ESPN spells it UTIL; ESPN rows never emit 'U')
    'UTIL': 120,
    'SP': 130,
    'RP': 140,
    'P': 150,
}


def format_team_week_row(row, hitting_specs, pitching_specs, league_id=None,
                         schedule_lookup=None):
    """Project one team-week fact row into the archive tab layout."""
    schedule_lookup = schedule_lookup or {}
    result = row.get('result') or ''
    matchup_label = records.format_week_label(
        row.get('season_year'),
        row.get('matchup_period'),
        schedule_lookup,
    )
    return [
        row.get('sort_key') or '',
        row.get('season_year') or '',
        _period_boxscore_formula(
            matchup_label,
            league_id,
            row.get('season_year'),
            row.get('matchup_period'),
            row.get('team_id'),
        ),
        row.get('team_name') or '',
        *[_format_team_week_stat(row, spec) for spec in hitting_specs],
        '',
        *[_format_team_week_stat(row, spec) for spec in pitching_specs],
        '',
        _one_decimal(row.get('calculated_hitting_pts')),
        _one_decimal(row.get('calculated_pitching_pts')),
        _one_decimal(row.get('calculated_points')),
        _one_decimal(row.get('calculated_margin')),
        1 if result == 'W' else '',
        1 if result == 'L' else '',
        '',
        _one_decimal(row.get('matchup_calculated_hitting_pts')),
        _one_decimal(row.get('matchup_calculated_pitching_pts')),
        _one_decimal(row.get('matchup_calculated_points')),
        '',
        _one_decimal(row.get('league_avg_hitting_points')),
        _one_decimal(row.get('league_avg_pitching_points')),
        _one_decimal(row.get('league_avg_total_points')),
    ]


def _records_matrix_scope_header(section_title):
    return [
        section_title,
        'Current Season', '', '', '', '',
        '',
        'All-Time', '', '', '', '',
    ]


def _team_history_scope_header(with_yos=False):
    """Row 4: the scope labels + the merged header text (Kyle 2026-07-17)
    -- 'Roster Days' over its vertically-merged E/U pair and the 'Points'
    banner over the Total/Active/Inactive trio. The writers merge the
    cells; a merge keeps the top-left value, so the text lives here."""
    span = len(TEAM_HISTORY_DETAIL_HEADER)
    side = [''] * span
    side[4] = 'Roster Days'
    side[6] = 'Points'
    right = list(side)
    if with_yos:
        right.append('')
    return ['Current Season', *side[1:], '', 'All-Time', *right[1:]]


def format_team_history_matrix_row(label, current_row=None, all_time_row=None,
                                   with_yos=False):
    if label == TEAM_HISTORY_HITTER_HEADER:
        return _team_history_section_header_row(TEAM_HISTORY_HITTER_STATS, with_yos)
    if label == TEAM_HISTORY_PITCHER_HEADER:
        return _team_history_section_header_row(TEAM_HISTORY_PITCHER_STATS, with_yos)
    if label == TEAM_HISTORY_MIXED_HEADER:
        return _team_history_section_header_row(TEAM_HISTORY_MIXED_STATS, with_yos)
    return [
        *_team_history_side_cells(current_row),
        '',
        *_team_history_side_cells(all_time_row, with_yos=with_yos),
    ]


def _team_history_section_header_row(stat_labels, with_yos=False):
    side = [''] * len(TEAM_HISTORY_DETAIL_HEADER)
    side[10:] = stat_labels
    all_side = [*side, ''] if with_yos else side   # YoS col has no stat sub-header
    return [*side, '', *all_side]


def _team_history_display_row(row, label, display_slot=None, active_games=None,
                              active_points=None):
    active_games = int(active_games if active_games is not None else row.get('active_games') or 0)
    active_points = _one_decimal(
        active_points if active_points is not None else row.get('active_points')
    )
    stat_line = _team_history_stat_line(row, display_slot or label)
    # Total = the player's FULL active + inactive for this team/scope --
    # deliberately the undecomposed active (a two-way starter's Total is
    # his true total even where his Active cell shows the slot's
    # discipline only). Kyle 2026-07-17.
    total_points = (float(row.get('active_points') or 0)
                    + float(row.get('bench_il_points') or 0))
    return {
        'slot_label': label,
        'display_slot': display_slot or label,
        'player': _bref_player_cell(row),
        'pro_team': row.get('pro_team') or '',
        'current_fantasy_team': row.get('current_fantasy_team') or '',
        'rostered_days': int(row.get('rostered_days') or 0),
        'active_games': active_games,
        'total_points': _round_half_up(total_points),
        'active_points': _round_half_up(active_points),
        'bench_il_points': _round_half_up(float(row.get('bench_il_points') or 0)),
        'points_per_active_game': (
            f"{active_points / active_games:.2f}" if active_games else ''
        ),
        'years_of_service': _format_years_of_service(row.get('service_years')),
        **stat_line,
    }


def _empty_team_history_display_row():
    return {
        'display_slot': '',
        'player': '',
        'pro_team': '',
        'current_fantasy_team': '',
        'rostered_days': '',
        'active_games': '',
        'total_points': '',
        'active_points': '',
        'bench_il_points': '',
        'points_per_active_game': '',
        'years_of_service': '',
        'stat_1': '',
        'stat_2': '',
        'stat_3': '',
        'stat_4': '',
        'stat_5': '',
    }


def _format_years_of_service(service_years):
    """The SQL LISTAGG "2024,2025,2026" -> the CBS-style "count: year-ranges"
    longevity string, e.g. "3: 2024-2026". Empty when the player logged no
    active (started, nonzero) seasons. En-dash ranges match the CBS almanac."""
    if not service_years:
        return ''
    years = sorted({int(y) for y in str(service_years).split(',') if y.strip()})
    if not years:
        return ''
    ranges, start, prev = [], years[0], years[0]
    for y in years[1:]:
        if y == prev + 1:
            prev = y
        else:
            ranges.append((start, prev))
            start = prev = y
    ranges.append((start, prev))
    span = ', '.join(str(a) if a == b else f'{a}–{b}' for a, b in ranges)
    return f'{len(years)}: {span}'


def _team_history_side_cells(row, with_yos=False):
    row = row or _empty_team_history_display_row()
    cells = [
        row.get('current_fantasy_team') or '',
        row.get('display_slot') or '',
        row.get('player') or '',
        row.get('pro_team') or '',
        row.get('rostered_days'),
        row.get('active_games'),
        row.get('total_points'),
        row.get('active_points'),
        row.get('bench_il_points'),
        row.get('points_per_active_game'),
        row.get('stat_1'),
        row.get('stat_2'),
        row.get('stat_3'),
        row.get('stat_4'),
        row.get('stat_5'),
    ]
    if with_yos:
        cells.append(row.get('years_of_service') or '')
    return cells


def _team_history_stat_line(row, display_slot):
    if _team_history_is_pitcher(row, display_slot):
        return {
            'stat_1': _pitching_decision_display(row, display_slot),
            'stat_2': _pitching_rate(row, 'era'),
            'stat_3': _pitching_rate(row, 'whip'),
            'stat_4': int(row.get('k') or 0),
            'stat_5': int(row.get('p_bb') or 0),
        }
    return {
        'stat_1': _hitting_rate(row, 'avg'),
        'stat_2': _hitting_rate(row, 'obp'),
        'stat_3': _hitting_rate(row, 'slg'),
        'stat_4': int(row.get('hr') or 0),
        'stat_5': int(row.get('sb') or 0),
    }


def _team_history_is_pitcher(row, display_slot):
    slots = _display_slot_tokens(display_slot)
    if not slots:
        slots = _display_slot_tokens(row.get('active_slots_played'))
    if 'SP' in slots or 'RP' in slots:
        return True
    if 'P' in slots:
        return True
    pitching_volume = sum(row.get(k) or 0 for k in ('outs', 'k', 'sv', 'w', 'l'))
    hitting_volume = sum(row.get(k) or 0 for k in ('ab', 'h', 'hr', 'sb'))
    return pitching_volume > hitting_volume


def _display_slot_tokens(value):
    cleaned = str(value or '').replace('-', ',').replace(' ', ',')
    return {part.strip() for part in cleaned.split(',') if part.strip()}


def _pitching_decision_display(row, display_slot):
    wins = int(row.get('w') or 0)
    losses = int(row.get('l') or 0)
    saves = int(row.get('sv') or 0)
    # v1.1.2: W-L when the pitcher logged no saves (6-4); W-L-Sv when
    # they did (2-1-15). Replaces the "decisions vs. saves, show whichever
    # is larger" rule, which dropped W-L for closers and saves for
    # swingmen who had both.
    if saves > 0:
        return f"{wins}-{losses}-{saves}"
    return f"{wins}-{losses}"


def _hitting_rate(row, stat_name):
    at_bats = row.get('ab') or 0
    if not at_bats:
        return ''
    hits = row.get('h') or 0
    if stat_name == 'avg':
        return _rate_as_whole_number(hits / at_bats)
    if stat_name == 'obp':
        denominator = at_bats + (row.get('b_bb') or 0) + (row.get('hbp') or 0) + (row.get('sf') or 0)
        return _rate_as_whole_number(
            (hits + (row.get('b_bb') or 0) + (row.get('hbp') or 0)) / denominator
        ) if denominator else ''
    if stat_name == 'slg':
        return _rate_as_whole_number((row.get('tb') or 0) / at_bats)
    return ''


def _rate_as_whole_number(value):
    return f"{int(round(value * 1000)):03d}"


def _round_half_up(value):
    if value < 0:
        return math.ceil(value - 0.5)
    return math.floor(value + 0.5)


def _pitching_rate(row, stat_name):
    outs = row.get('outs') or 0
    if not outs:
        return ''
    innings = outs / 3
    if stat_name == 'era':
        return f"{((row.get('er') or 0) * 9 / innings):.2f}"
    if stat_name == 'whip':
        return f"{(((row.get('p_bb') or 0) + (row.get('p_h') or 0)) / innings):.2f}"
    return ''


def _inactive_position_display(row):
    return row.get('active_slots_played') or row.get('position') or ''


def _compact_inactive_slot(slot, position):
    clean_position = ','.join(part.strip() for part in str(position or '').split(',') if part.strip())
    if clean_position:
        return f'{slot} - {clean_position}'
    return slot


def _all_league_slash_line(row):
    """Compact slash line for the All-League Team. Hitters: AVG/OBP/SLG
    as leading-dot 3-digit (e.g. .294/.390/.559). Pitchers: W-L-Sv/ERA/WHIP
    (e.g. 6-4-2/3.00/0.82). Reuses the team-tab rate helpers, so the
    underlying values stay consistent with the per-team pages."""
    slot = str(row.get('lineup_slot') or row.get('slot_label') or '')
    if slot.startswith(('SP', 'RP', 'P')):
        return (
            f"{_pitching_decision_display(row, slot)}"
            f"/{_pitching_rate(row, 'era')}"
            f"/{_pitching_rate(row, 'whip')}"
        )
    return (
        f"{_dotted_rate(_hitting_rate(row, 'avg'))}"
        f"/{_dotted_rate(_hitting_rate(row, 'obp'))}"
        f"/{_dotted_rate(_hitting_rate(row, 'slg'))}"
    )


def _dotted_rate(rate):
    """Format a no-dot integer-scaled rate ('294' -> '.294'). Values that reach
    or pass 1.000 arrive as 4+ digits ('1422' for a 1.422 SLG/OPS) and take the
    dot after the ones place ('1.422') rather than a bogus leading '.1422'.
    Empty stays empty so a no-AB hitter still renders '//', not './/.'."""
    if not rate:
        return ''
    return f'{rate[:-3]}.{rate[-3:]}' if len(rate) > 3 else f'.{rate}'


def format_all_league_team_row(row, league_id=None):
    """Project one selected slot row into the Home tab table shape."""
    season = row.get('season_year')
    matchup_period = row.get('matchup_period')
    team_id = row.get('team_id')
    is_season_row = row.get('period_label') == 'Season'
    points = _one_decimal(row.get('platform_points'))
    # v1.2: embed the boxscore as a hyperlink on the Points cell (week
    # rows only) so we keep both Slash and the verbose Stat Line without
    # a separate Boxscore column. Season / all-time rows span multiple
    # matchups, so there's no single boxscore to link.
    points_cell = points if is_season_row else _period_boxscore_formula(
        points, league_id, season, matchup_period, team_id,
    )
    return [
        row.get('slot_label') or row.get('lineup_slot') or '',
        row.get('pro_team') or '',
        _bref_player_cell(row),
        row.get('team_abbrev') or row.get('team_name') or '',
        row.get('owner_name') or '',
        points_cell,
        _all_league_slash_line(row),
        format_top_scorer_stats_line(row),
    ]


def format_all_league_thin_row(row):
    """Project one optimal-team pick into the thin left-band shape:
    Slot | Player | Pts | ppg.

    Used by the Home all-time All-League Team (#22). ppg = points /
    games_played; games_played comes from _enrich_optimal_team_with_stats
    and is active-games when the team was built points_type='active'
    (the all-time team is), so ppg reads "points per active game" --
    the same convention as the per-team tabs' points_per_active_game.
    """
    slot = row.get('slot_label') or row.get('lineup_slot') or ''
    player = _bref_player_cell(row)
    if not player:
        return [slot, '', '', '']
    pts_raw = row.get('platform_points') or 0
    # Whole number -- 1-decimal precision is overkill at the all-time scale.
    points = _round_half_up(pts_raw)
    games = int(row.get('games_played') or 0)
    ppg = f"{(pts_raw / games):.2f}" if games else ''
    return [slot, player, points, ppg]


def format_all_league_team_row_with_deviation(row, deviation_pick, league_id=None):
    """Right-band All-League row: the 8 standard columns plus the two
    Total-Pts deviation columns (#23).

    ``deviation_pick`` is the points_type='all' pick for this slot when it
    is a DIFFERENT player than the active pick (the caller decides this;
    a same-player points-only delta passes None). The two appended cells
    are the alternate player's name and their total (active+inactive+FA)
    points. Both blank when there's no deviation at this slot.
    """
    base = format_all_league_team_row(row, league_id=league_id)
    if not deviation_pick:
        return [*base, '', '']
    dev_player = _bref_player_cell(deviation_pick)
    dev_points = _one_decimal(deviation_pick.get('platform_points'))
    return [*base, dev_player, dev_points]


def home_nav_link(label, tab_title=None, gid_map=None):
    """Render one Home nav cell (#23/#25).

    With a gid for ``tab_title`` in ``gid_map`` (live write), emit an
    in-sheet =HYPERLINK to that tab's A1. Otherwise plain text -- the TSV
    preview (no gids exist) or a not-yet-built target like Draft Recap
    (tab_title=None).
    """
    gid = (gid_map or {}).get(tab_title) if tab_title is not None else None
    if gid is None:
        return label
    safe = str(label).replace('"', '""')
    return f'=HYPERLINK("#gid={gid}&range=A1", "{safe}")'


# -------------------------------------------------------------------------
# Draft Recap tab (v1.2): round x team board + Best Value / Biggest Bust
# leaderboards. value_delta = overall_pick - points_rank (a steal is a high
# positive; a bust is a large negative). (K) marks keeper picks.
# -------------------------------------------------------------------------


def _draft_pick_label(pick):
    """Compact draft-position label, e.g. 'R14 #195' (round + overall pick)."""
    return f"R{pick.get('round_num')} #{pick.get('overall_pick')}"


def _draft_player_label(pick):
    """Player name with a keeper marker, linked to the player's bref page. The
    visible label (incl. the keeper mark) keys its link off the official draft
    name, so a nickname in player_name never breaks the URL."""
    name = pick.get('player_name') or ''
    label = f"{name} (K)" if pick.get('keeper') else name
    return _bref_link(pick.get('official_player_name'), label)


def draft_initial_text(name):
    """First-initial + last name -- 'M Trout'. An already-initialised first
    name (JJ, TJ, CC, AJ) stays whole rather than collapsing to one letter
    (Kyle 2026-07-18); a mononym is left as-is. Shared by both books'
    board cells so the short form is identical platform to platform."""
    name = (name or '').strip()
    if not name:
        return ''
    parts = name.split()
    first, rest = parts[0], ' '.join(parts[1:])
    core = first.replace('.', '')
    if not rest:
        return first
    if core.isupper() and 2 <= len(core) <= 3:
        return f'{core} {rest}'
    return f'{(core[:1] or first[:1]).upper()} {rest}'


def _draft_initial_label(pick):
    """Board cell: first-initial + last name, keeper-marked, bref-linked --
    'M Trout', 'F Lindor (K)'."""
    label = draft_initial_text(pick.get('player_name'))
    if not label:
        return ''
    if pick.get('keeper'):
        label = f'{label} (K)'
    return _bref_link(pick.get('official_player_name'), label)


def format_draft_value_row(pick):
    """One Best-Value / Biggest-Bust leaderboard row (Kyle 2026-07-18
    order): Pts | Tm | Player (+K) | (Rd) #Pick | Δ Rank(+/-). Pts stay
    one-decimal; the writer forces the trailing .0."""
    value = pick.get('value_delta')
    return [
        _one_decimal(pick.get('season_points')),
        pick.get('team_abbrev') or '',
        _draft_player_label(pick),
        _draft_pick_label(pick),
        f"{int(value):+d}" if value is not None else '',
    ]


def format_draft_board_cell(pick):
    """Round x team grid cell: the drafted player as a first-initial link,
    keeper-marked. Blank for an unfilled (round, team) slot."""
    return _draft_initial_label(pick) if pick else ''


def format_standings_row(rank, row, hitting_specs, pitching_specs):
    """One Advanced Standings (Table A) data row, mirroring
    standings_header's layout. Every stat / points cell is a per-standard-
    matchup average (value * standard_matchup_days / scoring_days_played),
    so abnormal-length weeks (opening week, All-Star break) normalize by
    their actual gameplay days. W-L is the official platform record; ties
    only show when present."""
    wins = row.get('wins') or 0
    losses = row.get('losses') or 0
    ties = row.get('ties') or 0
    record = f"{wins}-{losses}-{ties}" if ties else f"{wins}-{losses}"
    return [
        rank,
        row.get('team_abbrev') or '',
        row.get('owner_display') or '',
        record,
        *[_per_week_stat(row, spec) for spec in hitting_specs],
        _per_week_value(row, row.get('calculated_hitting_pts')),
        '',
        *[_per_week_stat(row, spec) for spec in pitching_specs],
        _per_week_value(row, row.get('calculated_pitching_pts')),
        '',
        _per_week_value(row, row.get('calculated_points')),
        _per_week_value(row, row.get('against_calculated_points')),
    ]


def _per_week_stat(row, spec):
    """Per-standard-matchup average for one scored counting stat. OUTS
    converts to innings pitched first and renders as a plain base-10
    decimal -- baseball .1/.2 thirds notation doesn't survive averaging."""
    value = row.get(_fact_stat_column_name(spec.get('stat_name')))
    if spec.get('stat_name') == 'OUTS':
        value = (value or 0) / 3.0
    return _per_week_value(row, value)


def _per_week_value(row, value):
    """Normalize a season total into a per-standard-matchup average using
    the mart's gameplay-day denominators. standard_matchup_days is derived
    (the modal regular-week length), so a league playing 2-week matchups
    would normalize per-14 with no change here; 7 is only the fallback for
    rows missing the derived value."""
    days = row.get('scoring_days_played')
    if not days:
        return ''
    std = row.get('standard_matchup_days') or 7
    return round(float(value or 0) * float(std) / float(days), 1)


def standings_gradient_columns(hitting_specs, pitching_specs):
    """(column_index, direction) pairs for Table A's value columns,
    matching standings_header's layout. direction is 'most' (more is
    better -> green high) or 'fewest' (negative-weighted stats like L /
    ER / BLSV, plus Against -> green low). Zero-weighted stats come back
    with direction None -- callers skip the gradient for those. Positional
    on purpose: several abbrevs (K / BB / H / HR / R) appear in both the
    hitting and pitching blocks, so header-label lookup would misfire."""
    columns = []
    col = len(STANDINGS_FIXED_HEADER)
    for spec in hitting_specs:
        columns.append((col, almanac_data._team_week_good_record_direction(spec)))
        col += 1
    columns.append((col, 'most'))        # Offense
    col += 2                             # buffer
    for spec in pitching_specs:
        columns.append((col, almanac_data._team_week_good_record_direction(spec)))
        col += 1
    columns.append((col, 'most'))        # Defense
    col += 2                             # buffer
    columns.append((col, 'most'))        # Total
    columns.append((col + 1, 'fewest'))  # Against
    return columns


# v2.0 Advanced Standings, acquisition blocks (MLB-17). Indented one cell like
# the slot grid so Team / Owner line up under Table A, but the 'Keeper' column
# is what the write layer keys off to tell the two apart. Buffer '' cells sit
# between the Acquired group, the Lost group, and the Net deltas.
# Kyle 2026-07-17/18 (CBS restyle mirrored back + the formalized L/R
# divider): one table per lens, season half left / all-time half right,
# terse labels under 'Points Acquired/Lost Via' + 'Net Points via' group
# bands. On the ESPN almanac every L/R split shares ONE divider column --
# U (0-based 20), Table A's own offense/defense buffer -- so left halves
# pad out to T and right halves all start at V.
ESPN_DIVIDER_COL0 = 20

# ESPN's box scores carry MLB clubs as abbreviations; the affinity spine
# shows full names (Kyle round 13, matching CBS). Static platform
# vocabulary -- unknown abbrevs fall back to themselves.
ESPN_PRO_TEAM_NAMES = {
    'Ari': 'Arizona Diamondbacks', 'Atl': 'Atlanta Braves',
    'Bal': 'Baltimore Orioles', 'Bos': 'Boston Red Sox',
    'ChC': 'Chicago Cubs', 'ChW': 'Chicago White Sox',
    'Cin': 'Cincinnati Reds', 'Cle': 'Cleveland Guardians',
    'Col': 'Colorado Rockies', 'Det': 'Detroit Tigers',
    'Hou': 'Houston Astros', 'KC': 'Kansas City Royals',
    'LAA': 'Los Angeles Angels', 'LAD': 'Los Angeles Dodgers',
    'Mia': 'Miami Marlins', 'Mil': 'Milwaukee Brewers',
    'Min': 'Minnesota Twins', 'NYM': 'New York Mets',
    'NYY': 'New York Yankees', 'Oak': 'Athletics',
    'Phi': 'Philadelphia Phillies', 'Pit': 'Pittsburgh Pirates',
    'SD': 'San Diego Padres', 'Sea': 'Seattle Mariners',
    'SF': 'San Francisco Giants', 'StL': 'St. Louis Cardinals',
    'TB': 'Tampa Bay Rays', 'Tex': 'Texas Rangers',
    'Tor': 'Toronto Blue Jays', 'Wsh': 'Washington Nationals',
}

# The affinity spine's non-club row (MLB-159), kept OUT of the dict above
# on purpose: that dict is a club dictionary -- real platform vocabulary,
# thirty entries, one per MLB franchise -- and a sentinel sitting in it
# would make every reader of it handle a member that is not a team.
#
# The wording is load-bearing. This production was previously dropped for
# carrying ESPN's 'FA' stamp, and labelling the band 'FA' would replace a
# silent error with a stated falsehood ("these players were free agents
# when they played" -- Tyler Anderson's 24 Angels starts say otherwise).
# The honest claim is that the club is unknown, so the label says that.
ESPN_UNATTRIBUTED_CLUB = 'Unattributed'

_ACQ_HALF = ['Keeper', 'Draft', 'Pickup', 'Trade', 'Total', '',
             'Release', 'Trade', 'Total', '', 'FA', 'Trade']
_ACQ_BAND_HALF = ['Points Acquired Via', '', '', '', '', '',
                  'Points Lost Via', '', '', '', 'Net Points via', '']

# 3 identity cols + the 12-wide half ends at index 14; pad through the
# shared divider (indexes 15..20) so the right half starts at V (21).
_ACQ_PAD = [''] * (ESPN_DIVIDER_COL0 + 1 - (3 + len(_ACQ_HALF)))

ACQUISITION_HEADER = ['', 'Team', 'Owner', *_ACQ_HALF, *_ACQ_PAD,
                      *_ACQ_HALF]

ACQUISITION_BAND_ROW = ['', '', '', *_ACQ_BAND_HALF, *_ACQ_PAD,
                        *_ACQ_BAND_HALF]

# Which mart column family each lens reads.
_ACQ_LENS_SUFFIX = {'active': 'active_pts', 'rostered': 'rostered_pts'}


def _acq_num(value):
    """Display a mart points value: coerce Decimal/None to a 1-decimal float
    so the tab (and its golden TSV) stay clean and deterministic."""
    return round(float(value or 0), 1)


def acquisition_half_values(team_row, lens):
    """One HALF of an acquisition-table data row (season or all-time)
    under the given lens, mirroring _ACQ_HALF: the four acquired channels
    + their total, the two lost buckets + their total, then the two Net
    deltas, with buffer cells between the groups."""
    sfx = _ACQ_LENS_SUFFIX[lens]

    def v(channel):
        return _acq_num(team_row.get(f'{channel}_{sfx}'))

    return [
        v('keeper'), v('draft'), v('fa_add'), v('trade'), v('acquired'), '',
        v('dropped'), v('traded_away'), v('lost'), '',
        _acq_num(team_row.get(f'fa_delta_{sfx}')),
        _acq_num(team_row.get(f'trade_delta_{sfx}')),
    ]


def acquisition_gradient_columns():
    """(column_index, direction) pairs matching ACQUISITION_HEADER, BOTH
    halves. Acquired channels + total paint green-high ('most'); the Lost
    buckets + total green-low ('fewest' -- forfeiting less is better);
    the Net deltas zero-centered ('diverging'). Buffer/pad columns skip.
    Positional, like standings_gradient_columns."""
    per_half = [(0, 'most'), (1, 'most'), (2, 'most'), (3, 'most'),
                (4, 'most'),
                (6, 'fewest'), (7, 'fewest'), (8, 'fewest'),
                (10, 'diverging'), (11, 'diverging')]
    return [(base + off, direction)
            for base in (3, ESPN_DIVIDER_COL0 + 1)
            for off, direction in per_half]
# Umbrella eligibility slots suppressed on the Trades tab: every
# 1B/2B/3B/SS player is IF-eligible, every LF/CF/RF player is OF-eligible,
# every SP/RP is P-eligible, and UTIL is universal -- listing them next to
# the real positions is noise. ESPN's combo slots ('2B/SS', '1B/3B') are
# not SLOT_ORDER keys, so they drop out of the intersection on their own.
_TRADE_ELIGIBILITY_UMBRELLAS = {'IF', 'OF', 'UTIL', 'P'}


def trade_eligibility_display(slot_names):
    """Collapse an ESPN eligibleSlots name list to its atomic positions in
    lineup order, e.g. ['LF', 'CF', 'OF', 'UTIL', 'BE', 'IL'] -> 'LF/CF'.

    Falls back to the umbrella slots when no atomic position is present
    (a UTIL-only bat), and to '--' when nothing displayable remains."""
    names = set(slot_names or [])
    atomic = [s for s in sorted(names & set(SLOT_ORDER), key=SLOT_ORDER.get)
              if s not in _TRADE_ELIGIBILITY_UMBRELLAS]
    if atomic:
        return '/'.join(atomic)
    umbrella = sorted(names & _TRADE_ELIGIBILITY_UMBRELLAS, key=SLOT_ORDER.get)
    return '/'.join(umbrella) or '--'


def format_trades_row(row):
    """One Trading Block data row, in TRADES_HEADER order. `availability`
    is the raw ESPN tradeBlock status (or None); `interest` the count of
    teams that marked Interested In; the points are the player's season
    Total / Active (Home-glossary semantics, unscoped by team)."""
    name = row.get('player_name') or ''
    return [
        row.get('fantasy_team') or '',
        row.get('pro_team') or '',
        trade_eligibility_display(row.get('eligible_slots')),
        _bref_link(name, name),
        TRADE_AVAILABILITY_LABELS.get(row.get('availability'), ''),
        row.get('interest') or 0,
        row.get('total_pts') or 0,
        row.get('active_pts') or 0,
    ]


# What a since-trade points cell says when the season's opener could not be
# resolved (MLB-235 rung 4B-2). ASCII, and the house dash style.
#
# THE CELL THIS REPLACES SAID `0`, via a permissive `cutoff_sp = 1` fallback
# that admitted every scoring period of the season -- so an unresolved opener
# published whole-season production under a "since the trade" heading. A wrong
# number that looks right is worse than no number, and this is the smallest
# state the existing tab already supports: the row, the player and the date
# still render, only the four point cells decline to answer.
TRADE_POINTS_UNAVAILABLE = '--'


def _trade_points_cell(value):
    """A since-trade points cell: the number, or the unavailable marker.

    None means the opener did not resolve. Zero is a REAL total and must keep
    rendering as 0 -- collapsing the two is exactly the confusion this
    distinction exists to prevent.
    """
    return TRADE_POINTS_UNAVAILABLE if value is None else value


def format_trade_record_row(leg, team_sums=None, date_display=None):
    """One Trade Record leg row, in TRADE_RECORD_HEADER order.

    team_sums (total, active) appear only on the first row of a receiving
    side; date_display only on the first row of the whole trade -- the
    write layer merges those cells down their spans, so continuation rows
    carry empty strings there."""
    name = leg.get('player_name') or ''
    sum_total, sum_active = team_sums if team_sums else ('', '')
    return [
        leg.get('receiving_team') or '',
        leg.get('pro_team') or '',
        trade_eligibility_display(leg.get('eligible_slots')),
        _bref_link(name, name),
        leg.get('sending_abbrev') or '',
        '',
        # `or 0` would turn an unavailable cell into a confident zero, which
        # is the same class of mistake as the fallback this rung removed.
        _trade_points_cell(leg.get('total_pts')),
        _trade_points_cell(leg.get('active_pts')),
        sum_total,
        sum_active,
        date_display or '',
    ]


def format_record_matrix_row(spec, current_record=None, all_time_record=None,
                             league_id=None, display_map=None, schedule_lookup=None,
                             season_long=False):
    """Project current/all-time holders into one side-by-side record row.

    season_long (MLB-243): the league scores one season-long period rather
    than weekly matchups, so the Period cell must not say "Week 1" or link
    to a matchup boxscore. Defaults False -- every H2H caller is unchanged.
    """
    display_map = display_map or stat_catalog.get_display_map()
    schedule_lookup = schedule_lookup or records.load_schedule_lookup()
    return [
        spec.get('label') or display_map.get(spec.get('stat_name'), spec.get('stat_name')),
        *_format_record_side(
            current_record,
            scope='current_season',
            league_id=league_id,
            display_map=display_map,
            schedule_lookup=schedule_lookup,
            season_long=season_long,
        ),
        '',
        *_format_record_side(
            all_time_record,
            scope='all_time',
            league_id=league_id,
            display_map=display_map,
            schedule_lookup=schedule_lookup,
            season_long=season_long,
        ),
    ]


def _format_record_side(record, scope, league_id=None, display_map=None,
                        schedule_lookup=None, season_long=False):
    """Format one current-season or all-time side of a matrix row."""
    if not record:
        return ['', '', '', '', '']

    display_map = display_map or stat_catalog.get_display_map()
    schedule_lookup = schedule_lookup or records.load_schedule_lookup()

    season = record.get('season_year')
    matchup_period = record.get('matchup_period')
    if record.get('is_collapsed'):
        holder = _collapsed_holder(record)
        owner = _collapsed_owner(record)
        period = _collapsed_period(record, schedule_lookup, scope=scope)
    elif record.get('entity_grain') == 'player':
        holder = _bref_player_cell(record)
        owner = record.get('owner_name') or ''
        period = (
            records.format_week_label(season, matchup_period, schedule_lookup)
            if season is not None and matchup_period is not None
            else ''
        )
    else:
        holder = record.get('team_abbrev') or record.get('team_name') or ''
        owner = record.get('owner_name') or ''
        period = (
            records.format_week_label(season, matchup_period, schedule_lookup)
            if season is not None and matchup_period is not None
            else ''
        )
    if season_long:
        # ONE PERIOD, WHICH IS THE SEASON (MLB-243). "Week 1" would be a
        # matchup label over a league with no matchups, and the boxscore
        # URL it links to is a matchup view that does not describe how this
        # record was set. The season is the honest answer, as plain text.
        period = str(season) if season is not None else ''
    else:
        if scope == 'all_time' and not record.get('is_collapsed') and period and season:
            period = f"{period}: {season}"
        period = _period_boxscore_formula(
            period, league_id, season, matchup_period, record.get('team_id')
        )
    return [
        holder,
        owner,
        _format_record_value(record.get('stat_name'), record.get('stat_value')),
        period,
        _record_details(record, display_map),
    ]


def format_record_row(record, scope_label, league_id=None, display_map=None,
                      polarity_map=None, schedule_lookup=None):
    """Project one record holder into the curated Records tab shape."""
    display_map = display_map or stat_catalog.get_display_map()
    polarity_map = polarity_map or stat_catalog.get_polarity_map()
    schedule_lookup = schedule_lookup or records.load_schedule_lookup()

    grain = record.get('entity_grain')
    stat_name = record.get('stat_name')
    direction = record.get('record_direction')
    season = record.get('season_year')
    matchup_period = record.get('matchup_period')
    record_label = _record_label(record, display_map, polarity_map)

    if record.get('is_collapsed'):
        holder = _collapsed_holder(record)
        fantasy_team = ''
        owner = ''
        boxscore = ''
    elif grain == 'player':
        holder = _bref_player_cell(record)
        fantasy_team = record.get('team_name') or ''
        owner = record.get('owner_name') or ''
        boxscore = boxscore_formula(league_id, season, matchup_period, record.get('team_id'))
    else:
        holder = record.get('team_name') or ''
        fantasy_team = record.get('team_abbrev') or ''
        owner = record.get('owner_name') or ''
        boxscore = boxscore_formula(league_id, season, matchup_period, record.get('team_id'))

    period = (
        records.format_week_label(season, matchup_period, schedule_lookup)
        if season is not None and matchup_period is not None
        else ''
    )

    return [
        scope_label,
        record_label,
        holder,
        fantasy_team,
        owner,
        _format_record_value(stat_name, record.get('stat_value')),
        season or '',
        period,
        _record_details(record, display_map),
        boxscore,
    ]


def _record_label(record, display_map, polarity_map):
    """Build a compact label like 'Best Team Total Points'."""
    grain = (record.get('entity_grain') or '').title()
    stat_name = record.get('stat_name')
    direction = record.get('record_direction')
    outcome = records.best_or_worst_label(stat_name, direction, polarity_map)
    stat_label = display_map.get(stat_name, stat_name)
    return f"{outcome} {grain} {stat_label}".strip()


def _collapsed_holder(record):
    """Render a collapsed top tied tier."""
    holders = record.get('holders') or []
    grain = record.get('entity_grain')
    if holders:
        if grain == 'player':
            return ', '.join(h.get('display_name') or '' for h in holders)
        return ', '.join(h.get('team_abbrev') or h.get('team_name') or '' for h in holders)
    unit = 'players' if grain == 'player' else 'teams'
    return f"{record.get('tie_count', 0)} {unit} tied"


def _collapsed_owner(record):
    """Render owner names for a small collapsed tied tier."""
    holders = record.get('holders') or []
    if not holders:
        return ''
    owners = [h.get('owner_name') or '' for h in holders]
    return ', '.join(owners)


def _collapsed_season(record):
    """Render seasons for collapsed tiers, compacting single-season ties."""
    holders = record.get('holders') or []
    if not holders:
        return record.get('season_year') or ''
    seasons = [str(h.get('season_year')) for h in holders if h.get('season_year')]
    unique = []
    for season in seasons:
        if season not in unique:
            unique.append(season)
    return unique[0] if len(unique) == 1 else ', '.join(unique)


def _collapsed_period(record, schedule_lookup, scope='current_season'):
    """Render holder-period pairs for small collapsed tied tiers."""
    holders = record.get('holders') or []
    if not holders:
        season = record.get('season_year')
        matchup_period = record.get('matchup_period')
        return (
            _period_label(season, matchup_period, schedule_lookup, scope)
            if season is not None and matchup_period is not None
            else ''
        )

    parts = []
    for holder in holders:
        season = holder.get('season_year')
        matchup_period = holder.get('matchup_period')
        if season is None or matchup_period is None:
            continue
        name = (
            holder.get('team_abbrev')
            or holder.get('display_name')
            or holder.get('team_name')
            or holder.get('player_name')
            or ''
        )
        period = _period_label(season, matchup_period, schedule_lookup, scope)
        parts.append(f"{name} {period}".strip())
    return '; '.join(parts)


def _period_label(season, matchup_period, schedule_lookup, scope):
    label = records.format_week_label(season, matchup_period, schedule_lookup)
    if scope == 'all_time' and season:
        return f"{label}: {season}"
    return label


def _record_details(record, display_map):
    """Render contributor context for a record row."""
    rate_detail = _rate_qualifier_detail(record)
    if record.get('is_collapsed'):
        value = _format_record_value(record.get('stat_name'), record.get('stat_value'))
        unit = 'players' if record.get('entity_grain') == 'player' else 'teams'
        detail = f"{value} recorded by {record.get('tie_count', 0)} {unit}"
        return f"{detail}; {rate_detail}" if rate_detail else detail

    if rate_detail:
        return rate_detail

    contributors = record.get('contributors') or []
    if not contributors:
        return ''

    parts = []
    if record.get('entity_grain') == 'team':
        stat_name = record.get('stat_name')
        for item in contributors[:3]:
            value = fmt_record_value(stat_name, item.get('stat_value'))
            parts.append(f"{item.get('display_name')}: {value}")
    else:
        for item in contributors[:3]:
            stat_name = item.get('stat_name')
            label = _detail_stat_label(stat_name, display_map)
            value = fmt_record_value(stat_name, item.get('count_value'))
            parts.append(f"{value} {label}")
    return ', '.join(parts)


def _detail_stat_label(stat_name, display_map):
    label = display_map.get(stat_name, stat_name)
    replacements = {
        'Innings Pitched': 'IP',
        'Strikeouts (Pitcher)': 'K',
        'Strikeouts (Batter)': 'K',
        'Home Runs': 'HR',
        'Quality Starts': 'Quality Starts',
        'RBIs': 'RBI',
    }
    return replacements.get(label, label)


def _rate_qualifier_detail(record):
    """Render AB/IP qualification context for team rate records."""
    stat_name = record.get('stat_name')
    qualifier = record.get('qualifier_value')
    if qualifier is None:
        return ''
    if stat_name in {'AVG', 'OBP', 'SLG'}:
        return f"{int(qualifier)} AB"
    if stat_name in {'ERA', 'WHIP', 'K_PER_9', 'K_PER_BB', 'HR_PER_9', 'BB_PER_9'}:
        return f"{fmt_ip(qualifier)} IP"
    return ''


def _format_record_value(stat_name, value):
    """Almanac-specific record value rendering."""
    if value is None:
        return ''
    if stat_name in {'AVG', 'OBP', 'SLG'}:
        return fmt_avg(value)
    if stat_name in {'ERA', 'WHIP', 'K_PER_9', 'K_PER_BB', 'HR_PER_9', 'BB_PER_9'}:
        return f"{value:.2f}"
    if str(stat_name or '').startswith('LINEUP_SLOT_POINTS__'):
        return f"{value:.1f}"
    return fmt_record_value(stat_name, value)


def format_team_roster_row(row, league_id=None):
    """Project one current roster row into a team active-stat table row."""
    slot = slot_label(
        row.get('lineup_slot'),
        int(row.get('slot_rank') or 1),
        int(row.get('slots_to_fill') or 1),
    )
    if row.get('is_empty_slot'):
        return [slot, *([''] * (len(TEAM_ROSTER_HEADER) - 1))]

    return [
        slot,
        row.get('pro_team') or '',
        _bref_player_cell(row),
        row.get('position') or '',
        _one_decimal(row.get('active_points')),
        int(row.get('active_weeks') or 0),
        int(row.get('active_days') or 0),
        int(row.get('rostered_days') or 0),
        _one_decimal(row.get('inactive_points')),
        int(row.get('hr') or 0),
        int(row.get('rbi') or 0),
        int(row.get('r') or 0),
        int(row.get('sb') or 0),
        int(row.get('w') or 0),
        int(row.get('sv') or 0),
        int(row.get('hld') or 0),
        int(row.get('k') or 0),
        fmt_ip(row.get('outs')),
    ]


def team_tab_title(row):
    """Return a compact worksheet title for a team roster page."""
    team_id = row.get('team_id')
    name = row.get('team_abbrev') or row.get('team_name') or str(team_id or '')
    return _safe_sheet_title(name)


def _bref_link(official_name, display_text):
    """A player-name cell linked to the player's Baseball Reference page via
    bref's name search (which resolves the OFFICIAL name). Visible text is
    display_text (a nickname-or-official label); the URL keys off official_name,
    so a nickname never breaks the link. Plain text when there's no official
    name to key off."""
    display = '' if display_text is None else str(display_text)
    if not official_name:
        return display
    safe = display.replace('"', '""')
    # bref's name search chokes on the periods in disambiguation initials --
    # "Francisco J. Rodriguez" / "Chris B. Young" return nothing, but
    # "Francisco J Rodriguez" / "Chris B Young" resolve (and dotless leading
    # initials like "CC Sabathia" work either way). It also chokes on any
    # parenthetical qualifier (CBS's two-way split assets: "Shohei Ohtani
    # (Batter)"), so chop from the first ' (' -- Kyle 2026-07-17: anything
    # after a parenthesis is safe to chop until proven wrong. Both apply to
    # the search KEY only; the visible label keeps them.
    search_key = str(official_name).split(' (', 1)[0].replace('.', '')
    url = ('https://www.baseball-reference.com/search/search.fcgi?search='
           + urllib.parse.quote_plus(search_key))
    return f'=HYPERLINK("{url}", "{safe}")'


def _bref_player_cell(d):
    """_bref_link for the common row shape: display_name is the visible text
    (nickname-or-official), player_name is the official key for the URL."""
    display = d.get('display_name') or d.get('player_name') or ''
    official = d.get('player_name') or d.get('display_name') or ''
    return _bref_link(official, display)


def boxscore_formula(league_id, season_year, matchup_period, team_id):
    """Return a Google Sheets HYPERLINK formula for an ESPN boxscore."""
    if not (league_id and season_year and matchup_period and team_id):
        return ''
    url = _boxscore_url(league_id, season_year, matchup_period, team_id)
    return f'=HYPERLINK("{url}", "boxscore")'


def _period_boxscore_formula(label, league_id, season_year, matchup_period, team_id):
    """Return a HYPERLINK formula whose visible text is the period label."""
    if not label:
        return ''
    if not (league_id and season_year and matchup_period and team_id):
        return label
    url = _boxscore_url(league_id, season_year, matchup_period, team_id)
    safe_label = str(label).replace('"', '""')
    return f'=HYPERLINK("{url}", "{safe_label}")'


def _boxscore_url(league_id, season_year, matchup_period, team_id):
    return (
        'https://fantasy.espn.com/baseball/boxscore?'
        f'leagueId={league_id}&matchupPeriodId={matchup_period}'
        f'&seasonId={season_year}&teamId={team_id}&view=matchup'
    )


# _team_week_stat_sort_key moved to almanac_data.py (Tier 2c.1).


def _team_week_specs_for_category(stat_specs, category):
    return [spec for spec in stat_specs if spec.get('stat_category') == category]


def _team_week_stat_headers(stat_specs):
    return [_team_week_stat_header(spec) for spec in stat_specs]


def _team_week_stat_header(spec):
    stat_name = spec.get('stat_name')
    if stat_name == 'OUTS':
        return 'IP'
    return spec.get('abbrev') or spec.get('display_name') or stat_name


def _format_team_week_stat(row, spec):
    stat_name = spec.get('stat_name')
    value = row.get(_fact_stat_column_name(stat_name))
    if stat_name == 'OUTS':
        return fmt_ip(value)
    if value is None:
        return ''
    if float(value).is_integer():
        return int(value)
    return round(value, 3)


# _team_week_good_record_direction moved to almanac_data.py (Tier 2c.1).


def _is_rare_team_week_stat(stat_name):
    return stat_name in TEAM_WEEKS_RARE_STATS


def _slot_sort_key(slot):
    return (SLOT_ORDER.get(slot, 999), slot)


def _one_decimal(value):
    return round(value or 0, 1)


def _whole(value):
    """Decimal-free display value (board Max/Med, all-time cells)."""
    return int(round(value or 0))


def _safe_sheet_title(title):
    bad_chars = set('[]:*?/\\')
    cleaned = ''.join('-' if c in bad_chars else c for c in str(title))
    cleaned = cleaned.strip("'").strip()
    return cleaned[:100] or 'Sheet'


def _format_sheet_date(value):
    if not value:
        return ''
    if hasattr(value, 'strftime'):
        return value.strftime('%b %-d, %Y') if os.name != 'nt' else value.strftime('%b %#d, %Y')
    text = str(value)
    try:
        from datetime import date
        return date.fromisoformat(text[:10]).strftime('%b %#d, %Y' if os.name == 'nt' else '%b %-d, %Y')
    except ValueError:
        return text


def _a1_column(index_1_based):
    """1-based column index -> A1 letters (1 -> A, 27 -> AA)."""
    letters = ''
    index = index_1_based
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def team_tab_format_specs(rows):
    """The ENTIRE team-tab format spec as {'range','format'} entries --
    shared by the ESPN writer (_replace_team_tab) and the CBS writer
    (Kyle 2026-07-16: CBS team tabs identical to ESPN's, one format
    source so they can't drift). Pure: no Sheets calls.

    Covers: bold-13 title row, italic pale-blue subtitle, navy scope +
    column header band (rows 4-5), the Q1:Q3 points glossary reset,
    font-5 Tm columns (A/P), wrapped row-5 stat headers, number formats,
    the pipe-column stat-header bolding, and the pitcher-left /
    hitter-right stat alignment per data row. Freeze counts, column
    widths, and the A1 abbrev text run stay writer-side (they need the
    sheet id / different request shapes)."""
    width = max((len(r) for r in rows if r), default=31)
    last_col = _a1_column(width)
    white = {'red': 1, 'green': 1, 'blue': 1}
    navy = {'red': 0.12, 'green': 0.20, 'blue': 0.30}
    # Google's "Dark Gray 4" -- the header-note text color (Kyle's
    # gold-standard mock, 2026-07-17).
    dark_gray_4 = {'red': 0.263, 'green': 0.263, 'blue': 0.263}
    # DOCUMENTED DEVIATION from the size-9 explainer token (MLB-170,
    # confirmed by Kyle 08-02). These notes -- the A3 scoring line, the
    # H1:H3 points glossary, the R1/S1:S3 Lineup Data block -- are
    # explainer-class by content, but they live in the header strip
    # beside the title rather than in the body, and Kyle mocked them at 8
    # in dark gray on 2026-07-17 specifically so they sit quieter than
    # body captions. The token's supersession note only ever ruled on the
    # MLB-142 tens; nobody ruled on these. Kept at 8, derived from the
    # token rather than hardcoded, so it stays a NAMED deviation -- flip
    # the fontSize override out and this block rejoins the default.
    note_format = {
        'textFormat': explainer_text_format(fontSize=8,
                                            foregroundColor=dark_gray_4),
    }
    formats = [
        {
            'range': 'A1',
            'format': {'textFormat': {'bold': True, 'fontSize': 13}},
        },
        {
            'range': 'A2',
            'format': {'textFormat': {'italic': True}},
        },
        # Everything else in the header rows -- the A3 scoring note, the
        # H1:H3 points glossary, and the Lineup Data block (R1 label +
        # S1:S3 era lines) -- reads as size-8 italic dark-gray notes
        # (Kyle's gold standard, 2026-07-17).
        {'range': 'A3', 'format': note_format},
        {'range': 'H1:H3', 'format': note_format},
        {'range': 'R1',
         'format': {**note_format, 'horizontalAlignment': 'RIGHT'}},
        {'range': 'S1:S3', 'format': note_format},
        {
            'range': f'A4:{last_col}5',
            'format': {
                'textFormat': {'bold': True, 'foregroundColor': white},
                'backgroundColor': navy,
            },
        },
        {
            'range': 'A5:A',
            'format': {'textFormat': {'fontSize': 5}},
        },
        {
            'range': 'Q5:Q',
            'format': {'textFormat': {'fontSize': 5}},
        },
        # The vertically-merged Roster Days pair (E4:E5 / U4:U5): wrapped
        # + middled so the two-word label sits centered in its 50px slot.
        {
            'range': 'E4:F5',
            'format': {'wrapStrategy': 'WRAP', 'verticalAlignment': 'MIDDLE'},
        },
        {
            'range': 'U4:V5',
            'format': {'wrapStrategy': 'WRAP', 'verticalAlignment': 'MIDDLE'},
        },
        # The merged 'Points' banner over Total/Active/Inactive: centered,
        # size 10, keeping the navy band's bold white.
        {
            'range': 'G4:I4',
            'format': {
                'horizontalAlignment': 'CENTER',
                'textFormat': {'bold': True, 'fontSize': 10,
                               'foregroundColor': white},
            },
        },
        {
            'range': 'W4:Y4',
            'format': {
                'horizontalAlignment': 'CENTER',
                'textFormat': {'bold': True, 'fontSize': 10,
                               'foregroundColor': white},
            },
        },
        # Whole numbers: RosterDays/Games (E:F, U:V) + the points trio
        # (G:I, W:Y); ppg keeps a decimal (J, Z).
        {
            'range': 'E:F',
            'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0'}},
        },
        {
            'range': 'U:V',
            'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0'}},
        },
        {
            'range': 'G:I',
            'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0'}},
        },
        {
            'range': 'W:Y',
            'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0'}},
        },
        {
            'range': 'J:J',
            'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}},
        },
        {
            'range': 'Z:Z',
            'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}},
        },
    ]
    stat_labels = {'Avg', 'W-L (Sv)', 'Avg|W-L-Sv'}
    for row_number, row in enumerate(rows, 1):
        if row and row[0] == TEAM_HISTORY_BEST_SEASON_BANNER:
            # The Best Individual Seasons banner: navy band across the
            # left side (A:O), centered bold white -- the writers merge
            # the range (team_tab_banner_merges).
            formats.append({
                'range': f'A{row_number}:O{row_number}',
                'format': {
                    'horizontalAlignment': 'CENTER',
                    'textFormat': {'bold': True, 'foregroundColor': white},
                    'backgroundColor': navy,
                },
            })
        # Stat sub-headers bold per SIDE independently -- the left side's
        # Best Individual Seasons block (Kyle 2026-07-17) means the two
        # sides no longer share row alignment below the roster sections.
        text_format = {'bold': True}
        if row_number <= 5:
            text_format['foregroundColor'] = white
        if len(row) > 10 and row[10] in stat_labels:
            formats.append({
                'range': f'K{row_number}:O{row_number}',
                'format': {'textFormat': text_format},
            })
        if len(row) > 26 and row[26] in stat_labels:
            formats.append({
                'range': f'AA{row_number}:AE{row_number}',
                'format': {'textFormat': text_format},
            })
        # Header rows (team name / subtitle / note / scope / column
        # header) carry labels + glossary text, not slot codes -- skip
        # the per-row data-cell alignment so the left-aligned glossary
        # (Q1:Q3) is not clobbered by a spurious RIGHT.
        if row_number <= 5:
            continue
        # The Best Individual Seasons sub-headers relabel the Team column
        # 'Year' -- bold it like its sibling stat labels.
        if len(row) > 3 and row[3] == 'Year':
            formats.append({
                'range': f'D{row_number}:D{row_number}',
                'format': {'textFormat': {'bold': True}},
            })
        if _is_active_display_slot(row[1] if len(row) > 1 else ''):
            formats.append({
                'range': f'B{row_number}:B{row_number}',
                'format': {'horizontalAlignment': 'RIGHT'},
            })
        if _is_active_display_slot(row[17] if len(row) > 17 else ''):
            formats.append({
                'range': f'R{row_number}:R{row_number}',
                'format': {'horizontalAlignment': 'RIGHT'},
            })
        if _is_pitcher_display_slot(row[1] if len(row) > 1 else ''):
            formats.append({
                'range': f'K{row_number}:O{row_number}',
                'format': {'horizontalAlignment': 'LEFT'},
            })
        elif _is_hitter_display_slot(row[1] if len(row) > 1 else ''):
            formats.append({
                'range': f'K{row_number}:O{row_number}',
                'format': {'horizontalAlignment': 'RIGHT'},
            })
        if _is_pitcher_display_slot(row[17] if len(row) > 17 else ''):
            formats.append({
                'range': f'AA{row_number}:AE{row_number}',
                'format': {'horizontalAlignment': 'LEFT'},
            })
        elif _is_hitter_display_slot(row[17] if len(row) > 17 else ''):
            formats.append({
                'range': f'AA{row_number}:AE{row_number}',
                'format': {'horizontalAlignment': 'RIGHT'},
            })
    return formats


def team_tab_merge_ranges(with_lineup_data=False):
    """The team-tab header merges (Kyle 2026-07-17), as zero-based grid
    ranges WITHOUT a sheetId (each writer stamps its own): the vertical
    Roster Days pairs (E4:E5, U4:U5) and the Points banners over
    Total/Active/Inactive (G4:I4, W4:Y4). with_lineup_data adds the
    S1:X1..S3:X3 merges for the era lines -- merging keeps the right
    Player column's auto-resize from fitting to the long note text
    (auto-fit ignores merged cells). Writers should unmerge first --
    re-merging an already-merged range errors."""
    ranges = [
        {'startRowIndex': 3, 'endRowIndex': 5, 'startColumnIndex': 4, 'endColumnIndex': 5},
        {'startRowIndex': 3, 'endRowIndex': 4, 'startColumnIndex': 6, 'endColumnIndex': 9},
        {'startRowIndex': 3, 'endRowIndex': 5, 'startColumnIndex': 20, 'endColumnIndex': 21},
        {'startRowIndex': 3, 'endRowIndex': 4, 'startColumnIndex': 22, 'endColumnIndex': 25},
    ]
    if with_lineup_data:
        ranges.extend(
            {'startRowIndex': r, 'endRowIndex': r + 1,
             'startColumnIndex': 18, 'endColumnIndex': 24}
            for r in (0, 1, 2))
    return ranges


def team_tab_banner_merges(rows):
    """A1 ranges of the Best Individual Seasons banner rows (A:O), for
    each writer to merge -- the banner's row index varies per tab."""
    return [
        f'A{i}:O{i}'
        for i, row in enumerate(rows, 1)
        if row and row[0] == TEAM_HISTORY_BEST_SEASON_BANNER
    ]


def _is_pitcher_display_slot(slot):
    slot_text = str(slot or '')
    if not slot_text:
        return False
    if slot_text.startswith(('SP', 'RP', 'P ')):
        return True
    return any(token in slot_text for token in ('- SP', '- RP', '- P'))


def _is_hitter_display_slot(slot):
    slot_text = str(slot or '')
    if not slot_text:
        return False
    if slot_text in {'Slot', 'Avg', 'W-L (Sv)', 'Avg|W-L-Sv'}:
        return False
    return not _is_pitcher_display_slot(slot_text)


def _is_active_display_slot(slot):
    slot_text = str(slot or '')
    return bool(slot_text) and not slot_text.startswith(('BE', 'IL', 'Other'))
