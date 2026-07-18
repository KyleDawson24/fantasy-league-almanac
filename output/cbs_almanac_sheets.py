"""output/cbs_almanac_sheets.py

The POINTS-LEAGUE almanac, v2 (Kyle's 2026-07-12 course correction +
2026-07-13 re-sequencing): the CBS workbook MIRRORS the ESPN almanac's
ARCHITECTURE -- nav-first Home with All-League boards, a Records tab, a
Standings tab, and per-franchise team pages whose meat is the Best
Lineup, current season x all-time side by side -- assembled from the
UNIFIED fact family (MLB-72: CBS day-grain rides int_player_daily ->
fct_player_daily_performance -> fct_player_position_pts), never from
CBS-specific assembly.

Tab set:
  Home       navigation table (live #gid links, two-pass write like
             ESPN's), points glossary + provenance notes, All-League
             Team boards: Season-to-Date + All-Time ONLY (no
             Team-of-the-Period -- period boundaries don't exist
             historically).
  Records    PLAYER records only: Best Season (mart_player_season_
             records) side by side with Best Career Totals
             (mart_player_career_records, the MLB-69 axis).
  Standings  the 2026 period-by-period arc + 25 years of season
             finishes (champions marked), active franchises first.
  Team pages one per CURRENTLY-ACTIVE franchise: Best Lineup by
             weighted ACTIVE points per eligible position (slot
             template C/1B/2B/3B/SS/OF*3/DH/U/P*9), current season x
             all-time, bench ranked by total rostered points, with
             the era-fidelity label.

OWNER RE-KEY ISOLATION (MLB-64): every franchise-scoped aggregation
routes its scoping through _entity_where() / the entity_id column
returned by the data getters. When the owner chain-of-custody lands,
re-keying the almanac to OWNERS means changing that one seam (plus tab
naming), not the page builders.

Eligibility semantics (the shared model): a player is a candidate at a
position only where int_cbs__eligibility_windows opened a date-scoped
window (CBS's captured rule -- primary + 20 games last year or 10 this
year, after-achievement). The DH and U SLOTS are universal-fill
(CBS: "Everyone is eligible at DH"; U is the utility slot) --
implemented by synthesizing DH/U candidacies from each player's hitting
production, mirroring how CBS's own lineup page offers those slots.

Points lens: weighted_active_pts -- identical to binary active points
wherever the day's state is KNOWN (2026 captures; 2001-03 + 2021+
reconstruction), the start-share estimator on 2004-2020, zero where
membership is confirmed but activity unknown. The provenance labels on
every page own that caveat; placement is Kyle's to tweak.

Determinism: no wall-clock timestamps -- every cell is a pure function
of warehouse state, so TSV previews stay golden-able.
"""

import re
import statistics
import time
from datetime import date

import gspread

import db
from db import league_predicate, query_snowflake
from almanac_data import get_optimal_season_candidates, get_optimal_team_candidates
from cbs_draft_recap_data import get_draft_history
# Shared board machinery ((a) reuse per Kyle 2026-07-13): the CBS Home
# mirrors the ESPN Home by CALLING its builders, not by imitating them.
# The private imports are deliberate and noted in BRAINTHOUGHTS as the
# (b)-refactor seam -- promoting these to a shared library module.
from almanac_logic import (
    _HOME_SCORING_CALLOUT,
    _deviation_by_slot,
    _merge_home_bands,
    build_team_history_tabs,
    get_optimal_team_selections,
    season_pace_factors,
)
# The ESPN draft board's red->white->green cell math, reused verbatim so
# the two books' boards read identically (private import, same doctrine
# as the almanac_logic ones above).
from almanac_write import _draft_gradient_color
from almanac_render import (
    HOME_DEVIATION_LABEL,
    HOME_HEADER,
    SLOT_ORDER,
    _bref_link,
    _bref_player_cell,
    draft_initial_text,
    _hitting_rate,
    _pitching_rate,
    format_all_league_team_row,
    format_all_league_team_row_with_deviation,
    home_nav_link,
    team_tab_banner_merges,
    team_tab_format_specs,
    team_tab_merge_ranges,
)
from formatters import fmt_ip, format_top_scorer_stats_line
from sheets_writer import _get_authorized_client


HOME_TAB = 'Home'
RECORDS_TAB = 'Records'
STANDINGS_TAB = 'Advanced Standings'
DRAFT_TAB = 'Draft Recap'

# The league's active-lineup shape, verbatim from the captured rules
# (roster.positions): 19 active = C/1B/2B/3B/SS + OF*3 + DH + U + P*9.
CBS_SLOT_CAPS = {
    'C': 1, '1B': 1, '2B': 1, '3B': 1, 'SS': 1,
    'OF': 3, 'DH': 1, 'U': 1, 'P': 9,
}

# Records-tab curation: the scored categories + marquee counting stats.
_RECORDS_POINTS = ['CALCULATED_POINTS', 'CALCULATED_HITTING_PTS',
                   'CALCULATED_PITCHING_PTS']
_RECORDS_HITTING = ['R', 'RBI', 'B_BB', 'SB', 'TB', 'HR', 'H', 'XBH']
_RECORDS_PITCHING = ['K', 'W', 'SV', 'HLD', 'QS', 'CG', 'IRSTR', 'NH']

# Records v2 (Kyle 2026-07-13): the record catalog auto-derives from what
# the league SCORES (get_cbs_record_catalog below); these maps say which
# union-fact column carries each cataloged stat's per-franchise, per-day
# production -- the substrate for the best-season / by-owner / contributor
# leaders. Only stats the attributed union fact carries are recordable at
# team/owner grain (that's exactly the scored set); non-scored marquee
# overrides (HR / XBH) need the season-stats path and are a follow-up.
_REC_POINTS_COL = {
    'CALCULATED_POINTS': 'total_stat_pts',
    'CALCULATED_HITTING_PTS': 'total_hitting_stat_pts',
    'CALCULATED_PITCHING_PTS': 'total_pitching_stat_pts',
}
_REC_STAT_COL = {
    'R': 'r', 'RBI': 'rbi', 'B_BB': 'b_bb', 'SB': 'sb', 'TB': 'tb', 'H': 'h',
    'HR': 'hr', '2B': 'doubles', '3B': 'triples', 'XBH': 'xbh',
    'W': 'w', 'SV': 'sv', 'HLD': 'hld', 'CG': 'cg', 'QS': 'qs', 'OUTS': 'outs',
    'K': 'k', 'P_H': 'p_h', 'P_BB': 'p_bb', 'ER': 'er',
}
# Extra components summed for the rate-stat records (AVG/OBP/SLG/OPS,
# ERA/WHIP/K9/BB9/K:BB). The rest of each rate's inputs (h/tb/b_bb/outs/er/
# p_h/p_bb/k) already ride _REC_STAT_COL; these four don't otherwise appear.
_REC_RATE_COL = {'AB': 'ab', 'HBP': 'hbp', 'SF': 'sf', 'L': 'l'}

# Rate-stat records (Kyle 2026-07-14): pass CBS's numbers through ESPN's own
# _hitting_rate/_pitching_rate (same components, same display) -- no CBS rate
# math. OPS/K9/BB9/K:BB are the small inline extras the ESPN helper doesn't
# cover. (key, display, category, higher_is_better).
_RATE_SPECS = [
    ('AVG', 'Batting Average', 'hitting', True),
    ('OBP', 'On-Base %', 'hitting', True),
    ('SLG', 'Slugging %', 'hitting', True),
    ('OPS', 'OPS', 'hitting', True),
    ('ERA', 'ERA', 'pitching', False),
    ('WHIP', 'WHIP', 'pitching', False),
    ('K9', 'K/9', 'pitching', True),
    ('BB9', 'BB/9', 'pitching', False),
    ('KBB', 'K:BB', 'pitching', True),
]
# Interim min-sample qualifiers on the ACTIVE-weighted sums (hitting gates on
# AB, pitching on IP-as-outs; season floor / career floor). MLB-80 makes these
# rigorous for the fantasy scale; for now a "real full-time contributor" bar.
_RATE_QUAL = {
    ('hitting', True): ('ab', 350), ('hitting', False): ('ab', 1500),
    ('pitching', True): ('outs', 300), ('pitching', False): ('outs', 1200),
}
# Lineup Slot Records: the fct_player_position_pts position vocabulary
# (LF/CF/RF -> OF, SP/RP -> P, no U -- U is synthesized from the best hitter).
_SLOT_POSITIONS = ['C', '1B', '2B', '3B', 'SS', 'OF', 'DH', 'P']
# The CURRENT-roster slot template (Kyle 2026-07-14): (display slot, source
# position, 0-based rank). Multi-slots take the 2nd/3rd/... best at that
# position, so the section reads as the all-time best possible active lineup.
# 'U' is synthesized (see the slot builder). 2026 active-slot census: 3 OF, 9 P.
_ROSTER_SLOTS = (
    [('C', 'C', 0), ('1B', '1B', 0), ('2B', '2B', 0), ('3B', '3B', 0), ('SS', 'SS', 0)]
    + [('OF', 'OF', i) for i in range(3)]
    + [('U', 'U', 0), ('DH', 'DH', 0)]
    + [('P', 'P', i) for i in range(9)]
)
_HIT_SLOT_POSITIONS = ['C', '1B', '2B', '3B', 'SS', 'OF', 'DH']

_NUM_WORDS = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven',
              'eight', 'nine', 'ten', 'eleven', 'twelve', 'thirteen', 'fourteen',
              'fifteen', 'sixteen', 'seventeen', 'eighteen', 'nineteen', 'twenty']


def _spell(n):
    """Small non-negative integer as an English word ('four'); digits above the
    table (a player on 20+ franchises never happens in a 16-team league)."""
    return _NUM_WORDS[n] if 0 <= n < len(_NUM_WORDS) else str(n)


def _dot(rate3):
    """'.294' from a no-dot integer-scaled rate; a >=1.000 value ('1422' for a
    1.422 SLG/OPS) dots after the ones place ('1.422'), not '.1422'."""
    if not rate3:
        return ''
    return f'{rate3[:-3]}.{rate3[-3:]}' if len(rate3) > 3 else f'.{rate3}'


def _rate_num_disp(row, key):
    """(numeric value for ranking, display string) for one rate stat, reusing
    ESPN's _hitting_rate/_pitching_rate for the shared ones so CBS and ESPN
    read identically. row keys are lowercase (season agg rows, or a lowercased
    career accumulation)."""
    ab = _rec_fnum(row.get('ab')); h = _rec_fnum(row.get('h'))
    bb = _rec_fnum(row.get('b_bb')); hbp = _rec_fnum(row.get('hbp'))
    sf = _rec_fnum(row.get('sf')); tb = _rec_fnum(row.get('tb'))
    outs = _rec_fnum(row.get('outs')); er = _rec_fnum(row.get('er'))
    ph = _rec_fnum(row.get('p_h')); pbb = _rec_fnum(row.get('p_bb'))
    k = _rec_fnum(row.get('k')); ip = outs / 3.0
    pa = ab + bb + hbp + sf
    if key == 'AVG':
        return (h / ab if ab else None, _dot(_hitting_rate(row, 'avg')))
    if key == 'OBP':
        return ((h + bb + hbp) / pa if pa else None, _dot(_hitting_rate(row, 'obp')))
    if key == 'SLG':
        return (tb / ab if ab else None, _dot(_hitting_rate(row, 'slg')))
    if key == 'OPS':
        if not (pa and ab):
            return (None, '')
        ops = (h + bb + hbp) / pa + tb / ab
        return (ops, f'{ops:.3f}'.lstrip('0') or '.000')
    if key == 'ERA':
        return (er * 9 / ip if ip else None, _pitching_rate(row, 'era'))
    if key == 'WHIP':
        return ((pbb + ph) / ip if ip else None, _pitching_rate(row, 'whip'))
    if key == 'K9':
        return (k * 9 / ip if ip else None, f'{k * 9 / ip:.2f}' if ip else '')
    if key == 'BB9':
        return (pbb * 9 / ip if ip else None, f'{pbb * 9 / ip:.2f}' if ip else '')
    if key == 'KBB':
        return (k / pbb if pbb else None, f'{k / pbb:.2f}' if pbb else '')
    return (None, '')


def _rate_qual_detail(row, category):
    """The min-sample the rate cleared, ESPN-style: '512 AB' / '182.0 IP'."""
    if category == 'hitting':
        return f"{int(_rec_fnum(row.get('ab')))} AB"
    return f"{fmt_ip(_rec_fnum(row.get('outs')))} IP"


def _rate_component_detail(row, key):
    """A rate record's Details = the raw components behind the ratio (Kyle
    2026-07-14): AVG '254 hits in 683 At Bats'; OBP 'H Hits, W Walks, X HBP in
    N Plate Appearances'; SLG the extra-base mix; OPS its two parts. The volume
    (AB / PA / IP) subsumes the old min-sample qualifier."""
    def _i(c):
        return int(round(_rec_fnum(row.get(c))))
    ab, h, bb, hbp, sf = _i('ab'), _i('h'), _i('b_bb'), _i('hbp'), _i('sf')
    pa = ab + bb + hbp + sf
    if key == 'AVG':
        return f'{h:,} hits in {ab:,} At Bats'
    if key == 'OBP':
        return f'{h:,} Hits, {bb:,} Walks, {hbp:,} HBP in {pa:,} Plate Appearances'
    if key == 'SLG':
        # _rec_agg aliases columns by their map KEY, so doubles/triples land in
        # '2b'/'3b' (not 'doubles'/'triples').
        d, t, hr = _i('2b'), _i('3b'), _i('hr')
        singles = max(0, h - d - t - hr)
        return f'{singles:,} 1B, {d:,} 2B, {t:,} 3B, {hr:,} HR in {pa:,} PA'
    if key == 'OPS':
        return (f"OBP {_dot(_hitting_rate(row, 'obp'))}, "
                f"SLG {_dot(_hitting_rate(row, 'slg'))}")
    ip = fmt_ip(_rec_fnum(row.get('outs')))
    er, ph, pbb, k = _i('er'), _i('p_h'), _i('p_bb'), _i('k')
    if key == 'ERA':
        return f'{er:,} ER in {ip} IP'
    if key == 'WHIP':
        return f'{ph:,} Hits, {pbb:,} Walks in {ip} IP'
    if key == 'K9':
        return f'{k:,} K in {ip} IP'
    if key == 'BB9':
        return f'{pbb:,} Walks in {ip} IP'
    if key == 'KBB':
        return f'{k:,} K, {pbb:,} Walks'
    return ''


def _best_rate(items, key, higher, qual_col, qual_min):
    """The best qualifying (min-sample) group for a rate stat. Returns
    (row, display) or (None, None)."""
    best = best_num = best_disp = None
    for row in items:
        if _rec_fnum(row.get(qual_col)) < qual_min:
            continue
        num, disp = _rate_num_disp(row, key)
        if num is None:
            continue
        if best is None or (num > best_num if higher else num < best_num):
            best, best_num, best_disp = row, num, disp
    return (best, best_disp) if best is not None else (None, None)

# The synthetic holding-pen franchise for 2001-2002 zero-event players (see
# fct_cbs_player_game_attribution). Fenced out of team records + team pages;
# its players still surface in player/league records.
_CBS_SENTINEL_FID = 9999

# The player-record Details stat-line: marquee counting stats, headline first.
# A hitter's pitching cells are zero and vice-versa, so one combined order
# serves both; XBH/points are excluded (derived / shown as the Value).
# OUTS rides as IP (Kyle 2026-07-18: pitcher statlines never surfaced
# innings -- the list simply lacked it, while the Home boards' shared
# formatter always had it; unifying the two statline builders into one
# top-N-by-POINT-CONTRIBUTION helper is a logged wishlist item).
_STAT_LINE_ORDER = ['HR', 'RBI', 'R', 'SB', 'W', 'SV', 'K', 'QS', 'HLD', 'CG',
                    '2B', '3B', 'H', 'TB', 'OUTS']
_STAT_LINE_LABELS = {s: s for s in _STAT_LINE_ORDER}
_STAT_LINE_LABELS['OUTS'] = 'IP'

# Records section stat order (Kyle round 7): mirror the natural box-score order
# rather than alphabetical. Hits, 2B, 3B, HR, XBH, then TB, then the rest.
# Negative-polarity pitching stats (ER, Hits/Walks Allowed) live in Negative
# Records as "Most ...", never as a positive record.
_HIT_ORDER = ['H', '2B', '3B', 'HR', 'XBH', 'TB', 'R', 'RBI', 'SB', 'B_BB']
_PIT_ORDER = ['W', 'QS', 'K', 'SV', 'HLD', 'CG', 'OUTS']
_NEG_ORDER = ['ER', 'P_H', 'P_BB']
# Display-name fixups over dim_stat (kept CBS-side to avoid ESPN golden drift).
_DISPLAY_FIX = {'RBIs': 'RBI'}

_NAVY = {'red': 0.12, 'green': 0.20, 'blue': 0.30}
_WHITE = {'red': 1, 'green': 1, 'blue': 1}
_PALE_BLUE = {'red': 0.95, 'green': 0.97, 'blue': 0.99}
_GOLD = {'red': 1.0, 'green': 0.95, 'blue': 0.75}
# Season-finish scale: the Sheets-standard green -> yellow -> red preset
# colors. Champions render as a trophy -- a TEXT cell the numeric gradient
# skips -- so they carry the scale's best-finish green as a static fill.
_FINISH_GREEN = {'red': 0.341, 'green': 0.733, 'blue': 0.541}   # #57BB8A
_FINISH_YELLOW = {'red': 1.0, 'green': 0.839, 'blue': 0.4}      # #FFD666
_FINISH_RED = {'red': 0.902, 'green': 0.486, 'blue': 0.451}     # #E67C73
# The ESPN writer's gradient endpoints (softer than the finish trio) --
# the slot grids and the affinity chart share them for one-tab coherence.
_SCALE_RED = {'red': 0.96, 'green': 0.62, 'blue': 0.60}
_SCALE_GREEN = {'red': 0.67, 'green': 0.86, 'blue': 0.64}
# True-zero/null cells on the affinity chart (Kyle 2026-07-17 round 5).
_LIGHT_GRAY = {'red': 0.937, 'green': 0.937, 'blue': 0.937}


def _finish_gradient():
    """TRUE auto-scale per YEAR column (Kyle 2026-07-17 round 2, replacing
    the fixed 1/8.5/16 anchors): one rule per season, ranged over that
    year's cells in BOTH matrices, so 2020's 12-team last place paints
    full red -- they literally couldn't have done worse that year. Fresh
    dicts per call (the gspread in-place-mutation lesson)."""
    return {
        'minpoint': {'type': 'MIN', 'color': _FINISH_GREEN},
        'midpoint': {'type': 'PERCENTILE', 'value': '50',
                     'color': _FINISH_YELLOW},
        'maxpoint': {'type': 'MAX', 'color': _FINISH_RED},
    }


def _points_gradient():
    """The ESPN writer's red -> white -> green scale (min / median / max),
    per value column of the slot-points grids -- CBS mirrors ESPN's
    Advanced Standings Table B exactly."""
    return {
        'minpoint': {'type': 'MIN', 'color': _SCALE_RED},
        'midpoint': {'type': 'PERCENTILE', 'value': '50', 'color': _WHITE},
        'maxpoint': {'type': 'MAX', 'color': _SCALE_GREEN},
    }


def _share_gradient():
    """Affinity scale (Kyle 2026-07-17 rounds 4+5): red -> WHITE -> green,
    one rule PER BLOCK so each matrix scales to its own spread. Blank
    cells (true zero/null -- never rostered anyone from that club) can't
    take a gradient; the builder lays a static light-gray base under the
    blocks so they read as 'nothing here' rather than low-but-alive."""
    return {
        'minpoint': {'type': 'NUMBER', 'value': '0', 'color': _SCALE_RED},
        'midpoint': {'type': 'PERCENTILE', 'value': '50', 'color': _WHITE},
        'maxpoint': {'type': 'MAX', 'color': _SCALE_GREEN},
    }


def _points_gradient_low():
    """Reversed points scale for fewer-is-better columns (the Lost side
    of the acquisition blocks): green at the minimum, red at the max."""
    return {
        'minpoint': {'type': 'MIN', 'color': _SCALE_GREEN},
        'midpoint': {'type': 'PERCENTILE', 'value': '50', 'color': _WHITE},
        'maxpoint': {'type': 'MAX', 'color': _SCALE_RED},
    }


def _diverging_gradient():
    """Zero-centered scale for the acquisition Net columns: the sign is
    the story -- red below zero, white at exactly zero, green above."""
    return {
        'minpoint': {'type': 'MIN', 'color': _SCALE_RED},
        'midpoint': {'type': 'NUMBER', 'value': '0', 'color': _WHITE},
        'maxpoint': {'type': 'MAX', 'color': _SCALE_GREEN},
    }
# ESPN Records palette (Kyle 2026-07-13): powder-blue #f2f7fc section/scope
# headers, and a light-orange recency wash for records held in the live season.
_POWDER = {'red': 0.949, 'green': 0.969, 'blue': 0.988}   # #f2f7fc
_WHITE = {'red': 1, 'green': 1, 'blue': 1}
# Saturated powder for the draft board's Pick/Team/Player header trio against
# the navy band (matches the ESPN writer's _DRAFT_POWDER_BG; Kyle 2026-07-18).
_POWDER_HEADER = {'red': 0.72, 'green': 0.85, 'blue': 0.92}
_ORANGE = {'red': 0.988, 'green': 0.898, 'blue': 0.804}   # #fce5cd

# Inline twin of the dbt cbs_name_key macro (macros/cbs_name_key.sql) --
# KEEP IN SYNC. Used once, to bridge the stint machine's name_key grain
# to player_key for the RosterDays column.
def _name_key_sql(col):
    return (
        "trim(regexp_replace("
        "trim(regexp_replace(lower("
        "regexp_replace("
        "regexp_replace("
        f"replace({col}, '.', ''),"
        "'^([^,]+,.+?)\\\\s+[A-Z0-9]{1,3}\\\\s+[A-Z]{2,4}$', '\\\\1'"
        "),"
        "'^([^,]+),\\\\s*(.+)$', '\\\\2 \\\\1'"
        ")"
        "), ' +', ' ')),"
        "' (jr|sr|ii|iii|iv)$', ''"
        "))"
    )


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def is_points_league():
    """Format dispatch by data presence: delivered period standings exist
    only for non-H2H leagues (F7). Zero rows -> not a points league."""
    rows = query_snowflake(
        f"SELECT COUNT(*) AS n FROM mart_period_standings"
        f" WHERE {league_predicate()}"
    )
    return rows[0]['n'] > 0


def get_season_context():
    """The almanac's data horizon: active season, latest closed period,
    latest captured roster date, historic era span. All from the data --
    no wall clock."""
    season = query_snowflake(
        f"SELECT MAX(season_year) AS sy FROM mart_period_standings"
        f" WHERE {league_predicate()}"
    )[0]['sy']
    period = query_snowflake(
        f"SELECT MAX(period) AS p FROM mart_period_standings"
        f" WHERE {league_predicate()} AND season_year = {season}"
    )[0]['p']
    roster_date = query_snowflake(
        f"SELECT MAX(roster_date) AS d FROM stg_cbs__rosters"
        f" WHERE {league_predicate()} AND season_year = {season}"
    )[0]['d']
    era = query_snowflake(
        f"SELECT MIN(season_year) AS lo, MAX(season_year) AS hi"
        f" FROM stg_cbs__ui_standings WHERE {league_predicate()}"
    )[0]
    return {'season_year': season, 'latest_period': period,
            'roster_date': roster_date,
            'first_season': era['lo'], 'last_closed_season': era['hi']}


def _entity_where(entity_id, alias=''):
    """THE owner-re-key seam (MLB-64): every franchise-scoped filter in
    this module routes through here. Today the entity is franchise_id
    (= the facts' team_id); the future owner re-key swaps this predicate
    (and the tab labels) without touching the page builders."""
    col = f'{alias}.team_id' if alias else 'team_id'
    return f'{col} = {int(entity_id)}'


def get_standings_arc(season_year):
    """Every period row for the season, standings-ordered within period."""
    return query_snowflake(
        f"SELECT period, team_id, team_name, division_name, standings_rank,"
        f"       points, period_points, rank_change, points_behind_leader,"
        f"       is_latest_period"
        f" FROM mart_period_standings"
        f" WHERE {league_predicate()} AND season_year = {season_year}"
        f" ORDER BY period, standings_rank"
    )


def get_historic_finishes():
    """25 years of season finishes from the parsed UI standings: one row
    per (season, franchise), champions flagged, with per-season names
    (names drift; franchise_id is the spine). division_name rides along
    so the builder can crown division champions (best league rank within
    the division that season)."""
    return query_snowflake(
        f"SELECT season_year, franchise_id, team_name, division_name,"
        f"       standings_rank, is_champion, total_points, teams_in_season"
        f" FROM stg_cbs__ui_standings"
        f" WHERE {league_predicate()}"
        f" ORDER BY season_year, standings_rank"
    )


def get_acquisition_channels(season_year):
    """Season production by acquisition channel, both lenses -- the CBS
    twin of ESPN's mart_team_acquisition_channels (MLB-17 shape), built
    from the UI transaction log + the attribution fact. CBS channels:
    OPENING (no logged acquisition -- the recovered season-start roster;
    drafts were never logged, so draft/keeper collapse here), FA ADD,
    and TRADE. A game credits the channel of the player's latest
    acquisition by that franchise on/before the game. Lost = what
    departed players produced AFTER leaving (drop vs trade split),
    windowed to the player's next re-acquisition by the same franchise
    so a drop/re-add/re-drop never double-counts: the active lens counts
    other franchises' started points; the rostered lens adds unowned
    (free-agent) production. Season-scoped like ESPN's."""
    yr = int(season_year)
    return query_snowflake(f"""
        WITH attr AS (
            SELECT cbs_player_id, stat_group, game_date, game_pk, game_index,
                   franchise_id,
                   COALESCE(active_weight, 0) AS w,
                   COALESCE(calculated_fpts, 0) AS fpts
            FROM fct_cbs_player_game_attribution
            WHERE {league_predicate()} AND season_year = {yr}
              AND franchise_id <> {_CBS_SENTINEL_FID}
        ),
        events AS (
            -- The UI report logs trades ONE-SIDED (the receiver's
            -- trade_in); the sender's departure is synthesized from the
            -- counterparty -- without it every Trade-lost column reads 0
            -- (Kyle caught this live, 2026-07-17 round 7).
            SELECT franchise_id, player_cbs_id, effective_date, move_type,
                   CASE WHEN move_type IN ('add', 'trade_in')
                        THEN 1 ELSE 0 END AS is_acq
            FROM stg_cbs__ui_transactions
            WHERE {league_predicate()} AND season_year = {yr}
              AND move_type IN ('add', 'trade_in', 'drop', 'trade_out')
            UNION ALL
            SELECT counterparty_franchise_id, player_cbs_id,
                   effective_date, 'trade_out', 0
            FROM stg_cbs__ui_transactions
            WHERE {league_predicate()} AND season_year = {yr}
              AND move_type = 'trade_in'
              AND counterparty_franchise_id IS NOT NULL
        ),
        channeled AS (
            SELECT a.franchise_id, a.w, a.fpts,
                   COALESCE(e.move_type, 'opening') AS channel
            FROM attr a
            LEFT JOIN events e
              ON e.player_cbs_id = a.cbs_player_id
             AND e.franchise_id = a.franchise_id
             AND e.is_acq = 1
             AND e.effective_date <= a.game_date
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY a.cbs_player_id, a.stat_group, a.game_date,
                             a.game_pk, a.game_index, a.franchise_id
                ORDER BY e.effective_date DESC NULLS LAST) = 1
        ),
        acquired AS (
            SELECT franchise_id,
                   ROUND(SUM(CASE WHEN channel = 'opening' THEN fpts * w END), 1)   AS opening_active_pts,
                   ROUND(SUM(CASE WHEN channel = 'add' THEN fpts * w END), 1)       AS fa_add_active_pts,
                   ROUND(SUM(CASE WHEN channel = 'trade_in' THEN fpts * w END), 1)  AS trade_active_pts,
                   ROUND(SUM(fpts * w), 1)                                          AS acquired_active_pts,
                   ROUND(SUM(CASE WHEN channel = 'opening' THEN fpts END), 1)       AS opening_rostered_pts,
                   ROUND(SUM(CASE WHEN channel = 'add' THEN fpts END), 1)           AS fa_add_rostered_pts,
                   ROUND(SUM(CASE WHEN channel = 'trade_in' THEN fpts END), 1)      AS trade_rostered_pts,
                   ROUND(SUM(fpts), 1)                                              AS acquired_rostered_pts
            FROM channeled
            GROUP BY franchise_id
        ),
        departures AS (
            -- Each departure opens a Lost window that closes at the
            -- player's next re-acquisition by the SAME franchise.
            SELECT franchise_id, player_cbs_id, effective_date, move_type,
                   COALESCE(MIN(CASE WHEN is_acq = 1 THEN effective_date END)
                       OVER (PARTITION BY franchise_id, player_cbs_id
                             ORDER BY effective_date, is_acq
                             ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING),
                       DATE '9999-12-31') AS window_end
            FROM events
        ),
        dep_windows AS (
            SELECT * FROM departures WHERE move_type IN ('drop', 'trade_out')
        ),
        lost_rostered_franchise AS (
            SELECT d.franchise_id, d.move_type,
                   SUM(a.fpts * a.w) AS lost_active,
                   SUM(a.fpts) AS lost_rostered
            FROM dep_windows d
            JOIN attr a
              ON a.cbs_player_id = d.player_cbs_id
             AND a.franchise_id <> d.franchise_id
             AND a.game_date > d.effective_date
             AND a.game_date < d.window_end
            GROUP BY d.franchise_id, d.move_type
        ),
        unowned AS (
            -- Priced games with no attribution row = free-agent games.
            SELECT g.cbs_player_id, g.game_date,
                   SUM(g.calculated_fpts) AS fpts
            FROM int_cbs__player_game_points g
            LEFT JOIN fct_cbs_player_game_attribution a
              ON a.league_key = g.league_key
             AND a.cbs_player_id = g.cbs_player_id
             AND a.stat_group = g.stat_group
             AND a.game_date = g.game_date
             AND a.game_pk = g.game_pk
             AND a.game_index = g.game_index
            WHERE {league_predicate('g')}
              AND g.season_year = {yr}
              AND a.cbs_player_id IS NULL
            GROUP BY g.cbs_player_id, g.game_date
        ),
        lost_unowned AS (
            SELECT d.franchise_id, d.move_type, SUM(u.fpts) AS lost_unowned
            FROM dep_windows d
            JOIN unowned u
              ON u.cbs_player_id = d.player_cbs_id
             AND u.game_date > d.effective_date
             AND u.game_date < d.window_end
            GROUP BY d.franchise_id, d.move_type
        ),
        lost AS (
            SELECT COALESCE(f.franchise_id, u.franchise_id) AS franchise_id,
                   COALESCE(f.move_type, u.move_type) AS move_type,
                   COALESCE(f.lost_active, 0) AS lost_active,
                   COALESCE(f.lost_rostered, 0)
                       + COALESCE(u.lost_unowned, 0) AS lost_rostered
            FROM lost_rostered_franchise f
            FULL OUTER JOIN lost_unowned u
              ON f.franchise_id = u.franchise_id
             AND f.move_type = u.move_type
        ),
        lost_pivot AS (
            SELECT franchise_id,
                   ROUND(SUM(CASE WHEN move_type = 'drop' THEN lost_active END), 1)          AS dropped_active_pts,
                   ROUND(SUM(CASE WHEN move_type = 'trade_out' THEN lost_active END), 1)     AS traded_away_active_pts,
                   ROUND(SUM(lost_active), 1)                                                AS lost_active_pts,
                   ROUND(SUM(CASE WHEN move_type = 'drop' THEN lost_rostered END), 1)        AS dropped_rostered_pts,
                   ROUND(SUM(CASE WHEN move_type = 'trade_out' THEN lost_rostered END), 1)   AS traded_away_rostered_pts,
                   ROUND(SUM(lost_rostered), 1)                                              AS lost_rostered_pts
            FROM lost
            GROUP BY franchise_id
        )
        SELECT a.franchise_id AS team_id,
               COALESCE(a.opening_active_pts, 0)   AS opening_active_pts,
               COALESCE(a.fa_add_active_pts, 0)    AS fa_add_active_pts,
               COALESCE(a.trade_active_pts, 0)     AS trade_active_pts,
               COALESCE(a.acquired_active_pts, 0)  AS acquired_active_pts,
               COALESCE(l.dropped_active_pts, 0)   AS dropped_active_pts,
               COALESCE(l.traded_away_active_pts, 0) AS traded_away_active_pts,
               COALESCE(l.lost_active_pts, 0)      AS lost_active_pts,
               COALESCE(a.opening_rostered_pts, 0)  AS opening_rostered_pts,
               COALESCE(a.fa_add_rostered_pts, 0)   AS fa_add_rostered_pts,
               COALESCE(a.trade_rostered_pts, 0)    AS trade_rostered_pts,
               COALESCE(a.acquired_rostered_pts, 0) AS acquired_rostered_pts,
               COALESCE(l.dropped_rostered_pts, 0)  AS dropped_rostered_pts,
               COALESCE(l.traded_away_rostered_pts, 0) AS traded_away_rostered_pts,
               COALESCE(l.lost_rostered_pts, 0)     AS lost_rostered_pts
        FROM acquired a
        LEFT JOIN lost_pivot l ON l.franchise_id = a.franchise_id
    """)


def get_active_franchises(roster_date):
    """The currently-active franchises (the 2026 capture is the roster of
    record): [(franchise_id, current_name)] -- ONLY these get team tabs."""
    return query_snowflake(
        f"SELECT DISTINCT team_id, team_name"
        f" FROM stg_cbs__rosters"
        f" WHERE {league_predicate()} AND roster_date = '{roster_date}'"
        f" ORDER BY team_id"
    )


def get_franchise_map():
    """franchise_id -> {canonical_id, name, abbrev} from dim_franchise, the
    MLB-64 continuity overlay that stitches a franchise's re-minted ids
    (Foster's Folly 13 + 30) into one identity. Every surface that keys by
    franchise rolls up through this. The #### sentinel is excluded."""
    rows = query_snowflake(
        f"SELECT franchise_id, canonical_franchise_id, canonical_name,"
        f"       canonical_abbrev"
        f" FROM dim_franchise WHERE {league_predicate()}"
    )
    return {int(r['franchise_id']): {
                'canonical_id': int(r['canonical_franchise_id']),
                'name': r['canonical_name'],
                'abbrev': r['canonical_abbrev'],
            } for r in rows if str(r['franchise_id']) != str(_CBS_SENTINEL_FID)}


def get_slot_points(season_year):
    """Current-season points by DEPLOYED lineup slot, per franchise -- the
    CBS twin of almanac_data.get_team_slot_points. Real slot deployments
    exist only where lineups are captured live (2026 onward), which is
    exactly the current-season window; the slot list is the league's own
    active-roster shape, so reconstruction placeholders (ACT/RS/EST) and
    any future bench vocabulary can never leak in as columns."""
    slots = ", ".join(f"'{s}'" for s in CBS_SLOT_CAPS)
    return query_snowflake(f"""
        SELECT team_id, lineup_slot,
               ROUND(SUM(total_stat_pts), 1) AS slot_pts
        FROM fct_player_daily_performance
        WHERE {league_predicate()} AND season_year = {int(season_year)}
          AND game_date IS NOT NULL AND lineup_slot IN ({slots})
        GROUP BY team_id, lineup_slot
    """)


def get_slot_points_alltime():
    """Capture-era points by DEPLOYED slot, per (franchise, slot, season).
    Real slots exist only where daily lineups are captured (2026 onward)
    -- placeholder eras carry ACT/RS/EST and can't match -- so no season
    filter is needed; the season stays in the grain so the builder can
    derive the capture-era exposure window. Hitter slots + U only: the
    era-complete P column comes from get_pitching_points_alltime (the
    Records-page convention -- Kyle 2026-07-17 round 3)."""
    slots = ", ".join(f"'{s}'" for s in CBS_SLOT_CAPS if s != 'P')
    return query_snowflake(f"""
        SELECT team_id, lineup_slot, season_year,
               ROUND(SUM(total_stat_pts), 1) AS slot_pts
        FROM fct_player_daily_performance
        WHERE {league_predicate()} AND game_date IS NOT NULL
          AND lineup_slot IN ({slots})
        GROUP BY team_id, lineup_slot, season_year
    """)


def get_pitching_points_alltime():
    """All-years ACTIVE-weighted pitching points per franchise -- the
    all-time P column. Pitching production IS the P slot by construction
    in every era (a started pitcher can only have occupied P), unlike
    hitter slots which only exist where captured."""
    return query_snowflake(f"""
        SELECT team_id,
               ROUND(SUM(total_pitching_stat_pts
                         * COALESCE(active_weight, 0)), 1) AS p_pts
        FROM fct_player_daily_performance
        WHERE {league_predicate()} AND game_date IS NOT NULL
        GROUP BY team_id
    """)


def get_detailed_stats_alltime():
    """All-time ACTIVE-weighted totals of the marquee scored stats per
    franchise -- the substrate for the all-time detailed standings (Kyle
    2026-07-17 round 8: CBS's first detailed standings is all-time-only;
    the current season reads fine on the CBS site). The builder divides
    by standard-season equivalents to render paces."""
    stat_cols = ([_REC_STAT_COL[s] for s in _HIT_ORDER]
                 + [_REC_STAT_COL[s] for s in _PIT_ORDER])
    col_select = ',\n               '.join(
        f'ROUND(SUM({c} * COALESCE(active_weight, 0)), 1) AS {c}'
        for c in stat_cols)
    return query_snowflake(f"""
        SELECT team_id,
               {col_select},
               ROUND(SUM(total_hitting_stat_pts
                         * COALESCE(active_weight, 0)), 1) AS hit_pts,
               ROUND(SUM(total_pitching_stat_pts
                         * COALESCE(active_weight, 0)), 1) AS pit_pts,
               ROUND(SUM(total_stat_pts
                         * COALESCE(active_weight, 0)), 1) AS total_pts
        FROM fct_player_daily_performance
        WHERE {league_predicate()} AND game_date IS NOT NULL
        GROUP BY team_id
    """)


def get_season_gameplay_days():
    """League gameplay days per season (distinct attributed game dates)
    -- the 'standard season' clock (Kyle 2026-07-17 round 3): N = the
    median CLOSED season's days, and every season weighs days/N
    season-equivalents. 2020 (~a third of a season) and the year in
    flight count exactly what they played; an accidental short season
    (late draft, a missed Japan opener) self-reports short instead of
    diluting a per-season average."""
    return query_snowflake(f"""
        SELECT season_year, COUNT(DISTINCT game_date) AS days
        FROM fct_cbs_player_game_attribution
        WHERE {league_predicate()} AND franchise_id <> {_CBS_SENTINEL_FID}
        GROUP BY season_year
    """)


def get_mlb_affinity(season_year):
    """Active-lineup INVOLVEMENT with each MLB club per franchise -- the
    affinity-chart substrate. Weighted by PA + BF (Kyle 2026-07-17 round
    10: pure games-played underweights pitchers ~5:1): a hitting row
    contributes plate appearances (AB+BB+HBP+SF), a pitching row batters
    faced (outs+H+BB -- pitcher-HBP isn't priced at game grain, a
    negligible undercount), each active-weighted (estimated-era rows
    count by start share). The old per-game MAX(weight) dedup died with
    the switch: a two-way or pitcher-batting game now legitimately ADDS
    its PA and its BF. The MLB team-of-game lives only in the gamelog
    layer, so this joins the attribution fact back to
    int_cbs__player_game_points on the engine's own game key (mart
    promotion is a candidate follow-up). Names label as the club's
    latest-era name (Expos rows read Nationals; the id is the spine)."""
    return query_snowflake(f"""
        WITH involvement AS (
            SELECT a.franchise_id AS team_id,
                   g.team_id AS mlb_team_id,
                   MAX(g.team_name) AS mlb_team_name,
                   a.season_year,
                   SUM((CASE WHEN g.stat_group = 'hitting'
                             THEN COALESCE(g.ab, 0) + COALESCE(g.bb, 0)
                                  + COALESCE(g.hbp, 0) + COALESCE(g.sf, 0)
                             ELSE COALESCE(g.outs, 0) + COALESCE(g.ha, 0)
                                  + COALESCE(g.bbi, 0) END)
                       * COALESCE(a.active_weight, 0)) AS wt
            FROM fct_cbs_player_game_attribution a
            JOIN int_cbs__player_game_points g
              ON  a.league_key = g.league_key
              AND a.cbs_player_id = g.cbs_player_id
              AND a.stat_group = g.stat_group
              AND a.game_date = g.game_date
              AND a.game_pk = g.game_pk
              AND a.game_index = g.game_index
            WHERE {league_predicate('a')}
              AND a.franchise_id <> {_CBS_SENTINEL_FID}
            GROUP BY a.franchise_id, g.team_id, a.season_year
        )
        , latest_names AS (
            -- League-wide latest-era name per club: without this, a
            -- franchise whose last Expos-era game was 2004 would label
            -- the club differently than one that faced the Nationals.
            SELECT mlb_team_id,
                   MAX_BY(mlb_team_name, season_year) AS mlb_team_name
            FROM involvement
            GROUP BY mlb_team_id
        )
        SELECT i.team_id, i.mlb_team_id, n.mlb_team_name,
               ROUND(SUM(CASE WHEN i.season_year = {int(season_year)}
                              THEN i.wt ELSE 0 END), 1) AS season_wt,
               ROUND(SUM(i.wt), 1) AS alltime_wt
        FROM involvement i
        JOIN latest_names n ON n.mlb_team_id = i.mlb_team_id
        GROUP BY i.team_id, i.mlb_team_id, n.mlb_team_name
    """)


def get_acquisition_channels_alltime(last_closed_season):
    """Historic (through the last CLOSED season) production by acquisition
    channel, both lenses -- the walk-back-era half of the all-time
    acquisition table (Kyle 2026-07-17 round 6). Where the season table
    reads the raw transaction log, this reads int_cbs__roster_stints --
    the engine already resolved the historic log's warts (void trades,
    truncations, suffix splits), so channels come from each stint's
    open_channel (lineup_opening / lineup_evidence collapse into
    OPENING: recovered starts, no logged acquisition) and a game credits
    the stint that holds its date. Lost stays SEASON-BOUNDED: a
    drop/trade_out's window runs to the player's next stint with the
    same franchise that season, else season end -- decades of a dropped
    prospect's career never count. The builder sums these rows with the
    current-season query's for the all-time blocks."""
    yr = int(last_closed_season)
    return query_snowflake(f"""
        WITH attr AS (
            SELECT cbs_player_id, stat_group, season_year, game_date,
                   game_pk, game_index, franchise_id,
                   COALESCE(active_weight, 0) AS w,
                   COALESCE(calculated_fpts, 0) AS fpts
            FROM fct_cbs_player_game_attribution
            WHERE {league_predicate()} AND season_year <= {yr}
              AND franchise_id <> {_CBS_SENTINEL_FID}
        ),
        stints AS (
            SELECT franchise_id, resolved_cbs_player_id AS pid, season_year,
                   stint_start,
                   COALESCE(stint_end, DATE '9999-12-31') AS stint_end,
                   CASE open_channel
                        WHEN 'add' THEN 'add'
                        WHEN 'trade_in' THEN 'trade_in'
                        ELSE 'opening' END AS channel,
                   close_type
            FROM int_cbs__roster_stints
            WHERE {league_predicate()} AND season_year <= {yr}
        ),
        channeled AS (
            SELECT a.franchise_id, a.w, a.fpts,
                   COALESCE(s.channel, 'opening') AS channel
            FROM attr a
            LEFT JOIN stints s
              ON s.pid = a.cbs_player_id
             AND s.franchise_id = a.franchise_id
             AND s.season_year = a.season_year
             AND a.game_date >= s.stint_start
             AND a.game_date < s.stint_end
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY a.cbs_player_id, a.stat_group, a.season_year,
                             a.game_date, a.game_pk, a.game_index,
                             a.franchise_id
                ORDER BY s.stint_start DESC NULLS LAST) = 1
        ),
        acquired AS (
            SELECT franchise_id,
                   ROUND(SUM(CASE WHEN channel = 'opening' THEN fpts * w END), 1)   AS opening_active_pts,
                   ROUND(SUM(CASE WHEN channel = 'add' THEN fpts * w END), 1)       AS fa_add_active_pts,
                   ROUND(SUM(CASE WHEN channel = 'trade_in' THEN fpts * w END), 1)  AS trade_active_pts,
                   ROUND(SUM(fpts * w), 1)                                          AS acquired_active_pts,
                   ROUND(SUM(CASE WHEN channel = 'opening' THEN fpts END), 1)       AS opening_rostered_pts,
                   ROUND(SUM(CASE WHEN channel = 'add' THEN fpts END), 1)           AS fa_add_rostered_pts,
                   ROUND(SUM(CASE WHEN channel = 'trade_in' THEN fpts END), 1)      AS trade_rostered_pts,
                   ROUND(SUM(fpts), 1)                                              AS acquired_rostered_pts
            FROM channeled
            GROUP BY franchise_id
        ),
        dep_windows AS (
            SELECT franchise_id, pid, season_year, close_type,
                   stint_end AS dep_date,
                   COALESCE(MIN(stint_start) OVER (
                       PARTITION BY franchise_id, pid, season_year
                       ORDER BY stint_start
                       ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING),
                       DATE_FROM_PARTS(season_year, 12, 31)) AS window_end
            FROM stints
        ),
        departures AS (
            SELECT * FROM dep_windows
            WHERE close_type IN ('drop', 'trade_out')
        ),
        lost_franchise AS (
            SELECT d.franchise_id, d.close_type,
                   SUM(a.fpts * a.w) AS lost_active,
                   SUM(a.fpts) AS lost_rostered
            FROM departures d
            JOIN attr a
              ON a.cbs_player_id = d.pid
             AND a.season_year = d.season_year
             AND a.franchise_id <> d.franchise_id
             AND a.game_date >= d.dep_date
             AND a.game_date < d.window_end
            GROUP BY d.franchise_id, d.close_type
        ),
        unowned AS (
            SELECT g.cbs_player_id, g.season_year, g.game_date,
                   SUM(g.calculated_fpts) AS fpts
            FROM int_cbs__player_game_points g
            LEFT JOIN fct_cbs_player_game_attribution a
              ON a.league_key = g.league_key
             AND a.cbs_player_id = g.cbs_player_id
             AND a.stat_group = g.stat_group
             AND a.game_date = g.game_date
             AND a.game_pk = g.game_pk
             AND a.game_index = g.game_index
            WHERE {league_predicate('g')} AND g.season_year <= {yr}
              AND a.cbs_player_id IS NULL
            GROUP BY g.cbs_player_id, g.season_year, g.game_date
        ),
        lost_unowned AS (
            SELECT d.franchise_id, d.close_type, SUM(u.fpts) AS lost_unowned
            FROM departures d
            JOIN unowned u
              ON u.cbs_player_id = d.pid
             AND u.season_year = d.season_year
             AND u.game_date >= d.dep_date
             AND u.game_date < d.window_end
            GROUP BY d.franchise_id, d.close_type
        ),
        lost AS (
            SELECT COALESCE(f.franchise_id, u.franchise_id) AS franchise_id,
                   COALESCE(f.close_type, u.close_type) AS close_type,
                   COALESCE(f.lost_active, 0) AS lost_active,
                   COALESCE(f.lost_rostered, 0)
                       + COALESCE(u.lost_unowned, 0) AS lost_rostered
            FROM lost_franchise f
            FULL OUTER JOIN lost_unowned u
              ON f.franchise_id = u.franchise_id
             AND f.close_type = u.close_type
        ),
        lost_pivot AS (
            SELECT franchise_id,
                   ROUND(SUM(CASE WHEN close_type = 'drop' THEN lost_active END), 1)          AS dropped_active_pts,
                   ROUND(SUM(CASE WHEN close_type = 'trade_out' THEN lost_active END), 1)     AS traded_away_active_pts,
                   ROUND(SUM(lost_active), 1)                                                 AS lost_active_pts,
                   ROUND(SUM(CASE WHEN close_type = 'drop' THEN lost_rostered END), 1)        AS dropped_rostered_pts,
                   ROUND(SUM(CASE WHEN close_type = 'trade_out' THEN lost_rostered END), 1)   AS traded_away_rostered_pts,
                   ROUND(SUM(lost_rostered), 1)                                               AS lost_rostered_pts
            FROM lost
            GROUP BY franchise_id
        )
        SELECT a.franchise_id AS team_id,
               COALESCE(a.opening_active_pts, 0)   AS opening_active_pts,
               COALESCE(a.fa_add_active_pts, 0)    AS fa_add_active_pts,
               COALESCE(a.trade_active_pts, 0)     AS trade_active_pts,
               COALESCE(a.acquired_active_pts, 0)  AS acquired_active_pts,
               COALESCE(l.dropped_active_pts, 0)   AS dropped_active_pts,
               COALESCE(l.traded_away_active_pts, 0) AS traded_away_active_pts,
               COALESCE(l.lost_active_pts, 0)      AS lost_active_pts,
               COALESCE(a.opening_rostered_pts, 0)  AS opening_rostered_pts,
               COALESCE(a.fa_add_rostered_pts, 0)   AS fa_add_rostered_pts,
               COALESCE(a.trade_rostered_pts, 0)    AS trade_rostered_pts,
               COALESCE(a.acquired_rostered_pts, 0) AS acquired_rostered_pts,
               COALESCE(l.dropped_rostered_pts, 0)  AS dropped_rostered_pts,
               COALESCE(l.traded_away_rostered_pts, 0) AS traded_away_rostered_pts,
               COALESCE(l.lost_rostered_pts, 0)     AS lost_rostered_pts
        FROM acquired a
        LEFT JOIN lost_pivot l ON l.franchise_id = a.franchise_id
    """)


def get_season_records():
    wanted = ", ".join(f"'{s}'" for s in
                       _RECORDS_POINTS + _RECORDS_HITTING + _RECORDS_PITCHING)
    return query_snowflake(
        f"SELECT stat_name, display_name, rank, player_name, season_year,"
        f"       stat_value"
        f" FROM mart_player_season_records"
        f" WHERE {league_predicate()} AND stat_name IN ({wanted})"
        f" ORDER BY stat_name, rank"
    )


def get_career_records():
    wanted = ", ".join(f"'{s}'" for s in
                       _RECORDS_POINTS + _RECORDS_HITTING + _RECORDS_PITCHING)
    return query_snowflake(
        f"SELECT stat_name, display_name, rank, player_name, seasons_played,"
        f"       first_season, last_season, stat_value"
        f" FROM mart_player_career_records"
        f" WHERE {league_predicate()} AND stat_name IN ({wanted})"
        f" ORDER BY stat_name, rank"
    )


# ---------------------------------------------------------------------------
# Records v2 -- the auto-cataloged, ESPN-shaped record book
# ---------------------------------------------------------------------------

def get_cbs_record_catalog():
    """AUTO-CATALOG (Kyle 2026-07-13): the records to display, derived from
    what the league SCORES -- dim_stat.is_record_candidate joined to CBS's
    scoring settings, plus dim_stat.auto_tracked overrides -- intersected
    with the union-fact-carryable stats (the scored set). Returns
    {stat_name: {display_name, stat_category, polarity}} so a league that
    scores different categories catalogs different records from the same
    code. Points records are handled as their own section, not here."""
    # Key on stat_name, not leaderboard_name: the union fact and _REC_STAT_COL
    # both identify a stat by stat_name (2B/3B/HR/...), whereas leaderboard_name
    # diverges for some (2B->DOUBLES, 3B->TRIPLES) and would silently drop them.
    carryable = ", ".join(f"'{n}'" for n in _REC_STAT_COL)
    rows = query_snowflake(f"""
        SELECT DISTINCT d.stat_name, d.display_name,
               d.stat_category, d.polarity
        FROM dim_stat d
        LEFT JOIN stg_cbs__scoring_settings s
            ON s.canonical_key = d.canonical_key
            AND {league_predicate('s')}
        WHERE d.is_record_candidate
          AND d.stat_name IN ({carryable})
          AND (s.canonical_key IS NOT NULL OR d.auto_tracked)
    """)
    return {r['stat_name']: r for r in rows}


def _rec_agg(group_cols, extra_selects=''):
    """Wide per-group SUM of every recordable stat + points column, over the
    attributed CBS union fact, ACTIVE-WEIGHTED (Kyle 2026-07-13: the
    'real baseball league' lens -- production only counts while the player
    was actively started; the 2004-2020 estimated era weights fractionally).
    group_cols sets the grain (season+team, season+player, season+team+
    player)."""
    cols = ", ".join(
        f'ROUND(SUM({c} * COALESCE(active_weight, 0)), 1) AS "{n}"'
        for n, c in {**_REC_STAT_COL, **_REC_RATE_COL, **_REC_POINTS_COL}.items())
    return query_snowflake(f"""
        SELECT {group_cols}{extra_selects}, {cols}
        FROM fct_player_daily_performance
        WHERE {league_predicate()} AND game_date IS NOT NULL
        GROUP BY {group_cols}
    """)


def _franchise_owner_labels():
    """franchise_id -> {abbrev, owner}. abbrev is the record Holder for team
    records; owner is the Owner column. MLB-64 re-key: bridges renames/re-ids
    through dim_franchise's CANONICAL identity instead of the old abbrev hack
    (which false-linked 14/17 -- two DISTINCT 'Bent Spokes' franchises), and
    reads the real per-lineage owner from dim_team_owner's now-full history --
    so a defunct id like 14 shows Gideon Osborn (its actual 2008 owner), not a
    BENT-inherited one. Every raw id maps to its canonical franchise's abbrev +
    latest owner. Multi-owner names join with ' & ' (a comma read as
    'Last, First'). (Per-SEASON owner on a season record is the next refinement,
    for the team-pages session.)"""
    rows = query_snowflake(f"""
        WITH canon_owner AS (
            SELECT df.canonical_franchise_id AS cid,
                   MAX(df.canonical_abbrev)  AS abbrev,
                   MAX_BY(o.owner_display, o.season_year) AS owner
            FROM dim_franchise df
            LEFT JOIN dim_team_owner o
                ON df.league_key = o.league_key AND df.franchise_id = o.team_id
            WHERE {league_predicate('df')}
            GROUP BY df.canonical_franchise_id
        )
        SELECT df.franchise_id, c.abbrev, c.owner
        FROM dim_franchise df
        JOIN canon_owner c ON df.canonical_franchise_id = c.cid
        WHERE {league_predicate('df')}
    """)
    return {int(r['franchise_id']):
            {'abbrev': r['abbrev'],
             'owner': (r['owner'] or '').replace(', ', ' & ')}
            for r in rows}


def _rec_fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def get_cbs_records_data():
    """Assemble every record leader in one active-weighted pass over the
    union fact, in the ESPN two-scope shape: for each stat, the best
    single SEASON and the best ALL-TIME TOTAL (career accumulation), at
    both player and team grain, with the holder's owner and -- for team
    records -- the contributing players. 'Season' is ESPN's current-season
    column re-aimed at best-single-season; 'All-Time Total' is the career
    axis this deep-history league leans on."""
    stat_names = list(_REC_STAT_COL) + list(_REC_RATE_COL) + list(_REC_POINTS_COL)
    owner_label = _franchise_owner_labels()

    team_season = _rec_agg('season_year, team_id', ', MAX(team_abbrev) AS team_abbrev')
    # FENCE the sentinel holding-pen franchise (9999, '####') out of every TEAM
    # aggregation: it holds the 2001-2002 zero-event stars at 100% and would
    # otherwise landslide the best/worst team records. Its players still count
    # in PLAYER records (player_season is franchise-blind) and its stint drives
    # their main_team below, so it stays in player_team_season.
    team_season = [r for r in team_season
                   if _rec_fnum(r.get('team_id')) != _CBS_SENTINEL_FID]
    player_season = _rec_agg(
        'season_year, player_key',
        ', MAX(display_name) AS display_name, MAX(player_name) AS player_name')
    player_team_season = _rec_agg(
        'season_year, team_id, player_key',
        ', MAX(display_name) AS display_name')

    def _fid(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    def _owner(fid):
        f = _fid(fid)
        return owner_label.get(f, {}).get('owner', '') if f is not None else ''

    def _abbrev(fid):
        f = _fid(fid)
        return owner_label.get(f, {}).get('abbrev') or (f'#{f}' if f is not None else '')

    # A player's main franchise per season = the team they earned the most
    # active points with (drives the player-record Owner column).
    main_team = {}
    for r in player_team_season:
        k = (r['season_year'], r['player_key'])
        p = _rec_fnum(r.get('calculated_points'))
        if k not in main_team or p > main_team[k][1]:
            main_team[k] = (r.get('team_id'), p)
    pname = {r['player_key']: (r.get('display_name'), r.get('player_name'))
             for r in player_season}

    # Careers: active-weighted sums over seasons, per entity, with span.
    def _careers(rows, idk):
        acc = {}
        for r in rows:
            eid = r.get(idk)
            if eid is None:
                continue
            a = acc.setdefault(eid, {'seasons': set()})
            a['seasons'].add(int(r['season_year']))
            for s in stat_names:
                a[s] = a.get(s, 0.0) + _rec_fnum(r.get(s.lower()))
        return acc
    # Career TEAM records: currently-active franchises only, keyed by ABBREV so
    # a franchise's re-registrations (FULT 13 + 30) combine into one career
    # (Kyle item 6.1). Season records stay fid-grained; sentinel already fenced.
    _abbrev_of = {f: m['abbrev'] for f, m in owner_label.items()}
    active_fids = {int(r['team_id']) for r in query_snowflake(
        f"SELECT DISTINCT team_id FROM stg_cbs__rosters WHERE {league_predicate()}"
        f" AND roster_date = (SELECT MAX(roster_date) FROM stg_cbs__rosters"
        f"                    WHERE {league_predicate()})")}
    active_abbrevs = {_abbrev_of[f] for f in active_fids if f in _abbrev_of}
    owner_by_abbrev = {m['abbrev']: m['owner'] for m in owner_label.values() if m['owner']}
    for r in team_season:
        r['_abbrev'] = _abbrev_of.get(_fid(r.get('team_id')))
    for r in player_team_season:
        r['_abbrev'] = _abbrev_of.get(_fid(r.get('team_id')))
    team_career = _careers(
        [r for r in team_season if r.get('_abbrev') in active_abbrevs], '_abbrev')
    player_career = _careers(player_season, 'player_key')

    # Negative Records eligibility. Three artifacts would otherwise own every
    # 'fewest points' line, none of them actual futility: (1) short/anomalous
    # seasons where the whole league scored less -- the 2020 COVID 60-gamer,
    # the 2001-2002 coin-flip era; (2) under-attributed team-seasons (a partly
    # reconstructed roster); (3) short-lived franchises, trivially lowest on
    # any career SUM (longevity, not futility). So worst-SEASON is gated to
    # full-length seasons (season max team-total within 60% of the median) AND
    # roster-complete team-seasons; worst-CAREER is dropped (no honest
    # single-number analog). The season gate self-heals as Track B rebuilds
    # the early era, with no per-year hardcoding.
    _ROSTER_FLOOR = 20
    _rsize = {}
    for r in player_team_season:
        _rsize.setdefault((r['season_year'], r.get('team_id')), set()).add(r['player_key'])
    _season_max = {}
    for r in team_season:
        s = int(r['season_year'])
        _season_max[s] = max(_season_max.get(s, 0.0), _rec_fnum(r.get('calculated_points')))
    _maxes = sorted(_season_max.values())
    _median_max = _maxes[len(_maxes) // 2] if _maxes else 0.0
    _full_len = {s for s, m in _season_max.items() if m >= 0.6 * _median_max}
    # Completed seasons only: the live season is half-played, so its trailing
    # teams are trivially low. Year-end standings exist only for closed seasons.
    _closed = {int(r['season_year']) for r in query_snowflake(
        f"SELECT DISTINCT season_year FROM stg_cbs__ui_standings"
        f" WHERE {league_predicate()}")}
    # Attribution-complete seasons only: a team's total is only its 'fewest
    # points' worth if the WHOLE roster is attributed to it. The no-anchor era
    # (2001-2002) still has its zero-event draft-and-hold stars unplaced (the
    # Track B backfill), so those team totals under-count and would trivially
    # own every worst line. Gate on anchor presence -- self-healing: the
    # moment the backfill lands 2001-2002 anchors, those seasons qualify.
    _anchored = {int(r['season_year']) for r in query_snowflake(
        f"SELECT DISTINCT season_year FROM stg_cbs__ui_rosters"
        f" WHERE {league_predicate()}")}
    team_season_complete = [
        r for r in team_season
        if int(r['season_year']) in _full_len
        and int(r['season_year']) in _closed
        and int(r['season_year']) in _anchored
        and len(_rsize.get((r['season_year'], r.get('team_id')), ())) >= _ROSTER_FLOOR]

    def _best_row(rows, col):
        best = None
        for r in rows:
            v = _rec_fnum(r.get(col))
            if v > 0 and (best is None or v > _rec_fnum(best.get(col))):
                best = r
        return best

    def _best_career(acc, stat):
        best = None
        for eid, a in acc.items():
            v = a.get(stat, 0.0)
            if v > 0 and (best is None or v > best[1].get(stat, 0.0)):
                best = (eid, a)
        return best

    def _best_career_avg(acc, stat):
        """All-Time TEAM records ranked by COMPLETED-SEASON AVERAGE (Kyle
        2026-07-15), not raw total -- the total just tallies who's been in the
        league longest. avg = total / seasons played, the current season riding
        in as a rolling partial; min 1 completed (closed) season to qualify.
        (acc is already abbrev-keyed to active franchises.)"""
        best, best_avg = None, 0.0
        for eid, a in acc.items():
            if not (a['seasons'] & _closed):        # need >=1 completed season
                continue
            n = len(a['seasons'])
            avg = a.get(stat, 0.0) / n if n else 0.0
            if avg > 0 and (best is None or avg > best_avg):
                best, best_avg = (eid, a), avg
        return best

    # Negative Records (Kyle 2026-07-13): the futility mirror of the best
    # block -- the fewest points in a completed full season. Team grain only
    # (a career SUM just measures longevity), over the gated season set built
    # below.
    def _worst_row(rows, col):
        worst = None
        for r in rows:
            v = _rec_fnum(r.get(col))
            if worst is None or v < _rec_fnum(worst.get(col)):
                worst = r
        return worst

    # A player's season/career stat-line detail (ESPN shows one on every
    # player record): the top marquee counting stats they posted, most first.
    def _player_line(statvals):
        picks = []
        for s in _STAT_LINE_ORDER:
            v = statvals.get(s, 0.0)
            if s == 'OUTS':
                v = v / 3.0                     # display as innings pitched
            if v >= 1:
                picks.append((_STAT_LINE_LABELS[s], v))
        picks.sort(key=lambda t: -t[1])
        return ', '.join(f'{int(round(v))} {lbl}' for lbl, v in picks[:3])

    def _contribs(rows_filter, col):
        agg = {}
        for r in rows_filter:
            v = _rec_fnum(r.get(col))
            if v <= 0:
                continue
            k = r['player_key']
            nm, tot = agg.get(k, (r.get('display_name'), 0.0))
            agg[k] = (nm, tot + v)
        return sorted(agg.values(), key=lambda t: -t[1])[:3]

    def _player_team_list(pk, stat, col):
        """An all-time PLAYER record's Details = where he earned it: franchises
        named with their totals, top-first. Beyond the top 3, a LONE remaining
        team is still named (Kyle: never bucket a single team); 2+ collapse to
        '[N spelled out] other teams: [remaining]'. The #### sentinel counts as
        an owner here (unknown-team 2001-02 production) -- it's fenced only from
        TEAM records -- so the breakdown reconciles to the headline value."""
        agg = {}
        for r in player_team_season:
            if r['player_key'] != pk:
                continue
            ab = r.get('_abbrev')
            if not ab:
                continue
            v = _rec_fnum(r.get(col))
            if v > 0:
                agg[ab] = agg.get(ab, 0.0) + v
        ranked = sorted(agg.items(), key=lambda t: -t[1])
        head, rest = ranked[:3], ranked[3:]
        if len(rest) == 1:                      # name a lone extra, don't bucket 1
            head, rest = ranked[:4], []
        parts = [f'{ab}: {_rec_value(stat, v)}' for ab, v in head]
        if rest:
            rem = sum(v for _, v in rest)
            parts.append(f'{_spell(len(rest))} other teams: {_rec_value(stat, rem)}')
        # Owner (Kyle 2026-07-15): the NAMED franchises comma-joined WITHOUT the
        # numbers -- mirrors Details and fills the otherwise-blank player-record
        # Owner cell (Verlander -> 'FLV, JUNK, KCM').
        owner = ', '.join(ab for ab, _ in head)
        return ', '.join(parts), owner

    def _season_statvals(row):
        return {s: _rec_fnum(row.get(s.lower())) for s in stat_names}

    def _team_side(row, col):
        """A team record's 5-cell payload for one season row."""
        return {
            'holder': row.get('team_abbrev') or '',
            'owner': _owner(row.get('team_id')),
            'value': _rec_fnum(row.get(col)), 'period': _num(row.get('season_year')),
            'year': _fid(row.get('season_year')),
            'details': _contribs(
                [r for r in player_team_season
                 if r['season_year'] == row['season_year']
                 and r.get('team_id') == row.get('team_id')], col),
        }

    def _team_career_side(entry, stat, col):
        ab, a = entry   # career is abbrev-keyed (active franchises only)
        n = len(a['seasons'])
        avg = a.get(stat, 0.0) / n if n else 0.0   # completed-season average
        return {
            'holder': ab, 'owner': owner_by_abbrev.get(ab, ''),
            'value': avg,
            # Averages want a decimal; _rec_value would round to whole. OUTS
            # renders as its IP average.
            'value_disp': fmt_ip(avg) if stat == 'OUTS' else f'{avg:,.1f}',
            'period': _span_from_years(a['seasons']),
            'last_season': max(a['seasons']),
            'details': _contribs(   # Details stay TOTALS, not averages (Kyle)
                [r for r in player_team_season if r.get('_abbrev') == ab], col),
        }

    data = {}
    for stat in stat_names:
        col = stat.lower()
        # season-scope leaders
        bts = _best_row(team_season, col)
        bps = _best_row(player_season, col)
        season_team = _team_side(bts, col) if bts else None
        season_player = None
        if bps:
            mt = main_team.get((bps['season_year'], bps['player_key']), (None, 0))
            season_player = {
                'display_name': bps.get('display_name'),
                'player_name': bps.get('player_name'),
                'value': _rec_fnum(bps.get(col)),
                'owner': _owner(mt[0]), 'period': _num(bps.get('season_year')),
                'year': _fid(bps.get('season_year')),
                'details': _player_line(_season_statvals(bps)),
            }
        # career-scope leaders. TEAM = completed-season AVERAGE (Kyle); PLAYER
        # stays a raw career total.
        btc = _best_career_avg(team_career, stat)
        bpc = _best_career(player_career, stat)
        career_team = _team_career_side(btc, stat, col) if btc else None
        career_player = None
        if bpc:
            pk, a = bpc
            nm = pname.get(pk, (None, None))
            _det, _own = _player_team_list(pk, stat, col)
            career_player = {
                'display_name': nm[0], 'player_name': nm[1],
                'value': a.get(stat, 0.0), 'owner': _own,
                'period': _span_from_years(a['seasons']),
                'last_season': max(a['seasons']),
                'details': _det,
            }
        # worst-scope leader (Negative Records; single SEASON, roster-complete
        # post-coin-flip only). Career-worst is intentionally omitted.
        wts = _worst_row(team_season_complete, col)
        worst_team_season = _team_side(wts, col) if wts else None
        worst_team_career = None
        data[stat] = {
            'season_team': season_team, 'season_player': season_player,
            'career_team': career_team, 'career_player': career_player,
            'worst_team_season': worst_team_season,
            'worst_team_career': worst_team_career,
        }

    # ---- Rate-stat records (reuse the ESPN rate helpers; MLB-80 thresholds).
    def _career_rows(acc):
        rows_ = []
        for eid, a in acc.items():
            row = {k.lower(): v for k, v in a.items() if k != 'seasons'}
            row['_eid'] = eid
            row['_seasons'] = a['seasons']
            rows_.append(row)
        return rows_
    team_career_rows = _career_rows(team_career)
    player_career_rows = _career_rows(player_career)

    for key, _label, cat, higher in _RATE_SPECS:
        qc_s, qm_s = _RATE_QUAL[(cat, True)]
        qc_c, qm_c = _RATE_QUAL[(cat, False)]
        bps, dsp = _best_rate(player_season, key, higher, qc_s, qm_s)
        rate_sp = None
        if bps:
            mt = main_team.get((bps['season_year'], bps['player_key']), (None, 0))
            rate_sp = {'display_name': bps.get('display_name'),
                       'player_name': bps.get('player_name'), 'value': dsp,
                       'owner': _owner(mt[0]), 'period': _num(bps.get('season_year')),
                       'details': _rate_component_detail(bps, key), 'is_rate': True}
        bts, dst = _best_rate(team_season, key, higher, qc_s, qm_s)
        rate_st = None
        if bts:
            rate_st = {'holder': bts.get('team_abbrev') or '',
                       'owner': _owner(bts.get('team_id')), 'value': dst,
                       'period': _num(bts.get('season_year')),
                       'details': _rate_component_detail(bts, key), 'is_rate': True}
        bpc, dcp = _best_rate(player_career_rows, key, higher, qc_c, qm_c)
        rate_cp = None
        if bpc:
            nm = pname.get(bpc['_eid'], (None, None))
            rate_cp = {'display_name': nm[0], 'player_name': nm[1], 'value': dcp,
                       'owner': '', 'period': _span_from_years(bpc['_seasons']),
                       'details': _rate_component_detail(bpc, key), 'is_rate': True}
        btc, dct = _best_rate(team_career_rows, key, higher, qc_c, qm_c)
        rate_ct = None
        if btc:
            rate_ct = {'holder': btc['_eid'], 'owner': owner_by_abbrev.get(btc['_eid'], ''),
                       'value': dct, 'period': _span_from_years(btc['_seasons']),
                       'details': _rate_component_detail(btc, key), 'is_rate': True}
        data[key] = {'season_team': rate_st, 'season_player': rate_sp,
                     'career_team': rate_ct, 'career_player': rate_cp,
                     'worst_team_season': None, 'worst_team_career': None}

    # ---- Franchise Hall of Fame (Kyle 2026-07-14): top 25 (player × franchise)
    # career ACTIVE points -- a player's run WITH one team, not his whole career.
    # Keyed by abbrev (re-registrations combine); the #### holding pen excluded.
    # Slash | Stat Line for a player's run with a franchise (Kyle 2026-07-15):
    # discipline picks hitting (AVG/OBP/SLG) vs pitching (ERA/WHIP) slash, then
    # the marquee counting line -- both through the same ESPN helpers the rate
    # records use, so it reads identically.
    _HOF_LINE_COLS = ['h', 'ab', 'b_bb', 'hbp', 'sf', 'tb', '2b', '3b', 'hr',
                      'r', 'rbi', 'sb', 'outs', 'er', 'p_h', 'p_bb', 'k',
                      'w', 'l', 'sv', 'hld', 'qs', 'cg']

    def _hof_line(agg):
        pitcher = _rec_fnum(agg.get('outs')) > _rec_fnum(agg.get('ab'))
        if pitcher:
            # Kyle 2026-07-15: pitcher slash leads with the W-L record, then
            # LABELED ERA / WHIP (a deliberate break from the usual bare slash).
            parts = [f"{int(round(_rec_fnum(agg.get('w'))))}W - "
                     f"{int(round(_rec_fnum(agg.get('l'))))}L"]
            era, whip = _rate_num_disp(agg, 'ERA')[1], _rate_num_disp(agg, 'WHIP')[1]
            if era:
                parts.append(f"{era} ERA")
            if whip:
                parts.append(f"{whip} WHIP")
            slash = ' / '.join(parts)
        else:
            trip = [_rate_num_disp(agg, k)[1] for k in ('AVG', 'OBP', 'SLG')]
            slash = '/'.join(trip) if trip[0] else ''
        statvals = {s: _rec_fnum(agg.get(s.lower(), 0.0)) for s in _STAT_LINE_ORDER}
        return ' || '.join(p for p in (slash, _player_line(statvals)) if p)

    hof = {}
    for r in player_team_season:
        ab = r.get('_abbrev')
        if not ab or ab == '####':
            continue
        e = hof.setdefault((r['player_key'], ab),
                           {'abbrev': ab, 'pts': 0.0, 'seasons': set(),
                            'pk': r['player_key'], 'agg': {}})
        e['pts'] += _rec_fnum(r.get('calculated_points'))
        e['seasons'].add(int(r['season_year']))
        for c in _HOF_LINE_COLS:
            e['agg'][c] = e['agg'].get(c, 0.0) + _rec_fnum(r.get(c))
    for e in hof.values():
        nm = pname.get(e['pk'], (None, None))
        e['display_name'], e['player_name'] = nm[0], nm[1]
        e['span'] = len(e['seasons'])   # Kyle 2026-07-15: just the count, centered
        e['statline'] = _hof_line(e['agg'])
    data['_hof'] = sorted(hof.values(), key=lambda e: -e['pts'])[:25]

    # ---- Lineup Slot Records (Kyle 2026-07-14): left = best player-SEASON by
    # active points at each slot; right = the active FRANCHISE with the most
    # all-time active points from that slot (abbrev-combined). 2004-2020 slots
    # are eligibility estimates (no lineup log) -- caveated at render.
    slot_rows = query_snowflake(f"""
        SELECT position, season_year, team_id, player_key,
               MAX(display_name) AS display_name,
               ROUND(SUM(weighted_active_pts), 1) AS pts
        FROM fct_player_position_pts
        WHERE {league_predicate()} AND weighted_active_pts IS NOT NULL
        GROUP BY position, season_year, team_id, player_key
    """)
    # LEFT ("Season") pool: eligibility-based per-position season points, all
    # years -- the best-lineup selector below optimizes over these.
    ps = {}
    for r in slot_rows:
        pos = r.get('position')
        if not pos:
            continue
        pts = _rec_fnum(r.get('pts'))
        pk, sy = r['player_key'], int(r['season_year'])
        e = ps.setdefault((pos, sy, pk),
                          {'pts': 0.0, 'name': r.get('display_name'), 'main': (None, 0.0)})
        e['pts'] += pts
        if pts > e['main'][1]:
            e['main'] = (_fid(r.get('team_id')), pts)

    # RIGHT ("All-Time Team Totals") side: ACTUAL lineup slots (Kyle 2026-07-15).
    # PITCHERS keep the eligibility model (a pitcher is ALWAYS in a P slot, so
    # every year is honest, estimated era included). HITTER slots use the real
    # lineup_slot -- but the league only logged specific hitter positions from
    # the 2026 daily capture on (2001-25 recorded 'active', not which slot), so
    # pre-2026 hitter slotting is zeroed rather than guessed.
    slot_actual_rows = query_snowflake(f"""
        SELECT lineup_slot AS position, season_year, team_id, player_key,
               MAX(display_name) AS display_name,
               ROUND(SUM(total_hitting_stat_pts * COALESCE(active_weight, 0)), 1)
                   AS pts
        FROM fct_player_daily_performance
        WHERE {league_predicate()} AND game_date IS NOT NULL
          AND lineup_slot IN ('C', '1B', '2B', '3B', 'SS', 'OF', 'DH', 'U')
        GROUP BY lineup_slot, season_year, team_id, player_key
    """)
    pf = {}   # (pos, abbrev) -> franchise all-time total at that slot

    def _add_pf(pos, sy, fid, pk, disp, pts):
        ab = _abbrev_of.get(fid)
        if not ab or ab == '####' or ab not in active_abbrevs or pts <= 0:
            return
        f = pf.setdefault((pos, ab), {'pts': 0.0, 'seasons': set(), 'contrib': {}})
        f['pts'] += pts
        f['seasons'].add(sy)
        cn, cp = f['contrib'].get(pk, (disp, 0.0))
        f['contrib'][pk] = (cn, cp + pts)

    for r in slot_rows:                       # pitchers: all years, as-is
        if r.get('position') != 'P':
            continue
        _add_pf('P', int(r['season_year']), _fid(r.get('team_id')),
                r['player_key'], r.get('display_name'), _rec_fnum(r.get('pts')))
    for r in slot_actual_rows:                # hitters: actual slot (2026-only)
        _add_pf(r['position'], int(r['season_year']), _fid(r.get('team_id')),
                r['player_key'], r.get('display_name'), _rec_fnum(r.get('pts')))

    # Franchise ranking per position -- the RIGHT ("All-Time Team Totals") side;
    # top-N feeds OF x3 / P x9. U is a REAL captured slot now (the 2026 daily
    # 'U' lineup_slot), so it ranks on its OWN actual-slot total like every
    # other slot -- NOT the old sum-of-every-hitter-position, which inflated the
    # single U slot ~7x (Kyle 2026-07-15).
    ranked_pf = {}
    for (pos, ab), f in pf.items():
        ranked_pf.setdefault(pos, []).append((ab, f))
    for lst in ranked_pf.values():
        lst.sort(key=lambda t: -t[1]['pts'])

    # The LEFT ("Season") side is a true OPTIMIZE-LINEUP over all-time player-
    # SEASONS (Kyle 2026-07-14): the SAME selector the team pages use, but the
    # pool is individual seasons, each keyed as its own asset (player_key =
    # 'pk|season'). Repeat PLAYERS are fine (A-Rod's best 3B year AND his best
    # SS year), but no single season fills two slots -- so U is the best
    # REMAINING hitter, never an echo of the best OF. Hitters get U + DH
    # eligibility rows; pruned to the top per position for speed.
    season_pos = {}
    for (pos, sy, pk), e in ps.items():
        d = season_pos.setdefault((sy, pk),
                                  {'pos': {}, 'name': e['name'], 'main': (None, 0.0)})
        d['pos'][pos] = e['pts']
        if e['pts'] > d['main'][1]:
            d['main'] = e['main']
    raw_cands = []
    for (sy, pk), d in season_pos.items():
        base = {'player_key': f'{pk}|{sy}', 'player_id': f'{pk}|{sy}',
                'sy': sy, 'pk': pk, 'name': d['name'], 'main_fid': d['main'][0]}
        positions = set(d['pos'])
        if 'P' in positions:
            raw_cands.append({**base, 'position': 'P', 'position_pts': d['pos']['P']})
        hit = positions - {'P'}
        if hit:
            hit_pts = max(d['pos'][p] for p in hit)
            for p in hit:
                raw_cands.append({**base, 'position': p, 'position_pts': d['pos'][p]})
            for extra in ('U', 'DH'):        # every hitter is U- and DH-eligible
                if extra not in hit:
                    raw_cands.append({**base, 'position': extra, 'position_pts': hit_pts})
    by_pos = {}
    for c in raw_cands:
        by_pos.setdefault(c['position'], []).append(c)
    candidates = []
    for lst in by_pos.values():
        lst.sort(key=lambda c: -c['position_pts'])
        candidates.extend(lst[:40])          # a 19-slot fill never reaches deeper
    candidates.sort(key=lambda c: (c['position'], -c['position_pts']))
    slot_caps = {'C': 1, '1B': 1, '2B': 1, '3B': 1, 'SS': 1,
                 'OF': 3, 'U': 1, 'DH': 1, 'P': 9}
    opt_by_slot = {(r.get('lineup_slot'), r.get('slot_rank')): r
                   for r in get_optimal_team_selections(candidates, slot_caps)}

    season_statvals_by = {(int(r['season_year']), r['player_key']): _season_statvals(r)
                          for r in player_season}

    def _opt_season_cell(row):
        sy, pk = row['sy'], row['pk']
        nm = pname.get(pk, (None, None))
        return {'display_name': nm[0] or row.get('name'), 'player_name': nm[1],
                'value': row.get('position_pts'), 'owner': _owner(row.get('main_fid')),
                'period': sy, 'details': _player_line(season_statvals_by.get((sy, pk), {}))}

    def _slot_career_cell(ab, f):
        return {'holder': ab, 'owner': owner_by_abbrev.get(ab, ''),
                'value': f['pts'], 'period': _span_from_years(f['seasons']),
                'details': sorted(f['contrib'].values(), key=lambda t: -t[1])[:3]}

    slots = []
    for label, src, rank in _ROSTER_SLOTS:
        opt = opt_by_slot.get((src, rank + 1))
        pool_pf = ranked_pf.get(src, [])
        slots.append({
            'label': label, 'pos': 'DH' if src == 'U' else src,
            'season_player': _opt_season_cell(opt) if opt and opt.get('pk') is not None else None,
            'career_team': _slot_career_cell(*pool_pf[rank]) if rank < len(pool_pf) else None,
        })
    data['_slots'] = slots

    # ---- Wasted Hall of Shame (Kyle 2026-07-14): top 25 players by career
    # WASTED points -- unrostered (on the wire) OR benched (rostered, sat).
    # Built from the DAILY fact (the HoF's substrate): active = pts x weight,
    # benched = pts x (1 - weight) -- the estimator's complement covers
    # 2004-2020, so benched there is an estimate like active is. NOT from
    # fct_player_position_pts (its known-state active_pts column is empty for
    # the estimated era, and it full-credits every eligible position -- the
    # 2026-07-14 Verlander false-87%-unrostered lesson). Unrostered = record-
    # book career total minus everything attributed while rostered. Sentinel
    # (####) rows count as rostered/active but never as the shame franchise.
    hos_rows = query_snowflake(f"""
        SELECT player_key, team_id, MAX(display_name) AS display_name,
               ROUND(SUM(total_stat_pts * COALESCE(active_weight, 0)), 1) AS act,
               ROUND(SUM(total_stat_pts * (1 - COALESCE(active_weight, 0))), 1)
                   AS benched
        FROM fct_player_daily_performance
        WHERE {league_predicate()} AND game_date IS NOT NULL
        GROUP BY player_key, team_id
    """)
    total_by_pk = {r['cbs_player_id']: _rec_fnum(r['pts']) for r in query_snowflake(f"""
        SELECT cbs_player_id, SUM(stat_value) AS pts
        FROM int_cbs__player_season_stats
        WHERE {league_predicate()} AND stat_name = 'CALCULATED_POINTS'
        GROUP BY cbs_player_id
    """)}
    # Discipline split (Kyle 2026-07-15): the shame list runs Pitchers | Hitters
    # side by side. A player is a pitcher if his career pitching production
    # outweighs his hitting (two-way 900/901 pseudo-ids fall out cleanly).
    disc_pit = {r['player_key'] for r in query_snowflake(f"""
        SELECT player_key FROM fct_player_daily_performance
        WHERE {league_predicate()}
        GROUP BY player_key
        HAVING SUM(total_pitching_stat_pts) > SUM(total_hitting_stat_pts)""")}
    hos = {}
    for r in hos_rows:
        ab = _abbrev_of.get(_fid(r.get('team_id')))
        e = hos.setdefault(r['player_key'], {'name': r.get('display_name'),
                           'act': 0.0, 'inact': 0.0, 'bench_by': {}})
        e['act'] += _rec_fnum(r.get('act'))
        e['inact'] += _rec_fnum(r.get('benched'))
        if ab and ab != '####':
            e['bench_by'][ab] = e['bench_by'].get(ab, 0.0) + _rec_fnum(r.get('benched'))
    hos_list = []
    for pk, e in hos.items():
        total = total_by_pk.get(pk, e['act'] + e['inact'])
        unrostered = max(0.0, total - e['act'] - e['inact'])
        benched = e['inact']
        # Back to TRUE wasted (Kyle 2026-07-15): unrostered (on the wire) OR
        # benched. Ranking by benched alone just surfaced start-limited SPs;
        # wasted is the honest futility measure.
        wasted = unrostered + benched
        if wasted <= 0:
            continue
        shame = ''
        if e['bench_by']:
            shame_ab, shame_pts = max(e['bench_by'].items(), key=lambda kv: kv[1])
            if shame_pts > 0:
                shame = f"{shame_ab} ({int(round(shame_pts)):,})"
        pct = (wasted / total * 100) if total else 0.0
        nm = pname.get(pk, (None, None))
        hos_list.append({
            'display_name': nm[0] or e['name'], 'player_name': nm[1],
            'is_pitcher': pk in disc_pit, 'shame': shame, 'wasted': wasted,
            'details': (f"{int(round(unrostered)):,} unrostered · "
                        f"{int(round(benched)):,} benched · "
                        f"{int(round(e['act'])):,} active · "
                        f"{pct:.0f}% of career unused")})
    hos_list.sort(key=lambda e: -e['wasted'])
    data['_hos'] = {   # Pitchers | Hitters, each top 25 by wasted
        'pitchers': [e for e in hos_list if e['is_pitcher']][:25],
        'hitters': [e for e in hos_list if not e['is_pitcher']][:25],
    }
    return data


def _span_from_years(years):
    ys = sorted(int(y) for y in years)
    if not ys:
        return ''
    return str(ys[0]) if ys[0] == ys[-1] else f'{ys[0]}–{ys[-1]}'


def get_provenance_mix(entity_id=None):
    """How the roster states behind the numbers are known, as game-day
    counts per (season, provenance) -- the input to every fidelity label.
    League-wide when entity_id is None, else franchise-scoped. Season
    grain so the sentence can bucket by ERA (Kyle 2026-07-17)."""
    scope = f' AND {_entity_where(entity_id)}' if entity_id is not None else ''
    return query_snowflake(
        f"SELECT season_year, provenance, COUNT(*) AS n"
        f" FROM fct_player_daily_performance"
        f" WHERE {league_predicate()} AND provenance IS NOT NULL"
        f"   AND game_date IS NOT NULL{scope}"
        f" GROUP BY season_year, provenance"
    )


def get_stat_sources():
    """The Home 'Stat sources' table (Kyle, 2026-07-13): one row per
    provenance tier -- its season coverage (compressed to ranges), a
    human description, and its share of all attributed player-days. The
    tiers collapse the four walk-back provenance codes into the three
    lenses a reader cares about: captured live, reconstructed day-by-day,
    and estimated. Percentages come from the same mix the fidelity
    sentence uses, so the two always agree."""
    # The mix is (season, provenance) grain now -- aggregate to provenance.
    mix = {}
    for r in get_provenance_mix():
        mix[r['provenance']] = mix.get(r['provenance'], 0) + r['n']
    total = sum(mix.values()) or 1
    seasons = query_snowflake(
        f"SELECT season_year, provenance, COUNT(*) AS n"
        f" FROM fct_player_daily_performance"
        f" WHERE {league_predicate()} AND provenance IS NOT NULL"
        f"   AND game_date IS NOT NULL"
        f" GROUP BY 1, 2"
        f" QUALIFY ROW_NUMBER() OVER (PARTITION BY season_year"
        f"                           ORDER BY COUNT(*) DESC) = 1"
    )
    tier_of = {'captured': 'captured', 'reconstructed_day': 'reconstructed',
               'estimated_startshare': 'estimated',
               'estimated_membership': 'estimated'}
    tier_years = {}
    for r in seasons:
        tier_years.setdefault(tier_of.get(r['provenance']), []).append(r['season_year'])
    cap_start = query_snowflake(
        f"SELECT MIN(game_date) AS d FROM fct_player_daily_performance"
        f" WHERE {league_predicate()} AND provenance = 'captured'"
    )[0]['d']

    def pct(*codes):
        return round(100.0 * sum(mix.get(c, 0) for c in codes) / total)

    return [
        {'dates': f'From {_fmt_date(cap_start)}',
         'desc': "Collected live and verified against CBS's output.",
         'pct': pct('captured')},
        {'dates': _compress_years(tier_years.get('reconstructed', [])),
         'desc': 'Rostered Stats & Active Stats reconstructed on daily level.',
         'pct': pct('reconstructed_day')},
        {'dates': _compress_years(tier_years.get('estimated', [])),
         'desc': 'Rostered States reconstructed on daily level. Active Stats '
                 'estimated by year-end start share. See the Almanac User '
                 'Guide for the full method.',
         'pct': pct('estimated_startshare', 'estimated_membership')},
    ]


def _norm_name(s):
    """Lowercase, drop the two-way discipline suffix, trim -- the key for
    the current-roster name fallback."""
    return (s or '').lower().rsplit(' (', 1)[0].strip()


def get_current_rostered():
    """The currently-rostered player set (the 2026 capture of record) ->
    each player's CURRENT franchise abbrev + owner display. The all-time
    board reads this to answer 'is this player still active': if so, show
    their current team + owner; if not, they read as a retired career.

    Returns (by_key, by_name). by_key is the primary lookup (roster
    player_id). by_name is an UNAMBIGUOUS-name fallback for the id-split
    class: a rostered player whose all-time board identity is a ui-only
    synthetic id (their history) rather than their real roster id -- keying
    only that off player_key would wrongly read them as retired. Ambiguous
    names (a shared current-roster name) are excluded from the fallback to
    avoid a Will-Smith collision."""
    from collections import Counter
    rows = query_snowflake(f"""
        SELECT r.player_id            AS player_key,
               r.player_name          AS player_name,
               f.abbrev               AS abbrev,
               o.owner_display        AS owner
        FROM stg_cbs__rosters r
        LEFT JOIN cbs_franchises f
            ON r.league_key = f.league_key
            AND try_to_number(r.team_id) = f.franchise_id
        LEFT JOIN dim_team_owner o
            ON r.league_key = o.league_key
            AND try_to_number(r.team_id) = o.team_id
            AND o.season_year = r.season_year
        WHERE {league_predicate('r')}
          AND r.roster_date = (SELECT MAX(roster_date) FROM stg_cbs__rosters
                               WHERE {league_predicate()})
    """)
    by_key = {r['player_key']: r for r in rows}
    counts = Counter(_norm_name(r['player_name']) for r in rows)
    by_name = {_norm_name(r['player_name']): r for r in rows
               if counts[_norm_name(r['player_name'])] == 1}
    return by_key, by_name


def get_years_of_service(keys, entity_id=None):
    """Per player_key, the seasons with ANY active production -- a season
    counts if the player was actively started and produced nonzero active
    points, net-NEGATIVE seasons included (a bad season is still service,
    Kyle 2026-07-15). Scoped to a franchise for team pages, league-wide
    (entity_id None) for the all-time board. The renderer compresses these
    to the 'count: year-ranges' longevity string."""
    if not keys:
        return {}
    quoted = ", ".join("'%s'" % k.replace("'", "''") for k in keys)
    filters = [league_predicate(), f"player_key IN ({quoted})",
               "game_date IS NOT NULL"]
    if entity_id is not None:
        filters.append(_entity_where(entity_id))
    rows = query_snowflake(f"""
        SELECT player_key, season_year
        FROM fct_player_daily_performance
        WHERE {' AND '.join(filters)}
        GROUP BY player_key, season_year
        HAVING SUM(total_stat_pts * COALESCE(active_weight, 0)) <> 0
    """)
    out = {}
    for r in rows:
        out.setdefault(r['player_key'], []).append(r['season_year'])
    return out


def _synthesize_universal_slots(candidates):
    """DH and U are universal-fill SLOTS (everyone is DH-eligible; U is
    the utility slot), but the eligibility arrays deliberately carry only
    EARNED positions. Give every player with hitting production a DH and
    a U candidacy at their hitting-position points so the selector can
    fill those slots. Players whose only rows are P (pitcher-scoped
    identities) stay pitchers -- a pitcher never beats a hitter for DH/U
    anyway, and CBS's own cards show them as plain 'P'."""
    out = list(candidates)
    best_hitting = {}
    for c in candidates:
        if c['position'] in ('P',):
            continue
        key = c.get('player_key') or c['player_id']
        cur = best_hitting.get(key)
        if cur is None or (c['position_pts'] or 0) > (cur['position_pts'] or 0):
            best_hitting[key] = c
    have = {(c.get('player_key') or c['player_id'], c['position'])
            for c in candidates}
    for key, base in best_hitting.items():
        for slot in ('DH', 'U'):
            if (key, slot) not in have:
                clone = dict(base)
                clone['position'] = slot
                out.append(clone)
    # Selector contract: sorted by position then points DESC.
    out.sort(key=lambda c: (c['position'], -(c['position_pts'] or 0),
                            str(c.get('player_key') or c['player_id'])))
    return out


def get_best_lineup(entity_id=None, season_year=None,
                    points_type='weighted_active', bench=0):
    """The Best Lineup for a scope: candidates from the unified position
    fact -> the shared gap-based selector over the CBS slot template ->
    CBS enrichment. entity_id=None gives the league-wide All-League
    boards; season_year=None gives all-time. points_type
    'weighted_active' is the display lineup; 'rostered' builds the
    alternate lineup behind the Total-Pts Best deviation columns. bench>0
    appends that many reserve picks (the league's 11 reserve slots).

    Lens split (Kyle, 2026-07-13, universal with the team pages): STARTERS
    by ACTIVE points (weighted_active -- which INCLUDES the estimated
    active production from 2004-2020 start shares); BENCH by TOTAL
    (rostered) points, so a benched star's whole line counts."""
    candidates = get_optimal_team_candidates(
        season_year=season_year,
        team_id=entity_id,
        points_type=points_type,
    )
    candidates = _synthesize_universal_slots(candidates)
    lineup = get_optimal_team_selections(candidates, CBS_SLOT_CAPS)
    if bench:
        # Bench pool = TOTAL points (not the starters' active lens), and
        # UN-synthesized so each reserve carries a real position for its
        # "BE - Pos" label rather than a universal DH/U clone.
        bench_pool = get_optimal_team_candidates(
            season_year=season_year, team_id=entity_id, points_type='rostered')
        lineup = lineup + _select_bench(bench_pool, lineup, bench)
    _enrich_lineup(lineup, entity_id=entity_id, season_year=season_year)
    # Finalize bench labels after enrichment so the position reads from the
    # player's primary (their current-ish display position for actives,
    # their historical primary for retirees) rather than the arbitrary
    # tie-break among equal-value eligibility rows.
    for sel in lineup:
        if sel.get('lineup_slot') == 'BE':
            pos = sel.get('primary_position') or sel.get('_bench_pos') or ''
            sel['slot_label'] = f'BE - {pos}' if pos else 'BE'
            # Point the shared slash-line helper at the player's discipline
            # (it keys off lineup_slot: P -> W-L/ERA/WHIP, else AVG/OBP/SLG).
            # slot_label keeps the "BE - Pos" display; only the slash
            # discipline reads lineup_slot.
            sel['lineup_slot'] = pos or 'BE'
    return lineup


def _select_bench(candidates, starters, n):
    """The reserve block: the n best players NOT in the starting lineup, by
    TOTAL (rostered) points. CBS reserve slots are position-blind (11 of
    them), so this ranks whole players; each carries its best real
    position as the '_bench_pos' fallback for the BE - Pos label."""
    used = {s.get('player_key') or s.get('player_id') for s in starters}
    best = {}
    for c in candidates:
        key = c.get('player_key') or c['player_id']
        if key in used:
            continue
        cur = best.get(key)
        if cur is None or (c['position_pts'] or 0) > (cur['position_pts'] or 0):
            best[key] = c
    ranked = sorted(best.values(),
                    key=lambda c: (-(c['position_pts'] or 0),
                                   str(c.get('player_key') or c['player_id'])))[:n]
    bench = []
    for base in ranked:
        row = dict(base)
        row['lineup_slot'] = 'BE'
        row['_bench_pos'] = base.get('position')
        # The selector stamps platform_points on starters (from position_pts);
        # bench rows bypass it, so carry the total-points value across for
        # the shared formatter's Points cell.
        row['platform_points'] = base.get('position_pts')
        bench.append(row)
    return bench


def _month_of_last_day(d):
    """Last calendar day of d's month."""
    from datetime import timedelta
    nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return nxt - timedelta(days=1)


def _month_window():
    """The RUNNING Team-of-the-Month window with an 8th-of-month rollover
    (Kyle, 2026-07-13): from the 8th onward we show the CURRENT month as it
    accrues; in the first week of a new month we retrospect on the PREVIOUS
    (completed) month. This is the ONE deliberately-live board -- it reads
    TODAY'S date, not just warehouse state, so it turns over with the
    calendar ("changes regularly, feels alive"). The window caps at the
    latest game date, so a running month shows only the data we have; if
    the chosen month has no data yet (extraction lag), it steps back to the
    last month that does."""
    from datetime import date, timedelta
    today = date.today()
    anchor = today if today.day >= 8 else (today.replace(day=1) - timedelta(days=1))
    first = anchor.replace(day=1)
    max_d = query_snowflake(
        f"SELECT MAX(game_date) AS d FROM fct_player_daily_performance"
        f" WHERE {league_predicate()} AND game_date IS NOT NULL"
    )[0]['d']
    hi = min(_month_of_last_day(first), max_d) if max_d else _month_of_last_day(first)
    while max_d and hi < first:
        first = (first - timedelta(days=1)).replace(day=1)
        hi = min(_month_of_last_day(first), max_d)
    return first, hi


def get_window_lineup(date_from, date_to, weighted=True):
    """Best lineup over a date window, built from the daily fact directly
    (fct_player_position_pts aggregates CBS to season grain, so a
    sub-season window needs its own candidate query). weighted=True is the
    active lens (the display lineup); weighted=False is the total/rostered
    lens that drives the Total-Pts Best deviation. Feeds the Team of the
    Month board + its deviation."""
    weight = 'COALESCE(active_weight, 0)' if weighted else '1'
    candidates = query_snowflake(f"""
        WITH exploded AS (
            SELECT
                player_key, player_id, player_name, display_name,
                slot.value::string AS position,
                CASE WHEN slot.value::string = 'P'
                     THEN total_pitching_stat_pts
                     ELSE total_hitting_stat_pts END
                    * {weight} AS pos_pts
            FROM fct_player_daily_performance,
                 LATERAL FLATTEN(input => eligible_slots) slot
            WHERE {league_predicate()}
              AND game_date BETWEEN '{date_from}' AND '{date_to}'
              AND slot.value::string NOT IN ('BE', 'IL')
        )
        SELECT
            player_key,
            MAX(player_id)    AS player_id,
            MAX(player_name)  AS player_name,
            MAX(display_name) AS display_name,
            position,
            ROUND(SUM(pos_pts), 1) AS position_pts
        FROM exploded
        GROUP BY player_key, position
        HAVING SUM(pos_pts) > 0
        ORDER BY position, position_pts DESC, player_key
    """)
    candidates = _synthesize_universal_slots(candidates)
    lineup = get_optimal_team_selections(candidates, CBS_SLOT_CAPS)
    _enrich_lineup(lineup, date_from=date_from, date_to=date_to)
    return lineup


def _enrich_lineup(lineup, entity_id=None, season_year=None,
                   date_from=None, date_to=None):
    """Merge the CBS stat tail + slash inputs + roster context onto
    selected rows, from the unified daily fact (one query per lineup).
    Weighted games mirror the points lens (estimated days count
    fractionally). The MAX_BY(_, game_date) columns implement the boards'
    current-vs-retired semantics for free: an active player's latest row
    is a captured 2026 day (pro_team / owner filled), a retired player's
    latest row predates the capture era (era-honest NULL -> blank cell).

    Scope is season_year (a board) OR a date window (Team of the Week);
    both are optional and entity_id further scopes to a franchise.

    period_label='Season' on every row keeps the shared formatter's
    Points cell plain (season-long numbers carry no boxscore link)."""
    keys = [r.get('player_key') for r in lineup if r.get('player_key')]
    for sel in lineup:
        sel['period_label'] = 'Season'
    if not keys:
        return lineup
    quoted = ", ".join("'%s'" % k.replace("'", "''") for k in keys)
    filters = [league_predicate(), f"player_key IN ({quoted})",
               "game_date IS NOT NULL"]
    if date_from is not None:
        filters.append(f"game_date BETWEEN '{date_from}' AND '{date_to}'")
    elif season_year is not None:
        filters.append(f"season_year = {season_year}")
    if entity_id is not None:
        filters.append(_entity_where(entity_id))
    rows = query_snowflake(f"""
        SELECT
            player_key,
            MIN(season_year)                          AS first_season,
            MAX(season_year)                          AS last_season,
            SUM(games_played)                         AS games,
            ROUND(SUM(games_played * COALESCE(active_weight, 0)), 1)
                                                      AS weighted_games,
            SUM(r) AS r, SUM(rbi) AS rbi, SUM(b_bb) AS b_bb,
            SUM(sb) AS sb, SUM(tb) AS tb,
            SUM(h) AS h, SUM(ab) AS ab, SUM(hbp) AS hbp, SUM(sf) AS sf,
            SUM(k) AS k, SUM(w) AS w, SUM(l) AS l, SUM(sv) AS sv,
            SUM(hld) AS hld, SUM(qs) AS qs, SUM(outs) AS outs,
            SUM(cg) AS cg, SUM(er) AS er,
            SUM(p_h) AS p_h, SUM(p_bb) AS p_bb,
            SUM(r_pts) AS r_pts, SUM(rbi_pts) AS rbi_pts,
            SUM(b_bb_pts) AS b_bb_pts, SUM(sb_pts) AS sb_pts,
            SUM(tb_pts) AS tb_pts, SUM(k_pts) AS k_pts, SUM(w_pts) AS w_pts,
            SUM(sv_pts) AS sv_pts, SUM(hld_pts) AS hld_pts,
            SUM(qs_pts) AS qs_pts, SUM(outs_pts) AS outs_pts,
            SUM(cg_pts) AS cg_pts, SUM(er_pts) AS er_pts,
            MAX_BY(team_name, game_date)              AS latest_team_name,
            MAX_BY(team_abbrev, game_date)            AS team_abbrev,
            MAX_BY(owner_name, game_date)             AS owner_name,
            MAX_BY(pro_team, game_date)               AS pro_team,
            MAX_BY(position, game_date)               AS primary_position
        FROM fct_player_daily_performance
        WHERE {' AND '.join(filters)}
        GROUP BY player_key
    """)
    by_key = {r['player_key']: r for r in rows}
    for sel in lineup:
        extra = by_key.get(sel.get('player_key'))
        if extra:
            for k, v in extra.items():
                sel.setdefault(k, v)
        # The two-way pseudo identities display their CBS split name
        # ("Shohei Ohtani (Batter)" -- MLB-68, reported as two players)
        # but the bref search URL wants the human: the shared formatter
        # builds the link from player_name and the text from
        # display_name, so strip the suffix from the former only.
        name = sel.get('player_name') or ''
        if name.endswith(' (Batter)') or name.endswith(' (Pitcher)'):
            sel.setdefault('display_name', name)
            sel['player_name'] = name.rsplit(' (', 1)[0]
    return lineup


def _apply_alltime_board_context(lineup, current_key, current_name, years_map, top_n=3):
    """All-Time board column semantics (Kyle, 2026-07-13):

      ACTIVE player (currently rostered): Fantasy Team = their CURRENT
      franchise abbrev only; Owner = their current owner. (He reconsidered
      mid-request -- an active player shows only where he is now, not his
      career trail.)

      RETIRED player (not on the current capture): Fantasy Team = his top
      franchises by career active points, comma-joined, capped at top_n
      and flagged for gray rendering (they're all 'former'); Owner blank
      (owner-by-era is MLB-64).

    Also stamps the years-of-service string and the retired flag the Home
    builder reads for the gray format."""
    keys = [r.get('player_key') for r in lineup if r.get('player_key')]
    franchises = {}
    if keys:
        quoted = ", ".join("'%s'" % k.replace("'", "''") for k in keys)
        rows = query_snowflake(f"""
            WITH per_franchise AS (
                SELECT
                    player_key,
                    MAX_BY(team_abbrev, game_date)                 AS abbrev,
                    MAX_BY(team_name, game_date)                   AS name,
                    SUM(total_stat_pts * COALESCE(active_weight, 0)) AS pts
                FROM fct_player_daily_performance
                WHERE {league_predicate()} AND game_date IS NOT NULL
                  AND player_key IN ({quoted})
                GROUP BY player_key, team_id
                HAVING SUM(total_stat_pts * COALESCE(active_weight, 0)) > 0
            )
            SELECT player_key,
                   LISTAGG(COALESCE(abbrev, name), ', ')
                       WITHIN GROUP (ORDER BY pts DESC) AS franchises
            FROM (
                SELECT *,
                       ROW_NUMBER() OVER (PARTITION BY player_key
                                          ORDER BY pts DESC) AS rn
                FROM per_franchise
            )
            WHERE rn <= {int(top_n)}
            GROUP BY player_key
        """)
        franchises = {r['player_key']: r['franchises'] for r in rows}
    for sel in lineup:
        key = sel.get('player_key')
        current = current_key.get(key) or current_name.get(
            _norm_name(sel.get('display_name') or sel.get('player_name')))
        if current:
            sel['team_abbrev'] = current.get('abbrev') or ''
            sel['owner_name'] = current.get('owner') or ''
            sel['_alltime_retired'] = False
        else:
            sel['team_abbrev'] = franchises.get(key, '')
            sel['owner_name'] = ''
            sel['_alltime_retired'] = True
        sel['_years_of_service'] = _years_of_service(years_map.get(key, []))
    return lineup


def get_roster_days(entity_id, season_year=None):
    """Calendar days rostered per player for one franchise: historic stint
    spans (the walk-back's effective intervals, name_key-bridged to
    player identity) + the current season's captured roster dates.
    season_year scopes to one season (the team page's current side);
    None spans the franchise's whole history.

    The stint join keys on the PERSON: a two-way split asset's fact name
    ("Shohei Ohtani (Batter)") never matches the transaction log's
    person-grain stints, which zeroed his roster days (Kyle 2026-07-17,
    Deuces' all-time DH with 155 games and 0 days). Chop the
    parenthetical before the name key; both split assets then carry the
    person's tenure, which is the honest per-asset answer. The captured
    (2026) side already joins by the pseudo-ids and needs no change."""
    person_name = "regexp_replace(p.player_name, ' \\\\(.*\\\\)$', '')"
    name_key_expr = _name_key_sql(person_name)
    stint_season = (f" AND season_year = {int(season_year)}"
                    if season_year is not None else '')
    return query_snowflake(f"""
        WITH stint_days AS (
            SELECT name_key,
                   SUM(DATEDIFF('day', stint_start, attribution_end_exclusive))
                       AS days
            FROM int_cbs__roster_stints_effective
            WHERE {league_predicate()} AND franchise_id = {int(entity_id)}
                  {stint_season}
            GROUP BY name_key
        ),
        players AS (
            SELECT DISTINCT p.player_key, p.player_name
            FROM fct_player_daily_performance p
            WHERE {league_predicate('p')} AND {_entity_where(entity_id, 'p')}
              AND p.game_date IS NOT NULL
        ),
        captured AS (
            SELECT player_id AS player_key, COUNT(DISTINCT roster_date) AS days
            FROM stg_cbs__rosters
            WHERE {league_predicate()} AND team_id = '{int(entity_id)}'
                  {stint_season}
            GROUP BY player_id
        )
        SELECT p.player_key,
               COALESCE(s.days, 0) + COALESCE(c.days, 0) AS roster_days
        FROM players p
        LEFT JOIN stint_days s ON s.name_key = {name_key_expr}
        LEFT JOIN captured c ON c.player_key = p.player_key
    """)


def get_roster_days_by_season(entity_id):
    """get_roster_days at SEASON grain, one query for the franchise's
    whole history -> {(player_key, season_year): days}. Feeds the Best
    Individual Seasons block's Roster Days column (Kyle 2026-07-17).
    Same person-keyed stint join (two-way split assets carry the
    person's tenure) + captured 2026 dates."""
    person_name = "regexp_replace(p.player_name, ' \\\\(.*\\\\)$', '')"
    name_key_expr = _name_key_sql(person_name)
    rows = query_snowflake(f"""
        WITH players AS (
            SELECT DISTINCT p.player_key, p.player_name
            FROM fct_player_daily_performance p
            WHERE {league_predicate('p')} AND {_entity_where(entity_id, 'p')}
              AND p.game_date IS NOT NULL
        ),
        stint_days AS (
            SELECT name_key, season_year,
                   SUM(DATEDIFF('day', stint_start, attribution_end_exclusive))
                       AS days
            FROM int_cbs__roster_stints_effective
            WHERE {league_predicate()} AND franchise_id = {int(entity_id)}
            GROUP BY 1, 2
        ),
        captured AS (
            SELECT player_id AS player_key, season_year,
                   COUNT(DISTINCT roster_date) AS days
            FROM stg_cbs__rosters
            WHERE {league_predicate()} AND team_id = '{int(entity_id)}'
            GROUP BY 1, 2
        ),
        unioned AS (
            SELECT p.player_key, s.season_year, s.days
            FROM players p
            JOIN stint_days s ON s.name_key = {name_key_expr}
            UNION ALL
            SELECT player_key, season_year, days FROM captured
        )
        SELECT player_key, season_year, SUM(days) AS roster_days
        FROM unioned
        GROUP BY 1, 2
    """)
    return {(r['player_key'], int(r['season_year'])): int(r['roster_days'] or 0)
            for r in rows}


def get_cbs_team_history_data(context, franchises, franchise_map):
    """ESPN-shaped team-history rows for every active CBS franchise, both
    scopes -- the exact row contract of almanac_data.get_team_roster_
    history_stats, so almanac_logic.build_team_history_tabs renders CBS
    team tabs IDENTICALLY to ESPN's (Kyle 2026-07-16: the tide flows
    ESPN->CBS; nothing from the old CBS tab shape survives).

    CBS mapping choices:
      active_*        ACTIVE-weighted sums (the Records 'real league'
                      lens; 2004-2020 estimated days count fractionally)
      bench_il_points production while rostered but NOT active
                      (total * (1 - active_weight)); CBS reserves = its
                      bench, header stays ESPN's "Bench/IL Points"
      il_days         always 0 (CBS has RS, no IL slots -> no IL section)
      rostered_days   calendar days from get_roster_days (stints +
                      captured), the walk-back's roster-tenure number
      active_slots_   real lineup slots the player started (captured/
      played          reconstructed eras); estimated-era EST rows are
                      excluded -- 'position' backfills the display
      service_years   distinct seasons with nonzero active production
                      (negatives count), for the trailing YoS column
    """
    season = int(context['season_year'])
    roster_date = context['roster_date']
    fids = [int(f['team_id']) for f in franchises]
    id_list = ", ".join(str(f) for f in fids)
    w = 'COALESCE(active_weight, 0)'

    # Shared aggregate columns: the whole-history query below and the
    # by-season query (the Best Individual Seasons block) sum the same
    # fields at different grains. Rate inputs keep 1dp; stats the tab
    # displays as bare integers round to whole.
    agg_cols = f"""
                MAX_BY(player_name, game_date)  AS player_name,
                MAX_BY(display_name, game_date) AS display_name,
                MAX_BY(position, game_date)     AS position,
                MAX_BY(pro_team, game_date)     AS pro_team,
                ROUND(SUM(total_stat_pts * {w}), 1)          AS active_points,
                ROUND(SUM(total_hitting_stat_pts * {w}), 1)  AS active_hitting_points,
                ROUND(SUM(total_pitching_stat_pts * {w}), 1) AS active_pitching_points,
                ROUND(SUM(total_stat_pts * (1 - {w})), 1)    AS bench_il_points,
                ROUND(SUM(games_played * {w}))               AS active_games,
                ROUND(SUM(h * {w}), 1)    AS h,
                ROUND(SUM(ab * {w}), 1)   AS ab,
                ROUND(SUM(b_bb * {w}), 1) AS b_bb,
                ROUND(SUM(hbp * {w}), 1)  AS hbp,
                ROUND(SUM(sf * {w}), 1)   AS sf,
                ROUND(SUM(tb * {w}), 1)   AS tb,
                ROUND(SUM(hr * {w}))      AS hr,
                ROUND(SUM(sb * {w}))      AS sb,
                ROUND(SUM(w * {w}))       AS w,
                ROUND(SUM(l * {w}))       AS l,
                ROUND(SUM(sv * {w}))      AS sv,
                ROUND(SUM(er * {w}), 1)   AS er,
                ROUND(SUM(outs * {w}), 1) AS outs,
                ROUND(SUM(k * {w}))       AS k,
                ROUND(SUM(p_bb * {w}))    AS p_bb,
                ROUND(SUM(p_h * {w}), 1)  AS p_h,
                LISTAGG(DISTINCT CASE
                    WHEN {w} > 0
                     AND lineup_slot NOT IN ('BE', 'IL', 'FA', 'RS', 'EST', 'ACT')
                    THEN lineup_slot END, ',') AS active_slots_played"""

    rows = query_snowflake(f"""
        WITH scoped AS (
            SELECT 'current_season' AS scope, f.*
            FROM fct_player_daily_performance f
            WHERE {league_predicate('f')} AND f.game_date IS NOT NULL
              AND f.team_id IN ({id_list}) AND f.season_year = {season}
            UNION ALL
            SELECT 'all_time' AS scope, f.*
            FROM fct_player_daily_performance f
            WHERE {league_predicate('f')} AND f.game_date IS NOT NULL
              AND f.team_id IN ({id_list})
        ),
        totals AS (
            SELECT
                scope, team_id, player_key,
                {agg_cols}
            FROM scoped
            GROUP BY scope, team_id, player_key
        ),
        service AS (
            -- Mirrors the ESPN service_seasons CTE + get_years_of_service:
            -- a season counts on ANY nonzero active production (negative
            -- seasons are still service -- Kyle 2026-07-15).
            SELECT scope, team_id, player_key,
                   LISTAGG(TO_VARCHAR(season_year), ',')
                       WITHIN GROUP (ORDER BY season_year) AS service_years
            FROM (
                SELECT scope, team_id, player_key, season_year
                FROM scoped
                GROUP BY scope, team_id, player_key, season_year
                HAVING SUM(total_stat_pts * {w}) <> 0
            )
            GROUP BY scope, team_id, player_key
        )
        SELECT t.*, COALESCE(s.service_years, '') AS service_years
        FROM totals t
        LEFT JOIN service s
            ON t.scope = s.scope AND t.team_id = s.team_id
           AND t.player_key = s.player_key
    """)

    # Season-grain rows for the Best Individual Seasons block (Kyle
    # 2026-07-17): same aggregates, +season_year in the grain.
    season_rows = query_snowflake(f"""
        SELECT
            team_id, player_key, season_year,
            {agg_cols}
        FROM fct_player_daily_performance
        WHERE {league_predicate()} AND game_date IS NOT NULL
          AND team_id IN ({id_list})
        GROUP BY team_id, player_key, season_year
    """)

    by_key, by_name = get_current_rostered()
    meta = {}
    for f in franchises:
        fid = int(f['team_id'])
        meta[fid] = {
            'team_name': f['team_name'],
            'team_abbrev': franchise_map.get(fid, {}).get('abbrev') or '',
        }
    # Roster-tenure days per franchise: both scopes + the by-season map
    # (3x16 queries; the old tabs ran 2x16).
    days = {}
    season_days = {}
    for fid in fids:
        days[('current_season', fid)] = {
            r['player_key']: r['roster_days']
            for r in get_roster_days(fid, season_year=season)}
        days[('all_time', fid)] = {
            r['player_key']: r['roster_days'] for r in get_roster_days(fid)}
        season_days[fid] = get_roster_days_by_season(fid)

    stat_tail = ('h', 'ab', 'b_bb', 'hbp', 'sf', 'tb', 'hr', 'sb',
                 'w', 'l', 'sv', 'er', 'outs', 'k', 'p_bb', 'p_h')

    def _shape(r):
        """The common row transform: names (two-way pseudo identities keep
        CBS's "(Batter)" split label on display; the bref search key gets
        the human name, same convention as _enrich_lineup), the Tm column
        ('*' on this page's team, abbrev on another, blank unclaimed --
        by_name fallback covers the id-split class), and the slot list
        filtered to REAL display slots (SLOT_ORDER's vocabulary; stray
        slot-unknown codes like 'ACT' burned us)."""
        fid = int(r['team_id'])
        m = meta.get(fid)
        if m is None:
            return None
        name = r['player_name'] or ''
        display = r['display_name'] or name
        if name.endswith(' (Batter)') or name.endswith(' (Pitcher)'):
            display = display or name
            name = name.rsplit(' (', 1)[0]
        cur = by_key.get(r['player_key']) or by_name.get(_norm_name(display))
        cur_abbrev = (cur or {}).get('abbrev') or ''
        tm_now = '*' if (cur_abbrev and cur_abbrev == m['team_abbrev']) else cur_abbrev
        slots = {s for s in (r.get('active_slots_played') or '').split(',')
                 if s in SLOT_ORDER}
        return {
            'team_id': fid,
            'team_name': m['team_name'],
            'team_abbrev': m['team_abbrev'],
            'latest_matchup_end_date': roster_date,
            'player_id': r['player_key'],
            'player_name': name,
            'display_name': display,
            'position': r.get('position') or '',
            'pro_team': r.get('pro_team') or '',
            'current_fantasy_team': tm_now,
            'active_slots_played': ','.join(sorted(slots, key=SLOT_ORDER.get)),
            'il_days': 0,
            'active_games': int(r.get('active_games') or 0),
            'active_points': float(r.get('active_points') or 0),
            'active_hitting_points': float(r.get('active_hitting_points') or 0),
            'active_pitching_points': float(r.get('active_pitching_points') or 0),
            'bench_il_points': float(r.get('bench_il_points') or 0),
            **{col: r.get(col) for col in stat_tail},
        }

    players = []
    for r in rows:
        shaped = _shape(r)
        if shaped is None:
            continue
        shaped['scope'] = r['scope']
        shaped['rostered_days'] = int(
            days[(r['scope'], shaped['team_id'])].get(r['player_key']) or 0)
        shaped['service_years'] = r.get('service_years') or ''
        players.append(shaped)

    player_seasons = []
    for r in season_rows:
        shaped = _shape(r)
        if shaped is None:
            continue
        year = int(r['season_year'])
        shaped['season_year'] = year
        shaped['rostered_days'] = int(
            season_days[shaped['team_id']].get((r['player_key'], year)) or 0)
        player_seasons.append(shaped)

    return {'players': players, 'player_seasons': player_seasons}


def _cbs_optimal_team(season_year=None, team_id=None):
    """Starters selector for the shared team-tab builder: CBS's own
    get_best_lineup (shared gap-based selection over CBS_SLOT_CAPS,
    weighted-active lens, CBS eligibility + U synthesis), mapped to the
    builder's (player_id, slot_label, lineup_slot) contract."""
    lineup = get_best_lineup(entity_id=team_id, season_year=season_year)
    return [{'player_id': s.get('player_key'),
             'slot_label': s.get('slot_label'),
             'lineup_slot': s.get('lineup_slot')}
            for s in lineup]


# ---------------------------------------------------------------------------
# Row building
# ---------------------------------------------------------------------------

def _pts(value):
    """Integer-style display for point/stat values (326.0 -> '326')."""
    if value is None:
        return ''
    return f"{float(value):,.0f}"


def _num(value):
    if value is None:
        return ''
    return int(value)


def _whole(value):
    """Round a points cell to a whole number for the CBS boards (Kyle,
    2026-07-13). Non-numeric cells (blanks, '=HYPERLINK...' formulas)
    pass through untouched."""
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return value


def _movement(rank_change):
    if rank_change is None:
        return ''
    rank_change = int(rank_change)
    if rank_change > 0:
        return f"↑{rank_change}"
    if rank_change < 0:
        return f"↓{-rank_change}"
    return '–'


def _safe_sheet_title(title):
    # Mirrors almanac_render._safe_sheet_title (Sheets-invalid characters).
    bad_chars = set('[]:*?/\\')
    cleaned = ''.join('-' if c in bad_chars else c for c in str(title))
    cleaned = cleaned.strip("'").strip()
    return cleaned[:100] or 'Sheet'


def _col(n):
    """1-based column index -> A1 letter(s)."""
    return gspread.utils.rowcol_to_a1(1, n)[:-1]


def _fmt_date(value):
    if value is None:
        return ''
    if isinstance(value, str):
        value = date.fromisoformat(value[:10])
    return f'{value:%b} {value.day}, {value.year}'


def _compress_years(years):
    """Sorted distinct years -> compact range list: [2001,2002,2003,2009]
    -> '2001–2003, 2009'. The building block of the stat-sources dates
    and the years-of-service string."""
    ys = sorted({int(y) for y in years if y is not None})
    if not ys:
        return ''
    ranges, start, prev = [], ys[0], ys[0]
    for y in ys[1:]:
        if y == prev + 1:
            prev = y
        else:
            ranges.append((start, prev))
            start = prev = y
    ranges.append((start, prev))
    return ', '.join(str(a) if a == b else f'{a}–{b}' for a, b in ranges)


def _years_of_service(years):
    """Kyle's longevity string (2026-07-13): '[count of seasons with
    active production]: [year ranges]', e.g. '7: 2001–2006, 2009'. Empty
    when the player logged no active seasons in scope."""
    ys = sorted({int(y) for y in years if y is not None})
    if not ys:
        return ''
    return f'{len(ys)}: {_compress_years(ys)}'


# Era buckets for the fidelity label (the walk-back fidelity map): each
# era's game-days as a share of ALL game-days, labeled by how that era's
# lineup states are known. Bucketing by ERA (not provenance enum) means
# every day lands somewhere -- the old enum sentence dropped
# estimated_adjacent + sentinel and summed to ~93% (Kyle 2026-07-17).
_PROVENANCE_ERAS = [
    (2026, 9999, '2026 onward', 'captured live'),
    (2021, 2025, '2021–2025', 'reconstructed day-by-day from the transaction log'),
    (2004, 2020, '2004–2020', 'estimated from year-end start shares'),
    (2001, 2003, '2001–2003', 'reconstructed from the transaction log'),
]


def _provenance_era_lines(mix_rows):
    """Era-keyed fidelity lines from a (season, provenance) mix -- one
    string per era, shares summing to ~100% of game-days, feeding the
    team-tab "Lineup Data:" block (Kyle's gold standard, 2026-07-17).
    The block has three rows; when all four eras are present, the two
    transaction-log-reconstructed eras merge onto one line."""
    total = sum(r['n'] for r in mix_rows) or 1
    by_era = {}
    for r in mix_rows:
        year = int(r['season_year'])
        for lo, hi, era, _label in _PROVENANCE_ERAS:
            if lo <= year <= hi:
                by_era[era] = by_era.get(era, 0) + r['n']
                break
    present = [(era, label, by_era[era])
               for _lo, _hi, era, label in _PROVENANCE_ERAS
               if by_era.get(era)]
    if len(present) > 3:
        recon = [(e, lab, n) for e, lab, n in present
                 if lab.startswith('reconstructed')]
        if len(recon) > 1:
            eras = ', '.join(sorted(e for e, _lab, _n in recon))
            merged = (eras, recon[0][1], sum(n for _e, _lab, n in recon))
            present = [merged if (e, lab, n) == recon[0] else (e, lab, n)
                       for e, lab, n in present
                       if (e, lab, n) not in recon[1:]]
    return [f'{era}: {label} ({100.0 * n / total:.0f}%)'
            for era, label, n in present[:3]]


# The one wording deviation from ESPN's deviation-column label: CBS
# attribution has no FA lens, so "(incl. bench & FA)" would overclaim.
_CBS_DEVIATION_LABEL = HOME_DEVIATION_LABEL.replace(' & FA', '')

_CBS_GLOSSARY = [
    ('Calculated Points', 'Universal MLB stats priced by the league\'s '
                          'current scoring rules -- verified against CBS\'s '
                          'own awarded totals.'),
    ('Active Points', 'Produced while in the starting lineup (weighted by '
                      'start-share estimates where 2004–2020 daily '
                      'lineups aren\'t recoverable).'),
    ('Rostered Points', 'Everything produced while on the roster, started '
                        'or benched.'),
    ('Wasted Points', 'Inactive points + the size of any negative active-game '
                      'totals (points left on the bench, plus points actively '
                      'lost).'),
]

# The league's 11 reserve slots -- the bench depth the All-League boards
# fill under Kyle's "bench/reserve spots per roster rules" request. A
# render-side knob, so flipping it (or the ESPN equivalent later) is one
# constant, not a data change.
_CBS_BENCH_SLOTS = 11


def build_home_rows(context, nav_targets=None):
    """Home as the ESPN two-band dashboard, built by the SHARED board
    machinery: left band (cols A-D) = navigation + team grid + points
    glossary + the provenance sentence; right band (cols F+) = the two
    All-League boards -- Season-to-Date and All-Time, each in ESPN's
    exact column shape (Slot | Team | Player | Fantasy Team | Owner |
    Points | Slash | Stat Line | Total-Pts Best) with the deviation
    columns driven by the rostered-lens alternate lineup.

    Three boards top-to-bottom (Kyle's lean, 2026-07-13): Team of the
    Month (a running board with an 8th-of-month rollover, carrying the
    Total-Pts Best deviation but no bench), Season-to-Date, All-Time.
    Season and All-Time carry the league's 11 reserve spots as a bench
    block (a blank buffer row separates it from the starters), labeled
    BE - Pos and ranked by TOTAL points (starters rank by active points).

    CBS exceptions to the ESPN shape, all Kyle-specified (2026-07-13):
    Points cells are plain whole numbers (season-long, no boxscore); the
    All-Time board's Team column is current-MLB-team-or-blank; an ACTIVE
    player's Fantasy Team is his current franchise + populated Owner,
    while a RETIRED player's is his top-3 franchises by career active
    points (gray) with a blank Owner; the All-Time board swaps the
    Total-Pts Best deviation for a Years-of-Service column (font 8,
    "count: year-ranges"); the deviation label drops "& FA" (no FA lens
    in CBS attribution). The left band's glossary gains a Stat sources
    table breaking the provenance tiers down by era + share.

    nav_targets: {tab_title: gid} on the live write -> in-sheet
    =HYPERLINK nav cells; None in previews -> plain text (ESPN pattern).
    """
    season = context['season_year']
    league_name = db.league().display_name
    era = f"{context['first_season']}–{season}"
    right_width = len(HOME_HEADER) + 2  # widest board (season: +deviation pair)

    # ------------------------------------------------ right band (F..O)
    # Three boards top-to-bottom, Kyle's lean (2026-07-13): Month, Season,
    # All-Time. Month is the running team (Total-Pts Best deviation, no
    # bench); Season carries the Total-Pts Best deviation + the
    # reserve bench; All-Time swaps the deviation for a Years-of-Service
    # column and carries the bench, with retired players' Fantasy Team
    # cells flagged for gray.
    season_dev = _deviation_by_slot(context['season_board'],
                                    context['season_board_rostered'])
    month_dev = _deviation_by_slot(context['month_board'],
                                   context['month_board_rostered'])
    lo, _hi = context['month_window']
    month_label = (f'Team of the Month - {lo:%B %Y} '
                   f'(rolls over on the 8th of each new month)')

    def _board(title, lineup, mode, dev_map=None, leading_blank=True):
        if mode == 'season':
            hdr = [*HOME_HEADER, _CBS_DEVIATION_LABEL, '']
        elif mode == 'alltime':
            hdr = [*HOME_HEADER, 'Years of Service']
        else:  # 'plain' -- the month board: base columns, no deviation/years
            hdr = list(HOME_HEADER)
        # Title sits directly on its header row. The spacer blank moves ABOVE
        # the title for all but the FIRST board, which stays flush with the top
        # of the band so the right half aligns with the left (Kyle 2026-07-16).
        if leading_blank:
            rows_ = [[], [title], hdr]
            meta_ = [{'k': 'blank'}, {'k': 'title'}, {'k': 'header'}]
        else:
            rows_ = [[title], hdr]
            meta_ = [{'k': 'title'}, {'k': 'header'}]
        prev_bench = False
        for sel in lineup:
            is_bench = str(sel.get('slot_label') or '').startswith('BE')
            if is_bench and not prev_bench:   # blank buffer between starters + bench
                rows_.append([])
                meta_.append({'k': 'blank'})
            prev_bench = is_bench
            if mode == 'season':
                r = format_all_league_team_row_with_deviation(
                    sel, (dev_map or {}).get(sel.get('slot_label')))
                r[9] = _whole(r[9])          # deviation total pts
            elif mode == 'alltime':
                r = format_all_league_team_row(sel) + [sel.get('_years_of_service', '')]
            else:
                r = format_all_league_team_row(sel)
            r[5] = _whole(r[5])              # Points -> whole number (Kyle)
            rows_.append(r)
            meta_.append({'k': 'data',
                          'retired': mode == 'alltime' and sel.get('_alltime_retired'),
                          'years': mode == 'alltime',
                          'bench': is_bench})
        return rows_, meta_

    right, meta = [], []
    boards = [
        (month_label, context['month_board'], 'season', month_dev),
        (f'All-League Team Season-to-Date: {season}', context['season_board'],
         'season', season_dev),
        (f'All-League Team: All-Time ({era})', context['alltime_board'],
         'alltime', None),
    ]
    for i, (title, lineup, mode, dev) in enumerate(boards):
        # First board flush with the top; later boards get a spacer above.
        rws, mta = _board(title, lineup, mode, dev, leading_blank=(i > 0))
        right += rws + [[]]
        meta += mta + [{'k': 'blank'}]

    # ------------------------------------------------ left band (A..D)
    left = [['Navigate']]
    left.append([home_nav_link(RECORDS_TAB, RECORDS_TAB, nav_targets),
                 'Best seasons & careers, all-time.'])
    left.append([home_nav_link(STANDINGS_TAB, STANDINGS_TAB, nav_targets),
                 f'{season} race + every finish since '
                 f'{context["first_season"]}.'])
    left.append(['Team Pages', 'Best lineups & benches, current + all-time.'])
    team_titles = context['team_titles']
    for i in range(0, len(team_titles), 2):
        left.append(['', *(home_nav_link(t, t, nav_targets)
                           for t in team_titles[i:i + 2])])
    left.append([home_nav_link(DRAFT_TAB, DRAFT_TAB, nav_targets),
                 'Every recorded draft: 2026 recap + all-time pick value.'])
    left.append([])
    left.append(['Points Glossary & Documentation'])
    left.extend([term, definition] for term, definition in _CBS_GLOSSARY)
    left.append([])
    left.append(['Stat sources'])
    for src in get_stat_sources():
        left.append([src['dates'], src['desc'], '', f"{src['pct']}%"])

    rows = [
        [f'{league_name} Almanac'],
        [_HOME_SCORING_CALLOUT],
        [],
        *_merge_home_bands(left, right, 4, right_width),
    ]

    # ESPN-restrained styling (mirrors almanac_write._replace_home_tab +
    # _home_label_formats): bold-14 title, pale-blue callout, bold left
    # labels, navy board headers, whole-number points, gray retired
    # teams, font-8 years-of-service.
    last_col = _col(5 + right_width)
    _left_labels = {'Navigate', 'Points Glossary & Documentation',
                    'Stat sources'}
    formats = [
        {'range': f'A1:{last_col}1',
         'format': {'textFormat': {'bold': True, 'fontSize': 14}}},
        {'range': f'A2:{last_col}2',
         'format': {'textFormat': {'italic': True},
                    'backgroundColor': {'red': 0.90, 'green': 0.94, 'blue': 0.98}}},
        # Points (K) and the deviation total (O) round to whole numbers.
        {'range': 'K:K', 'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0'}}},
        {'range': 'O:O', 'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0'}}},
    ]
    # Left-band section labels (col A scan).
    for i, row in enumerate(rows, 1):
        if row and row[0] in _left_labels:
            formats.append({'range': f'A{i}:D{i}',
                            'format': {'textFormat': {'bold': True}}})
    # Right-band per-row formats from meta (merged row i -> sheet row i+4).
    for i, m in enumerate(meta):
        r = i + 4
        if m['k'] == 'title':
            formats.append({'range': f'F{r}:{last_col}{r}',
                            'format': {'textFormat': {'bold': True}}})
        elif m['k'] == 'header':
            formats.append({'range': f'F{r}:{last_col}{r}',
                            'format': {'textFormat': {'bold': True,
                                                      'foregroundColor': _WHITE},
                                       'backgroundColor': _NAVY}})
        elif m['k'] == 'data':
            if m.get('retired'):  # gray + font-8 the former-teams cell (I = Fantasy Team)
                formats.append({'range': f'I{r}:I{r}',
                                'format': {'textFormat': {
                                    'fontSize': 8,
                                    'foregroundColor': {'red': 0.6, 'green': 0.6, 'blue': 0.6}}}})
            if m.get('years'):    # font-8 the years-of-service cell (N)
                formats.append({'range': f'N{r}:N{r}',
                                'format': {'textFormat': {'fontSize': 8}}})
            if m.get('bench'):    # font-8 the "BE - Pos" slot label (F)
                formats.append({'range': f'F{r}:F{r}',
                                'format': {'textFormat': {'fontSize': 8}}})
    return rows, formats


def _rec_value(stat, value):
    """Record value display: IP for OUTS, else whole number."""
    if value is None:
        return ''
    if stat == 'OUTS':
        return fmt_ip(value)
    return f'{float(value):,.0f}'


def _contributor_detail(stat, contributors):
    """Team-record Details = the top active players behind that team scope."""
    return ', '.join(f'{nm}: {_rec_value(stat, v)}' for nm, v in (contributors or []))


def _rec_side(cell, stat, player=False, with_period=True):
    """One scope's 5 cells (ESPN shape): Holder | Owner | Value | Period |
    Details. Player holders link to baseball-reference; team holders show
    the franchise abbrev with a contributor detail."""
    if not cell:
        return ['', '', '', '', ''] if with_period else ['', '', '', '']
    holder = _bref_player_cell(cell) if player else cell.get('holder', '')
    # Rate cells carry a pre-formatted display value + a min-sample qualifier
    # as details (both grains), so they bypass _rec_value / _contributor_detail.
    if cell.get('is_rate'):
        value, details = cell.get('value', ''), cell.get('details', '')
    else:
        value = cell.get('value_disp') or _rec_value(stat, cell.get('value'))
        details = (cell.get('details') or '') if player else \
            _contributor_detail(stat, cell.get('details'))
    # with_period=False drops the span cell -- the All-Time side has no 'Yrs'
    # column (Kyle 2026-07-14: readability over the rare useful span).
    side = [holder, cell.get('owner', ''), value]
    if with_period:
        side.append(cell.get('period', ''))
    side.append(details)
    return side


# Records v2.1 layout: Record | [Season: Holder|Owner|Value|Year|Details]
# | gap | [All-Time Total: Holder|Owner|Value|Yrs|Details] -- the ESPN
# two-scope Records shape, Season replacing "Current Season" and All-Time
# Total replacing "All-Time".
_REC_LAST_COL = 'K'


def build_records_rows(context, catalog, data):
    """Records, mirrored on the ESPN Records page (Kyle 2026-07-13): a
    two-scope matrix -- best single SEASON | best ALL-TIME TOTAL (career) --
    at team and player grain, auto-cataloged from what the league scores,
    ACTIVE-weighted (the 'real baseball league' lens). Powder-blue #f2f7fc
    scope/column headers with the scope labels sat over their blocks; Score
    Records carries the polar Best/Worst point marquees, then per-stat Player
    and Team sections."""
    era = f"{context['first_season']}–{context['season_year']}"
    HDR = ['Record', 'Holder', 'Owner', 'Value', 'Year', 'Details', '',
           'Holder', 'Owner', 'Value', 'Details']

    rows = [
        ['League Records'],
        [f'Active-lineup production only — if a player wasn\'t started, it '
         f'didn\'t happen for the league ({era}). Auto-cataloged from the '
         f'categories this league scores plus tracked counting stats. '
         f'"Season" = best single season all-time; "All-Time Total" = best '
         f'career (players: total; teams: completed-season AVERAGE, active '
         f'franchises, min 1 completed season). Owner shows a team record\'s '
         f'franchise owner and, for player records, the franchises they earned '
         f'it with.'],
        [],
    ]
    formats = [
        {'range': f'A1:{_REC_LAST_COL}1',
         'format': {'textFormat': {'bold': True, 'fontSize': 14}}},
        {'range': f'A2:{_REC_LAST_COL}2',
         'format': {'textFormat': {'italic': True}, 'backgroundColor': _PALE_BLUE}},
    ]

    def _band():
        formats.append({'range': f'A{len(rows)}:{_REC_LAST_COL}{len(rows)}',
                        'format': {'textFormat': {'bold': True},
                                   'backgroundColor': _POWDER}})

    def _section(label):
        # Scope labels sit OVER their blocks: 'Season' at col B (the first
        # Holder), 'All-Time Total' at col H (the second Holder).
        rows.append([label, 'Season', '', '', '', '', '',
                     'All-Time Total', '', '', ''])
        _band()

    def _header():
        rows.append(list(HDR))
        _band()

    def _emit(label, season_cell, career_cell, stat, player):
        rows.append([label, *_rec_side(season_cell, stat, player), '',
                     *_rec_side(career_cell, stat, player, with_period=False)])

    def _emit_stat(label, stat, player):
        d = data.get(stat, {})
        _emit(label, d.get('season_player' if player else 'season_team'),
              d.get('career_player' if player else 'career_team'), stat, player)

    _point_labels = {
        'CALCULATED_POINTS': 'Total Points',
        'CALCULATED_HITTING_PTS': 'Hitting Points',
        'CALCULATED_PITCHING_PTS': 'Pitching Points',
    }

    def _disp(stat):
        d = catalog[stat]['display_name']
        return _DISPLAY_FIX.get(d, d)

    def _ordered(stats, order):
        idx = {s: i for i, s in enumerate(order)}
        return sorted(stats, key=lambda s: (idx.get(s, 999), _disp(s)))

    # Route by polarity (Kyle round 7): positive stats are 'best' records in
    # the main sections; negative-polarity pitching stats (Earned Runs, Hits
    # Allowed, Walks Allowed) are futility -> the Negative Records section.
    hitting = _ordered([s for s, m in catalog.items()
                        if m['stat_category'] == 'hitting' and m['polarity'] == 'positive'],
                       _HIT_ORDER)
    pitching = _ordered([s for s, m in catalog.items()
                         if m['stat_category'] == 'pitching' and m['polarity'] == 'positive'],
                        _PIT_ORDER)
    negatives = _ordered([s for s, m in catalog.items() if m['polarity'] == 'negative'],
                         _NEG_ORDER)

    # ---- Score Records: the point marquees, best only (worst moves to the
    # Negative Records section below).
    _section('Score Records')
    _header()
    for stat, label in _point_labels.items():
        _emit_stat(f'Best Team {label}', stat, player=False)
    for stat, label in _point_labels.items():
        _emit_stat(f'Best Player {label}', stat, player=True)
    rows.append([])

    # ---- Per-stat 'best' sections: Player leads, then Team.
    # Rate records close each per-stat section (counting stats first, then the
    # rates -- Kyle 2026-07-14). ERA/WHIP/etc. reuse the ESPN rate helpers.
    _rate_by_cat = {'Hitting': [k for k, _l, c, _h in _RATE_SPECS if c == 'hitting'],
                    'Pitching': [k for k, _l, c, _h in _RATE_SPECS if c == 'pitching']}
    _rate_label = {k: l for k, l, _c, _h in _RATE_SPECS}
    for grain, player in (('Player', True), ('Team', False)):
        for cat_label, stats in (('Hitting', hitting), ('Pitching', pitching)):
            if not stats:
                continue
            _section(f'{grain} {cat_label} Records')
            _header()
            for stat in stats:
                _emit_stat(_disp(stat), stat, player)
            for rk in _rate_by_cat[cat_label]:
                _emit_stat(_rate_label[rk], rk, player)
            rows.append([])

    # ---- Lineup Slot Records (Kyle 2026-07-14): the CURRENT-roster shape --
    # C/1B/2B/3B/SS, OF x3, U, DH, P x9 -- each slot filled with the best
    # available player-SEASON (left, statline detail) and the ranked active
    # FRANCHISE all-time (right = "All-Time Team Totals", contributor detail).
    # 2004-2020 positions are eligibility estimates.
    slots = data.get('_slots') or []
    if any(s.get('season_player') or s.get('career_team') for s in slots):
        # Custom header: the All-Time side is TEAM totals here, and the estimate
        # caveat rides beside it at col I instead of a separate row (Kyle: I77).
        rows.append(['Lineup Slot Records', 'Season', '', '', '', '', '',
                     'All-Time Team Totals',
                     '* Team Totals: pitcher slots span all years; HITTER slots '
                     'are 2026-only — specific hitter positions weren’t '
                     'logged before the 2026 daily capture (2001–25 recorded '
                     '"active", not the slot).', '', ''])
        _band()
        formats.append({'range': f'I{len(rows)}',
                        'format': {'textFormat': {'bold': False, 'italic': True,
                                                  'fontSize': 9}}})
        _header()
        for s in slots:
            rows.append([s['label'],
                         *_rec_side(s.get('season_player'), s['pos'], player=True), '',
                         *_rec_side(s.get('career_team'), s['pos'], player=False,
                                    with_period=False)])
        rows.append([])

    # ---- Negative Records: EXCISED (Kyle 2026-07-14) for symmetry with the
    # ESPN records page, which has no futility block. The worst-* data is still
    # computed upstream (harmless, unused) so re-adding is render-only.

    # ---- Franchise Hall of Fame (A-F) | buffer G | Wasted Hall of Shame,
    # split Pitchers (H-K) + Hitters (L-O), side by side (Kyle 2026-07-15). HoF
    # = career active pts with one franchise; HoS = career WASTED (unrostered +
    # benched), by discipline, each 25 deep. Breakdowns at K & O.
    hof = data.get('_hof') or []
    hos = data.get('_hos') or {}
    hos_pit = hos.get('pitchers') or []
    hos_hit = hos.get('hitters') or []
    n = max(len(hof), len(hos_pit), len(hos_hit))
    if n:
        def _wide_band():
            formats.append({'range': f'A{len(rows)}:O{len(rows)}',
                            'format': {'textFormat': {'bold': True},
                                       'backgroundColor': _POWDER}})
        rows.append(['Franchise Hall of Fame — top 25 careers with one franchise',
                     '', '', '', '', '', '',
                     'Wasted Hall of Shame — top 25 by career wasted points '
                     '(unrostered + benched)'])
        _wide_band()
        rows.append(['Rank', 'Player', 'Franchise', 'Active Points',
                     'Years of Service',
                     'Slash | Stat Line (While Active for Listed Team)', '',
                     'Pitchers', 'Benched Most By', 'Wasted Points', 'Breakdown',
                     'Hitters', 'Benched Most By', 'Wasted Points', 'Breakdown'])
        _wide_band()
        first_data = len(rows) + 1
        for i in range(n):
            hf = ['', '', '', '', '', '']
            if i < len(hof):
                e = hof[i]
                hf = [i + 1, _bref_player_cell(e), e.get('abbrev', ''),
                      _pts(e.get('pts')), e.get('span', ''), e.get('statline', '')]
            pit = ['', '', '', '']
            if i < len(hos_pit):
                e = hos_pit[i]
                pit = [_bref_player_cell(e), e.get('shame', ''),
                       _pts(e.get('wasted')), e.get('details', '')]
            hit = ['', '', '', '']
            if i < len(hos_hit):
                e = hos_hit[i]
                hit = [_bref_player_cell(e), e.get('shame', ''),
                       _pts(e.get('wasted')), e.get('details', '')]
            rows.append(hf + [''] + pit + hit)
        # Breakdown cells (K = pitchers, O = hitters): centered, 8pt.
        for col in ('K', 'O'):
            formats.append({'range': f'{col}{first_data}:{col}{len(rows)}',
                            'format': {'horizontalAlignment': 'CENTER',
                                       'textFormat': {'fontSize': 8}}})
        # Years of Service (col E): just the count, centered.
        formats.append({'range': f'E{first_data}:E{len(rows)}',
                        'format': {'horizontalAlignment': 'CENTER'}})
        rows.append([])

    return rows, formats


def build_standings_rows(context, arc, finishes, active_franchises,
                         slot_rows=None, alltime_slot_rows=None,
                         alltime_pitching_rows=None, season_days=None,
                         detailed_alltime_rows=None,
                         acquisition_rows=None, alltime_acquisition_rows=None,
                         affinity_rows=None):
    """Advanced Standings: the rank-by-period arc with its toggleable
    line chart, the points-by-slot grids (season totals by deployed slot
    left; all-time PACES PER STANDARD SEASON right -- P era-complete,
    hitter slots capture-era, the Records-page convention), the
    acquisition-channel blocks (MLB-17's CBS twin), every season finish
    since the league began (champions marked, Div/Avg columns, former
    franchises folded into a hidden row group), and the MLB affinity
    chart (share of games by MLB club, season left / all-time right) at
    the bottom. The optional row sets render their sections only when
    supplied, so layout-only callers skip the heavier queries."""
    season = context['season_year']
    period = context['latest_period']

    rows = [
        ['Advanced Standings'],
        [f'{season} through period {period} · finishes back to '
         f'{context["first_season"]} from the league\'s own year-end '
         f'standings pages.'],
        [],
    ]
    formats = [
        {'range': 'A1:AA1', 'format': {'textFormat': {'bold': True, 'fontSize': 14}}},
        {'range': 'A2:AA2', 'format': {'textFormat': {'italic': True},
                                       'backgroundColor': _PALE_BLUE}},
    ]

    def _section(label, width='AA'):
        rows.append([label])
        formats.append({'range': f'A{len(rows)}:{width}{len(rows)}',
                        'format': {'textFormat': {'bold': True, 'foregroundColor': _WHITE},
                                   'backgroundColor': _NAVY}})

    def _header(cells, width='AA'):
        rows.append(cells)
        formats.append({'range': f'A{len(rows)}:{width}{len(rows)}',
                        'format': {'textFormat': {'bold': True}}})

    def _note(text, width='AA'):
        rows.append([text])
        formats.append({'range': f'A{len(rows)}:{width}{len(rows)}',
                        'format': {'textFormat': {'italic': True, 'fontSize': 9}}})

    def _sub_labels(width_cols, left_label, right_start, right_label):
        cells = [''] * width_cols
        cells[1] = left_label
        cells[right_start] = right_label
        rows.append(cells)
        formats.append({'range': f'A{len(rows)}:{_col(width_cols)}{len(rows)}',
                        'format': {'textFormat': {'bold': True}}})

    latest = [r for r in arc if r['is_latest_period']]

    # Roll up by CANONICAL franchise (MLB-64): a club that left and returned
    # under a new id (Foster's Folly 13 -> 30) is ONE row/column on every
    # franchise-keyed surface below.
    fmap = get_franchise_map()

    def _canon(fid):
        return fmap.get(fid, {}).get('canonical_id', fid)

    # Active canonical franchises in current-standings order -- the shared
    # row/column order of the chart toggles, slot grids, acquisition
    # blocks, and affinity chart.
    ranked_canon, canon_label = [], {}
    for row in latest:
        cid = _canon(int(row['team_id']))
        if cid not in canon_label:
            ranked_canon.append(cid)
            canon_label[cid] = row['team_name']
    canon_abbrevs = [fmap.get(cid, {}).get('abbrev') or f'#{cid}'
                     for cid in ranked_canon]
    n_teams = len(ranked_canon)

    # Closed seasons per canonical franchise (membership windows).
    seasons_played = {}
    for r in finishes:
        seasons_played.setdefault(
            _canon(int(r['franchise_id'])), set()).add(int(r['season_year']))

    # The standard-season clock (Kyle 2026-07-17 round 3): N = the median
    # CLOSED season's gameplay days; every season weighs days/N
    # season-equivalents -- 2020 counts the third it played, the in-flight
    # year counts its days so far, and an accidental short season (late
    # draft) self-reports short. Shared by the slot grids and the
    # all-time detailed standings.
    days_by_season = {int(r['season_year']): int(r['days'])
                      for r in season_days or ()}
    closed_days = sorted(d for s, d in days_by_season.items()
                         if s != int(season))
    n_std = closed_days[len(closed_days) // 2] if closed_days else None

    def _season_equivalents(season_set):
        if not n_std or not season_set:
            return None
        return sum(days_by_season.get(s, 0) for s in season_set) / n_std

    def _member_equivalents(cid):
        return _season_equivalents(
            set(seasons_played.get(cid, set())) | {int(season)})

    # ---- the season arc: the chart IS the section (Kyle round 7 -- the
    # rank matrix went away entirely; its data lives in the hidden helper).
    _section(f'{season} RANK BY PERIOD')
    periods = sorted({int(r['period']) for r in arc})
    rank_by = {}
    for r in arc:
        rank_by[(r['team_id'], int(r['period']))] = int(r['standings_rank'])
    latest_sorted = sorted(latest, key=lambda r: r['standings_rank'])

    # Team toggles: abbrev labels over checkboxes + one ALL master.
    # Native checkboxes can't rewrite each other (that needs Apps
    # Script), so the default state is Kyle's round-7 scheme: individual
    # boxes OFF, ALL on -- plotted = OR(ALL, own box). Uncheck ALL, then
    # check the team(s) you want: a one-click path to a single line.
    # Toggles are SHARED sheet state; a re-render resets the defaults.
    rows.append(['Chart teams:', *canon_abbrevs, 'ALL'])
    formats.append({'range': f'A{len(rows)}:{_col(2 + n_teams)}{len(rows)}',
                    'format': {'textFormat': {'bold': True}}})
    rows.append(['(check to plot)', *[False] * n_teams, True])
    checkbox_row = len(rows)                # 1-based
    formats.append({'checkboxes':
                    f'B{checkbox_row}:{_col(2 + n_teams)}{checkbox_row}'})

    # Chart area over a hidden, SELF-CONTAINED helper block (cols AK+):
    # col AK = period, then one plot-formula column per team, then the
    # raw ranks as plain values (same rows, further right) that the
    # formulas read. flip = n+1 - rank puts 1st place at the TOP (the
    # Sheets API cannot reverse a chart axis, so the y numbers are
    # transformed and the axis is windowed).
    helper_col0 = 36                        # 0-based col AK, past the grid
    chart_first_row0 = len(rows)            # 0-based helper header row
    n_chart_rows = max(18, 1 + len(periods))
    flip = n_teams + 1
    all_cell = f'${_col(2 + n_teams)}${checkbox_row}'
    raw_col0 = helper_col0 + 1 + n_teams    # first raw-rank column, 0-based
    helper = [[''] * helper_col0
              + ['Period', *canon_abbrevs, *canon_abbrevs]]
    for j, p in enumerate(periods):
        cells = [''] * helper_col0 + [p]
        helper_row = chart_first_row0 + 1 + j + 1   # 1-based sheet row
        for t in range(n_teams):
            own = f'{_col(2 + t)}${checkbox_row}'
            raw_cell = f'{_col(raw_col0 + t + 1)}{helper_row}'
            cells.append(f'=IF(AND(OR({all_cell},{own}),{raw_cell}<>""),'
                         f'{flip}-{raw_cell},NA())')
        for t, team in enumerate(latest_sorted):
            cells.append(rank_by.get((team['team_id'], p), ''))
        helper.append(cells)
    rows.extend(helper)
    rows.extend([[]] * (n_chart_rows - len(helper)))
    formats.append({'hide_cols': (helper_col0, raw_col0 + n_teams)})
    formats.append({'chart': {
        'anchor': (chart_first_row0, 0),
        'first_row': chart_first_row0,
        'last_row': chart_first_row0 + 1 + len(periods),
        'domain_col': helper_col0,
        'series_cols': [helper_col0 + 1 + t for t in range(n_teams)],
        'view_max': flip,
        'title': f'{season} standings position by period (top = 1st)',
    }})
    rows.append([])

    # ---- historic finishes matrix (under the chart -- Kyle round 7)
    seasons = sorted({int(r['season_year']) for r in finishes})
    by_franchise = {}          # canonical_id -> {season: finish row}
    latest_name = {}           # canonical_id -> most-recent observed name
    _latest_year = {}
    for r in finishes:
        cid = _canon(int(r['franchise_id']))
        yr = int(r['season_year'])
        by_franchise.setdefault(cid, {})[yr] = r
        if yr >= _latest_year.get(cid, 0):
            _latest_year[cid] = yr
            latest_name[cid] = r['team_name']
    # A canonical franchise is active if ANY of its ids is on the current roster.
    active_ids = [_canon(int(r['team_id'])) for r in active_franchises]
    active_name = {_canon(int(r['team_id'])): r['team_name']
                   for r in active_franchises}

    # Division champions: best league finish within the division that
    # season (seasons without division data contribute none).
    div_champs = {}                # (season, division) -> best finish row
    for r in finishes:
        dv = r.get('division_name')
        if not dv:
            continue
        key = (int(r['season_year']), dv)
        cur = div_champs.get(key)
        if cur is None or int(r['standings_rank']) < int(cur['standings_rank']):
            div_champs[key] = r
    div_by_canon = {}
    div_cells = set()              # (canonical_id, season): green border
    for r in div_champs.values():
        cid = _canon(int(r['franchise_id']))
        div_by_canon[cid] = div_by_canon.get(cid, 0) + 1
        div_cells.add((cid, int(r['season_year'])))

    # The in-flight season rides as the LAST column: current rank, plain
    # numbers (rank 1 stays '1', no trophy) -- and it counts toward
    # nothing (Titles / Div / Avg are closed-seasons-only).
    current_rank = {_canon(int(r['team_id'])): int(r['standings_rank'])
                    for r in latest}

    def _franchise_stats(fid):
        entries = by_franchise.get(fid, {})
        titles = sum(1 for e in entries.values() if e['is_champion'])
        ranks = [int(e['standings_rank']) for e in entries.values()]
        avg = round(sum(ranks) / len(ranks), 1) if ranks else None
        return titles, div_by_canon.get(fid, 0), avg

    def _finish_sort_key(fid):
        titles, _div, avg = _franchise_stats(fid)
        return (-titles, avg if avg is not None else 99.0,
                latest_name.get(fid, ''))

    year_labels = [str(y) for y in seasons] + [str(season)]
    finish_header = ['Franchise', 'Titles', 'Div', 'Avg'] + year_labels
    last_finish_col = _col(4 + len(year_labels))
    _section(f'SEASON FINISHES {seasons[0]}–{season}', width=last_finish_col)
    _note('🏆 = Season Champion. Bright Green Border = Division Champion. '
          'Uses most current Team Names; franchises stitched across '
          'renames + re-ids.', width=last_finish_col)
    # The trophy glyph stays upright inside the italic note (Kyle round
    # 12); the emoji is 2 UTF-16 units, so italics resume at index 2.
    formats.append({'range': 'A' + str(len(rows)), 'runs': [
        {'startIndex': 0, 'format': {'italic': False}},
        {'startIndex': 2, 'format': {'italic': True}},
    ]})
    _header(finish_header, width=last_finish_col)

    def _matrix_section_formats(header_row, n_rows, hide=False):
        """Finish-matrix dressing for one section: center everything right
        of the Franchise column (header included) and give Avg its one
        decimal. hide=True (the former-franchise section) additionally
        folds the header + data rows into a hidden row group -- the navy
        section band stays visible as the cue that there's something to
        expand. The rank gradient is emitted per-YEAR after both sections
        exist (each year's rule spans both matrices)."""
        first, last = header_row + 1, header_row + n_rows
        formats.append({'range': f'B{header_row}:{last_finish_col}{last}',
                        'format': {'horizontalAlignment': 'CENTER'}})
        formats.append({'range': f'D{first}:D{last}',
                        'format': {'numberFormat':
                                   {'type': 'NUMBER', 'pattern': '0.0'}}})
        if hide:
            formats.append({'hide_rows': (header_row - 1, last)})

    def _div_border():
        # Fresh dicts per cell (the gspread mutation lesson). #00ff00 is
        # bright enough to read against the green-shaded cells (Kyle).
        side = {'style': 'SOLID_MEDIUM',
                'color': {'red': 0.0, 'green': 1.0, 'blue': 0.0}}
        return {'top': dict(side), 'bottom': dict(side),
                'left': dict(side), 'right': dict(side)}

    def _append_finish_row(fid, name):
        titles, div_titles, avg = _franchise_stats(fid)
        cells = []
        for y in seasons:
            entry = by_franchise.get(fid, {}).get(y)
            if entry is None:
                cells.append('')
            elif entry['is_champion']:
                cells.append('🏆')
            else:
                cells.append(int(entry['standings_rank']))
        rows.append([name, titles or '', div_titles or '',
                     avg if avg is not None else '']
                    + cells + [current_rank.get(fid, '')])
        r_num = len(rows)
        for k, y in enumerate(seasons):
            if (fid, y) in div_cells:
                cell = f'{_col(5 + k)}{r_num}'
                formats.append({'range': f'{cell}:{cell}',
                                'format': {'borders': _div_border()}})

    matrix_start = len(rows)
    active_header_row = len(rows)   # 1-based row number of the header row
    for fid in sorted(active_ids, key=_finish_sort_key):
        _append_finish_row(
            fid, active_name.get(fid, latest_name.get(fid, f'#{fid}')))
    _matrix_section_formats(active_header_row, len(active_ids))
    defunct = sorted(
        (fid for fid in by_franchise if fid not in set(active_ids)),
        key=_finish_sort_key,
    )
    former_span = None
    if defunct:
        rows.append([])
        _section('FORMER FRANCHISES (hidden by default -- expand the row '
                 'group)', width=last_finish_col)
        _header(finish_header, width=last_finish_col)
        former_header_row = len(rows)
        for fid in defunct:
            _append_finish_row(fid, latest_name.get(fid, f'#{fid}'))
        _matrix_section_formats(former_header_row, len(defunct), hide=True)
        former_span = (former_header_row + 1, former_header_row + len(defunct))

    # Rank gradient, ONE RULE PER YEAR COLUMN spanning both matrices (the
    # in-flight column included): MIN/median/MAX auto-scale within the
    # year's own field, so a short-field season (2020 ran 12) still
    # paints its last place full red.
    active_span = (active_header_row + 1, active_header_row + len(active_ids))
    for k in range(len(year_labels)):
        c = _col(5 + k)
        year_ranges = [f'{c}{active_span[0]}:{c}{active_span[1]}']
        if former_span:
            year_ranges.append(f'{c}{former_span[0]}:{c}{former_span[1]}')
        formats.append({'ranges': year_ranges, 'gradient': _finish_gradient()})

    # champion highlight: the finish scale's best-finish green on 🏆 cells
    # (the numeric gradient skips text cells, so they need the static fill)
    for i, row in enumerate(rows[matrix_start:], start=matrix_start):
        for j, cell in enumerate(row):
            if cell == '🏆':
                col = gspread.utils.rowcol_to_a1(i + 1, j + 1)
                formats.append({'range': f'{col}:{col}',
                                'format': {'backgroundColor': _FINISH_GREEN,
                                           'textFormat': {'bold': True}}})
    _note('Div counts division titles (best league finish within the '
          'division that season); Avg is the mean finish across CLOSED '
          'seasons -- the in-flight column shows current rank and counts '
          'toward nothing. 2002 ran 15 teams and 2020 ran 12.',
          width=last_finish_col)
    rows.append([])

    # ---- points by lineup slot (season totals left, all-time paces right)
    if slot_rows or alltime_slot_rows or alltime_pitching_rows:
        season_slots = list(CBS_SLOT_CAPS)
        n_l = len(season_slots)
        grid_width = 2 + 2 * n_l            # Team + left + buffer + right
        grid_last_col = _col(grid_width)

        slot_by, capture_by, p_by = {}, {}, {}
        capture_seasons = set()
        for r in slot_rows or ():
            key = (_canon(int(r['team_id'])), r['lineup_slot'])
            slot_by[key] = slot_by.get(key, 0.0) + float(r['slot_pts'] or 0)
        for r in alltime_slot_rows or ():
            key = (_canon(int(r['team_id'])), r['lineup_slot'])
            capture_by[key] = (capture_by.get(key, 0.0)
                               + float(r['slot_pts'] or 0))
            capture_seasons.add(int(r['season_year']))
        for r in alltime_pitching_rows or ():
            cid = _canon(int(r['team_id']))
            p_by[cid] = p_by.get(cid, 0.0) + float(r['p_pts'] or 0)

        capture_eq = _season_equivalents(capture_seasons)

        _section('POINTS BY LINEUP SLOT', width=grid_last_col)
        _note('All-time cells are paces per standard season '
              f'(= {n_std} gameplay days); short seasons (2020) and the '
              'season in flight weigh exactly the days they played. The P '
              'column spans all years -- started pitching is the P slot '
              'in every era -- while hitter slots exist only where daily '
              'lineups are captured (2001-25 logged "active", not the '
              'slot), matching the Records page.', width=grid_last_col)
        _sub_labels(grid_width,
                    f'{season} to date -- totals by deployed slot',
                    2 + n_l,
                    'All-time -- pace per standard season')
        _header(['Team', *season_slots, '', *season_slots],
                width=grid_last_col)
        grid_first = len(rows) + 1
        for cid in ranked_canon:
            member = set(seasons_played.get(cid, set())) | {int(season)}
            member_eq = _season_equivalents(member)
            left = [slot_by.get((cid, s), '') for s in season_slots]
            right = []
            for s in season_slots:
                if s == 'P':
                    pts, eq = p_by.get(cid), member_eq
                else:
                    pts, eq = capture_by.get((cid, s)), capture_eq
                right.append(round(pts / eq, 1) if pts and eq else '')
            rows.append([canon_label.get(cid, f'#{cid}'), *left, '', *right])
        grid_last = len(rows)
        for ci in [*range(1, 1 + n_l), *range(2 + n_l, grid_width)]:
            col = _col(ci + 1)
            formats.append({'range': f'{col}{grid_first}:{col}{grid_last}',
                            'gradient': _points_gradient()})
        # Whole-point display (Kyle 2026-07-17 round 5): the underlying
        # values keep their precision for the gradients.
        formats.append({'range': f'B{grid_first}:{grid_last_col}{grid_last}',
                        'format': {'numberFormat':
                                   {'type': 'NUMBER', 'pattern': '0'}}})
        rows.append([])

    # ---- all-time detailed standings (Kyle round 8: CBS's first
    # per-stat standings table is all-time-only -- the current season
    # reads fine on the CBS site; too wide for the L/R split, so it
    # stands alone on the franchise spine, paced per standard season)
    if detailed_alltime_rows and season_days:
        det_by = {}
        for r in detailed_alltime_rows:
            cid = _canon(int(r['team_id']))
            bucket = det_by.setdefault(cid, {})
            for k, v in r.items():
                if k != 'team_id':
                    bucket[k] = bucket.get(k, 0.0) + float(v or 0)

        hit_cols = [(s, _REC_STAT_COL[s]) for s in _HIT_ORDER]
        pit_cols = [('IP' if s == 'OUTS' else s, _REC_STAT_COL[s])
                    for s in _PIT_ORDER]
        det_header = (['Franchise']
                      + ['BB' if s == 'B_BB' else s for s, _ in hit_cols]
                      + ['Hit Pts', '']
                      + [s for s, _ in pit_cols] + ['Pit Pts', '', 'Total'])
        det_width = len(det_header)
        det_last_col = _col(det_width)
        _section('ALL-TIME DETAILED STANDINGS', width=det_last_col)
        _note('Active-lens production paced per standard season (the '
              f'{n_std}-gameplay-day clock above); IP = innings pitched. '
              'Ordered by total pace.', width=det_last_col)
        _header(det_header, width=det_last_col)
        det_first = len(rows) + 1

        def _det_pace(cid, col, eq):
            v = det_by.get(cid, {}).get(col, 0.0)
            if col == 'outs':
                v /= 3.0
            return round(v / eq, 1) if eq else ''

        ranked_by_pace = sorted(
            ranked_canon,
            key=lambda c: -(det_by.get(c, {}).get('total_pts', 0.0)
                            / (_member_equivalents(c) or 1)))
        for cid in ranked_by_pace:
            eq = _member_equivalents(cid)
            rows.append([
                canon_label.get(cid, f'#{cid}'),
                *[_det_pace(cid, col, eq) for _, col in hit_cols],
                _det_pace(cid, 'hit_pts', eq), '',
                *[_det_pace(cid, col, eq) for _, col in pit_cols],
                _det_pace(cid, 'pit_pts', eq), '',
                _det_pace(cid, 'total_pts', eq),
            ])
        det_last = len(rows)
        value_cols = [i for i, label in enumerate(det_header)
                      if i > 0 and label != '']
        for ci in value_cols:
            col = _col(ci + 1)
            formats.append({'range': f'{col}{det_first}:{col}{det_last}',
                            'gradient': _points_gradient()})
        formats.append({'range': f'B{det_first}:{det_last_col}{det_last}',
                        'format': {'numberFormat':
                                   {'type': 'NUMBER', 'pattern': '0'}}})
        rows.append([])

    # ---- production by acquisition channel (the ESPN MLB-17 blocks'
    # CBS twin: current season + the all-time mirror; Kyle rounds 5+6)
    if acquisition_rows or alltime_acquisition_rows:
        def _bucketize(rowset):
            out = {}
            for r in rowset or ():
                cid = _canon(int(r['team_id']))
                bucket = out.setdefault(cid, {})
                for k, v in r.items():
                    if k.endswith('_pts'):
                        bucket[k] = bucket.get(k, 0.0) + float(v or 0)
            return out

        season_acq = _bucketize(acquisition_rows)
        # All-time = the walk-back-era rows (through the last closed
        # season, stint-channeled) + this season's log-channeled rows.
        alltime_acq = _bucketize(alltime_acquisition_rows)
        if alltime_acq:
            for cid, bucket in season_acq.items():
                target = alltime_acq.setdefault(cid, {})
                for k, v in bucket.items():
                    target[k] = target.get(k, 0.0) + v

        half = ['Opening', 'Pickup', 'Trade', 'Total', '',
                'Release', 'Trade', 'Total', '', 'FA', 'Trade']
        n_half = len(half)                      # 11
        acq_width = 2 + 2 * n_half              # Team + halves + buffer
        acq_last_col = _col(acq_width)
        _section('PRODUCTION BY ACQUISITION CHANNEL', width=acq_last_col)
        _note("Points each franchise's roster produced, split by how each "
              "player arrived (Opening = on the roster at first pitch -- "
              "CBS never logged drafts, so draft and keeper both live "
              "there), against what departed players went on to produce "
              "after leaving. Lost is season-bounded everywhere: a "
              "departure's window ends with that season (or the player's "
              "return). Net FA = pickups acquired minus releases lost; "
              "Net Trade = trades acquired minus trades lost.",
              width=acq_last_col)

        def _half_values(bucket, lens):
            opening = bucket.get(f'opening_{lens}_pts', 0.0)
            fa = bucket.get(f'fa_add_{lens}_pts', 0.0)
            trade = bucket.get(f'trade_{lens}_pts', 0.0)
            dropped = bucket.get(f'dropped_{lens}_pts', 0.0)
            traded = bucket.get(f'traded_away_{lens}_pts', 0.0)
            return [
                round(opening, 1), round(fa, 1), round(trade, 1),
                round(bucket.get(f'acquired_{lens}_pts', 0.0), 1), '',
                round(dropped, 1), round(traded, 1),
                round(bucket.get(f'lost_{lens}_pts', 0.0), 1), '',
                round(fa - dropped, 1), round(trade - traded, 1),
            ]

        def _emit_lens_table(lens, label):
            # Kyle's round-7 shape: ONE table per lens, season half left /
            # all-time half right on the ACTIVE-franchise spine (formers
            # filtered by construction), group bands over each half.
            rows.append([label])
            formats.append({'range': f'A{len(rows)}:{acq_last_col}{len(rows)}',
                            'format': {'textFormat': {'bold': True}}})
            _sub_labels(acq_width, f'{season} to date', 2 + n_half,
                        f'All-Time ({context["first_season"]}-{season})')
            bands = [''] * acq_width
            for base in (1, 2 + n_half):
                bands[base] = 'Points Acquired Via'
                bands[base + 5] = 'Points Lost Via'
                bands[base + 9] = 'Net Points via'
            rows.append(bands)
            band_row = len(rows)
            formats.append({'range': f'A{band_row}:{acq_last_col}{band_row}',
                            'format': {'textFormat': {'bold': True},
                                       'horizontalAlignment': 'CENTER'}})
            for base in (1, 2 + n_half):
                for start, end in ((base, base + 3), (base + 5, base + 7),
                                   (base + 9, base + 10)):
                    formats.append({'range': f'{_col(start + 1)}{band_row}:'
                                             f'{_col(end + 1)}{band_row}',
                                    'merge': True})
            _header(['Team', *half, '', *half], width=acq_last_col)
            first = len(rows) + 1
            ranked = sorted(
                ranked_canon,
                key=lambda c: (-season_acq.get(c, {}).get(
                    f'acquired_{lens}_pts', 0.0), canon_label.get(c, '')))
            for cid in ranked:
                rows.append([
                    canon_label.get(cid, f'#{cid}'),
                    *_half_values(season_acq.get(cid, {}), lens), '',
                    *(_half_values(alltime_acq.get(cid, {}), lens)
                      if alltime_acq else [''] * n_half),
                ])
            last = len(rows)
            grads = ((0, _points_gradient), (1, _points_gradient),
                     (2, _points_gradient), (3, _points_gradient),
                     (5, _points_gradient_low), (6, _points_gradient_low),
                     (7, _points_gradient_low),
                     (9, _diverging_gradient), (10, _diverging_gradient))
            bases = (1, 2 + n_half) if alltime_acq else (1,)
            for base in bases:
                for off, grad in grads:
                    col = _col(base + off + 1)
                    formats.append({'range': f'{col}{first}:{col}{last}',
                                    'gradient': grad()})
            formats.append({'range': f'B{first}:{acq_last_col}{last}',
                            'format': {'numberFormat':
                                       {'type': 'NUMBER', 'pattern': '0'}}})
            rows.append([])

        _emit_lens_table(
            'active',
            'Active Lens - started points only '
            '(Lost = production started by other franchises)')
        _emit_lens_table(
            'rostered',
            'Rostered Lens - all points incl. reserves '
            '(Lost = other franchises AND unowned)')

    # ---- MLB affinity (season left, all-time right; shared MLB spine)
    if affinity_rows:
        names, season_g, alltime_g = {}, {}, {}
        for r in affinity_rows:
            cid = _canon(int(r['team_id']))
            mid = int(r['mlb_team_id'])
            names[mid] = r['mlb_team_name']
            key = (cid, mid)
            season_g[key] = season_g.get(key, 0.0) + float(r['season_wt'] or 0)
            alltime_g[key] = alltime_g.get(key, 0.0) + float(r['alltime_wt'] or 0)
        mlb_ids = sorted(names, key=lambda m: names[m])
        season_tot = {cid: sum(season_g.get((cid, m), 0.0) for m in mlb_ids)
                      for cid in ranked_canon}
        alltime_tot = {cid: sum(alltime_g.get((cid, m), 0.0) for m in mlb_ids)
                       for cid in ranked_canon}
        abbrevs = [fmap.get(cid, {}).get('abbrev') or f'#{cid}'
                   for cid in ranked_canon]
        n_t = len(ranked_canon)
        aff_width = 2 + 2 * n_t
        aff_last_col = _col(aff_width)

        def _share(games, total):
            # Fractions, not x100 -- the blocks carry a PERCENT number
            # format so the sheet displays 0.123 as 12.3%.
            return round(games / total, 3) if games and total else ''

        rows.append([])
        _section('MLB Affinity Chart', width=aff_last_col)
        _note("Share of each franchise's active-lineup involvement -- "
              "defined as plate appearances + batters faced -- with each "
              "MLB club (pure GP would underweight pitchers). 2004-2020 "
              "is estimated by start share; other years reconstruct from "
              "the league's own lineup logs (2026 captured live). Bold "
              "indicates highest value for given MLB team.",
              width=aff_last_col)
        _sub_labels(aff_width, f'{season} to date', 2 + n_t, 'All-time')
        _header(['MLB Team', *abbrevs, '', *abbrevs], width=aff_last_col)
        aff_first = len(rows) + 1

        def _bold_row_max(row_number, values, first_col_1b):
            # The club's biggest devotee per block, bolded (ties all bold).
            numeric = [v for v in values if isinstance(v, (int, float))]
            if not numeric:
                return
            peak = max(numeric)
            for k, v in enumerate(values):
                if isinstance(v, (int, float)) and v == peak:
                    cell = f'{_col(first_col_1b + k)}{row_number}'
                    formats.append({'range': f'{cell}:{cell}',
                                    'format': {'textFormat': {'bold': True}}})

        for mid in mlb_ids:
            left = [_share(season_g.get((cid, mid)), season_tot.get(cid))
                    for cid in ranked_canon]
            right = [_share(alltime_g.get((cid, mid)), alltime_tot.get(cid))
                     for cid in ranked_canon]
            rows.append([names[mid], *left, '', *right])
            _bold_row_max(len(rows), left, 2)
            _bold_row_max(len(rows), right, 3 + n_t)
        aff_last = len(rows)
        block_ranges = [
            f'{_col(2)}{aff_first}:{_col(1 + n_t)}{aff_last}',
            f'{_col(3 + n_t)}{aff_first}:{_col(aff_width)}{aff_last}',
        ]
        for rng in block_ranges:
            # Light-gray base for true zero/null cells (the gradient only
            # paints numeric cells over it), whole-percent display, and
            # centered values (Kyle 2026-07-17 round 5).
            formats.append({'range': rng, 'format': {
                'backgroundColor': _LIGHT_GRAY,
                'horizontalAlignment': 'CENTER',
                'numberFormat': {'type': 'PERCENT', 'pattern': '0%'},
            }})
            # Per-block rule: each matrix scales to its own spread.
            formats.append({'range': rng, 'gradient': _share_gradient()})

    # Unified navy width (Kyle round 12): every section band runs as far
    # as the widest one on the tab.
    navy_specs = [s for s in formats
                  if s.get('format', {}).get('backgroundColor') == _NAVY]
    if navy_specs:
        def _range_end_width(a1):
            letters = ''.join(c for c in a1.split(':')[1] if c.isalpha())
            n = 0
            for ch in letters:
                n = n * 26 + (ord(ch) - 64)
            return n

        band_col = _col(max(_range_end_width(s['range'])
                            for s in navy_specs))
        for s in navy_specs:
            row_num = ''.join(c for c in s['range'].split(':')[0]
                              if c.isdigit())
            s['range'] = f'A{row_num}:{band_col}{row_num}'

    return rows, formats


# build_team_tab / _lineup_block / _lineup_row / _merge_bands and the old
# two-band CBS team-page shape retired 2026-07-16: team tabs now render
# through the SHARED almanac_logic.build_team_history_tabs in ESPN's exact
# shape (see get_cbs_team_history_data + build_all_tabs).


# ---------------------------------------------------------------------------
# Draft Recap tab (2026-07-18, first cut for Kyle's QA)
# ---------------------------------------------------------------------------

_DRAFT_LAST_COL = 'V'   # Rd/Pick/Team/Player/Max/Med (A-F) + 16 slot/team cols
_DRAFT_VALUE_HEADER = ['Pts', 'Tm', 'Player', '(Rd) #Pick', 'Δ Rank']
# All-time board coverage: pick sequence exists only where CBS recorded
# it ('true': the 2025/2026 online drafts). Every other year is EXCLUDED
# from the board and surfaces in Draft Classes instead: the team-list
# years ride roster order, not draft order (proven: rho ~ -0.3..0.3 vs
# value; the 2020 'rounds' are entry batches), and the 2024 zip of the
# order skeleton x team lists inherits the same flaw on the PLAYER side
# (its first render put Ohtani in all-time Round 19 -- indefensible).
_DRAFT_ORDERED_TIERS = ('true',)
_DRAFT_SEQUENCE_LABELS = {
    'true': 'recorded',
    'zip': 'not recoverable',
    'rounds_suspect': 'as-entered (suspect)',
    'none': 'not recorded',
}
_ALLTIME_CELLS_LABEL = "Each Round × Pick's Historical Median Value"
# LEAGUE-SPECIFIC prose (Kyle 2026-07-18): true of bsb, NOT universal to
# CBS -- another CBS league might avail older drafts. The coverage YEARS
# beside it are data-driven (see _draft_coverage); only this explanation
# is hand-written, and it's the one bit that would move to a per-league
# config if a second CBS league ever lands.
_BSB_DRAFT_CAVEAT = ('*CBS does not avail draft data prior to 2025; if a league '
                     'member has any of that information we can append it here.')


def _draft_coverage(picks, lens):
    """Data-driven coverage string: the year range for which we have pick
    order (order_tier in _DRAFT_ORDERED_TIERS) AND who was taken -- the
    thing Kyle wants a script to keep honest. En-dash range when
    contiguous, else a comma list."""
    years = sorted({p['season_year'] for p in picks
                    if p['order_tier'] in _DRAFT_ORDERED_TIERS
                    and p.get(lens) is not None})
    if not years:
        return ''
    if len(years) == 1:
        return str(years[0])
    if years == list(range(years[0], years[-1] + 1)):
        return f'{years[0]}–{years[-1]}'
    return ', '.join(str(y) for y in years)


def _draft_points_gradient():
    """Red -> white -> green over season points, the ESPN board's scale.
    Fresh dicts per call (the gspread in-place-mutation lesson)."""
    return {
        'minpoint': {'type': 'MIN', 'color': _FINISH_RED},
        'midpoint': {'type': 'PERCENTILE', 'value': '50',
                     'color': {'red': 1, 'green': 1, 'blue': 1}},
        'maxpoint': {'type': 'MAX', 'color': _FINISH_GREEN},
    }


def _pts0(value):
    return int(round(value)) if value is not None else ''


def _draft_link(pick, label=None):
    name = pick.get('player_name_raw') or ''
    return _bref_link(name, label if label is not None else name)


def build_draft_recap_rows(season_year, franchise_map, value_lens='calc_total',
                           history=None, season_clocks=None):
    """The Draft Recap tab: (rows, formats).

    Three sections:
      1. The current season, mirroring the ESPN Draft Recap: Best Value /
         Biggest Busts leaderboards (value = overall pick minus season-
         points rank) over a round x team board with per-round
         Min/Median/Max. The season's Mini+Mega drafts are stitched.
      2. The all-time board, TEAM-AGNOSTIC and re-cut to the current
         16-team shape (Kyle's spec): all-time Round N = overall picks
         (N-1)*16+1..N*16 of each ordered season; each slot cell is the
         average points of that slot across seasons; Med/Max/Top Pick
         summarize each round (Top Pick = the Max's 'Player -year').
         Coverage is honestly thin -- see _DRAFT_ORDERED_TIERS.
      3. Draft Classes: the order-free digest every recorded draft gets
         (best picks by season points), carrying the per-year provenance
         the no-order years can't put on a board.

    value_lens keys the pick-value metric ('calc_total' now; 'calc_hitting',
    'calc_pitching', or the page lenses 'page_total_fpts'/'page_active_fpts'
    are drop-in swaps once Kyle picks -- everything downstream reads it).
    history injects (picks, report) for tests; None fetches live."""
    picks, report = history if history is not None else get_draft_history()
    lens = value_lens
    n_teams = 16
    abbrev_by_name = {}
    for meta in (franchise_map or {}).values():
        if meta.get('name'):
            abbrev_by_name[meta['name']] = meta.get('abbrev') or meta['name'][:4]

    def _abbrev(team):
        team = team or ''
        return abbrev_by_name.get(team, team[:4])

    def _initial_cell(p):
        return _bref_link(p['player_name_raw'], draft_initial_text(p['player_name_raw']))

    rows, formats = [], []

    def _band(label, note=None):
        rows.append([label])
        formats.append({'range': f'A{len(rows)}:{_DRAFT_LAST_COL}{len(rows)}',
                        'format': {'textFormat': {'bold': True},
                                   'backgroundColor': _POWDER}})
        if note:
            rows.append([note])
            formats.append({'range': f'A{len(rows)}:{_DRAFT_LAST_COL}{len(rows)}',
                            'format': {'textFormat': {'italic': True, 'fontSize': 9}}})

    def _board_headers(header_cols, cells_label=None):
        # Navy 'Top Pick' super-header (merged B:D), plus an optional label
        # merged over the cell columns (G:end), then the navy header row
        # with the Pick/Team/Player trio in powder -- identical to the ESPN
        # board (Kyle 2026-07-18).
        super_row = ['', 'Top Pick']
        if cells_label:
            super_row = super_row + [''] * 4 + [cells_label]   # label lands at G
        rows.append(super_row)
        sr = len(rows)
        formats.append({'range': f'A{sr}:{_DRAFT_LAST_COL}{sr}',
                        'format': {'textFormat': {'bold': True, 'foregroundColor': _WHITE},
                                   'backgroundColor': _NAVY,
                                   'horizontalAlignment': 'CENTER'}})
        formats.append({'range': f'B{sr}:D{sr}', 'merge': True})
        if cells_label:
            formats.append({'range': f'G{sr}:{_DRAFT_LAST_COL}{sr}', 'merge': True})
        rows.append(header_cols)
        hr = len(rows)
        formats.append({'range': f'A{hr}:{_DRAFT_LAST_COL}{hr}',
                        'format': {'textFormat': {'bold': True, 'foregroundColor': _WHITE},
                                   'backgroundColor': _NAVY}})
        formats.append({'range': f'B{hr}:D{hr}',
                        'format': {'textFormat': {'bold': True},
                                   'backgroundColor': _POWDER_HEADER}})
        return hr

    coverage = _draft_coverage(picks, lens)
    rows.append([DRAFT_TAB])
    formats.append({'range': f'A1:{_DRAFT_LAST_COL}1',
                    'format': {'textFormat': {'bold': True, 'fontSize': 14}}})
    rows.append([f'Coverage: {coverage}*  Points are calculated using current '
                 f'league scoring.'])
    formats.append({'range': f'A2:{_DRAFT_LAST_COL}2',
                    'format': {'textFormat': {'italic': True},
                               'backgroundColor': _PALE_BLUE}})
    rows.append([_BSB_DRAFT_CAVEAT])
    formats.append({'range': f'A3:{_DRAFT_LAST_COL}3',
                    'format': {'textFormat': {'italic': True, 'fontSize': 9}}})

    # ---- Section 1: the current season, ESPN-shaped ----------------------
    year_picks = [p for p in picks
                  if p['season_year'] == season_year and p.get('overall_pick')]
    ranked = sorted([p for p in year_picks if p.get(lens) is not None],
                    key=lambda p: (-p[lens], p['overall_pick']))
    for rank, p in enumerate(ranked, start=1):
        p['points_rank'] = rank
        p['value_delta'] = p['overall_pick'] - rank
    parts = ' + '.join(dict.fromkeys(p['draft_label'] for p in year_picks))
    _band(f'Draft Recap: {season_year}',
          f'{parts} stitched as one {len(year_picks)}-pick draft. Δ Rank = overall '
          f'pick minus season Total Points rank (positive = steal). (No keepers '
          f'in this league.)')
    rows.append([])
    # Value block B-F, a buffer column at G, busts block H-L (Kyle
    # 2026-07-18); the powder banner runs the full width across the buffer.
    rows.append(['', 'Best Value Picks', '', '', '', '', '', 'Biggest Busts'])
    for rng in (f'B{len(rows)}:F{len(rows)}', f'H{len(rows)}:L{len(rows)}'):
        formats.append({'range': rng,
                        'format': {'textFormat': {'bold': True, 'fontSize': 12}}})
    rows.append(['', *_DRAFT_VALUE_HEADER, '', *_DRAFT_VALUE_HEADER])
    hdr = len(rows)
    formats.append({'range': f'B{hdr}:L{hdr}',
                    'format': {'textFormat': {'bold': True},
                               'backgroundColor': _PALE_BLUE}})
    for col in ('B', 'H'):
        formats.append({'range': f'{col}{hdr + 1}:{col}{hdr + 10}',
                        'format': {'numberFormat': {'type': 'NUMBER', 'pattern': '0.0'}}})
    best = sorted(ranked, key=lambda p: (-p['value_delta'], p['overall_pick']))[:10]
    busts = sorted(ranked, key=lambda p: (p['value_delta'], p['overall_pick']))[:10]

    def _value_cells(p):
        return [round(p[lens], 1), _abbrev(p['team_name_raw']),
                _draft_link(p), f"R{p['round_num']} #{p['overall_pick']}",
                f"{p['value_delta']:+d}"]

    blank5 = ['', '', '', '', '']
    for i in range(max(len(best), len(busts))):
        good = _value_cells(best[i]) if i < len(best) else list(blank5)
        bad = _value_cells(busts[i]) if i < len(busts) else list(blank5)
        rows.append(['', *good, '', *bad])
    rows.append([])

    # ---- Current-season board (Top Pick trio + Max/Med + team cells) -----
    _band(f'Draft Board - {season_year}')
    team_order, seen = [], set()
    for p in sorted(year_picks, key=lambda p: p['overall_pick']):
        if p['team_name_raw'] not in seen:
            seen.add(p['team_name_raw'])
            team_order.append(p['team_name_raw'])
    board_header_row = _board_headers(
        ['Rd', 'Pick', 'Team', 'Player', 'Max', 'Med',
         *[_abbrev(t) for t in team_order]])
    by_round_team = {}
    for p in year_picks:
        by_round_team[(p['round_num'], p['team_name_raw'])] = p
    max_round = max((p['round_num'] or 0) for p in year_picks)
    lens_vals = [p[lens] for p in year_picks if p.get(lens) is not None]
    lens_lo, lens_hi = (min(lens_vals), max(lens_vals)) if lens_vals else (0.0, 1.0)
    color_grid = []
    for rnd in range(1, max_round + 1):
        round_picks = [by_round_team.get((rnd, t)) for t in team_order]
        present = [p for p in round_picks if p and p.get(lens) is not None]
        if present:
            top = max(present, key=lambda p: p[lens])
            pts = [p[lens] for p in present]
            head = [(top['overall_pick'] - 1) % n_teams + 1, _abbrev(top['team_name_raw']),
                    _draft_link(top), round(max(pts)), round(statistics.median(pts))]
        else:
            head = ['', '', '', '', '']
        rows.append([rnd, *head,
                     *[_initial_cell(p) if p else '' for p in round_picks]])
        color_grid.append([
            _draft_gradient_color(float(p[lens]), lens_lo, lens_hi)
            if p and p.get(lens) is not None else None
            for p in round_picks])
    # ESPN's board scale on the CBS board: red->white->green backgrounds per
    # player cell (text can't ride a numeric gradient rule). Slot cells begin
    # at col index 6 (after Rd/Pick/Team/Player/Max/Med), one row below the
    # header (board_header_row is 1-based == the 0-based first data row).
    formats.append({'cell_colors': {'start_row0': board_header_row,
                                    'start_col0': 6, 'grid': color_grid}})
    # Center the board's numeric columns -- Rd/Pick (A:B) + Max/Med (E:F);
    # Team/Player and the name cells stay left (Kyle 2026-07-18).
    if len(rows) > board_header_row:
        for rng in (f'A{board_header_row + 1}:B{len(rows)}',
                    f'E{board_header_row + 1}:F{len(rows)}'):
            formats.append({'range': rng,
                            'format': {'horizontalAlignment': 'CENTER'}})
    rows.append([])

    # ---- Section 2: the all-time board, 16-team shape, season-paced ------
    factors, _ = season_pace_factors(season_clocks or {}, season_year)
    hist = [p for p in picks
            if p['order_tier'] in _DRAFT_ORDERED_TIERS and p.get('overall_pick')
            and p.get(lens) is not None]
    rows.append(['All-Time Draft Board -- 16-Team Shape'])
    formats.append({'range': f'A{len(rows)}:{_DRAFT_LAST_COL}{len(rows)}',
                    'format': {'textFormat': {'bold': True},
                               'backgroundColor': _POWDER}})
    # Tightened note (Kyle 2026-07-18): the 'Each Round x Pick's Historical
    # Median Value' super-header now explains the cells, so the note just
    # defines Top Pick, with the data-driven Coverage + the bsb caveat
    # echoed down here.
    note = (['Team-agnostic, re-cut to the current draft shape: all-time. '
             'Top Pick = the top-scoring single pick ever made in that round.']
            + [''] * 10 + [f'Coverage: {coverage}', '', _BSB_DRAFT_CAVEAT])
    rows.append(note)
    formats.append({'range': f'A{len(rows)}:{_DRAFT_LAST_COL}{len(rows)}',
                    'format': {'textFormat': {'italic': True, 'fontSize': 9}}})
    alltime_header_row = _board_headers(
        ['Rd', 'Year', 'Team', 'Player', 'Max', 'Med',
         *[str(s) for s in range(1, 17)]],
        cells_label=_ALLTIME_CELLS_LABEL)
    slot_paced, round_paced, round_rows = {}, {}, {}
    for p in hist:
        rnd16 = (p['overall_pick'] - 1) // n_teams + 1
        slot16 = (p['overall_pick'] - 1) % n_teams + 1
        paced = p[lens] * factors.get(p['season_year'], 1.0)
        slot_paced.setdefault((rnd16, slot16), []).append(paced)
        round_paced.setdefault(rnd16, []).append(paced)
        round_rows.setdefault(rnd16, []).append(p)
    alltime_cells, med_col = [], []
    for rnd in sorted(round_rows):
        top = max(round_rows[rnd], key=lambda p: p[lens])   # straight, unpaced
        cells = []
        for slot in range(1, 17):
            vals = slot_paced.get((rnd, slot))
            cells.append(round(statistics.median(vals)) if vals else '')
        med_val = round(statistics.median(round_paced[rnd]))
        rows.append([rnd, top['season_year'], _abbrev(top['team_name_raw']),
                     _draft_link(top), round(top[lens]), med_val, *cells])
        alltime_cells.append(cells)
        med_col.append(med_val)
    flat = [c for row in alltime_cells for c in row if c != '']
    lo, hi = (min(flat), max(flat)) if flat else (0.0, 1.0)
    # Colour-grade the Med column (F) + the median cells (G:V) on one scale;
    # Max (E) stays plain -- it's a straight top-pick total on a different
    # scale, and pick numbers never grade (Kyle 2026-07-18).
    color_grid2 = [
        [_draft_gradient_color(float(med_col[i]), lo, hi)]
        + [_draft_gradient_color(float(c), lo, hi) if c != '' else None
           for c in alltime_cells[i]]
        for i in range(len(alltime_cells))]
    formats.append({'cell_colors': {'start_row0': alltime_header_row,
                                    'start_col0': 5, 'grid': color_grid2}})
    # Center Rd/Year (A:B) + Max/Med/cells (E:V); Team/Player stay left.
    if len(rows) > alltime_header_row:
        for rng in (f'A{alltime_header_row + 1}:B{len(rows)}',
                    f'E{alltime_header_row + 1}:{_DRAFT_LAST_COL}{len(rows)}'):
            formats.append({'range': rng,
                            'format': {'horizontalAlignment': 'CENTER'}})
    rows.append([])

    # ---- Section 3: Draft Classes (order-free, every draft) --------------
    _band('Draft Classes',
          'Every recorded draft, ranked by points regardless of pick order. '
          'CBS holds no draft records for 2009-2010, 2012, 2014, 2016 or '
          'earlier seasons (2009 kept the pick sequence but lost the players).')
    rows.append([])
    rows.append(['Year', 'Picks', 'Rounds', 'Sequence', 'Best Picks', '', '', 'Notes'])
    formats.append({'range': f'A{len(rows)}:{_DRAFT_LAST_COL}{len(rows)}',
                    'format': {'textFormat': {'bold': True}}})
    for year in sorted(report):
        info = report[year]
        year_all = [p for p in picks if p['season_year'] == year
                    and p.get(lens) is not None]
        top3 = sorted(year_all, key=lambda p: -p[lens])[:3]
        misses = sum(v for k, v in (info.get('resolution') or {}).items()
                     if k in ('ambiguous', 'unresolved'))
        notes = [info.get('note') or '']
        if misses:
            notes.append(f'{misses} unresolved name{"s" if misses > 1 else ""}')
        rows.append([
            year, info['picks'], info.get('rounds') or '',
            _DRAFT_SEQUENCE_LABELS.get(info['order'], info['order']),
            *[_draft_link(p, f"{p['player_name_raw']} ({_pts0(p[lens])})")
              for p in top3],
            '; '.join(n for n in notes if n),
        ])

    return rows, formats


def build_all_tabs(nav_targets=None):
    """Assemble every tab: [(title, rows, formats)], Home first, then
    Records, Standings, and one page per active franchise in current-
    standings order."""
    context = get_season_context()
    season = context['season_year']
    arc = get_standings_arc(season)
    finishes = get_historic_finishes()
    franchises = get_active_franchises(context['roster_date'])
    # Standings order for the Standings tab; team TABS sort alphabetically
    # by title inside the shared builder (Kyle 2026-07-17).
    latest = {r['team_id']: r for r in arc if r['is_latest_period']}
    franchises = sorted(
        franchises,
        key=lambda f: latest.get(f['team_id'], {}).get('standings_rank', 99),
    )

    # Canonical abbrev -> team-tab title, for the records-page hyperlinks: an
    # ACTIVE franchise's abbrev links to its team page (Kyle 2026-07-15). Only
    # actives are here, so a defunct-franchise abbrev never resolves to a link.
    _fmap = get_franchise_map()
    link_map = {_fmap.get(int(fr['team_id']), {}).get('abbrev'):
                _safe_sheet_title(fr['team_name'])
                for fr in franchises
                if _fmap.get(int(fr['team_id']), {}).get('abbrev')}

    # Team pages: ESPN's shape VERBATIM (Kyle 2026-07-16) -- the shared
    # almanac_logic builder over CBS data + the CBS starters selector.
    # Full-name tab titles (Kyle's one deliberate asymmetry), alphabetical
    # tab order (builder default), the era-keyed provenance lines in the
    # Lineup Data block, and the Best Individual Seasons block over
    # player-season candidates (Kyle 2026-07-17).
    history = get_cbs_team_history_data(context, franchises, _fmap)
    seasons_by_team = {}
    for r in history['player_seasons']:
        seasons_by_team.setdefault(r['team_id'], []).append(r)

    def _cbs_best_seasons(fid):
        candidates = get_optimal_season_candidates(fid)
        # DH/U are universal-fill slots: clone every hitter-season a DH +
        # U candidacy, same as the roster lineups (the eligibility arrays
        # deliberately carry only EARNED positions).
        candidates = _synthesize_universal_slots(candidates)
        return {'candidates': candidates,
                'seasons': seasons_by_team.get(fid, [])}

    team_pages = build_team_history_tabs(
        history,
        season_year=season,
        slot_caps={**CBS_SLOT_CAPS, 'BE': _CBS_BENCH_SLOTS},
        optimal_team_fn=_cbs_optimal_team,
        title_fn=lambda meta: _safe_sheet_title(meta.get('team_name') or ''),
        lineup_data=lambda fid: _provenance_era_lines(get_provenance_mix(fid)),
        best_seasons_fn=_cbs_best_seasons,
    )
    team_tabs = []
    for title, rows in team_pages:
        formats = team_tab_format_specs(rows)
        # A1 abbrev parenthetical: size-10 non-bold run (same treatment as
        # the ESPN writer's _title_abbrev_run_request).
        a1 = rows[0][0] if rows and rows[0] else ''
        split = a1.rfind(' (')
        if a1.endswith(')') and split > 0:
            formats.append({'range': 'A1', 'runs': [
                {'startIndex': 0, 'format': {'bold': True}},
                {'startIndex': split, 'format': {'bold': False, 'fontSize': 10}},
            ]})
        # Best Individual Seasons banner rows merge A:O (dynamic per tab;
        # the writer defers these until after its unmerge pass).
        formats.extend({'range': rng, 'merge': True}
                       for rng in team_tab_banner_merges(rows))
        team_tabs.append((title, rows, formats))

    context['team_titles'] = [title for title, _, _ in team_tabs]
    current_key, current_name = get_current_rostered()
    # Three Home boards. Week = lightweight trailing-week team (no bench).
    # Season = weighted-active display lineup + reserve bench, with the
    # rostered-lens starters (no bench) driving Total-Pts Best. All-Time =
    # display lineup + bench, re-keyed for the active/retired split +
    # years-of-service (its longevity column replaces the deviation).
    context['month_window'] = _month_window()
    context['month_board'] = get_window_lineup(*context['month_window'], weighted=True)
    context['month_board_rostered'] = get_window_lineup(
        *context['month_window'], weighted=False)
    context['season_board'] = get_best_lineup(
        entity_id=None, season_year=season, bench=_CBS_BENCH_SLOTS)
    context['season_board_rostered'] = get_best_lineup(
        entity_id=None, season_year=season, points_type='rostered')
    alltime = get_best_lineup(entity_id=None, season_year=None,
                              bench=_CBS_BENCH_SLOTS)
    alltime_keys = [s['player_key'] for s in alltime if s.get('player_key')]
    context['alltime_board'] = _apply_alltime_board_context(
        alltime, current_key, current_name, get_years_of_service(alltime_keys))

    home = build_home_rows(context, nav_targets=nav_targets)
    records = build_records_rows(context, get_cbs_record_catalog(),
                                 get_cbs_records_data())
    standings = build_standings_rows(
        context, arc, finishes, franchises,
        slot_rows=get_slot_points(season),
        alltime_slot_rows=get_slot_points_alltime(),
        alltime_pitching_rows=get_pitching_points_alltime(),
        season_days=get_season_gameplay_days(),
        detailed_alltime_rows=get_detailed_stats_alltime(),
        acquisition_rows=get_acquisition_channels(season),
        alltime_acquisition_rows=get_acquisition_channels_alltime(
            context['last_closed_season']),
        affinity_rows=get_mlb_affinity(season),
    )

    draft = build_draft_recap_rows(
        season, _fmap,
        season_clocks={r['season_year']: r['days'] for r in get_season_gameplay_days()})

    # Third element: the team-tab title set -- the writer bulk-writes those
    # tabs RAW (zero-padded rate strings survive) + reapplies '=' formulas,
    # exactly like the ESPN team tabs.
    team_titles = {title for title, _, _ in team_tabs}
    return ([(HOME_TAB, *home), (RECORDS_TAB, *records),
             (STANDINGS_TAB, *standings), (DRAFT_TAB, *draft)] + team_tabs
            ), link_map, team_titles


# ---------------------------------------------------------------------------
# Sheets write
# ---------------------------------------------------------------------------

# The Sheets API caps WRITE REQUESTS PER MINUTE per user. Two defenses,
# both learned from the ESPN writer's history with the same quota:
#   1. One styling batch_update per tab (freeze + cell formats + column
#      widths in a single request) -- ~3 write calls per tab instead of
#      ~6, which keeps a ~19-tab run under the per-minute cap outright.
#   2. When the quota still trips, wait PAST the minute window before
#      retrying (70s, mirroring almanac_write._sheets_call) -- an
#      exponential ladder that tops out under 60s can never outlast a
#      per-minute bucket.

_QUOTA_ATTEMPTS = 3
_QUOTA_WAIT_SECONDS = 70


def _is_quota_error(exc):
    message = str(exc).lower()
    return '[429]' in message or 'quota exceeded' in message or 'rate limit' in message


def _sheets_call(label, fn):
    """Run a Sheets mutation, backing off when the API write quota resets.
    Mirrors almanac_write._sheets_call; extracting the two into a shared
    plumbing module is a follow-up (kept separate so the golden-covered
    ESPN writer stays untouched)."""
    for attempt in range(1, _QUOTA_ATTEMPTS + 1):
        try:
            return fn()
        except gspread.exceptions.APIError as exc:
            if attempt == _QUOTA_ATTEMPTS or not _is_quota_error(exc):
                raise
            print(f"[cbs-almanac] Sheets quota hit during {label}; "
                  f"retrying in {_QUOTA_WAIT_SECONDS}s")
            time.sleep(_QUOTA_WAIT_SECONDS)


def _stale_style_state_requests(spreadsheet, worksheet, formats):
    """Wipe requests for sheet state the full-format reset can't reach:
    conditional-format rules, row groups, embedded charts, and data
    validations all ACCUMULATE (or linger at stale addresses) across
    reruns, so a tab that uses any of those spec kinds starts each render
    by deleting whatever the last render left. The metadata read only
    happens for tabs that carry those specs."""
    dynamic_kinds = ('gradient', 'hide_rows', 'hide_cols', 'chart',
                     'checkboxes')
    if not any(any(k in s for k in dynamic_kinds) for s in formats or ()):
        return []
    meta = _sheets_call(
        f'meta {worksheet.title}',
        lambda: spreadsheet.fetch_sheet_metadata({
            'fields': 'sheets(properties(sheetId),conditionalFormats,'
                      'rowGroups,charts(chartId))',
        }),
    )
    sheet = next(
        (s for s in meta.get('sheets', [])
         if s.get('properties', {}).get('sheetId') == worksheet.id),
        {},
    )
    requests = [{'deleteConditionalFormatRule':
                 {'sheetId': worksheet.id, 'index': 0}}
                for _ in sheet.get('conditionalFormats', ())]
    groups = sorted(sheet.get('rowGroups', ()),
                    key=lambda g: -(g.get('depth') or 1))
    for group in groups:
        requests.append({'deleteDimensionGroup': {
            'range': {**group.get('range', {}), 'sheetId': worksheet.id}}})
    if groups:
        # Deleting a group leaves its rows hidden; unhide everything so
        # this render's own hide_rows specs are the only hidden state.
        requests.append({'updateDimensionProperties': {
            'range': {'sheetId': worksheet.id, 'dimension': 'ROWS'},
            'properties': {'hiddenByUser': False},
            'fields': 'hiddenByUser',
        }})
    for chart in sheet.get('charts', ()):
        requests.append({'deleteEmbeddedObject':
                         {'objectId': chart['chartId']}})
    if any('hide_cols' in s for s in formats or ()):
        requests.append({'updateDimensionProperties': {
            'range': {'sheetId': worksheet.id, 'dimension': 'COLUMNS'},
            'properties': {'hiddenByUser': False},
            'fields': 'hiddenByUser',
        }})
    if any('checkboxes' in s for s in formats or ()):
        # Clear ALL validations on the tab so a moved checkbox row never
        # strands checkbox formatting at its old address.
        requests.append({'setDataValidation': {
            'range': {'sheetId': worksheet.id}, 'rule': None}})
    return requests


def _chart_request(sheet_gid, c):
    """addChart request for a builder {'chart': ...} spec: a LINE chart
    whose domain + series each read one helper column (headerCount 1
    names the series from the helper's abbrev header). SHOW_ALL keeps the
    HIDDEN helper columns feeding the chart; the left axis windows
    0..view_max because the helper stores flipped ranks (top = 1st)."""
    def _col_source(col):
        return {'sourceRange': {'sources': [{
            'sheetId': sheet_gid,
            'startRowIndex': c['first_row'],
            'endRowIndex': c['last_row'],
            'startColumnIndex': col,
            'endColumnIndex': col + 1,
        }]}}

    return {'addChart': {'chart': {
        'spec': {
            'title': c.get('title', ''),
            'basicChart': {
                'chartType': 'LINE',
                'legendPosition': 'RIGHT_LEGEND',
                'headerCount': 1,
                'domains': [{'domain': _col_source(c['domain_col'])}],
                'series': [{'series': _col_source(col),
                            'targetAxis': 'LEFT_AXIS'}
                           for col in c['series_cols']],
                'axis': [
                    {'position': 'BOTTOM_AXIS', 'title': 'Period'},
                    {'position': 'LEFT_AXIS',
                     'title': 'Position (top = 1st)',
                     'viewWindowOptions': {
                         'viewWindowMode': 'EXPLICIT',
                         'viewWindowMin': 0,
                         'viewWindowMax': c.get('view_max', 17)}},
                ],
            },
            'hiddenDimensionStrategy': 'SHOW_ALL',
        },
        'position': {'overlayPosition': {
            'anchorCell': {'sheetId': sheet_gid,
                           'rowIndex': c['anchor'][0],
                           'columnIndex': c['anchor'][1]},
            'widthPixels': 940,
            'heightPixels': 360,
        }},
    }}}


def _write_tab(spreadsheet, title, rows, formats, value_input_option='RAW',
               reapply_formulas=False):
    width = max((len(r) for r in rows if r), default=8)
    needed_rows = max(len(rows) + 10, 40)
    needed_cols = max(width, 10)
    try:
        worksheet = spreadsheet.worksheet(title)
        # Team tabs GREW with the ESPN-shape rebuild (uncapped Other ->
        # 1,000+ rows on the long-tenured franchises); an existing grid
        # from an earlier render may be too small for the values write
        # and the row-addressed style ranges.
        if (worksheet.row_count < needed_rows
                or worksheet.col_count < needed_cols):
            _sheets_call(
                f'resize {title}',
                lambda ws=worksheet: ws.resize(
                    rows=max(ws.row_count, needed_rows),
                    cols=max(ws.col_count, needed_cols)),
            )
    except gspread.WorksheetNotFound:
        worksheet = _sheets_call(
            f'create {title}',
            lambda t=title: spreadsheet.add_worksheet(
                title=t, rows=needed_rows, cols=needed_cols,
            ),
        )
    _sheets_call(f'clear {title}', worksheet.clear)
    _sheets_call(
        f'update {title}',
        lambda ws=worksheet, r=rows, vio=value_input_option: ws.update(
            r, 'A1', value_input_option=vio),
    )
    if reapply_formulas:
        # BEFORE the style pass: the team tabs auto-resize their Player
        # columns, and resizing against the raw 150-char '=HYPERLINK(...)'
        # literals (instead of the rendered short names) left them
        # comically wide (Kyle 2026-07-17).
        _reapply_formula_cells(worksheet, rows)
    stale_state = _stale_style_state_requests(spreadsheet, worksheet, formats)
    _sheets_call(
        f'style {title}',
        lambda ws=worksheet, t=title, f=formats, pre=stale_state:
            spreadsheet.batch_update(
                {'requests': pre + _tab_style_requests(ws.id, t, f)}),
    )
    print(f"[cbs-almanac] wrote tab: {title} ({len(rows)} rows)")
    return worksheet


def _reapply_formula_cells(worksheet, rows):
    """Re-send '='-prefixed cells with USER_ENTERED after a RAW bulk write
    (mirrors almanac_write._reapply_formula_cells): RAW keeps zero-padded
    rate strings verbatim but leaves the bref HYPERLINKs as literal text;
    this pass re-coerces only those cells. Fresh dicts per attempt --
    gspread rewrites each entry's 'range' in place, so a retry after a
    quota hit would double-prefix the title and 400."""
    formula_cells = [
        {'range': f'{_col(col)}{row_number}', 'values': [[value]]}
        for row_number, row in enumerate(rows, start=1)
        for col, value in enumerate(row, start=1)
        if isinstance(value, str) and value.startswith('=')
    ]
    if not formula_cells:
        return
    _sheets_call(
        f'reapply {len(formula_cells)} formula cells {worksheet.title}',
        lambda: worksheet.batch_update(
            [dict(cell) for cell in formula_cells],
            value_input_option='USER_ENTERED',
        ),
    )


def write_cbs_almanac(sheet_id):
    """Two-pass write, mirroring the ESPN almanac (#25): pass 1 writes
    every non-Home tab so their gids exist; pass 2 renders Home's nav as
    live =HYPERLINK("#gid=...") formulas and writes it last (USER_ENTERED
    so the formulas parse). Idempotent: a rerun overwrites every tab."""
    client = _get_authorized_client()
    spreadsheet = _sheets_call('open', lambda: client.open_by_key(sheet_id))

    # Tab-rename migration (2026-07-17): 'Standings' -> 'Advanced Standings'
    # (ESPN parity). Rename a legacy worksheet in place -- keeping its gid --
    # rather than stranding the old tab beside a freshly created one.
    try:
        legacy = spreadsheet.worksheet('Standings')
    except gspread.WorksheetNotFound:
        legacy = None
    if legacy is not None:
        try:
            spreadsheet.worksheet(STANDINGS_TAB)
        except gspread.WorksheetNotFound:
            _sheets_call('rename legacy Standings tab',
                         lambda: legacy.update_title(STANDINGS_TAB))

    tabs, link_map, team_titles = build_all_tabs()
    home = next(t for t in tabs if t[0] == HOME_TAB)
    others = [t for t in tabs if t[0] != HOME_TAB]

    nav_targets, ws_by_title = {}, {}
    for title, rows, formats in others:
        if title in team_titles:
            # Team tabs: RAW (mirrors the ESPN team-tab writer) so the
            # zero-padded rate strings ("040") survive verbatim; the bref
            # '=' cells re-coerce to formulas INSIDE the write, before the
            # style pass auto-resizes the Player columns.
            ws = _write_tab(spreadsheet, title, rows, formats,
                            value_input_option='RAW', reapply_formulas=True)
        else:
            # USER_ENTERED so the bref =HYPERLINK cells on Records parse
            # as links, not literal text (RAW left them as strings).
            ws = _write_tab(spreadsheet, title, rows, formats,
                            value_input_option='USER_ENTERED')
        nav_targets[title] = ws.id
        ws_by_title[title] = ws

    # Records-page hyperlinks (Kyle 2026-07-15): now the team-tab gids exist,
    # link every STANDALONE active-team abbrev cell to its team page. A bare
    # abbrev ('SED') or an 'ABBREV (count)' cell (the HoS shame) links; a list
    # ('SED, CSC') or a defunct abbrev doesn't. Values-only re-write (the pass-1
    # formats persist).
    abbrev_gid = {ab: nav_targets[t] for ab, t in link_map.items()
                  if ab and t in nav_targets}

    def _link_abbrev(val):
        if not isinstance(val, str) or not val or val.startswith('='):
            return val
        if val in abbrev_gid:                        # bare standalone abbrev
            return f'=HYPERLINK("#gid={abbrev_gid[val]}&range=A1", "{val}")'
        m = re.match(r'^([A-Z0-9]{2,5}) \(', val)    # 'ABBREV (count)' (shame)
        if m and m.group(1) in abbrev_gid:
            safe = val.replace('"', '""')
            return (f'=HYPERLINK("#gid={abbrev_gid[m.group(1)]}'
                    f'&range=A1", "{safe}")')
        return val

    rec = next((t for t in others if t[0] == RECORDS_TAB), None)
    if rec and abbrev_gid and RECORDS_TAB in ws_by_title:
        linked = [[_link_abbrev(c) for c in row] for row in rec[1]]
        rw = ws_by_title[RECORDS_TAB]
        _sheets_call('link Records', lambda: rw.update(
            linked, 'A1', value_input_option='USER_ENTERED'))

    # Rebuild Home's rows with live nav targets (cheap: row assembly only
    # -- the boards were already computed inside build_all_tabs; rebuild
    # via the same context is avoided by patching nav cells instead).
    home_title, home_rows, home_formats = home
    patched = []
    for row in home_rows:
        patched.append([
            (f'=HYPERLINK("#gid={nav_targets[cell]}&range=A1", "{cell}")'
             if isinstance(cell, str) and cell in nav_targets else cell)
            for cell in row
        ])
    _write_tab(spreadsheet, home_title, patched, home_formats,
               value_input_option='USER_ENTERED')

    # Tab order: Home, Records, Standings, team pages.
    order = [HOME_TAB] + [t for t, _, _ in others]
    by_title = {ws.title: ws for ws in spreadsheet.worksheets()}
    requests = []
    for idx, title in enumerate(order):
        ws = by_title.get(title)
        if ws is not None:
            requests.append({
                'updateSheetProperties': {
                    'properties': {'sheetId': ws.id, 'index': idx},
                    'fields': 'index',
                },
            })
    if requests:
        _sheets_call('sort tabs',
                     lambda: spreadsheet.batch_update({'requests': requests}))


# Mirrors almanac_write._apply_home_tab_dimensions (the ESPN Home): A-D
# left band, E spacer, F-O right band; Slash/Stat Line (L/M) keep the
# default width there and here.
_HOME_WIDTHS = [(0, 1, 100), (1, 2, 125), (2, 3, 100), (3, 4, 50),
                (4, 5, 100), (5, 6, 40), (6, 7, 40), (7, 8, 150),
                (8, 9, 100), (9, 10, 125), (10, 11, 50),
                (11, 12, 125), (12, 13, 250),   # L Slash / M Stat Line (Kyle)
                (13, 14, 150), (14, 15, 50)]
# Records widths: A Record 175, B/H Holder 150, C/I Owner 125, F Details 400,
# G buffer 25, K Details2 400. The All-Time side dropped its 'Yrs' column
# (Kyle 2026-07-14), so the second Details is now col K (index 10).
_RECORDS_WIDTHS = [(0, 1, 175), (1, 2, 150), (2, 3, 125),
                   (5, 6, 400), (6, 7, 25),
                   (7, 8, 150), (8, 9, 125), (9, 10, 50), (10, 11, 400),
                   # Wasted HoS Hitters block L-O (Kyle 2026-07-15).
                   (11, 12, 150), (12, 13, 125), (13, 14, 50), (14, 15, 400)]
_STANDINGS_WIDTHS = [(0, 1, 190), (1, 2, 60),
                     # Value columns (slot grids, finish years, affinity %)
                     # sit narrow like ESPN's 40px stat columns.
                     (2, 40, 44)]
# Team-tab widths: VERBATIM from almanac_write._apply_team_tab_dimensions
# (Kyle 2026-07-16: CBS team tabs identical to ESPN's) -- tiny Tm cols,
# 50px stat columns (incl. the new Total/Active/Inactive trio), 80px
# pipe columns, 325px trailing YoS at idx 31 (last column, let the
# come-and-go year ranges run free -- Kyle 2026-07-17).
_TEAM_WIDTHS = [(0, 1, 25), (1, 2, 75), (3, 4, 40), (4, 5, 50), (5, 6, 50),
                (6, 7, 50), (7, 8, 50), (8, 9, 55), (9, 10, 40), (10, 15, 80),
                (15, 16, 15), (16, 17, 25), (17, 18, 75), (19, 20, 40),
                (20, 21, 50), (21, 22, 50), (22, 23, 50), (23, 24, 50),
                (24, 25, 55), (25, 26, 40), (26, 31, 80), (31, 32, 325)]

# Draft Recap widths mirror the ESPN writer's (_apply_draft_tab_
# dimensions): A wide for player links + Rd/Year stubs, B-C narrow
# summaries, D fits the all-time Top Pick 'Player -year' links, E+ the
# 16-column board (2026 teams / all-time slots).
_DRAFT_WIDTHS = [(0, 1, 25), (1, 3, 40), (3, 4, 125), (4, 5, 75),
                 (5, 6, 40), (6, 22, 100)]


def _tab_style_requests(sheet_gid, title, formats):
    """Every non-value mutation for one tab as raw batch_update requests:
    a full-sheet format RESET (worksheet.clear() drops values but NOT cell
    formatting, so without this every re-render layers new colours over the
    old and stale artifacts accumulate -- Kyle round 7), then the frozen
    header band, the builder's cell formats, and column widths."""
    # Team tabs freeze through the column-header band (5, like ESPN's
    # worksheet.freeze(rows=5)); the dashboard tabs keep the 2-row band.
    is_team_tab = title not in (HOME_TAB, RECORDS_TAB, STANDINGS_TAB, DRAFT_TAB)
    requests = [{
        'repeatCell': {
            'range': {'sheetId': sheet_gid},   # whole sheet
            'cell': {},
            'fields': 'userEnteredFormat',
        },
    }, {
        'updateSheetProperties': {
            'properties': {
                'sheetId': sheet_gid,
                'gridProperties': {'frozenRowCount': 5 if is_team_tab else 2},
            },
            'fields': 'gridProperties.frozenRowCount',
        },
    }]
    deferred_merges = []
    for spec in formats or ():
        if 'checkboxes' in spec:
            requests.append({'setDataValidation': {
                'range': gspread.utils.a1_range_to_grid_range(
                    spec['checkboxes'], sheet_id=sheet_gid),
                'rule': {'condition': {'type': 'BOOLEAN'},
                         'strict': True, 'showCustomUi': True},
            }})
            continue
        if 'hide_cols' in spec:
            start, end = spec['hide_cols']
            requests.append({'updateDimensionProperties': {
                'range': {'sheetId': sheet_gid, 'dimension': 'COLUMNS',
                          'startIndex': start, 'endIndex': end},
                'properties': {'hiddenByUser': True},
                'fields': 'hiddenByUser',
            }})
            continue
        if 'chart' in spec:
            requests.append(_chart_request(sheet_gid, spec['chart']))
            continue
        if 'hide_rows' in spec:
            # A hidden row GROUP (0-based half-open row range): the group's
            # +/- expander in the margin is the discoverable affordance;
            # hiding the rows is what renders it collapsed. Reruns don't
            # stack -- _stale_style_state_requests unwinds prior groups.
            start, end = spec['hide_rows']
            dim_range = {'sheetId': sheet_gid, 'dimension': 'ROWS',
                         'startIndex': start, 'endIndex': end}
            requests.append({'addDimensionGroup': {'range': dim_range}})
            requests.append({'updateDimensionProperties': {
                'range': dim_range,
                'properties': {'hiddenByUser': True},
                'fields': 'hiddenByUser',
            }})
            continue
        if 'cell_colors' in spec:
            # Precomputed per-cell backgrounds (the draft board's ESPN-style
            # red->white->green over TEXT cells, which a numeric gradient
            # rule can't paint): one updateCells request with a
            # backgroundColor-only field mask, so values survive. The
            # full-sheet format reset above keeps reruns idempotent.
            payload = spec['cell_colors']
            grid = payload['grid']
            grid_width = max((len(r) for r in grid), default=0)
            cell_rows = []
            for grid_row in grid:
                values = []
                for col in range(grid_width):
                    color = grid_row[col] if col < len(grid_row) else None
                    values.append({'userEnteredFormat': {'backgroundColor':
                                   color or {'red': 1, 'green': 1, 'blue': 1}}})
                cell_rows.append({'values': values})
            requests.append({'updateCells': {
                'range': {'sheetId': sheet_gid,
                          'startRowIndex': payload['start_row0'],
                          'endRowIndex': payload['start_row0'] + len(grid),
                          'startColumnIndex': payload['start_col0'],
                          'endColumnIndex': payload['start_col0'] + grid_width},
                'rows': cell_rows,
                'fields': 'userEnteredFormat.backgroundColor',
            }})
            continue
        if 'gradient' in spec:
            # Conditional-format color scale; 'ranges' (list) or 'range'
            # (single A1) -- a multi-range rule scales MIN/PERCENTILE/MAX
            # across the UNION (per-year finish rules span both matrices;
            # the affinity rule spans both blocks). Numeric cells only --
            # text (the 🏆 markers) and blanks stay unpainted, which is why
            # champions and the affinity base carry static fills.
            a1_ranges = spec.get('ranges') or [spec['range']]
            requests.append({'addConditionalFormatRule': {
                'rule': {'ranges': [
                    gspread.utils.a1_range_to_grid_range(a, sheet_id=sheet_gid)
                    for a in a1_ranges],
                    'gradientRule': spec['gradient']},
                'index': 0,
            }})
            continue
        grid_range = gspread.utils.a1_range_to_grid_range(
            spec['range'], sheet_id=sheet_gid)
        if spec.get('merge'):
            # Dynamic merges from the builder (the Best Individual Seasons
            # banner rows). DEFERRED past the team-tab unmerge below --
            # merging first would be undone by the full-sheet unmerge.
            deferred_merges.append({
                'mergeCells': {'range': grid_range, 'mergeType': 'MERGE_ALL'},
            })
            continue
        if 'runs' in spec:
            # Per-character run styling (textFormatRuns) for a single cell --
            # the A1 team name (bold) + its size-10 abbrev parenthetical.
            # Written after the range formats so it wins on that cell.
            requests.append({
                'updateCells': {
                    'range': grid_range,
                    'rows': [{'values': [{'textFormatRuns': spec['runs']}]}],
                    'fields': 'textFormatRuns',
                },
            })
            continue
        fields = ','.join(sorted(spec['format'].keys()))
        requests.append({
            'repeatCell': {
                'range': grid_range,
                'cell': {'userEnteredFormat': spec['format']},
                'fields': f'userEnteredFormat({fields})',
            },
        })
    if title == HOME_TAB:
        widths = _HOME_WIDTHS
    elif title == RECORDS_TAB:
        widths = _RECORDS_WIDTHS
    elif title == STANDINGS_TAB:
        widths = _STANDINGS_WIDTHS
    elif title == DRAFT_TAB:
        widths = _DRAFT_WIDTHS
    else:
        widths = _TEAM_WIDTHS
    requests.extend({
        'updateDimensionProperties': {
            'range': {
                'sheetId': sheet_gid,
                'dimension': 'COLUMNS',
                'startIndex': start,
                'endIndex': end,
            },
            'properties': {'pixelSize': pixels},
            'fields': 'pixelSize',
        },
    } for start, end, pixels in widths)
    if is_team_tab:
        # Fixed 125px Player columns (C and S) -- the house full-name width.
        # Auto-fit blew them up on long CBS names (Kyle 2026-07-18).
        requests.extend({
            'updateDimensionProperties': {
                'range': {'sheetId': sheet_gid, 'dimension': 'COLUMNS',
                          'startIndex': start, 'endIndex': end},
                'properties': {'pixelSize': 125},
                'fields': 'pixelSize',
            },
        } for start, end in ((2, 3), (18, 19)))
        # Header merges (Roster Days pairs, the Points banners, the CBS
        # Lineup Data lines), shared with the ESPN writer, then the
        # builder's dynamic merges (Best Individual Seasons banners).
        # Unmerge the whole sheet first: re-merging an already-merged
        # range errors on rerun.
        requests.append({'unmergeCells': {'range': {'sheetId': sheet_gid}}})
        requests.extend({
            'mergeCells': {'range': {'sheetId': sheet_gid, **rng},
                           'mergeType': 'MERGE_ALL'},
        } for rng in team_tab_merge_ranges(with_lineup_data=True))
        requests.extend(deferred_merges)
    elif deferred_merges:
        # Non-team tabs with dynamic merges (the acquisition band rows on
        # Advanced Standings): unmerge the sheet first -- re-merging an
        # already-merged range errors on rerun.
        requests.append({'unmergeCells': {'range': {'sheetId': sheet_gid}}})
        requests.extend(deferred_merges)
    return requests
