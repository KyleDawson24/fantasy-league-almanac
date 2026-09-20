"""output/export_shared.py -- the shared almanac export (MLB-301).

ONE export that the Google Sheet writer and the browser prototype can both
read, written once per almanac update: every reporting mart the almanac's
standings pages print, each row carrying the number the page prints AND the
facts needed to recompute it honestly -- the raw total, which denominator
was used and its value, the display precision, the scoring lens and the
good/bad direction -- plus a presentation descriptor generated from the
writer's own gradient functions, and the all-play mart.

WHAT THIS IS AND IS NOT. This is the master-table contract's pre-baked layer
generalized to every mart, NOT a second data interface: its vocabulary
(league key, platform team id, season, scoring_lens = platform | calculated,
metric key, denominator_kind, polarity) is the contract's vocabulary, so a
later atom-derived surface replaces a pre-baked cell without the page
changing. It reads the same almanac_data functions the Sheet writer reads
and prints through the same almanac_render helpers, so a printed value here
equals the Sheet's by construction rather than by re-derivation.

TWO DIFFERENT DENOMINATORS ARE PRESERVED EXACTLY, because today's pages use
two and changing either is Kyle's call (MLB-302), never a side effect:

  * Detailed Standings:      total * standard_matchup_days / scoring_days_played
                             (almanac_render._per_week_value)
  * Points by Lineup Slot:   total / matchup_periods_played
                             (almanac_logic.build_advanced_standings_tab_rows
                              and almanac_data.get_team_slot_points_alltime)

Every applicable row says which one it used (`denominator_kind`) and with
what value, so a consumer can toggle Per Matchup / Totals without silently
changing meaning. Acquisition channels carry `denominator_kind = 'none'`:
no per-matchup denominator exists for them today, and inventing one is
MLB-302's scope.

NOTHING IS REVERSE-CALCULATED FROM A ROUNDED AVERAGE, and no Sheet cell is
read as a source. The Sheet's unformatted cells are the reconciliation
target for this export, never its input.

WASTED IS NOT HERE. Its definition is contract-bound and a garnish question
is open with Kyle; exporting it would freeze an answer nobody has given.

OUTPUT. data/exports/shared/<league_slug>/<snapshot_ts>/ (gitignored, local,
real names -- publishing an anonymized copy is MLB-299's job under the
MLB-176 guard): one JSON file per table (Parquet when a table reaches
PARQUET_THRESHOLD rows), manifest.json, presentation.json, and a LATEST
pointer one level up. Re-running on the same warehouse with the same
--snapshot-ts reproduces every byte.

Imports are one-way, like the rest of the almanac split: this module reads
almanac_data / almanac_render / almanac_logic and nothing reads it.
"""

import argparse
import re
import hashlib
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import db
from db import league_predicate, query_for_presentation
import almanac_data
import almanac_logic
import almanac_render

SCHEMA_VERSION = 1

# Denominator vocabulary. Every applicable row names one of these.
DENOMINATOR_SCORING_DAYS = 'scoring_days_played'
DENOMINATOR_MATCHUPS = 'matchup_periods_played'
DENOMINATOR_STANDARD_DAYS = 'standard_matchup_days'
DENOMINATOR_INVOLVEMENT = 'team_involvement_weight'
DENOMINATOR_NONE = 'none'

# Scoring-lens vocabulary (the contract's).
LENS_CALCULATED = 'calculated'
LENS_PLATFORM = 'platform'

# Polarity: +1 green-high, -1 green-low, 0 ungraded. The Net deltas carry
# +1 with the zero-centred palette -- more net is better, and the palette
# says where white sits.
POLARITY_HIGH, POLARITY_LOW, POLARITY_NONE = 1, -1, 0

PALETTE_BY_DIRECTION = {
    'most': 'three_good_high',
    'fewest': 'three_good_low',
    'diverging': 'diverging_zero',
    None: None,
}
POLARITY_BY_DIRECTION = {
    'most': POLARITY_HIGH,
    'fewest': POLARITY_LOW,
    'diverging': POLARITY_HIGH,
    None: POLARITY_NONE,
}

# Table rows at or above this count go to Parquet; smaller tables stay JSON
# so a browser can fetch them without a decoder.
PARQUET_THRESHOLD = 2000

ALL_TIME = 'all_time'

# Acquisition channels in the order _ACQ_HALF lays them out. offset is the
# position inside one half of ACQUISITION_HEADER, which is what
# acquisition_gradient_columns speaks in.
ACQUISITION_CHANNELS = (
    # key,          group,      label,     offset
    ('keeper',      'acquired', 'Keeper',  0),
    ('draft',       'acquired', 'Draft',   1),
    ('fa_add',      'acquired', 'Pickup',  2),
    ('trade',       'acquired', 'Trade',   3),
    ('acquired',    'acquired', 'Total',   4),
    ('dropped',     'lost',     'Release', 6),
    ('traded_away', 'lost',     'Trade',   7),
    ('lost',        'lost',     'Total',   8),
    ('fa_delta',    'net',      'FA',      10),
    ('trade_delta', 'net',      'Trade',   11),
)
ACQUISITION_LENSES = ('active', 'rostered')


class ExportError(RuntimeError):
    """A self-check the export refuses to paper over."""


# ---------------------------------------------------------------------------
# Small value helpers
# ---------------------------------------------------------------------------

def _num(value):
    """Warehouse scalar -> JSON-safe number (None stays None, '' -> None)."""
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    return value


def _int(value):
    value = _num(value)
    return None if value is None else int(value)


def _float(value):
    value = _num(value)
    return None if value is None else float(value)


def _printed(value):
    """almanac_render's per-week helpers return '' for a missing
    denominator; the export says None."""
    return None if value == '' else value


def _json_default(value):
    if isinstance(value, Decimal):
        return _num(value)
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    raise TypeError(f'not JSON serializable: {type(value).__name__}')


def _dumps(obj):
    return json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=False,
                      default=_json_default) + '\n'


def _record(wins, losses, ties):
    return f'{wins}-{losses}-{ties}' if ties else f'{wins}-{losses}'


# ---------------------------------------------------------------------------
# Pure shapers: warehouse rows -> long-form export rows. No queries here, so
# the unit suite can pin them against hand-built rows.
# ---------------------------------------------------------------------------

def _standings_base(scope, season_year, rank, row):
    wins, losses, ties = (_int(row.get('wins')) or 0, _int(row.get('losses')) or 0,
                          _int(row.get('ties')) or 0)
    return {
        'scope': scope,
        'season_year': season_year,
        'rank': rank,
        'team_id': _int(row.get('team_id')),
        'team_abbrev': row.get('team_abbrev') or '',
        'team_name': row.get('team_name') or '',
        'owner_display': row.get('owner_display') or '',
        'wins': wins,
        'losses': losses,
        'ties': ties,
        'record': _record(wins, losses, ties),
        'matchup_periods_played': _int(row.get('matchup_periods_played')),
        'scoring_days_played': _int(row.get('scoring_days_played')),
        'standard_matchup_days': _int(row.get('standard_matchup_days')),
    }


def standings_long_rows(scope, season_year, standings_rows, hitting_specs,
                        pitching_specs):
    """Detailed Standings as one row per (team, metric). The printed value
    is computed by almanac_render's own _per_week_stat / _per_week_value,
    so it is the Sheet's number and not a re-statement of the formula."""
    out = []
    points_metrics = (
        # key, column, label, category, polarity
        ('OFFENSE', 'calculated_hitting_pts', 'Offense', 'points', POLARITY_HIGH),
        ('DEFENSE', 'calculated_pitching_pts', 'Defense', 'points', POLARITY_HIGH),
        ('TOTAL', 'calculated_points', 'Total', 'points', POLARITY_HIGH),
        ('AGAINST', 'against_calculated_points', 'Against', 'points', POLARITY_LOW),
    )
    for rank, row in enumerate(standings_rows, start=1):
        base = _standings_base(scope, season_year, rank, row)
        order = 0

        def metric(**fields):
            nonlocal order
            order += 1
            record = dict(base)
            record.update({
                'metric_order': order,
                'denominator_kind': DENOMINATOR_SCORING_DAYS,
                'denominator_value': base['scoring_days_played'],
                'display_precision': 1,
            })
            record.update(fields)
            out.append(record)

        for category, specs in (('hitting', hitting_specs), ('pitching', pitching_specs)):
            for spec in specs:
                stat_name = spec.get('stat_name')
                column = almanac_data._fact_stat_column_name(stat_name)
                direction = almanac_data._team_week_good_record_direction(spec)
                raw = _float(row.get(column))
                metric(
                    metric_key=stat_name,
                    metric_category=category,
                    display_label=almanac_render._team_week_stat_header(spec),
                    points_per_unit=_float(spec.get('points_per_unit')),
                    scoring_lens=None,
                    polarity=POLARITY_BY_DIRECTION[direction],
                    palette=PALETTE_BY_DIRECTION[direction],
                    raw_total=raw,
                    raw_unit='outs' if stat_name == 'OUTS' else None,
                    display_transform='outs_to_innings' if stat_name == 'OUTS' else None,
                    per_matchup_value=_printed(almanac_render._per_week_stat(row, spec)),
                )
            if category == 'hitting':
                key, column, label, cat, polarity = points_metrics[0]
            else:
                key, column, label, cat, polarity = points_metrics[1]
            metric(
                metric_key=key, metric_category=cat, display_label=label,
                points_per_unit=None, scoring_lens=LENS_CALCULATED,
                polarity=polarity,
                palette='three_good_high' if polarity > 0 else 'three_good_low',
                raw_total=_float(row.get(column)), raw_unit=None,
                display_transform=None,
                per_matchup_value=_printed(almanac_render._per_week_value(row, row.get(column))),
            )
        for key, column, label, cat, polarity in points_metrics[2:]:
            metric(
                metric_key=key, metric_category=cat, display_label=label,
                points_per_unit=None, scoring_lens=LENS_CALCULATED,
                polarity=polarity,
                palette='three_good_high' if polarity > 0 else 'three_good_low',
                raw_total=_float(row.get(column)), raw_unit=None,
                display_transform=None,
                per_matchup_value=_printed(almanac_render._per_week_value(row, row.get(column))),
            )
    return out


def slot_long_rows(scope, season_year, slot_rows, matchups_by_team,
                   labels_by_team, printed_by_key=None):
    """Points by Lineup Slot as one row per (team, slot), normalized by
    matchup_periods_played exactly as the builder's _per_matchup does.

    printed_by_key, when given, is the writer's OWN printed value per
    (team_id, slot) -- get_team_slot_points_alltime rounds in SQL -- and the
    export refuses to disagree with it rather than quietly exporting two
    numbers for one cell."""
    out = []
    for r in slot_rows:
        team_id = _int(r.get('team_id'))
        slot = r.get('lineup_slot')
        raw = _float(r.get('slot_pts'))
        mp = matchups_by_team.get(team_id)
        printed = round(raw / mp, 1) if raw is not None and mp else None
        if printed_by_key is not None:
            expected = printed_by_key.get((team_id, slot))
            if expected is not None and printed is not None and abs(float(expected) - printed) > 1e-9:
                raise ExportError(
                    f'slot {slot} team {team_id} scope {scope}: writer prints '
                    f'{expected}, export computes {printed} from {raw} / {mp}')
        labels = labels_by_team.get(team_id, {})
        out.append({
            'scope': scope,
            'season_year': season_year,
            'team_id': team_id,
            'team_abbrev': labels.get('team_abbrev', ''),
            'owner_display': labels.get('owner_display', ''),
            'lineup_slot': slot,
            'sort_order': _int(r.get('sort_order')),
            'scoring_lens': LENS_CALCULATED,
            'polarity': POLARITY_HIGH,
            'palette': 'three_good_high',
            'raw_total': raw,
            'denominator_kind': DENOMINATOR_MATCHUPS,
            'denominator_value': mp,
            'display_precision': 1,
            'per_matchup_value': printed,
        })
    out.sort(key=lambda x: (x['sort_order'] if x['sort_order'] is not None else 999,
                            x['lineup_slot'] or '', x['team_id'] or 0))
    return out


def acquisition_long_rows(scope, season_year, acquisition_rows):
    """Production by Acquisition Channel as one row per (team, lens,
    channel). Printed values go through almanac_render._acq_num, the
    writer's own coercion. No denominator exists for these today (MLB-302
    owns whether one should), so denominator_kind is 'none'."""
    out = []
    polarity_by_group = {'acquired': POLARITY_HIGH, 'lost': POLARITY_LOW, 'net': POLARITY_HIGH}
    palette_by_group = {'acquired': 'three_good_high', 'lost': 'three_good_low',
                        'net': 'diverging_zero'}
    for lens in ACQUISITION_LENSES:
        suffix = almanac_render._ACQ_LENS_SUFFIX[lens]
        total_key = f'acquired_{suffix}'
        ranked = sorted(
            acquisition_rows,
            key=lambda r: (-float(r.get(total_key) or 0), r.get('team_abbrev') or ''))
        for lens_rank, row in enumerate(ranked, start=1):
            for channel, group, label, offset in ACQUISITION_CHANNELS:
                column = f'{channel}_{suffix}'
                out.append({
                    'scope': scope,
                    'season_year': season_year,
                    'lens': lens,
                    'lens_rank': lens_rank,
                    'team_id': _int(row.get('team_id')),
                    'team_abbrev': row.get('team_abbrev') or '',
                    'owner_display': row.get('owner_display') or '',
                    'channel_key': channel,
                    'channel_group': group,
                    'display_label': label,
                    'scoring_lens': LENS_CALCULATED,
                    'polarity': polarity_by_group[group],
                    'palette': palette_by_group[group],
                    'raw_total': _float(row.get(column)),
                    'denominator_kind': DENOMINATOR_NONE,
                    'denominator_value': None,
                    'display_precision': 1,
                    'printed_value': almanac_render._acq_num(row.get(column)),
                })
    return out


def affinity_long_rows(affinity_rows, standings_rows):
    """Roster affinity as one row per (scope, club, team): the raw PA+BF
    involvement weight, the team's total (the denominator), and the share
    the Sheet prints (a 3-decimal fraction formatted as a percent). The
    club spine, the sentinel's placement and the share arithmetic follow
    build_advanced_standings_tab_rows. is_row_max marks each club's
    highest share across teams, ties included -- the bold rule."""
    team_ids = [_int(t['team_id']) for t in standings_rows]
    id_set = set(team_ids)
    labels = {_int(t['team_id']): t for t in standings_rows}
    weights = {'season': {}, ALL_TIME: {}}
    clubs = set()
    for r in affinity_rows:
        tid = _int(r.get('team_id'))
        if tid not in id_set:
            continue
        club = r['pro_team']
        clubs.add(club)
        weights['season'][(tid, club)] = float(r.get('season_wt') or 0)
        weights[ALL_TIME][(tid, club)] = float(r.get('alltime_wt') or 0)
    club_name = {c: (almanac_render.ESPN_UNATTRIBUTED_CLUB
                     if c == almanac_data.AFFINITY_UNATTRIBUTED
                     else almanac_render.ESPN_PRO_TEAM_NAMES.get(c, c))
                 for c in clubs}
    club_list = sorted(clubs, key=lambda c: (c == almanac_data.AFFINITY_UNATTRIBUTED,
                                             club_name[c].lower()))
    out = []
    for scope in ('season', ALL_TIME):
        totals = {tid: sum(weights[scope].get((tid, c), 0.0) for c in club_list)
                  for tid in team_ids}
        for club_order, club in enumerate(club_list, start=1):
            rows = []
            for tid in team_ids:
                games = weights[scope].get((tid, club), 0.0)
                total = totals[tid]
                share = round(games / total, 3) if games and total else None
                rows.append({
                    'scope': scope,
                    'club_key': club,
                    'club_name': club_name[club],
                    'club_order': club_order,
                    'is_unattributed': club == almanac_data.AFFINITY_UNATTRIBUTED,
                    'team_id': tid,
                    'team_abbrev': labels[tid].get('team_abbrev') or '',
                    'owner_display': labels[tid].get('owner_display') or '',
                    'scoring_lens': None,
                    'polarity': POLARITY_HIGH,
                    'palette': 'affinity_block',
                    'raw_weight': games,
                    'denominator_kind': DENOMINATOR_INVOLVEMENT,
                    'denominator_value': total,
                    'display_precision': 3,
                    'display_format': 'percent',
                    'share': share,
                })
            shares = [r['share'] for r in rows if r['share'] is not None]
            best = max(shares) if shares else None
            for r in rows:
                r['is_row_max'] = bool(best is not None and r['share'] == best)
            out.extend(rows)
    return out


def finish_rows(finishes_rows, season_year):
    """Season finishes, one row per (season, team), with the medal and the
    printed cell decided the way the builder decides them: the trophy from
    is_champion, silver/bronze from the platform's post-playoff rank, and
    the in-flight season a plain seed."""
    out = []
    for e in finishes_rows:
        year = _int(e.get('season_year'))
        finish = _int(e.get('finish'))
        current = year == int(season_year)
        if current:
            medal = None
        elif e.get('is_champion'):
            medal = almanac_render.FINISH_MEDALS[1]
        else:
            medal = almanac_render.finish_medal(e.get('final_rank'))
            if medal == almanac_render.FINISH_MEDALS[1]:
                medal = None
        out.append({
            'season_year': year,
            'is_current_season': current,
            'team_id': _int(e.get('team_id')),
            'team_abbrev': e.get('team_abbrev') or '',
            'owner_display': e.get('owner_display') or '',
            'wins': _int(e.get('wins')),
            'losses': _int(e.get('losses')),
            'ties': _int(e.get('ties')),
            'finish': finish,
            'final_rank': _int(e.get('final_rank')),
            'is_champion': bool(e.get('is_champion')),
            'medal': medal,
            'printed_cell': (f'{medal} {finish}' if medal else finish),
            'polarity': POLARITY_LOW,
            'palette': 'finish',
            'display_precision': 0,
        })
    out.sort(key=lambda r: (r['season_year'], r['finish'] or 0, r['team_id'] or 0))
    return out


def finish_summary_rows(finishes_rows, standings_rows, season_year, rank_arc_rows):
    """The finishes side table's per-team spine -- Titles, all-time W%, Avg
    finish, current standing -- computed as build_advanced_standings_tab_rows
    computes it (titles closed-only, Avg including the season in flight,
    W% with half a win per tie), in its order."""
    season_year = int(season_year)
    closed = sorted({_int(r['season_year']) for r in finishes_rows
                     if _int(r['season_year']) != season_year})
    fin_by_team = {}
    for r in finishes_rows:
        fin_by_team.setdefault(_int(r['team_id']), {})[_int(r['season_year'])] = r
    rank_of = {}
    last_period = None
    if rank_arc_rows:
        periods = sorted({int(r['period']) for r in rank_arc_rows})
        last_period = periods[-1]
        rank_of = {(_int(r['team_id']), int(r['period'])): int(r['standings_rank'])
                   for r in rank_arc_rows}

    def stats(tid):
        entries = fin_by_team.get(tid, {})
        titles = sum(1 for y, e in entries.items() if y in closed and e.get('is_champion'))
        w = sum(int(e['wins'] or 0) for e in entries.values())
        losses = sum(int(e['losses'] or 0) for e in entries.values())
        t = sum(int(e['ties'] or 0) for e in entries.values())
        games = w + losses + t
        wpct = (w + 0.5 * t) / games if games else None
        ranks = [int(e['finish']) for e in entries.values()]
        avg = round(sum(ranks) / len(ranks), 1) if ranks else None
        return titles, wpct, avg, w, losses, t

    ordered = sorted(
        standings_rows,
        key=lambda t: (-stats(_int(t['team_id']))[0],
                       -(stats(_int(t['team_id']))[1] or 0.0),
                       t.get('owner_display') or ''))
    out = []
    for order, t in enumerate(ordered, start=1):
        tid = _int(t['team_id'])
        titles, wpct, avg, w, losses, ties = stats(tid)
        current = fin_by_team.get(tid, {}).get(season_year, {}).get('finish')
        current_rank = _int(current) or rank_of.get((tid, last_period))
        out.append({
            'display_order': order,
            'team_id': tid,
            'team_abbrev': t.get('team_abbrev') or '',
            'owner_display': t.get('owner_display') or '',
            'titles': titles,
            'wins': w,
            'losses': losses,
            'ties': ties,
            'win_pct': round(wpct, 3) if wpct is not None else None,
            'win_pct_display_precision': 3,
            'avg_finish': avg,
            'avg_finish_display_precision': 1,
            'avg_finish_includes_current_season': True,
            'current_season_rank': current_rank,
            'closed_seasons': closed,
        })
    return out


def all_play_summary_rows(all_play_rows):
    """Per (scope, team) all-play totals from the matchup-grain mart rows:
    strict wins / losses / ties summed, expected wins / losses / ties as the
    sum of per-matchup shares (so a changing league size sums honestly), and
    expected W% = expected wins / matchups. Printed forms follow the
    prototype's convention: one decimal, ties shown only when non-zero."""
    groups = defaultdict(lambda: {'matchups': 0, 'wins': 0, 'losses': 0, 'ties': 0,
                                  'expected_wins': 0.0, 'expected_losses': 0.0,
                                  'expected_ties': 0.0, 'label': None})
    for r in all_play_rows:
        tid = _int(r['team_id'])
        for scope in (str(_int(r['season_year'])), ALL_TIME):
            g = groups[(scope, tid)]
            g['matchups'] += 1
            g['wins'] += _int(r['all_play_wins'])
            g['losses'] += _int(r['all_play_losses'])
            g['ties'] += _int(r['all_play_ties'])
            g['expected_wins'] += float(r['expected_win_share'])
            g['expected_losses'] += float(r['expected_loss_share'])
            g['expected_ties'] += float(r['expected_tie_share'])
            key = (_int(r['season_year']), _int(r['matchup_period']))
            if g['label'] is None or key > g['label'][0]:
                g['label'] = (key, r.get('team_abbrev') or '', r.get('team_name') or '')
    out = []
    for (scope, tid), g in groups.items():
        ew, el, et = g['expected_wins'], g['expected_losses'], g['expected_ties']
        pct = ew / g['matchups'] if g['matchups'] else None
        printed_record = f'{ew:.1f}-{el:.1f}' + (f'-{et:.1f}' if round(et, 1) else '')
        out.append({
            'scope': scope,
            'season_year': None if scope == ALL_TIME else int(scope),
            'team_id': tid,
            'team_abbrev': g['label'][1],
            'team_name': g['label'][2],
            'scoring_lens': LENS_PLATFORM,
            'matchups': g['matchups'],
            'all_play_wins': g['wins'],
            'all_play_losses': g['losses'],
            'all_play_ties': g['ties'],
            'all_play_record': _record(g['wins'], g['losses'], g['ties']),
            'expected_wins': ew,
            'expected_losses': el,
            'expected_ties': et,
            'expected_record_printed': printed_record,
            'expected_win_pct': pct,
            'expected_win_pct_printed': f'{100 * pct:.1f}%' if pct is not None else None,
            'denominator_kind': 'matchups',
            'denominator_value': g['matchups'],
            'display_precision': 1,
            'polarity': POLARITY_HIGH,
            'palette': 'three_good_high',
        })
    out.sort(key=lambda r: (r['scope'], -r['expected_wins'], r['team_id'] or 0))
    return out


# ---------------------------------------------------------------------------
# Presentation descriptor, generated from the writer's own functions.
# ---------------------------------------------------------------------------

def _gradient(minpoint, midpoint, maxpoint):
    return {'minpoint': minpoint, 'midpoint': midpoint, 'maxpoint': maxpoint}


RED = {'red': 0.96, 'green': 0.62, 'blue': 0.60}
GREEN = {'red': 0.67, 'green': 0.86, 'blue': 0.64}
WHITE = {'red': 1, 'green': 1, 'blue': 1}

# The palettes almanac_write._color_scale_request and the affinity /
# finishes rules emit, spelled once here. Colours are Sheets 0..1 floats.
PALETTES = {
    'three_good_high': _gradient(
        {'type': 'MIN', 'color': RED},
        {'type': 'PERCENTILE', 'value': 50, 'color': WHITE},
        {'type': 'MAX', 'color': GREEN}),
    'three_good_low': _gradient(
        {'type': 'MIN', 'color': GREEN},
        {'type': 'PERCENTILE', 'value': 50, 'color': WHITE},
        {'type': 'MAX', 'color': RED}),
    'diverging_zero': _gradient(
        {'type': 'MIN', 'color': RED},
        {'type': 'NUMBER', 'value': 0, 'color': WHITE},
        {'type': 'MAX', 'color': GREEN}),
    'finish': _gradient(
        {'type': 'MIN', 'color': almanac_render.FINISH_GREEN},
        {'type': 'PERCENTILE', 'value': 50, 'color': almanac_render.FINISH_YELLOW},
        {'type': 'MAX', 'color': almanac_render.FINISH_RED}),
    'affinity_block': _gradient(
        {'type': 'NUMBER', 'value': 0, 'color': RED},
        {'type': 'PERCENTILE', 'value': 50, 'color': WHITE},
        {'type': 'MAX', 'color': GREEN}),
}


def presentation_descriptor(hitting_specs, pitching_specs):
    """The shared presentation semantics, as data.

    Standings polarity comes from almanac_render.standings_gradient_columns
    (positional, keyed back to stat keys by walking the same order and
    checking each label against standings_header, so K / BB / H / HR / R
    stay distinct by category). Acquisition polarity comes from
    acquisition_gradient_columns the same way. Finishes, affinity and the
    slot grids carry the rules the writer applies to those blocks."""
    header = almanac_render.standings_header(hitting_specs, pitching_specs)
    gradient = almanac_render.standings_gradient_columns(hitting_specs, pitching_specs)
    walk = ([('hitting', s) for s in hitting_specs] + [('points', 'OFFENSE')]
            + [('pitching', s) for s in pitching_specs] + [('points', 'DEFENSE')]
            + [('points', 'TOTAL'), ('points', 'AGAINST')])
    if len(walk) != len(gradient):
        raise ExportError('standings_gradient_columns changed shape; descriptor walk is stale')
    points_labels = {'OFFENSE': 'Offense', 'DEFENSE': 'Defense',
                     'TOTAL': 'Total', 'AGAINST': 'Against'}
    metrics = []
    for (category, item), (col, direction) in zip(walk, gradient):
        if category == 'points':
            key, label = item, points_labels[item]
            points_per_unit = None
        else:
            key, label = item['stat_name'], almanac_render._team_week_stat_header(item)
            points_per_unit = _float(item.get('points_per_unit'))
        if header[col] != label:
            raise ExportError(
                f'descriptor walk misaligned at column {col}: header says '
                f'{header[col]!r}, walk says {label!r}')
        metrics.append({
            'metric_key': key,
            'metric_category': category,
            'display_label': label,
            'sheet_column_index': col,
            'points_per_unit': points_per_unit,
            'direction': direction,
            'polarity': POLARITY_BY_DIRECTION[direction],
            'palette': PALETTE_BY_DIRECTION[direction],
        })

    acq_header = almanac_render.ACQUISITION_HEADER
    by_offset = {offset: (key, group, label) for key, group, label, offset in ACQUISITION_CHANNELS}
    channels = []
    for col, direction in almanac_render.acquisition_gradient_columns():
        half = 'season' if col < almanac_render.ESPN_DIVIDER_COL0 else ALL_TIME
        base = 3 if half == 'season' else almanac_render.ESPN_DIVIDER_COL0 + 1
        key, group, label = by_offset[col - base]
        if acq_header[col] != label:
            raise ExportError(
                f'acquisition walk misaligned at column {col}: header says '
                f'{acq_header[col]!r}, walk says {label!r}')
        channels.append({
            'channel_key': key,
            'channel_group': group,
            'display_label': label,
            'half': half,
            'sheet_column_index': col,
            'direction': direction,
            'polarity': POLARITY_BY_DIRECTION[direction],
            'palette': PALETTE_BY_DIRECTION[direction],
        })

    return {
        'schema_version': SCHEMA_VERSION,
        'generated_from': [
            'almanac_render.standings_gradient_columns',
            'almanac_render.acquisition_gradient_columns',
            'almanac_write._color_scale_request (palette colours)',
            'almanac_render.FINISH_GREEN / FINISH_YELLOW / FINISH_RED',
            'dim_league_scoring_rule (points_per_unit signs)',
        ],
        'polarity_vocabulary': {
            '1': 'green-high (more is better)',
            '-1': 'green-low (less is better)',
            '0': 'ungraded (zero-weighted stat; no gradient)',
        },
        'palettes': PALETTES,
        'tables': {
            'detailed_standings': {
                'rule': 'per-column gradient over the table; zero-weighted stats ungraded',
                'midpoint': 'percentile 50 of the column',
                'metrics': metrics,
            },
            'points_by_lineup_slot': {
                'rule': 'every slot column green-high, per-column gradient',
                'polarity': POLARITY_HIGH,
                'palette': 'three_good_high',
            },
            'acquisition_channels': {
                'rule': 'Acquired green-high, Lost green-low, Net zero-centred; per-column',
                'channels': channels,
            },
            'season_finishes': {
                'rule': ('per year column, numeric cells only (medal cells carry a '
                         'static fill interpolated on the same scale; the champion '
                         'takes the green end); lower finish is better'),
                'polarity': POLARITY_LOW,
                'palette': 'finish',
                'champion_fill': almanac_render.CHAMPION_FILL,
                'medals': {str(k): v for k, v in almanac_render.FINISH_MEDALS.items()},
            },
            'affinities': {
                'rule': ('one scale per block (season, all-time) over every cell in the '
                         'block, not per team column; bold the row (club) maximum, ties '
                         'included; 0 / blank stays unpainted'),
                'polarity': POLARITY_HIGH,
                'palette': 'affinity_block',
                'bold_row_max': True,
                'bold_ties': True,
            },
            'rivalries': {
                'rule': ("row team's win rate against each opponent; blank diagonal and "
                         '0-0 carry no colour'),
                'polarity': POLARITY_HIGH,
                'palette': 'three_good_high',
            },
            'all_play': {
                'rule': ('expected W% / expected W-L green-high. The Sheet does not '
                         'print all-play, so this is the browser default, not a writer rule'),
                'polarity': POLARITY_HIGH,
                'palette': 'three_good_high',
            },
        },
    }


# ---------------------------------------------------------------------------
# Queries beyond what almanac_data already exposes
# ---------------------------------------------------------------------------

def _seasons():
    rows = query_for_presentation(f"""
        SELECT DISTINCT season_year FROM mart_team_season_standings
        WHERE {league_predicate()} ORDER BY season_year
    """)
    return [_int(r['season_year']) for r in rows]


def _slot_points_alltime_raw():
    """get_team_slot_points_alltime's query WITHOUT the ROUND, plus the
    denominator, so the raw total and the writer's printed value travel
    together."""
    return query_for_presentation(f"""
        WITH slots AS (
            SELECT team_id, lineup_slot,
                   CAST(SUM(CAST(slot_calculated_points AS DECIMAL(18, 6))) AS DOUBLE) AS pts,
                   MIN(sort_order) AS sort_order
            FROM mart_team_slot_production
            WHERE is_active_lineup_slot AND {league_predicate()}
            GROUP BY team_id, lineup_slot
        ), matchups AS (
            SELECT team_id, SUM(matchup_periods_played) AS mp
            FROM mart_team_season_standings
            WHERE {league_predicate()}
            GROUP BY team_id
        )
        SELECT s.team_id, s.lineup_slot, s.pts AS slot_pts, s.sort_order, m.mp
        FROM slots s
        JOIN matchups m ON m.team_id = s.team_id
        ORDER BY s.sort_order, s.lineup_slot
    """)


def _matchup_history(stat_specs):
    stat_columns = [almanac_data._fact_stat_column_name(spec['stat_name']) for spec in stat_specs]
    for column in stat_columns:
        if not re.match(r'^[a-z][a-z0-9_]*$', column):
            raise ValueError(f'Unsafe stat column name: {column!r}')
    stat_select = ',\n            '.join(stat_columns)
    return query_for_presentation(f"""
        SELECT
            season_year, matchup_period, sort_key, team_id, team_name, team_abbrev,
            opponent_id, opponent_name, result, is_playoff, is_abnormal,
            is_record_eligible, days_in_period,
            platform_points, opponent_points,
            calculated_hitting_pts, calculated_pitching_pts, calculated_points,
            opponent_calculated_hitting_pts, opponent_calculated_pitching_pts,
            opponent_calculated_points, calculated_margin,
            matchup_calculated_hitting_pts, matchup_calculated_pitching_pts,
            matchup_calculated_points,
            league_avg_hitting_points, league_avg_pitching_points, league_avg_total_points,
            {stat_select}
        FROM mart_team_matchup
        WHERE {league_predicate()}
        ORDER BY sort_key DESC, calculated_points DESC, team_name, team_id
    """)


def _all_play_rows():
    return query_for_presentation(f"""
        SELECT season_year, matchup_period, team_id, team_name, team_abbrev,
               platform_points, opponent_id, matchup_result, opponent_count,
               all_play_wins, all_play_losses, all_play_ties,
               expected_win_share, expected_loss_share, expected_tie_share
        FROM mart_team_all_play
        WHERE {league_predicate()}
        ORDER BY season_year, matchup_period, team_id
    """)


def _performance_horizon():
    rows = query_for_presentation(f"""
        SELECT season_year,
               MAX(game_date) AS last_game_date,
               MAX(scoring_period) AS max_scoring_period,
               MAX(matchup_period) AS max_matchup_period
        FROM fct_player_daily_performance
        WHERE {league_predicate()}
        GROUP BY season_year ORDER BY season_year
    """)
    return [{'season_year': _int(r['season_year']),
             'last_game_date': str(r['last_game_date']) if r['last_game_date'] else None,
             'max_scoring_period': _int(r['max_scoring_period']),
             'max_matchup_period': _int(r['max_matchup_period'])} for r in rows]


def _club_identity_as_of():
    """When the platform's team/owner records were last observed -- the RAW
    team_owners extract stamp, kept apart from performance timestamps. Best
    effort: an installation without that RAW table says so instead of
    guessing."""
    try:
        rows = db.query_snowflake(
            f"SELECT MAX(extracted_at) AS as_of FROM raw.team_owners WHERE {league_predicate()}")
        value = rows[0]['as_of'] if rows else None
        return (value.isoformat() if hasattr(value, 'isoformat') else value,
                'raw.team_owners MAX(extracted_at)')
    except Exception as exc:  # noqa: BLE001 -- reported, not hidden
        return None, f'unavailable: {type(exc).__name__}: {exc}'


def _scoring_rules():
    return query_for_presentation(f"""
        SELECT stat_name, stat_category, points_per_unit, settings_season
        FROM dim_league_scoring_rule
        WHERE {league_predicate()} AND is_scored
        ORDER BY stat_name
    """)


def _identity_keys(season_year):
    """Best-effort identity_key per platform team id for the season, from
    dim_franchise_identity. ESPN franchise ids carry the team id; anything
    that does not resolve unambiguously stays None."""
    try:
        rows = query_for_presentation(f"""
            SELECT franchise_id, canonical_franchise_id, identity_key, identity_name
            FROM dim_franchise_identity
            WHERE {league_predicate()} AND season_year = %s
        """, (season_year,))
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for r in rows:
        fid = str(r.get('franchise_id') or '')
        tail = fid.rsplit(':', 1)[-1].rsplit('-', 1)[-1]
        if tail.isdigit():
            out.setdefault(int(tail), []).append(r)
    return {tid: rs[0] for tid, rs in out.items() if len(rs) == 1}


# ---------------------------------------------------------------------------
# The export
# ---------------------------------------------------------------------------

def _git(*args):
    try:
        return subprocess.run(['git', *args], cwd=Path(__file__).resolve().parents[1],
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return None


def _fixture_stamp(duckdb_path):
    if not duckdb_path:
        return None
    stamp = Path(duckdb_path).resolve().parent / 'STAMP.json'
    if stamp.exists():
        try:
            return json.loads(stamp.read_text(encoding='utf-8'))
        except ValueError:
            return None
    return None


def _write_table(out_dir, name, rows, meta):
    """One file per table: JSON below PARQUET_THRESHOLD rows, Parquet at or
    above it. Returns the manifest entry."""
    if len(rows) >= PARQUET_THRESHOLD:
        import pyarrow as pa
        import pyarrow.parquet as pq
        path = out_dir / f'{name}.parquet'
        table = pa.Table.from_pylist([
            {k: (_num(v) if not isinstance(v, (list, dict)) else json.dumps(v)) for k, v in r.items()}
            for r in rows])
        pq.write_table(table, path, compression='zstd')
        fmt = 'parquet'
    else:
        path = out_dir / f'{name}.json'
        path.write_text(_dumps({'table': name, 'rows': rows}), encoding='utf-8')
        fmt = 'json'
    entry = {'file': path.name, 'format': fmt, 'rows': len(rows),
             'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    entry.update(meta)
    return entry


def run_export(league_key, duckdb_path=None, out_root='data/exports/shared',
               season_year=None, snapshot_ts=None):
    if duckdb_path:
        db.use_duckdb(duckdb_path)
    db.set_league(league_key)
    snapshot_ts = snapshot_ts or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    league_slug = league_key.replace('-', '_')
    out_dir = Path(out_root) / league_slug / snapshot_ts
    out_dir.mkdir(parents=True, exist_ok=True)

    if season_year is None:
        season_year, _ = almanac_data.get_latest_matchup_period()
    season_year = int(season_year)

    specs = almanac_data.get_team_week_stat_specs()
    hitting = almanac_render._team_week_specs_for_category(specs, 'hitting')
    pitching = almanac_render._team_week_specs_for_category(specs, 'pitching')
    seasons = _seasons()
    tables = {}

    # -- Detailed standings: every season the mart holds, plus all-time.
    standings_by_season = {y: almanac_data.get_team_standings(y, specs) for y in seasons}
    standings_alltime = almanac_data.get_team_standings_alltime(specs)
    current_standings = standings_by_season.get(season_year) or []
    rows = []
    for y in seasons:
        rows.extend(standings_long_rows(str(y), y, standings_by_season[y], hitting, pitching))
    rows.extend(standings_long_rows(ALL_TIME, None, standings_alltime, hitting, pitching))
    tables['detailed_standings'] = _write_table(out_dir, 'detailed_standings', rows, {
        'grain': 'one row per (scope, team_id, metric_key)',
        'scopes': [str(y) for y in seasons] + [ALL_TIME],
        'denominator_kinds': [DENOMINATOR_SCORING_DAYS],
        'per_matchup_formula': 'raw_total * standard_matchup_days / scoring_days_played, rounded to 1 (almanac_render._per_week_value); OUTS divided by 3 first',
        'order': 'rank follows the platform seed for a season (get_team_standings) and all-time win rate for all_time (get_team_standings_alltime)',
        'source': ['mart_team_season_standings', 'dim_team_season_standing'],
    })

    # -- Matchup history (wide; both lenses present).
    history = _matchup_history(specs)
    history_rows = [{k: _num(v) for k, v in r.items()} for r in history]
    stat_columns = [{
        'key': s['stat_name'],
        'column': almanac_data._fact_stat_column_name(s['stat_name']),
        'label': almanac_render._team_week_stat_header(s),
        'category': s['stat_category'],
        'points_per_unit': _float(s.get('points_per_unit')),
        'polarity': POLARITY_BY_DIRECTION[almanac_data._team_week_good_record_direction(s)],
        'display_precision': 0 if s['stat_name'] != 'OUTS' else 1,
        'raw_unit': 'outs' if s['stat_name'] == 'OUTS' else None,
    } for s in specs]
    tables['matchup_history'] = _write_table(out_dir, 'matchup_history', history_rows, {
        'grain': 'one row per (season_year, matchup_period, team_id); regular season and playoffs',
        'denominator_kinds': [DENOMINATOR_NONE],
        'scoring_lenses': {'platform_points/opponent_points': LENS_PLATFORM,
                           'calculated_*': LENS_CALCULATED},
        'points_display_precision': 1,
        'stat_columns': stat_columns,
        'source': ['mart_team_matchup'],
    })

    # -- Points by lineup slot: season grids + the all-time grid.
    rows = []
    for y in seasons:
        srows = standings_by_season[y]
        mp_by_team = {_int(t['team_id']): _int(t.get('matchup_periods_played')) for t in srows}
        labels = {_int(t['team_id']): t for t in srows}
        rows.extend(slot_long_rows(str(y), y, almanac_data.get_team_slot_points(y),
                                   mp_by_team, labels))
    alltime_raw = _slot_points_alltime_raw()
    alltime_printed = {(_int(r['team_id']), r['lineup_slot']): _float(r['slot_pts'])
                       for r in almanac_data.get_team_slot_points_alltime()}
    mp_alltime = {_int(r['team_id']): _int(r['mp']) for r in alltime_raw}
    labels_alltime = {_int(t['team_id']): t for t in standings_alltime}
    rows.extend(slot_long_rows(ALL_TIME, None, alltime_raw, mp_alltime, labels_alltime,
                               printed_by_key=alltime_printed))
    tables['points_by_lineup_slot'] = _write_table(out_dir, 'points_by_lineup_slot', rows, {
        'grain': 'one row per (scope, team_id, lineup_slot); active slots only, regular season only',
        'scopes': [str(y) for y in seasons] + [ALL_TIME],
        'denominator_kinds': [DENOMINATOR_MATCHUPS],
        'per_matchup_formula': 'raw_total / matchup_periods_played, rounded to 1 (almanac_logic.build_advanced_standings_tab_rows._per_matchup; almanac_data.get_team_slot_points_alltime)',
        'source': ['mart_team_slot_production', 'mart_team_season_standings'],
    })

    # -- Acquisition channels: the seasons the mart holds, plus all-time.
    rows = []
    for y in seasons:
        acq = almanac_data.get_team_acquisition_channels(y)
        if acq:
            rows.extend(acquisition_long_rows(str(y), y, acq))
    rows.extend(acquisition_long_rows(ALL_TIME, None,
                                      almanac_data.get_team_acquisition_channels_alltime()))
    tables['acquisition_channels'] = _write_table(out_dir, 'acquisition_channels', rows, {
        'grain': 'one row per (scope, lens, team_id, channel_key)',
        'denominator_kinds': [DENOMINATOR_NONE],
        'note': 'cumulative totals as the Sheet prints them; no per-matchup denominator exists today (MLB-302 owns the scope question, including lost production after departure and playoff inclusion)',
        'source': ['mart_team_acquisition_channels'],
    })

    # -- Season finishes and their side-table summary.
    finishes = almanac_data.get_espn_season_finishes()
    rank_arc = almanac_data.get_team_rank_arc(season_year)
    tables['season_finishes'] = _write_table(
        out_dir, 'season_finishes', finish_rows(finishes, season_year), {
            'grain': 'one row per (season_year, team_id)',
            'denominator_kinds': [DENOMINATOR_NONE],
            'note': 'finish = platform regular-season seed; final_rank = post-playoff rank; is_champion = won every playoff week',
            'source': ['mart_team_season_standings', 'dim_team_season_standing',
                       'fct_team_weekly_active_performance'],
        })
    tables['season_finish_summary'] = _write_table(
        out_dir, 'season_finish_summary',
        finish_summary_rows(finishes, current_standings, season_year, rank_arc), {
            'grain': 'one row per team_id (current standings teams), in the side table order',
            'denominator_kinds': ['games (W%)', 'seasons (Avg)'],
            'source': ['season_finishes'],
        })

    # -- Affinities.
    affinity = almanac_data.get_team_affinity_weights(season_year)
    tables['affinities'] = _write_table(
        out_dir, 'affinities', affinity_long_rows(affinity, current_standings), {
            'grain': 'one row per (scope, club_key, team_id); scope season = current season',
            'denominator_kinds': [DENOMINATOR_INVOLVEMENT],
            'share_formula': 'raw_weight / team total, rounded to 3; raw_weight = (PA + BF) * active_weight summed over active-lineup player-days, playoff weeks included',
            'source': ['fct_player_daily_performance'],
        })

    # -- Rivalries (all-time, as today).
    axes = almanac_data.get_rivalry_axes()
    pairs = almanac_data.get_rivalry_matrix()
    axes_rows = [{k: _num(v) for k, v in a.items()} for a in axes]
    pair_rows = []
    for p in pairs:
        row = {k: _num(v) for k, v in p.items()}
        row['matchup_record_printed'] = almanac_logic.format_rivalry_record(
            _int(p['matchup_wins']) or 0, _int(p['matchup_losses']) or 0, _int(p['matchup_ties']) or 0)
        row['season_record_printed'] = almanac_logic.format_rivalry_record(
            _int(p['season_wins']) or 0, _int(p['season_losses']) or 0, _int(p['season_ties']) or 0)
        pair_rows.append(row)
    pair_rows.sort(key=lambda r: (r['row_identity_key'], r['opponent_identity_key']))
    grid = almanac_logic.rivalry_matrix_grid(axes, pairs)
    tables['rivalry_axes'] = _write_table(out_dir, 'rivalry_axes', axes_rows, {
        'grain': 'one row per active team identity, in matrix order',
        'source': ['mart_franchise_rivalry_axes'],
    })
    tables['rivalries'] = _write_table(out_dir, 'rivalries', pair_rows, {
        'grain': 'one row per ordered pair of team identities with a result; diagonal absent; never-met pairs absent (render as 0-0)',
        'denominator_kinds': [DENOMINATOR_NONE],
        'ledger': grid['ledger'] if grid else None,
        'available': bool(grid and grid.get('available')),
        'explainer': grid.get('explainer') if grid else None,
        'source': ['mart_franchise_rivalry'],
    })

    # -- All-play: matchup grain + per-scope summary.
    all_play = _all_play_rows()
    all_play_rows_out = [{k: _num(v) for k, v in r.items()} for r in all_play]
    for r in all_play_rows_out:
        r['scoring_lens'] = LENS_PLATFORM
    tables['all_play_matchups'] = _write_table(out_dir, 'all_play_matchups', all_play_rows_out, {
        'grain': 'one row per (season_year, matchup_period, team_id); completed regular-season matchups only',
        'denominator_kinds': ['opponent_count'],
        'source': ['mart_team_all_play'],
    })
    tables['all_play_summary'] = _write_table(
        out_dir, 'all_play_summary', all_play_summary_rows(all_play), {
            'grain': 'one row per (scope, team_id)',
            'denominator_kinds': ['matchups'],
            'expected_formula': 'expected_wins = SUM(all_play_wins / opponent_count); expected_win_pct = expected_wins / matchups; ties separate',
            'source': ['mart_team_all_play'],
        })

    # -- Teams: the identity spine consumers join on.
    identity = _identity_keys(season_year)
    seen = {}
    for y in seasons:
        for t in standings_by_season[y]:
            tid = _int(t['team_id'])
            entry = seen.setdefault(tid, {'team_id': tid, 'seasons': []})
            entry['seasons'].append(y)
            entry.update({'team_abbrev': t.get('team_abbrev') or '',
                          'team_name': t.get('team_name') or '',
                          'owner_display': t.get('owner_display') or ''})
    for tid, entry in seen.items():
        ident = identity.get(tid)
        entry['identity_key'] = ident.get('identity_key') if ident else None
        entry['is_current'] = season_year in entry['seasons']
    team_rows = sorted(seen.values(), key=lambda t: t['team_id'])
    tables['teams'] = _write_table(out_dir, 'teams', team_rows, {
        'grain': 'one row per platform team_id seen in any season; labels from the latest season',
        'source': ['mart_team_season_standings', 'dim_franchise_identity'],
    })

    # -- Presentation descriptor.
    presentation = presentation_descriptor(hitting, pitching)
    (out_dir / 'presentation.json').write_text(_dumps(presentation), encoding='utf-8')

    # -- Manifest.
    horizon = _performance_horizon()
    cutoff = max((h['last_game_date'] for h in horizon if h['last_game_date']), default=None)
    club_as_of, club_basis = _club_identity_as_of()
    rules = _scoring_rules()
    rule_text = '\n'.join(f"{r['stat_name']}={_float(r['points_per_unit'])}" for r in rules)
    settings_seasons = sorted({_int(r['settings_season']) for r in rules if r.get('settings_season') is not None})
    manifest = {
        'schema_version': SCHEMA_VERSION,
        'ticket': 'MLB-301',
        'league_key': league_key,
        'league_slug': league_slug,
        'snapshot_ts': snapshot_ts,
        'current_season': season_year,
        'seasons': seasons,
        'performance_cutoff': cutoff,
        'performance_horizon': horizon,
        'club_identity_as_of': club_as_of,
        'club_identity_basis': club_basis,
        'scoring_rule_version': {
            'settings_seasons': settings_seasons,
            'sha256': hashlib.sha256(rule_text.encode('utf-8')).hexdigest(),
            'scored_stats': len(rules),
        },
        'source': {
            'commit': _git('rev-parse', 'HEAD'),
            'uncommitted_tracked_changes': len([
                line for line in (_git('status', '--porcelain') or '').splitlines()
                if not line.startswith('??')]),
            'backend': db.backend(),
            'database': str(duckdb_path) if duckdb_path else None,
            'input_fixture': _fixture_stamp(duckdb_path),
        },
        'denominator_vocabulary': {
            DENOMINATOR_SCORING_DAYS: 'gameplay days played (per-matchup = total * standard_matchup_days / scoring_days_played)',
            DENOMINATOR_MATCHUPS: 'regular-season matchup periods played (per-matchup = total / matchup_periods_played)',
            DENOMINATOR_INVOLVEMENT: "the team's total PA+BF involvement weight (share = weight / total)",
            DENOMINATOR_NONE: 'no denominator; the value is a cumulative total',
        },
        'scoring_lens_vocabulary': {LENS_PLATFORM: "the platform's own points",
                                    LENS_CALCULATED: 'points recomputed under the current scoring rules'},
        'excluded': {'wasted': 'contract-bound definition with an open garnish question; not exported (MLB-301)'},
        'tables': tables,
        'presentation': {'file': 'presentation.json',
                         'sha256': hashlib.sha256((out_dir / 'presentation.json').read_bytes()).hexdigest()},
    }
    (out_dir / 'manifest.json').write_text(_dumps(manifest), encoding='utf-8')
    (out_dir.parent / 'LATEST').write_text(snapshot_ts + '\n', encoding='utf-8')
    return manifest, out_dir


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--league', required=True, help='league registry key, e.g. espn-main')
    parser.add_argument('--duckdb', default=None, metavar='PATH',
                        help='read from this DuckDB file (defaults to DBT_DUCKDB_PATH, then the profile path)')
    parser.add_argument('--snowflake', action='store_true',
                        help='read from Snowflake instead of DuckDB (the pre-flip weekly lane)')
    parser.add_argument('--season-year', type=int, default=None,
                        help='the current season; defaults to the latest loaded matchup period')
    parser.add_argument('--out-root', default='data/exports/shared')
    parser.add_argument('--snapshot-ts', default=None,
                        help='override the snapshot directory name (for reproducibility checks)')
    args = parser.parse_args(argv)
    duckdb_path = None
    if not args.snowflake:
        duckdb_path = args.duckdb or os.getenv('DBT_DUCKDB_PATH') or db.DEFAULT_DUCKDB_PATH
    manifest, out_dir = run_export(args.league, duckdb_path=duckdb_path,
                                   out_root=args.out_root, season_year=args.season_year,
                                   snapshot_ts=args.snapshot_ts)
    print(f'[export] {out_dir}')
    for name, entry in manifest['tables'].items():
        print(f"[export]   {name:26s} {entry['rows']:6d} rows  {entry['file']}")
    print(f"[export] performance_cutoff={manifest['performance_cutoff']} "
          f"club_identity_as_of={manifest['club_identity_as_of']} "
          f"commit={manifest['source']['commit']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
