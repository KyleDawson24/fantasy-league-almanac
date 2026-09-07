"""The redesigned Records surfaces (MLB-212): the pure engine + presenter.

ONE PRESENTER SET, BOTH BOOKS. Nothing in this module touches a warehouse:
it takes unit rows (a team-week, a player-team-day, a player-team-season,
...) that the data module hands it, decides every record, leaderboard and
Hall, and lays the three tab shapes out as sheet rows plus a format spec.
FORMAT decides which tabs and bands exist (the lens rule, spec section 1);
the record-row, leaderboard and Hall presenters below are shared, so a
7-day ESPN league and a season-long CBS league read the same page with
different bands.

Vocabulary (a "unit row" is a plain dict):
  season, unit (matchup period / week index / scoring period), date (day
  grain), mp (the matchup period a day belongs to, for links), team_id,
  team_name, abbrev, owner, pid, pname (official name, bref key), dname
  (display name), pts / hit_pts / pit_pts (active points), neg (negative
  active points, a positive magnitude), games, the counting-stat columns in
  the facts' spelling (h, ab, ..., sho), and for wasted rows benched_hit /
  benched_pit / unrostered_hit / unrostered_pit / neg_hit / neg_pit.

Every rule the spec states once is implemented once here and named after it
in a comment, so a tweak Kyle asks for after the dev render lands in one
place.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from almanac_render import _bref_link, draft_initial_text
from formatters import fmt_avg, fmt_ip


# ---------------------------------------------------------------------------
# Layout constants: 18 columns -- A record label, then three bands of five
# (Holder Owner Value Details Period) with a spacer column between bands.
# ---------------------------------------------------------------------------

WIDTH = 18
BAND_COLS = 5
BAND_STARTS = (1, 7, 13)          # 0-based first column of each band
SPACER_COLS = (6, 12)

TITLES = {'matchup': 'Matchup Records', 'season': 'Season Records',
          'lifetime': 'Lifetime Records'}

# Spec section 2.4 / MLB-80: the sample floors, in the facts' units.
AB_FLOOR = 225
OUTS_FLOOR = 150          # 50 IP

HALL_DEPTH = 25
TOP_BLOCK_DEPTH = 10
MAX_LISTED_TIE = 3        # section 2.9: beyond three holders collapse

IN_FLIGHT_MARK = '*'

# Slot display order (MLB-278) as a fallback when a slot has no sort_order.
_SLOT_FALLBACK_ORDER = ['C', '1B', '2B', '3B', 'SS', 'MI', 'CI', 'IF', 'LF',
                        'CF', 'RF', 'OF', 'DH', 'U', 'UTIL', 'SP', 'RP', 'P']


def _col(idx0):
    """0-based column index -> A1 letters."""
    idx0 += 1
    out = ''
    while idx0:
        idx0, rem = divmod(idx0 - 1, 26)
        out = chr(65 + rem) + out
    return out


# ---------------------------------------------------------------------------
# Section 1: which tabs and bands a league gets.
# ---------------------------------------------------------------------------

@dataclass
class Band:
    key: str            # e.g. 'week_cur', 'week_all', 'day_all', 'season_cur'
    title: str
    grain: str          # 'week' | 'day' | 'season'
    scope: str          # 'current' | 'all'
    last_col: str       # 'Period' | 'Season' | 'Runner-up'
    period_links: bool = True


def derive_tabs(league_format, drilldown=None, seasons_count=2,
                level_label='Weeks', drill_label=None):
    """The lens rule. Returns [{'key','title','bands'}] in tab order.

    league_format: 'h2h' (matchups shorter than a season) or 'points'
    (season-long). drilldown: 'day' | 'week' | 'month' | 'none'; defaults
    per shape (7-day -> day, season-long -> week). A one-season league has
    no All-Time scope; its Season tab collapses to one leaders band that
    rides the Matchup tab. The Lifetime tab always renders.
    """
    season_long = league_format == 'points'
    if drilldown is None:
        drilldown = 'week' if season_long else 'day'
    drill_label = drill_label or {'day': 'Days', 'week': 'Weeks',
                                  'month': 'Months'}.get(drilldown, '')
    multi = seasons_count > 1
    tabs = []

    if not season_long:
        bands = [Band('level_cur', f'Best {level_label} This Season',
                      'week', 'current', 'Period')]
        if multi:
            bands.append(Band('level_all', f'Best {level_label} All-Time',
                              'week', 'all', 'Period'))
        if drilldown != 'none' and drilldown != 'week':
            bands.append(Band(f'{drilldown}_all',
                              f'Best {drill_label} All-Time' if multi
                              else f'Best {drill_label} This Season',
                              drilldown, 'all' if multi else 'current',
                              'Period'))
        if not multi:
            # The Season tab's one leaders band rides the Matchup tab.
            bands.append(Band('season_cur', 'Best Performances This Season *',
                              'season', 'current', 'Runner-up', False))
        tabs.append({'key': 'matchup', 'title': TITLES['matchup'],
                     'bands': bands})

    if season_long or multi:
        bands = [Band('season_cur', 'Best Performances This Season *',
                      'season', 'current', 'Runner-up', False)]
        if multi:
            bands.append(Band('season_all', 'Best Performances Any Season',
                              'season', 'all', 'Season', False))
        if season_long and drilldown != 'none':
            bands.append(Band(f'{drilldown}_all',
                              f'Best {drill_label} All-Time' if multi
                              else f'Best {drill_label} This Season',
                              drilldown, 'all' if multi else 'current',
                              'Period'))
        tabs.append({'key': 'season', 'title': TITLES['season'],
                     'bands': bands})

    tabs.append({'key': 'lifetime', 'title': TITLES['lifetime'], 'bands': []})
    return tabs


# ---------------------------------------------------------------------------
# Metrics: what a record row measures.
# ---------------------------------------------------------------------------

@dataclass
class Metric:
    key: str                 # unit-row column
    label: str
    kind: str                # 'points' | 'wasted' | 'count' | 'rate' | 'slot'
    category: str            # 'hitting' | 'pitching' | 'total'
    good_dir: str            # 'desc' | 'asc'
    fmt: str                 # 'pts' | 'int' | 'avg' | 'rate' | 'ip'
    floor: str = None        # 'ab' | 'outs' | None (the sample floor column)
    polarity: str = 'positive'


POINTS_METRICS = [
    Metric('pts', 'Total Points', 'points', 'total', 'desc', 'pts'),
    Metric('hit_pts', 'Hitting Points', 'points', 'hitting', 'desc', 'pts'),
    Metric('pit_pts', 'Pitching Points', 'points', 'pitching', 'desc', 'pts'),
]
WASTED_METRICS = [
    Metric('wasted', 'Most Wasted Points', 'wasted', 'total', 'desc', 'pts'),
    Metric('wasted_hit', 'Most Wasted Hitting Points', 'wasted', 'hitting',
           'desc', 'pts'),
    Metric('wasted_pit', 'Most Wasted Pitching Points', 'wasted', 'pitching',
           'desc', 'pts'),
]
RATE_METRICS = {
    'hitting': [
        Metric('avg', 'AVG', 'rate', 'hitting', 'desc', 'avg', 'ab'),
        Metric('obp', 'OBP', 'rate', 'hitting', 'desc', 'avg', 'ab'),
        Metric('slg', 'SLG', 'rate', 'hitting', 'desc', 'avg', 'ab'),
    ],
    'pitching': [
        Metric('era', 'ERA', 'rate', 'pitching', 'asc', 'rate', 'outs'),
        Metric('whip', 'WHIP', 'rate', 'pitching', 'asc', 'rate', 'outs'),
        Metric('k9', 'K/9', 'rate', 'pitching', 'desc', 'rate', 'outs'),
    ],
}

# A few catalog labels read better short on a record row.
_LABEL_FIX = {'RBIs': 'RBI', 'Strikeouts (Pitcher)': 'Strikeouts',
              'Strikeouts (Batter)': 'Strikeouts', 'Walks (Batter)': 'Walks',
              'GIDP (Batter)': 'GIDP', 'Hit by Pitch': 'Hit By Pitch'}


def counting_metrics(catalog, category):
    """Section 3: one row per scored counting stat, at its good end.

    `catalog` rows carry key (unit column), display_name, stat_category,
    polarity. Negative polarity -> fewest, with the category's floor.
    """
    out = []
    for row in catalog:
        if row.get('stat_category') != category:
            continue
        negative = row.get('polarity') == 'negative'
        label = _LABEL_FIX.get(row['display_name'], row['display_name'])
        out.append(Metric(
            row['key'], label, 'count', category,
            'asc' if negative else 'desc',
            'ip' if row['key'] == 'outs' else 'int',
            ('ab' if category == 'hitting' else 'outs') if negative else None,
            'negative' if negative else 'positive',
        ))
    return out


# ---------------------------------------------------------------------------
# Rates from counting components -- section 2.4 floors applied here so a
# row below the floor simply has no rate.
# ---------------------------------------------------------------------------

def add_rates(row):
    ab = float(row.get('ab') or 0)
    h = float(row.get('h') or 0)
    bb = float(row.get('b_bb') or 0)
    hbp = float(row.get('hbp') or 0)
    sf = float(row.get('sf') or 0)
    tb = float(row.get('tb') or 0)
    outs = float(row.get('outs') or 0)
    if ab >= AB_FLOOR:
        row['avg'] = h / ab
        denom = ab + bb + hbp + sf
        row['obp'] = (h + bb + hbp) / denom if denom else None
        row['slg'] = tb / ab
    else:
        row['avg'] = row['obp'] = row['slg'] = None
    if outs >= OUTS_FLOOR:
        row['era'] = float(row.get('er') or 0) * 27.0 / outs
        row['whip'] = (float(row.get('p_bb') or 0) + float(row.get('p_h') or 0)) * 3.0 / outs
        row['k9'] = float(row.get('k') or 0) * 27.0 / outs
    else:
        row['era'] = row['whip'] = row['k9'] = None
    return row


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Pools: where candidates for a record come from.
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    value: float
    row: dict
    tie_n: int = 1


def _sort_key(row):
    """MLB-128: a total order so equal values never depend on row order."""
    return (-(row.get('season') or 0), -(row.get('unit') or 0),
            str(row.get('date') or ''), str(row.get('team_id') or ''),
            str(row.get('pid') or ''), str(row.get('slot') or ''))


class Pool:
    """Full unit rows held in memory; extremes are sorted here."""

    def __init__(self, rows):
        self.rows = list(rows)

    def top(self, metric, direction, k, season=None, complete_only=False,
            standard_only=False, complete_seasons=(), standard_seasons=(),
            floor=None, require=None):
        cands = []
        for row in self.rows:
            if season is not None and row.get('season') != season:
                continue
            if require is not None and not require(row):
                continue
            if complete_only and not row.get('complete', True):
                continue
            if standard_only and not row.get('standard', True):
                continue
            if floor and float(row.get(floor) or 0) < (
                    AB_FLOOR if floor == 'ab' else OUTS_FLOOR):
                continue
            value = _num(row.get(metric))
            if value is None:
                continue
            cands.append((value, row))
        reverse = direction == 'desc'
        cands.sort(key=lambda vr: ((-vr[0] if reverse else vr[0]),
                                   _sort_key(vr[1])))
        if not cands:
            return []
        best = cands[0][0]
        tie_n = sum(1 for v, _ in cands if v == best)
        return [Candidate(v, r, tie_n) for v, r in cands[:k]]


class CandidatePool:
    """Pre-ranked candidates per (metric, direction, scope) from a top-K
    query -- for a grain too big to hold in memory (CBS player-weeks).

    `cands` maps (metric, direction, scope) -> list of (value, row, tie_n)
    already ordered best-first; scope is 'all' or a season year.
    """

    def __init__(self, cands):
        self.cands = cands

    def top(self, metric, direction, k, season=None, complete_only=False,
            standard_only=False, complete_seasons=(), standard_seasons=(),
            floor=None, require=None):
        key = (metric, direction, season if season is not None else 'all')
        rows = self.cands.get(key) or []
        out = []
        for value, row, tie_n in rows:
            if require is not None and not require(row):
                continue
            if complete_only and not row.get('complete', True):
                continue
            if standard_only and not row.get('standard', True):
                continue
            out.append(Candidate(value, row, tie_n))
        return out[:k]


# ---------------------------------------------------------------------------
# Single-holder records (Matchup + Season tabs).
# ---------------------------------------------------------------------------

@dataclass
class RecordCell:
    value: float
    holders: list            # unit rows sharing the value (best first)
    tie_n: int
    runner_up: tuple = None  # (row, value) or None
    in_flight: bool = False


def record_cell(pool, metric, direction, band, current_season,
                floor_for_fewest=None, want_runner_up=False, grain='team'):
    """One band's cell for one record row, or None when nothing qualifies.

    Section 2.3 polarity, 2.4 floors, 2.5 never-empty, 2.14 completeness:
    a least/lowest record needs complete, standard-length units; a most/
    highest record admits in-flight units and marks them.
    """
    low_side = direction == 'asc'
    floor = metric.floor if metric.kind in ('count', 'rate') else None
    if metric.kind == 'points' and low_side:
        floor = None
    season = current_season if band.scope == 'current' else None
    k = 8 if not want_runner_up else 12
    strict = low_side and band.scope != 'current'
    require = None
    if low_side and metric.kind == 'points' and metric.category == 'hitting':
        # A worst hitting mark needs a hitter; at season grain (2.4) it also
        # needs the season floor, or a September call-up owns the row.
        need = AB_FLOOR if (grain == 'player' and band.grain == 'season') else 1
        require = lambda r, need=need: float(r.get('ab') or 0) >= need
    elif low_side and metric.kind == 'points' and metric.category == 'pitching':
        need = OUTS_FLOOR if (grain == 'player' and band.grain == 'season') else 1
        require = lambda r, need=need: float(r.get('outs') or 0) >= need
    cands = pool.top(metric.key, direction, k, season=season,
                     complete_only=strict, standard_only=strict,
                     floor=floor, require=require)
    if not cands:
        return None
    best = cands[0].value
    # A "most" record nobody has ever set is omitted, not blanked (2.5).
    if not low_side and metric.kind in ('count', 'slot', 'wasted') and best <= 0:
        return None
    holders = [c.row for c in cands if c.value == best]
    runner = None
    if want_runner_up:
        for c in cands:
            if c.value != best and (low_side or c.value > 0):
                runner = (c.row, c.value)
                break
    in_flight = (not low_side) and any(not h.get('complete', True) for h in holders)
    return RecordCell(best, holders, cands[0].tie_n, runner, in_flight)


# ---------------------------------------------------------------------------
# Cell text helpers.
# ---------------------------------------------------------------------------

def fmt_value(metric, value, mark=False):
    if value is None:
        return ''
    if metric.fmt == 'pts':
        text = f'{value:,.1f}'
    elif metric.fmt == 'avg':
        text = fmt_avg(value)
    elif metric.fmt == 'rate':
        text = f'{value:.2f}'
    elif metric.fmt == 'ip':
        text = fmt_ip(round(value))
    else:
        text = f'{value:,.0f}'
    return text + IN_FLIGHT_MARK if mark else text


def value_cell(metric, value, mark=False):
    """A numeric cell where Sheets can keep it numeric, text otherwise."""
    if value is None:
        return ''
    if mark:
        return fmt_value(metric, value, True)
    if metric.fmt == 'ip':
        return fmt_ip(round(value))
    if metric.fmt == 'int':
        return int(round(value))
    return round(float(value), 3 if metric.fmt == 'avg' else 2 if metric.fmt == 'rate' else 1)


def player_cell(row):
    return _bref_link(row.get('pname') or row.get('dname') or '',
                      row.get('dname') or row.get('pname') or '')


def owner_cell(row):
    """Section 2.8: full owner name, team abbreviation as the fallback."""
    return row.get('owner') or row.get('abbrev') or ''


def short_name(row):
    return draft_initial_text(row.get('dname') or row.get('pname') or '')


def stat_line(row, ppu, top_n=3, category=None):
    """Player Details: the counting stats that scored the most, 'count ABBR'.

    Ranks by count x points-per-unit (the league's own weights) so the line
    names what earned the record. `category` restricts to one discipline.
    """
    picks = []
    for col, (abbrev, cat) in STAT_LINE_COLS.items():
        if category and cat != category:
            continue
        count = _num(row.get(col))
        if not count:
            continue
        weight = ppu.get(col)
        score = count * weight if weight is not None else count * 0.01
        if score <= 0:
            continue
        picks.append((score, abbrev, count))
    picks.sort(key=lambda t: (-t[0], t[1]))
    parts = []
    for _, abbrev, count in picks[:top_n]:
        if abbrev == 'IP':
            parts.append(f'{fmt_ip(round(count))} IP')
        else:
            parts.append(f'{count:,.0f} {abbrev}')
    return ', '.join(parts)


STAT_LINE_COLS = {
    'h': ('H', 'hitting'), 'hr': ('HR', 'hitting'), 'r': ('R', 'hitting'),
    'rbi': ('RBI', 'hitting'), 'sb': ('SB', 'hitting'), 'tb': ('TB', 'hitting'),
    'b_bb': ('BB', 'hitting'), 'doubles': ('2B', 'hitting'),
    'triples': ('3B', 'hitting'), 'xbh': ('XBH', 'hitting'),
    'singles': ('1B', 'hitting'), 'hbp': ('HBP', 'hitting'),
    'outs': ('IP', 'pitching'), 'k': ('K', 'pitching'), 'w': ('W', 'pitching'),
    'qs': ('QS', 'pitching'), 'sv': ('SV', 'pitching'), 'hld': ('HLD', 'pitching'),
    'cg': ('CG', 'pitching'), 'sho': ('SHO', 'pitching'), 'nh': ('NH', 'pitching'),
}


def slash_line(row, discipline):
    """Hall stat line lead: AVG/OBP/SLG for hitters, W-L / ERA / WHIP for
    pitchers (the CBS Hall convention, Kyle 2026-07-15)."""
    ab = float(row.get('ab') or 0)
    outs = float(row.get('outs') or 0)
    if discipline == 'pitching':
        parts = [f"{float(row.get('w') or 0):.0f}W - {float(row.get('l') or 0):.0f}L"]
        if outs > 0:
            parts.append(f"{float(row.get('er') or 0) * 27 / outs:.2f} ERA")
            parts.append(f"{(float(row.get('p_bb') or 0) + float(row.get('p_h') or 0)) * 3 / outs:.2f} WHIP")
        return ' / '.join(parts)
    if ab <= 0:
        return ''
    h = float(row.get('h') or 0)
    denom = ab + float(row.get('b_bb') or 0) + float(row.get('hbp') or 0) + float(row.get('sf') or 0)
    obp = (h + float(row.get('b_bb') or 0) + float(row.get('hbp') or 0)) / denom if denom else 0
    return f"{fmt_avg(h / ab)}/{fmt_avg(obp)}/{fmt_avg(float(row.get('tb') or 0) / ab)}"


def qualifier_text(row, floor):
    if floor == 'ab':
        return f"{float(row.get('ab') or 0):,.0f} AB"
    if floor == 'outs':
        return f"{fmt_ip(round(float(row.get('outs') or 0)))} IP"
    return ''


def wasted_breakdown(row):
    """Section 3: the three-term breakdown, largest term first."""
    terms = [('benched', float(row.get('benched') or 0)),
             ('unrostered', float(row.get('unrostered') or 0)),
             ('negative', float(row.get('negative') or 0))]
    terms.sort(key=lambda t: -t[1])
    return ' · '.join(f'{v:,.0f} {label}' for label, v in terms)


# ---------------------------------------------------------------------------
# The record-row presenter: label + one 5-cell side per band.
# ---------------------------------------------------------------------------

class Context:
    """Everything the presenter needs that is not a unit row.

    period_label(row, band) -> text; period_link(row, band) -> url or None;
    contributors(row, metric) -> [(name, value)] for team rows; ppu; the
    current season, the latest (season, unit) closed period, team count.
    """

    def __init__(self, current_season, latest_unit, team_count, ppu,
                 period_label, period_link=None, contributors=None,
                 slot_details=None, in_flight_season=None):
        self.current_season = current_season
        self.latest_unit = latest_unit
        self.team_count = team_count
        self.ppu = ppu or {}
        self.period_label = period_label
        self.period_link = period_link or (lambda row, band: None)
        self.contributors = contributors or (lambda row, metric: [])
        self.slot_details = slot_details or (lambda row: '')
        self.in_flight_season = in_flight_season


def _link_cell(text, url):
    if not text:
        return ''
    if not url:
        return text
    safe = str(text).replace('"', '""')
    return f'=HYPERLINK("{url}", "{safe}")'


def _details_for(row, metric, grain, ctx, cell=None):
    if metric.kind == 'wasted':
        return wasted_breakdown(row)
    if metric.kind == 'rate' or (metric.kind == 'count' and metric.good_dir == 'asc'):
        qual = qualifier_text(row, metric.floor)
        if grain == 'team':
            contribs = ctx.contributors(row, metric)
            head = ', '.join(f'{n}: {fmt_value(metric, v)}' for n, v in contribs[:3])
            return f'{head} · {qual}' if head and qual else head or qual
        return qual
    if grain == 'team':
        if metric.kind == 'slot':
            return ctx.slot_details(row)
        contribs = ctx.contributors(row, metric)
        return ', '.join(f'{n}: {fmt_value(metric, v)}' for n, v in contribs[:3])
    # Player: the stat line, restricted to the metric's discipline where it has one.
    if metric.kind == 'slot':
        return ctx.slot_details(row)
    cat = metric.category if metric.category in ('hitting', 'pitching') else None
    return stat_line(row, ctx.ppu, category=cat)


def side_cells(cell, metric, grain, band, ctx):
    """Section 2.7 columns: Holder · Owner · Value · Details · Period.
    Returns (cells, marks) where marks is a dict of format hints."""
    if cell is None:
        return [''] * BAND_COLS, {}
    marks = {}
    holders = cell.holders
    # Section 4: the This-Season band is the in-flight leaderboard by
    # definition -- one asterisk on its header, none per cell.
    mark = cell.in_flight and band.grain == 'season' and band.scope == 'all'
    if cell.tie_n > 1:
        unit_word = 'players' if grain == 'player' else 'teams'
        details = f'{fmt_value(metric, cell.value)} recorded by {cell.tie_n} {unit_word}'
        if cell.tie_n <= MAX_LISTED_TIE and len(holders) >= cell.tie_n:
            abbrevs = _unique(h.get('abbrev') or '' for h in holders)
            holder = ', '.join(abbrevs)
            owner = ', '.join(_unique(owner_cell(h) for h in holders))
            if len(abbrevs) < cell.tie_n:
                one = unit_word[:-1]
                details = (f'{fmt_value(metric, cell.value)} recorded {cell.tie_n} times by '
                           f'{len(abbrevs)} {one if len(abbrevs) == 1 else unit_word}')
            if band.last_col == 'Period':
                period = '; '.join(f"{h.get('abbrev') or ''} {ctx.period_label(h, band)}".strip()
                                   for h in holders)
                period = _link_cell(period, ctx.period_link(holders[0], band))
            else:
                period = _period_col(holders[0], band, cell, ctx, grain)
        else:
            holder = f'{cell.tie_n} {unit_word} tied'
            owner = ''
            period = _period_col(holders[0], band, cell, ctx, grain)
        if grain == 'player' and cell.tie_n <= MAX_LISTED_TIE:
            marks['tie_players'] = [short_name(h) for h in holders]
        return [holder, owner, value_cell(metric, cell.value, mark), details, period], marks

    row = holders[0]
    holder = player_cell(row) if grain == 'player' else (row.get('team_name') or row.get('abbrev') or '')
    return [holder, owner_cell(row), value_cell(metric, cell.value, mark),
            _details_for(row, metric, grain, ctx, cell),
            _period_col(row, band, cell, ctx, grain)], marks


def _unique(items):
    seen, out = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _period_col(row, band, cell, ctx, grain):
    if band.last_col == 'Runner-up':
        if not cell.runner_up:
            return ''
        r_row, r_val = cell.runner_up
        if grain == 'team':
            who = r_row.get('abbrev') or ''
        elif r_row.get('abbrev'):
            who = f"{short_name(r_row)} ({r_row['abbrev']})"
        else:
            who = short_name(r_row)
        return f"{who} ({fmt_value(cell_metric_stub(cell), r_val)})"
    if band.last_col == 'Season':
        return str(row.get('season') or '')
    label = ctx.period_label(row, band)
    return _link_cell(label, ctx.period_link(row, band) if band.period_links else None)


def cell_metric_stub(cell):
    """The runner-up value formats like the record; carried on the cell."""
    return getattr(cell, 'metric', Metric('x', '', 'points', 'total', 'desc', 'pts'))


# ---------------------------------------------------------------------------
# Tab builders. Each returns (rows, formats, groups, jump_targets).
# ---------------------------------------------------------------------------

_POWDER = {'red': 0.949, 'green': 0.969, 'blue': 0.988}      # #f2f7fc
_BLOCK = {'red': 0.969, 'green': 0.976, 'blue': 0.984}       # #f7f9fb
_NAVY = {'red': 0.118, 'green': 0.2, 'blue': 0.298}          # #1e334c
_WHITE = {'red': 1, 'green': 1, 'blue': 1}
_HL = {'red': 1.0, 'green': 0.949, 'blue': 0.8}              # #fff2cc
_JUMP = {'red': 0.91, 'green': 0.941, 'blue': 0.996}          # #e8f0fe


class Sheet:
    """Accumulates rows + format specs with the section-2.6 shading rules."""

    def __init__(self):
        self.rows = []
        self.formats = []
        self.groups = []
        self.jump_targets = {}
        self._shaded_run = 0

    @property
    def n(self):
        return len(self.rows)

    def add(self, cells, shaded=False):
        cells = list(cells) + [''] * (WIDTH - len(cells))
        self.rows.append(cells[:WIDTH])
        if shaded:
            self._shaded_run += 1
            # Section 2.6: never more than two shaded rows in a row.
            assert self._shaded_run <= 2, 'three shaded rows in a row'
        else:
            self._shaded_run = 0
        return self.n

    def blank(self):
        if self.rows and any(c != '' for c in self.rows[-1]):
            self.add([''] * WIDTH)

    def fmt(self, a1, spec):
        self.formats.append({'range': a1, 'format': spec})

    def merge(self, a1):
        self.formats.append({'merge': a1})

    def row_fmt(self, n, spec, c0=0, c1=WIDTH - 1):
        self.fmt(f'{_col(c0)}{n}:{_col(c1)}{n}', spec)

    # -- the reusable pieces --

    def title(self, text):
        n = self.add([text])
        self.row_fmt(n, {'textFormat': {'bold': True, 'fontSize': 16}})

    def legend(self, text):
        n = self.add([text])
        self.row_fmt(n, {'textFormat': {'italic': True, 'fontSize': 9},
                         'backgroundColor': _POWDER})

    def banner(self, text, caption=None):
        self.blank()
        n = self.add([text] + [''] * 8 + [caption or ''])
        self.row_fmt(n, {'textFormat': {'bold': True, 'foregroundColor': _WHITE},
                         'backgroundColor': _NAVY})
        self.merge(f'A{n}:I{n}')
        if caption:
            self.fmt(f'J{n}', {'textFormat': {'italic': True, 'bold': False,
                                              'foregroundColor': _WHITE}})
            self.merge(f'J{n}:R{n}')
        else:
            self.merge(f'J{n}:R{n}')
        self._shaded_run = 0
        return n

    def section(self, title, band_titles, col_headers, key=None,
                first_label='Record'):
        """Section header (title + band titles) then the column-header row.
        Section 2.6: a buffer row before every section header, unless the
        row above is a banner (no blank row directly after a banner)."""
        if self.rows and not self._is_banner(self.rows[-1]):
            self.blank()
        cells = [title]
        for i, bt in enumerate(band_titles):
            cells += [''] * (BAND_STARTS[i] - len(cells)) + [bt]
        n = self.add(cells, shaded=True)
        self.row_fmt(n, {'textFormat': {'bold': True}, 'backgroundColor': _POWDER})
        for i in range(len(band_titles)):
            s = BAND_STARTS[i]
            self.merge(f'{_col(s)}{n}:{_col(s + BAND_COLS - 1)}{n}')
            self.fmt(f'{_col(s)}{n}', {'horizontalAlignment': 'CENTER',
                                       'textFormat': {'bold': True}})
        if key:
            self.jump_targets[key] = n
        self.column_header(first_label, col_headers, len(band_titles))
        return n

    def column_header(self, first, col_headers, nbands):
        cells = [first]
        for i in range(nbands):
            hdr = col_headers[i] if isinstance(col_headers[0], (list, tuple)) else col_headers
            cells += [''] * (BAND_STARTS[i] - len(cells)) + list(hdr)
        n = self.add(cells, shaded=True)
        self.row_fmt(n, {'textFormat': {'bold': True}, 'backgroundColor': _POWDER})
        return n

    def block_header(self, label, key=None):
        self.blank()
        n = self.add([label], shaded=True)
        self.row_fmt(n, {'textFormat': {'bold': True}, 'backgroundColor': _BLOCK})
        if key:
            self.jump_targets[key] = n
        return n

    def jump_row(self, items):
        """Row 4 (or under the Hall of Fame): the in-sheet jump index."""
        n = self.add(['Jump to:', ' · '.join(label for label, _ in items)])
        self.row_fmt(n, {'backgroundColor': _JUMP})
        self.fmt(f'A{n}', {'textFormat': {'bold': True}})
        self.merge(f'B{n}:R{n}')
        self.formats.append({'jump': {'cell': f'B{n}', 'items': items}})
        self._shaded_run = 0
        return n

    def group(self, start_n, end_n, collapsed=False):
        if end_n > start_n:
            self.groups.append((start_n, end_n, collapsed))

    @staticmethod
    def _is_banner(row):
        return bool(row and row[0] and str(row[0]).isupper() and len(str(row[0])) > 6
                    and str(row[0]).replace(' ', '').isalpha())


def _italic(sheet, n, band_idx):
    s = BAND_STARTS[band_idx]
    sheet.fmt(f'{_col(s)}{n}:{_col(s + BAND_COLS - 1)}{n}', {'textFormat': {'italic': True}})


def _highlight(sheet, n, band_idx):
    s = BAND_STARTS[band_idx]
    sheet.fmt(f'{_col(s)}{n}:{_col(s + BAND_COLS - 1)}{n}', {'backgroundColor': _HL})


def _recency(sheet, n, band_idx, cell, band, ctx):
    """Section 2.15 marks. Current-season band: set in the latest closed
    unit -> italic. All-time band: set this season -> italic; set in the
    latest unit -> italic + highlight."""
    if cell is None or cell.tie_n > MAX_LISTED_TIE:
        return
    row = cell.holders[0]
    latest = ctx.latest_unit
    is_latest = (latest is not None and band.grain != 'season'
                 and row.get('season') == latest[0]
                 and (row.get('mp') if band.grain == 'day' else row.get('unit')) == latest[1])
    if band.scope == 'current':
        if is_latest:
            _italic(sheet, n, band_idx)
    else:
        if row.get('season') == ctx.current_season:
            _italic(sheet, n, band_idx)
        if is_latest:
            _highlight(sheet, n, band_idx)


def _value_format(sheet, n, metric):
    pattern = {'pts': '#,##0.0', 'int': '#,##0', 'avg': '.000', 'rate': '0.00',
               'ip': '0.0'}[metric.fmt]
    for s in BAND_STARTS:
        sheet.fmt(f'{_col(s + 2)}{n}', {'numberFormat': {'type': 'NUMBER', 'pattern': pattern},
                                        'horizontalAlignment': 'RIGHT'})


def record_row(sheet, label, metric, direction, grain, bands, pools, ctx):
    """One record row across every band; omitted when no band qualifies."""
    cells = []
    for band in bands:
        pool = pools.get((grain, band.key))
        cell = None
        no_floor = (grain == 'player' and band.grain != 'season'
                    and (metric.kind == 'rate'
                         or (metric.kind == 'count' and direction == 'asc')))
        if pool is not None and not no_floor:
            cell = record_cell(pool, metric, direction, band, ctx.current_season,
                               want_runner_up=(band.last_col == 'Runner-up'), grain=grain)
            if cell is not None:
                cell.metric = metric
        cells.append(cell)
    if all(c is None for c in cells):
        return False
    out = [label]
    for i, (band, cell) in enumerate(zip(bands, cells)):
        side, _ = side_cells(cell, metric, grain, band, ctx)
        out += [''] * (BAND_STARTS[i] - len(out)) + side
    n = sheet.add(out)
    _value_format(sheet, n, metric)
    for i, (band, cell) in enumerate(zip(bands, cells)):
        _recency(sheet, n, i, cell, band, ctx)
    return True


def score_rows(sheet, grain, bands, pools, ctx, with_wasted=True):
    """Section 3 Score Records: Best x3, buffer, Worst x3, buffer, Wasted x3."""
    any_rows = False
    for m in POINTS_METRICS:
        any_rows |= record_row(sheet, f'Best {m.label}', m, 'desc', grain, bands, pools, ctx)
    sheet.blank()
    for m in POINTS_METRICS:
        any_rows |= record_row(sheet, f'Worst {m.label}', m, 'asc', grain, bands, pools, ctx)
    if with_wasted:
        sheet.blank()
        wpools = {(g, b): pools.get((g + '_wasted', b)) for (g, b) in
                  [(grain, band.key) for band in bands]}
        for m in WASTED_METRICS:
            any_rows |= record_row(sheet, m.label, m, 'desc', grain, bands, wpools, ctx)
    return any_rows


def stat_rows(sheet, grain, category, bands, pools, ctx, catalog,
              player_negatives=True, rates=True):
    """Hitting / Pitching Records: counting stats at their good end, then a
    buffer, then the rates. Section 2.4: players have no week/day floor, so
    the caller drops their negative-stat and rate rows on the Matchup tab."""
    for m in counting_metrics(catalog, category):
        if m.good_dir == 'asc' and grain == 'player' and not player_negatives:
            continue
        record_row(sheet, m.label, m, m.good_dir, grain, bands, pools, ctx)
    if rates:
        sheet.blank()
        for m in RATE_METRICS[category]:
            record_row(sheet, m.label, m, m.good_dir, grain, bands, pools, ctx)


def slot_rows(sheet, grain, bands, pools, ctx, slots):
    """Lineup Slot Records: one row per slot TYPE, lumped (section 2.11)."""
    spools = {(grain, band.key): pools.get((grain + '_slot', band.key)) for band in bands}
    for slot in slots:
        m = Metric('pts', slot['label'], 'slot', slot.get('category', 'total'),
                   'desc', 'pts')
        # Each slot has its own pool view: filter rows by slot label.
        filtered = {}
        for key, pool in spools.items():
            if pool is None:
                continue
            filtered[key] = Pool([r for r in pool.rows if r.get('slot') == slot['label']])
        record_row(sheet, slot['label'], m, 'desc', grain, bands, filtered, ctx)


def top_block(sheet, bands, pools, ctx, title, key):
    """Best Performances / Season Stars: Top-10 hitters then pitchers per band."""
    band_titles = [b.title for b in bands]
    if sheet.rows and not sheet._is_banner(sheet.rows[-1]):
        sheet.blank()
    cells = [title]
    for i, bt in enumerate(band_titles):
        cells += [''] * (BAND_STARTS[i] - len(cells)) + [bt]
    n = sheet.add(cells, shaded=True)
    sheet.row_fmt(n, {'textFormat': {'bold': True}, 'backgroundColor': _POWDER})
    for i in range(len(bands)):
        s = BAND_STARTS[i]
        sheet.merge(f'{_col(s)}{n}:{_col(s + BAND_COLS - 1)}{n}')
        sheet.fmt(f'{_col(s)}{n}', {'horizontalAlignment': 'CENTER', 'textFormat': {'bold': True}})
    sheet.jump_targets[key] = n
    start = n
    for disc, col, label in (('Hitters', 'hit_pts', 'hitting'), ('Pitchers', 'pit_pts', 'pitching')):
        if disc == 'Pitchers':
            sheet._shaded_run = 0
        hdr = ['Player', 'Franchise', 'Points', 'Stat Line',
               'Season' if bands[0].grain == 'season' else 'Period']
        headers = [['Player', 'Franchise', 'Points', 'Stat Line',
                    'Season' if b.grain == 'season' else 'Period'] for b in bands]
        sheet.column_header(disc, headers, len(bands))
        m = Metric(col, label, 'points', label, 'desc', 'pts')
        lists = []
        for band in bands:
            pool = pools.get(('player', band.key))
            season = ctx.current_season if band.scope == 'current' else None
            cands = pool.top(col, 'desc', TOP_BLOCK_DEPTH, season=season) if pool else []
            lists.append(cands)
        depth = max((len(l) for l in lists), default=0)
        for r in range(depth):
            out = [r + 1]
            for i, (band, cands) in enumerate(zip(bands, lists)):
                out += [''] * (BAND_STARTS[i] - len(out))
                if r < len(cands):
                    row = cands[r].row
                    mark = (band.grain == 'season' and band.scope == 'all'
                            and not row.get('complete', True))
                    period = (str(row.get('season')) if band.grain == 'season'
                              else _link_cell(ctx.period_label(row, band),
                                              ctx.period_link(row, band) if band.period_links else None))
                    out += [player_cell(row), row.get('abbrev') or '',
                            value_cell(m, cands[r].value, mark),
                            stat_line(row, ctx.ppu, category=label), period]
                else:
                    out += [''] * BAND_COLS
            rn = sheet.add(out)
            _value_format(sheet, rn, m)
            sheet.fmt(f'A{rn}', {'horizontalAlignment': 'CENTER'})
    sheet.group(start + 1, sheet.n)


LEGEND_POLARITY = (
    "Every record reads at the stat's good end -- most of a positive stat, "
    "fewest of a negative one (strikeouts, GIDP, walks allowed...), highest "
    "AVG/OBP/SLG/K/9, lowest ERA/WHIP -- unless its label says Worst or Wasted."
)


def build_period_tab(tab, pools, ctx, catalog, slots, legend_extra='',
                     top_titles=('Best Performances', 'Season Stars')):
    """The Matchup Records and Season Records tabs (sections 3 and 4)."""
    bands = tab['bands']
    is_season = tab['key'] == 'season'
    sheet = Sheet()
    sheet.title(tab['title'])
    floors = (f"Fewest-of and rate records need a sample: min {AB_FLOOR} AB / "
              f"{OUTS_FLOOR // 3} IP")
    if is_season:
        floors += " (players and teams)."
    else:
        floors += (" for teams; players have no week- or day-grain floor, so those "
                   "rows exist only on the Season and Lifetime tabs.")
    sheet.legend(
        "Every figure on this tab is points scored in a lineup (active points) "
        "unless its label says otherwise. Counting stats only look at "
        "standard-length periods. " + LEGEND_POLARITY + " " + floors +
        " Ties list team abbreviations; beyond three holders the count is shown. "
        "Lineup slots are lumped by type (any SP slot). Team record details list "
        "the top contributors. " + legend_extra)
    if is_season:
        sheet.legend(
            "* This Season is in progress: its figures are season-to-date, and its "
            "last column shows the runner-up instead of a season. Records set last "
            "week are italicized; all-time records set this season are italicized, "
            "and highlighted if set last week. An asterisk after a value marks an "
            "in-progress season among completed seasons -- it counts toward 'most' "
            "records and never toward 'fewest' or 'worst'.")
    else:
        sheet.legend(
            "Records set last week are italicized; all-time records set this season "
            "are italicized, and highlighted if set last week. An asterisk after a "
            "value marks an in-progress period -- it counts toward 'most' records "
            "and never toward 'fewest' or 'worst'.")
    p = 's-' if is_season else 'm-'
    jump = [('Team Score Records', p + 'tscore'), ('Team Hitting', p + 'thit'),
            ('Team Pitching', p + 'tpit'), ('Team Lineup Slots', p + 'tslot'),
            ('Player Score Records', p + 'pscore'),
            (top_titles[1] if is_season else top_titles[0], p + 'top'),
            ('Player Hitting', p + 'phit'), ('Player Pitching', p + 'ppit'),
            ('Player Lineup Slots', p + 'pslot')]
    sheet.jump_row(jump)
    band_titles = [b.title for b in bands]
    cols = [['Holder', 'Owner', 'Value', 'Details', b.last_col] for b in bands]

    sheet.banner('TEAM RECORDS')
    s0 = sheet.section('Score Records', band_titles, cols, p + 'tscore')
    score_rows(sheet, 'team', bands, pools, ctx)
    sheet.group(s0 + 1, sheet.n)
    s0 = sheet.section('Hitting Records', band_titles, cols, p + 'thit')
    stat_rows(sheet, 'team', 'hitting', bands, pools, ctx, catalog)
    sheet.group(s0 + 1, sheet.n)
    s0 = sheet.section('Pitching Records', band_titles, cols, p + 'tpit')
    stat_rows(sheet, 'team', 'pitching', bands, pools, ctx, catalog)
    sheet.group(s0 + 1, sheet.n)
    s0 = sheet.section('Lineup Slot Records', band_titles, cols, p + 'tslot')
    slot_rows(sheet, 'team', bands, pools, ctx, slots)
    sheet.group(s0 + 1, sheet.n)

    sheet.banner('PLAYER RECORDS')
    s0 = sheet.section('Score Records', band_titles, cols, p + 'pscore')
    score_rows(sheet, 'player', bands, pools, ctx)
    sheet.group(s0 + 1, sheet.n)
    top_block(sheet, bands, pools, ctx,
              top_titles[1] if is_season else top_titles[0], p + 'top')
    s0 = sheet.section('Hitting Records', band_titles, cols, p + 'phit')
    stat_rows(sheet, 'player', 'hitting', bands, pools, ctx, catalog,
              player_negatives=is_season, rates=is_season)
    sheet.group(s0 + 1, sheet.n)
    s0 = sheet.section('Pitching Records', band_titles, cols, p + 'ppit')
    stat_rows(sheet, 'player', 'pitching', bands, pools, ctx, catalog,
              player_negatives=is_season, rates=is_season)
    sheet.group(s0 + 1, sheet.n)
    s0 = sheet.section('Lineup Slot Records', band_titles, cols, p + 'pslot')
    slot_rows(sheet, 'player', bands, pools, ctx, slots)
    sheet.group(s0 + 1, sheet.n)
    return sheet


# ---------------------------------------------------------------------------
# Lifetime tab (section 5): leaderboards, Halls, team block.
# ---------------------------------------------------------------------------

def span_text(seasons):
    ys = sorted(int(y) for y in seasons if y is not None)
    if not ys:
        return ''
    n = len(ys)
    rng = str(ys[0]) if ys[0] == ys[-1] else f'{ys[0]}–{ys[-1]}'
    return f"{rng} · {n} seas." if n > 1 else rng


def aggregate(rows, key_fn, sum_cols):
    """Sum unit rows into lifetime rows keyed by key_fn; keeps identity from
    the most recent season and collects the season set."""
    acc = {}
    for row in sorted(rows, key=lambda r: (r.get('season') or 0, str(r.get('team_id') or ''))):
        k = key_fn(row)
        if k is None:
            continue
        a = acc.get(k)
        if a is None:
            a = {c: 0.0 for c in sum_cols}
            a['seasons'] = set()
            a['by_team'] = defaultdict(float)
            acc[k] = a
        for c in sum_cols:
            a[c] += float(row.get(c) or 0)
        a['seasons'].add(row.get('season'))
        a['by_team'][row.get('cid') or row.get('team_id')] += float(row.get('pts') or 0)
        for ident in ('team_id', 'team_name', 'abbrev', 'owner', 'pid', 'pname', 'dname'):
            if row.get(ident) not in (None, ''):
                a[ident] = row[ident]
    for a in acc.values():
        a['season'] = max(a['seasons']) if a['seasons'] else None
        a['unit'] = None
        add_rates(a)
    return acc


SUM_COLS = ['pts', 'hit_pts', 'pit_pts', 'neg', 'games', 'h', 'ab', 'b_bb', 'b_so',
            'hbp', 'sf', 'hr', 'r', 'rbi', 'sb', 'cs', 'tb', 'singles', 'doubles',
            'triples', 'xbh', 'gdp', 'b_ibb', 'cyc', 'w', 'l', 'k', 'er', 'outs', 'qs',
            'sv', 'hld', 'p_h', 'p_bb', 'p_hr', 'p_r', 'cg', 'blk', 'wp', 'hbp_p',
            'blsv', 'nh', 'pg', 'pk', 'sho', 'benched_hit', 'benched_pit',
            'unrostered_hit', 'unrostered_pit', 'neg_hit', 'neg_pit']


def add_wasted(row):
    """Derive the wasted columns from the term columns."""
    bh = float(row.get('benched_hit') or 0)
    bp = float(row.get('benched_pit') or 0)
    uh = float(row.get('unrostered_hit') or 0)
    up = float(row.get('unrostered_pit') or 0)
    nh = float(row.get('neg_hit') or 0)
    np_ = float(row.get('neg_pit') or 0)
    row['benched'] = bh + bp
    row['unrostered'] = uh + up
    row['negative'] = nh + np_
    row['wasted'] = row['benched'] + row['unrostered'] + row['negative']
    row['wasted_hit'] = bh + uh + nh
    row['wasted_pit'] = bp + up + np_
    return row


def slot_depth(team_count, copies):
    """Section 5: N + (k-1)*N/2 rows for a slot fielded k times."""
    return int(team_count + (max(copies, 1) - 1) * team_count / 2)


def leaderboard(sheet, label, metric, band_rows, ctx, depth, key=None,
                franchises_col=False, details_fn=None, floor=None):
    """One Top-N board across the three player bands (section 5.4)."""
    lists = []
    for rows in band_rows:
        pool = Pool(rows)
        cands = pool.top(metric.key, metric.good_dir, depth, floor=floor)
        cands = [c for c in cands if metric.good_dir == 'asc' or c.value > 0]
        lists.append(cands)
    if not any(lists):
        return False
    sheet.block_header(label, key)
    start = sheet.n
    depth_seen = max(len(l) for l in lists)
    for r in range(depth_seen):
        out = [r + 1]
        for i, cands in enumerate(lists):
            out += [''] * (BAND_STARTS[i] - len(out))
            if r < len(cands):
                row = cands[r].row
                mark = i == 2 and not row.get('complete', True)
                if i == 1:
                    fr = franchises_text(row)
                else:
                    fr = row.get('abbrev') or ''
                last = str(row.get('season') or '') if i == 2 else span_text(row.get('seasons') or [row.get('season')])
                details = details_fn(row, metric) if details_fn else ''
                out += [player_cell(row), fr, value_cell(metric, cands[r].value, mark),
                        details, last]
            else:
                out += [''] * BAND_COLS
        n = sheet.add(out)
        _value_format(sheet, n, metric)
        sheet.fmt(f'A{n}', {'horizontalAlignment': 'CENTER'})
    sheet.group(start, sheet.n, collapsed=True)
    return True


def franchises_text(row, top=3):
    """League-wide rows: the player's top franchises by active points."""
    by_team = row.get('by_team') or {}
    labels = row.get('team_labels') or {}
    ranked = sorted(by_team.items(), key=lambda kv: (-kv[1], str(kv[0])))
    return ' · '.join(f"{labels.get(t, t)} {v:,.0f}" for t, v in ranked[:top] if v >= 0.5)


def hall(sheet, banner, caption, boards, ctx, key, franchise_mode, details_fn):
    """A Hall: three side-by-side Top-25 boards (Overall / Hitters / Pitchers)."""
    sheet.banner(banner, caption)
    sheet.jump_targets[key] = sheet.n
    band_titles = ['Top 25 Careers — Overall', 'Top 25 Careers — Hitters',
                   'Top 25 Careers — Pitchers']
    cells = ['']
    for i, bt in enumerate(band_titles):
        cells += [''] * (BAND_STARTS[i] - len(cells)) + [bt]
    n = sheet.add(cells, shaded=True)
    sheet.row_fmt(n, {'textFormat': {'bold': True}, 'backgroundColor': _POWDER})
    for i in range(3):
        s = BAND_STARTS[i]
        sheet.merge(f'{_col(s)}{n}:{_col(s + BAND_COLS - 1)}{n}')
        sheet.fmt(f'{_col(s)}{n}', {'horizontalAlignment': 'CENTER', 'textFormat': {'bold': True}})
    fcol = 'Franchise' if franchise_mode else 'Franchises (points)'
    sheet.column_header('Rank', ['Player', fcol, 'Points', 'Stat Line (while active)', 'Span'], 3)
    start = sheet.n
    depth = max(len(b) for b in boards)
    m = Metric('pts', 'Points', 'points', 'total', 'desc', 'pts')
    for r in range(depth):
        out = [r + 1]
        for i, board in enumerate(boards):
            out += [''] * (BAND_STARTS[i] - len(out))
            if r < len(board):
                row, value, disc = board[r]
                fr = row.get('abbrev') or '' if franchise_mode else franchises_text(row)
                out += [player_cell(row), fr, value_cell(m, value), details_fn(row, disc),
                        span_text(row.get('seasons') or [])]
            else:
                out += [''] * BAND_COLS
        n = sheet.add(out)
        _value_format(sheet, n, m)
        sheet.fmt(f'A{n}', {'horizontalAlignment': 'CENTER'})
    sheet.group(start, sheet.n)


def hall_boards(rows, depth=HALL_DEPTH):
    """(Overall by pts, Hitters by hit_pts, Pitchers by pit_pts)."""
    def board(col, disc):
        ranked = sorted((r for r in rows if float(r.get(col) or 0) > 0),
                        key=lambda r: (-float(r.get(col) or 0), str(r.get('pid')),
                                       str(r.get('team_id') or '')))
        return [(r, float(r.get(col) or 0), disc) for r in ranked[:depth]]
    overall = sorted((r for r in rows if float(r.get('pts') or 0) > 0),
                     key=lambda r: (-float(r.get('pts') or 0), str(r.get('pid')),
                                    str(r.get('team_id') or '')))[:depth]
    overall = [(r, float(r.get('pts') or 0),
                'pitching' if float(r.get('pit_pts') or 0) > float(r.get('hit_pts') or 0) else 'hitting')
               for r in overall]
    return [overall, board('hit_pts', 'hitting'), board('pit_pts', 'pitching')]


def shame_board(rows, discipline, depth=HALL_DEPTH):
    """Wasted Hall of Shame: one discipline's top-25 by wasted points of
    that type; rows carry the term columns and bench_by_{disc}."""
    key = 'wasted_hit' if discipline == 'hitting' else 'wasted_pit'
    ranked = sorted((r for r in rows if float(r.get(key) or 0) > 0),
                    key=lambda r: (-float(r.get(key) or 0), str(r.get('pid'))))
    return ranked[:depth]


def shame_cells(row, discipline):
    d = 'hit' if discipline == 'hitting' else 'pit'
    benched = float(row.get(f'benched_{d}') or 0)
    unrostered = float(row.get(f'unrostered_{d}') or 0)
    negative = float(row.get(f'neg_{d}') or 0)
    active = float(row.get('hit_pts' if d == 'hit' else 'pit_pts') or 0)
    wasted = benched + unrostered + negative
    total = wasted + active
    pct = (wasted / total * 100) if total else 0.0
    terms = sorted([('benched', benched), ('unrostered', unrostered), ('negative', negative)],
                   key=lambda t: -t[1])
    breakdown = ' · '.join(f'{v:,.0f} {lbl}' for lbl, v in terms) + f' · {pct:.0f}% of career wasted'
    bench_by = row.get(f'bench_by_{d}') or ''
    return [player_cell(row), bench_by, round(wasted, 1), breakdown]


def build_lifetime_tab(tab, data, ctx, catalog, slots):
    """Section 5. `data` carries:
    player_franchise: rows keyed (pid, team_id) with sums + seasons
    player_league:    rows keyed pid (+ by_team, team_labels)
    player_season:    player-team-season rows
    shame:            per-player rows with the discipline term columns
    team_total:       per-franchise lifetime rows (active franchises)
    team_avg:         per-franchise per-completed-season average rows
    slot_*:           the same three shapes for slot points, keyed with 'slot'
    """
    N = ctx.team_count
    sheet = Sheet()
    sheet.title(tab['title'])
    sheet.legend(
        "Every figure on this tab is points scored in a lineup (active points) "
        "unless its label says otherwise. Every board reads at the stat's good "
        "end -- most of a positive stat, highest AVG/OBP/SLG/K/9, lowest ERA/WHIP "
        "-- unless its label says Wasted. Negative counting stats (strikeouts, "
        "GIDP, walks allowed...) have no lifetime board: fewest-over-a-career only "
        f"rewards short careers. Pitching rate stats require min {OUTS_FLOOR // 3} IP, "
        f"hitting rate stats require min {AB_FLOOR} AB. Leaderboards run Top-{N} -- "
        "one row per franchise in the league -- except the Halls (25) and lineup "
        "slots a league fields more than once, which add half a board per extra "
        "slot. " + data.get('legend_extra', ''))
    sheet.legend(
        "Lifetime records set this year are italicized. An asterisk after a value "
        "marks an in-progress season -- it counts toward 'most' boards and never "
        "toward 'fewest'. Sections and boards are collapsible (the +/- in the "
        "margin); the Halls open expanded.")

    def hall_details(row, disc):
        return ' || '.join(p for p in (slash_line(row, disc),
                                       stat_line(row, ctx.ppu, top_n=3, category=disc)) if p)

    league_rows = list(data['player_league'].values())
    hall(sheet, 'LEAGUE HALL OF FAME',
         'the league is its own fiction — these are its heroes · top 25 careers '
         'in the league, by points scored in any lineup',
         hall_boards(league_rows), ctx, 'l-lhof', False, hall_details)
    sheet.blank()
    fr_rows = list(data['player_franchise'].values())
    hall(sheet, 'FRANCHISE HALL OF FAME',
         'top 25 careers with one franchise, by points scored in that lineup',
         hall_boards(fr_rows), ctx, 'l-hof', True, hall_details)
    sheet.blank()
    sheet.jump_row([('Franchise Hall of Fame', 'l-hof'), ('Player Score Records', 'l-score'),
                    ('Player Hitting', 'l-hit'), ('Player Pitching', 'l-pit'),
                    ('Player Lineup Slots', 'l-slot'), ('Wasted Hall of Shame', 'l-hos'),
                    ('Team Records', 'l-team')])

    sheet.banner('PLAYER RECORDS')
    bands_pts = ['Player Lifetime Points by Franchise', 'Player Lifetime Points — League-wide',
                 'Single Season Player Points by Franchise']
    bands_tot = [b.replace('Points', 'Totals') for b in bands_pts]
    cols = [['Player', 'Franchise', 'Value', 'Details', 'Span'],
            ['Player', 'Franchises', 'Value', 'Details', 'Span'],
            ['Player', 'Franchise', 'Value', 'Details', 'Season']]
    band_rows = [fr_rows, league_rows, data['player_season']]

    def pts_details(row, metric):
        if metric.key == 'pts':
            return f"{float(row.get('hit_pts') or 0):,.0f} hitting · {float(row.get('pit_pts') or 0):,.0f} pitching"
        if metric.kind == 'wasted':
            return wasted_breakdown(row)
        cat = metric.category if metric.category in ('hitting', 'pitching') else None
        return stat_line(row, ctx.ppu, category=cat)

    def count_details(row, metric):
        if metric.kind == 'rate' or metric.good_dir == 'asc':
            return qualifier_text(row, metric.floor)
        cat = metric.category if metric.category in ('hitting', 'pitching') else None
        return f"{qualifier_text(row, 'ab' if cat == 'hitting' else 'outs')} · " \
               f"{stat_line(row, ctx.ppu, top_n=2, category=cat)}"

    s0 = sheet.section('Score Records', bands_pts, cols, 'l-score', first_label='Rank')
    for m in POINTS_METRICS:
        leaderboard(sheet, m.label, m, band_rows, ctx, N, details_fn=pts_details)
    leaderboard(sheet, 'Most Wasted Points', WASTED_METRICS[0], band_rows, ctx, N,
                details_fn=pts_details)
    sheet.group(s0 + 1, sheet.n, collapsed=True)

    for cat, key in (('hitting', 'l-hit'), ('pitching', 'l-pit')):
        s0 = sheet.section(f'{cat.title()} Records', bands_tot, cols, key, first_label='Rank')
        for m in counting_metrics(catalog, cat):
            if m.good_dir == 'asc':
                continue          # section 5: no negative-stat lifetime board
            leaderboard(sheet, m.label, m, band_rows, ctx, N, details_fn=count_details)
        for m in RATE_METRICS[cat]:
            leaderboard(sheet, m.label, m, band_rows, ctx, N, details_fn=count_details,
                        floor=m.floor)
        sheet.group(s0 + 1, sheet.n, collapsed=True)

    s0 = sheet.section('Lineup Slot Records', bands_pts, cols, 'l-slot', first_label='Rank')
    slot_bands = [list(data['slot_franchise'].values()), list(data['slot_league'].values()),
                  data['slot_season']]
    for slot in slots:
        m = Metric('pts', slot['label'], 'slot', slot.get('category', 'total'), 'desc', 'pts')
        rows_for = [[r for r in rows if r.get('slot') == slot['label']] for rows in slot_bands]
        leaderboard(sheet, slot['label'], m, rows_for, ctx,
                    slot_depth(N, slot.get('count', 1)),
                    details_fn=lambda r, mm: f"{float(r.get('games') or 0):,.0f} games at slot")
    sheet.group(s0 + 1, sheet.n, collapsed=True)

    # Wasted Hall of Shame closes the player section.
    sheet.banner('WASTED HALL OF SHAME',
                 'top 25 careers each side, by wasted points of that type '
                 '(unrostered + benched + negative)')
    sheet.jump_targets['l-hos'] = sheet.n
    cells = ['']
    for i, bt in enumerate(['Hitters — Most Wasted Career Points',
                            'Pitchers — Most Wasted Career Points']):
        cells += [''] * (BAND_STARTS[i] - len(cells)) + [bt]
    n = sheet.add(cells, shaded=True)
    sheet.row_fmt(n, {'textFormat': {'bold': True}, 'backgroundColor': _POWDER})
    for i in range(2):
        s = BAND_STARTS[i]
        sheet.merge(f'{_col(s)}{n}:{_col(s + BAND_COLS - 1)}{n}')
        sheet.fmt(f'{_col(s)}{n}', {'horizontalAlignment': 'CENTER', 'textFormat': {'bold': True}})
    sheet.column_header('Rank', ['Player', 'Benched Most By', 'Wasted Points', 'Breakdown', ''], 2)
    shame_rows = data['shame']
    hitters = shame_board(shame_rows, 'hitting')
    pitchers = shame_board(shame_rows, 'pitching')
    start = sheet.n
    for r in range(max(len(hitters), len(pitchers))):
        out = [r + 1]
        for i, board, disc in ((0, hitters, 'hitting'), (1, pitchers, 'pitching')):
            out += [''] * (BAND_STARTS[i] - len(out))
            out += shame_cells(board[r], disc) + [''] if r < len(board) else [''] * BAND_COLS
        n = sheet.add(out)
        sheet.fmt(f'A{n}', {'horizontalAlignment': 'CENTER'})
        for s in BAND_STARTS[:2]:
            sheet.fmt(f'{_col(s + 2)}{n}', {'numberFormat': {'type': 'NUMBER', 'pattern': '#,##0.0'},
                                            'horizontalAlignment': 'RIGHT'})
            sheet.fmt(f'{_col(s + 3)}{n}', {'textFormat': {'fontSize': 8}})
            sheet.merge(f'{_col(s + 3)}{n}:{_col(s + 4)}{n}')
    sheet.group(start, sheet.n)

    # Team block.
    sheet.banner('TEAM RECORDS',
                 'currently-active franchises · per-season figures are averages over '
                 'completed seasons · full stat-by-stat table on Advanced Standings')
    sheet.jump_targets['l-team'] = sheet.n
    tbands = ['Lifetime Totals', 'Average per Completed Season']
    tcols = [['Franchise', 'Owner', 'Value', 'Details', 'Span'],
             ['Franchise', 'Owner', 'Value', 'Details', 'Seasons']]
    t_total, t_avg = data['team_total'], data['team_avg']

    def team_board(label, metric, details_fn, key=None, floor=None):
        lists = []
        for rows in (t_total, t_avg):
            cands = Pool(rows).top(metric.key, metric.good_dir, len(rows), floor=floor)
            lists.append([c for c in cands if metric.good_dir == 'asc' or c.value > 0])
        if not any(lists):
            return
        sheet.block_header(label, key)
        start = sheet.n
        for r in range(max(len(l) for l in lists)):
            out = [r + 1]
            for i, cands in enumerate(lists):
                out += [''] * (BAND_STARTS[i] - len(out))
                if r < len(cands):
                    row = cands[r].row
                    last = span_text(row.get('seasons') or []) if i == 0 else \
                        len(row.get('completed_seasons') or [])
                    out += [row.get('team_name') or '', owner_cell(row),
                            value_cell(metric, cands[r].value), details_fn(row, metric), last]
                else:
                    out += [''] * BAND_COLS
            n = sheet.add(out)
            _value_format(sheet, n, metric)
            sheet.fmt(f'A{n}', {'horizontalAlignment': 'CENTER'})
        sheet.group(start, sheet.n, collapsed=True)

    def team_details(row, metric):
        if metric.key == 'pts':
            return f"{float(row.get('hit_pts') or 0):,.0f} hitting · {float(row.get('pit_pts') or 0):,.0f} pitching"
        if metric.kind == 'wasted':
            return wasted_breakdown(row)
        if metric.kind == 'rate':
            return qualifier_text(row, metric.floor)
        top = (row.get('top_players') or {}).get(metric.key) or []
        return ', '.join(f'{n}: {fmt_value(metric, v)}' for n, v in top[:3])

    s0 = sheet.section('Team Score Records', tbands, tcols, first_label='Rank')
    for m in POINTS_METRICS:
        team_board(m.label, m, team_details)
    team_board('Most Wasted Points', WASTED_METRICS[0], team_details)
    sheet.group(s0 + 1, sheet.n, collapsed=True)
    for cat in ('hitting', 'pitching'):
        s0 = sheet.section(f'Team {cat.title()} Records', tbands, tcols, first_label='Rank')
        for m in counting_metrics(catalog, cat):
            if m.good_dir == 'asc':
                continue
            team_board(m.label, m, team_details)
        for m in RATE_METRICS[cat]:
            team_board(m.label, m, team_details, floor=m.floor)
        sheet.group(s0 + 1, sheet.n, collapsed=True)
    return sheet


def order_slots(slots):
    """MLB-278 order via sort_order, falling back to the house list."""
    def key(s):
        so = s.get('sort_order')
        if so is not None:
            return (0, so)
        lab = s['label']
        return (1, _SLOT_FALLBACK_ORDER.index(lab) if lab in _SLOT_FALLBACK_ORDER else 99)
    return sorted(slots, key=key)
