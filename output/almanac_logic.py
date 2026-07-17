"""output/almanac_logic.py

Tier 2c.3 (v1.1.1): selection rules + tab-row orchestration for the
league almanac.

This module owns the consumer-side decisions that aren't pure data and
aren't pure rendering: pick the all-league roster from candidate rows,
group + sort record specs into sectioned shapes, decide which display
helper applies to which roster row, etc.

Dependencies (downward only): almanac_data, almanac_render. Logic
orchestrates render -- the build_* functions construct full tab-row
lists by calling individual format_* helpers in almanac_render.
"""

import math
import os
import statistics
from collections import defaultdict

import almanac_data
import almanac_render
import records
import stat_catalog
from almanac_data import (
    HITTING_RECORD_LABELS,
    HITTING_RECORD_ORDER,
    PITCHING_STAT_ORDER,
    RATE_RECORD_SPECS,
    _fact_stat_column_name,
    _lineup_slot_stat_name,
    _team_record_label,
    get_lineup_slot_record_specs,
    get_scored_record_specs,
    slot_label,
)
from almanac_render import (
    ACQUISITION_HEADER,
    ADVANCED_STANDINGS_TAB,
    HOME_ALLTIME_HEADER,
    HOME_DEVIATION_LABEL,
    HOME_HEADER,
    HOME_TAB,
    DRAFT_TAB,
    DRAFT_VALUE_HEADER,
    RECORDS_HEADER,
    RECORDS_MATRIX_DETAIL_HEADER,
    RECORDS_MATRIX_WIDTH,
    RECORDS_TAB,
    TEAM_HISTORY_DETAIL_HEADER,
    TEAM_HISTORY_ALLTIME_DETAIL_HEADER,
    TEAM_HISTORY_BEST_SEASON_BANNER,
    TEAM_HISTORY_GLOSS_LINES,
    TEAM_HISTORY_HITTER_HEADER,
    TEAM_HISTORY_HITTER_STATS,
    TEAM_HISTORY_MIXED_HEADER,
    TEAM_HISTORY_MIXED_STATS,
    TEAM_HISTORY_OTHER_CAP,
    TEAM_HISTORY_OTHER_MORE,
    TEAM_HISTORY_OTHER_WORST,
    TEAM_HISTORY_PITCHER_HEADER,
    TEAM_HISTORY_PITCHER_STATS,
    TEAM_ROSTER_HEADER,
    TEAM_ROSTER_MATRIX_WIDTH,
    TEAM_WEEKS_BASE_HEADER,
    TEAM_WEEKS_SCORE_HEADER,
    TEAM_WEEKS_TAB,
    boxscore_formula,
    format_all_league_team_row,
    format_all_league_team_row_with_deviation,
    format_all_league_thin_row,
    format_acquisition_row,
    format_draft_board_cell,
    format_draft_value_row,
    format_standings_row,
    home_nav_link,
    standings_header,
    format_record_matrix_row,
    format_record_row,
    format_team_history_matrix_row,
    format_team_roster_row,
    format_team_week_row,
    team_tab_title,
    _boxscore_url,
    _collapsed_holder,
    _compact_inactive_slot,
    _empty_team_history_display_row,
    _format_record_side,
    _format_record_value,
    _format_sheet_date,
    _format_team_week_stat,
    _inactive_position_display,
    _is_active_display_slot,
    _is_hitter_display_slot,
    _is_pitcher_display_slot,
    _one_decimal,
    _period_boxscore_formula,
    _record_details,
    _record_label,
    _records_matrix_scope_header,
    _round_half_up,
    _safe_sheet_title,
    _slot_sort_key,
    _team_history_display_row,
    _team_history_scope_header,
    _team_history_section_header_row,
    _team_history_side_cells,
    _team_history_stat_line,
    _team_week_specs_for_category,
    _team_week_stat_header,
    _team_week_stat_headers,
)
from formatters import fmt_ip, format_top_scorer_stats_line


SCORE_RECORD_SPECS = [
    {
        'section': 'Score Records',
        'label': 'Best Team Total Points',
        'grain': 'team',
        'stat_name': 'CALCULATED_POINTS',
        'direction': 'most',
    },
    {
        'section': 'Score Records',
        'label': 'Best Team Hitting Points',
        'grain': 'team',
        'stat_name': 'CALCULATED_HITTING_PTS',
        'direction': 'most',
    },
    {
        'section': 'Score Records',
        'label': 'Best Team Pitching Points',
        'grain': 'team',
        'stat_name': 'CALCULATED_PITCHING_PTS',
        'direction': 'most',
    },
    {
        'section': 'Score Records',
        'label': 'Best Player Total Points',
        'grain': 'player',
        'stat_name': 'CALCULATED_POINTS',
        'direction': 'most',
    },
    {
        'section': 'Score Records',
        'label': 'Best Player Hitting Points',
        'grain': 'player',
        'stat_name': 'CALCULATED_HITTING_PTS',
        'direction': 'most',
    },
    {
        'section': 'Score Records',
        'label': 'Best Player Pitching Points',
        'grain': 'player',
        'stat_name': 'CALCULATED_PITCHING_PTS',
        'direction': 'most',
    },
    {
        'section': 'Score Records',
        'spacer': True,
    },
    {
        'section': 'Score Records',
        'label': 'Worst Team Total Points',
        'grain': 'team',
        'stat_name': 'CALCULATED_POINTS',
        'direction': 'fewest',
    },
    {
        'section': 'Score Records',
        'label': 'Worst Team Hitting Points',
        'grain': 'team',
        'stat_name': 'CALCULATED_HITTING_PTS',
        'direction': 'fewest',
    },
    {
        'section': 'Score Records',
        'label': 'Worst Team Pitching Points',
        'grain': 'team',
        'stat_name': 'CALCULATED_PITCHING_PTS',
        'direction': 'fewest',
    },
    {
        'section': 'Score Records',
        'label': 'Worst Player Total Points',
        'grain': 'player',
        'stat_name': 'CALCULATED_POINTS',
        'direction': 'fewest',
    },
    {
        'section': 'Score Records',
        'label': 'Worst Player Hitting Points',
        'grain': 'player',
        'stat_name': 'CALCULATED_HITTING_PTS',
        'direction': 'fewest',
    },
    {
        'section': 'Score Records',
        'label': 'Worst Player Pitching Points',
        'grain': 'player',
        'stat_name': 'CALCULATED_PITCHING_PTS',
        'direction': 'fewest',
    },
]


def _attach_almanac_contributors(record_rows):
    """Attach contributor details after tie-collapse trims visible rows."""
    real_rows = [r for r in record_rows if not r.get('is_collapsed')]
    team_tuples = [
        (r['season_year'], r['matchup_period'], r['team_id'], r['stat_name'])
        for r in real_rows
        if r['entity_grain'] == 'team' and r['team_id'] is not None
    ]
    player_tuples = [
        (r['season_year'], r['matchup_period'], r['player_id'])
        for r in real_rows
        if r['entity_grain'] == 'player' and r['player_id'] is not None
    ]
    positive_player_tuples = [
        (r['season_year'], r['matchup_period'], r['player_id'])
        for r in real_rows
        if (
            r['entity_grain'] == 'player'
            and r['player_id'] is not None
            and r.get('record_direction') != 'fewest'
        )
    ]
    balanced_player_tuples = [
        (r['season_year'], r['matchup_period'], r['player_id'])
        for r in real_rows
        if (
            r['entity_grain'] == 'player'
            and r['player_id'] is not None
            and r.get('record_direction') == 'fewest'
        )
    ]

    team_contribs = records.get_team_contributors_bulk(team_tuples) if team_tuples else {}
    player_contribs = {}
    if positive_player_tuples:
        player_contribs.update(records.get_player_contributors_bulk(positive_player_tuples))
    if balanced_player_tuples:
        player_contribs.update(records.get_player_contributors_bulk(
            balanced_player_tuples,
            positives_only=False,
        ))

    for row in record_rows:
        if row.get('is_collapsed'):
            row['contributors'] = []
        elif row['entity_grain'] == 'team':
            key = (
                row['season_year'],
                row['matchup_period'],
                row['team_id'],
                row['stat_name'],
            )
            row['contributors'] = team_contribs.get(key, [])
        else:
            key = (row['season_year'], row['matchup_period'], row['player_id'])
            row['contributors'] = player_contribs.get(key, [])


def select_all_league_team(candidates, slot_caps):
    """Pick top active performers by actual lineup slot.

    The comprehensive weekly fact preserves `lineup_slot`, so this uses
    the same roster-slot lens ESPN users recognize. If a player appears
    in multiple active slots during a week, keep only their highest-
    scoring slot row so the all-league roster never selects the same
    player twice.
    """
    best_by_player = {}
    for row in candidates:
        player_id = row.get('player_id')
        if player_id is None:
            continue
        current = best_by_player.get(player_id)
        if current is None or _candidate_sort_key(row) < _candidate_sort_key(current):
            best_by_player[player_id] = row

    by_slot = defaultdict(list)
    for row in best_by_player.values():
        slot = row.get('lineup_slot')
        if slot in slot_caps:
            by_slot[slot].append(row)

    selected = []
    for slot in sorted(slot_caps, key=_slot_sort_key):
        capacity = slot_caps[slot]
        rows = sorted(by_slot.get(slot, []), key=_candidate_sort_key)
        for slot_rank, row in enumerate(rows[:capacity], 1):
            out = dict(row)
            out['slot_rank'] = slot_rank
            out['slots_to_fill'] = capacity
            out['slot_label'] = slot_label(slot, slot_rank, capacity)
            selected.append(out)

    return selected


# -------------------------------------------------------------------------
# v1.1.1: generalized optimal-team selection (Approach 1 per BRAINTHOUGHTS
# [ARCH]). Pairs with get_optimal_team_candidates in almanac_data.py.
#
# Gap-based heuristic: at each step, fill the slot type where the gap
# between its top eligible candidate and its second-best is largest --
# this is "fill the slot where picking the 2nd-best player hurts most."
# Better than pure greedy (fill-each-slot-by-rank) when slots compete
# for the same multi-position-eligible player.
#
# Disjoint-stat-categories rule: a player can be picked at most twice,
# and only if the two slot categories are different (hitting vs
# pitching). This handles two-way players (Shohei) correctly --
# fct_player_position_pts attributes (slot-filtered) pitching points to
# pitching positions and hitting points to hitting positions, so the
# pitcher-row and hitter-row are day-disjoint and picking Shohei at both
# SP and DH sums his slot-credited production without double-counting.
# (A genuine two-way day, slotted in one, zeroes the off-slot discipline
# upstream and does not recover it here -- by design; see the
# fct_player_position_pts model note.) Same-category double
# picks (e.g., 1B and DH for a hitter who's eligible at both) WOULD
# double-count and are blocked.
# -------------------------------------------------------------------------


_PITCHING_SLOTS = frozenset({'SP', 'RP', 'P'})


def _slot_category(slot):
    """'pitching' for SP/RP/P, 'hitting' for everything else.

    Mirrors the CASE expression in fct_player_position_pts that drives
    position_calculated_pts -- keep these two in sync.
    """
    return 'pitching' if slot in _PITCHING_SLOTS else 'hitting'


def get_optimal_team_selections(candidates, slot_caps):
    """Pick an optimal lineup from a candidate pool, given roster shape.

    Args:
      candidates: list of dicts from get_optimal_team_candidates, each
        carrying at least {player_id, position, position_pts} plus any
        display fields (player_name, display_name, pro_team). Must be
        sorted by position then position_pts DESC (the SQL ORDER BY
        in get_optimal_team_candidates guarantees this).
      slot_caps: dict {slot_code: starter_count}, e.g. from
        get_slot_capacities. Slot codes must match the position
        codes in candidates (a candidate with position='SP' can fill
        a slot keyed 'SP').

    Returns: list of dicts in canonical baseball-card SLOT_ORDER
    (C, 1B, 2B, 3B, SS, IF, LF, CF, RF, OF, DH, UTIL, SP*, RP*, P*),
    one per slot instance. Multi-instance slots stay in slot_rank
    ascending (SP 1 before SP 2). The gap-based selection still drives
    which player fills which slot; the final sort is purely
    presentation -- every consumer (Home All-League Team tab,
    per-team-tab Starters section, future consumers) wants display
    order, so the selector owns the sort to avoid each consumer
    duplicating it.

    Each row has the selected candidate's fields (copied through) PLUS:
      - lineup_slot:    the slot type (e.g., 'SP', 'OF')
      - slot_rank:      1, 2, ... for multi-instance slots (e.g., SP 1)
      - slots_to_fill:  total instances of this slot type
      - slot_label:     "SP" or "SP 1" depending on whether rank-
                        distinguished (matches slot_label helper)
      - platform_points: copy of position_pts under the field name
                         the renderer expects

    If a slot cannot be filled (no eligible candidates left), the
    output row has all `None` value fields plus the slot metadata.
    """
    from collections import defaultdict

    def _identity(c):
        # Used-tracking identity: player_key where present (the MLB-72
        # cross-league grain -- CBS ui-only synthetics carry player_id
        # NULL and would otherwise all collapse into one "player"),
        # else player_id (pre-player_key callers and cached shapes).
        return c.get('player_key') or c['player_id']

    # Group candidates by position. Input is already sorted by points
    # DESC within each position from the SQL ORDER BY.
    by_position = defaultdict(list)
    for c in candidates:
        by_position[c['position']].append(c)

    # Expand slot_caps into a list of pending slot instances.
    pending = []
    for slot, count in slot_caps.items():
        pending.extend([slot] * int(count))

    used = defaultdict(set)  # player_id -> set of categories already filled
    rank_counters = defaultdict(int)  # slot -> last assigned rank
    lineup = []

    while pending:
        # For each distinct slot still pending, find the (top, second)
        # eligible candidates and compute the gap. Eligible = position
        # matches AND the player hasn't already been used in this
        # slot's category.
        #
        # Iterate slots in slot_caps order (deterministic; matches the
        # league's roster shape declaration order via dim_roster_slot_
        # counts.sort_order) so tied-gap scenarios break ties stably.
        # Python set iteration order is NOT guaranteed stable across
        # calls; using a sorted-by-insertion list keeps byte-diff
        # deterministic.
        best_gap = None
        best_slot = None
        best_player = None
        distinct_pending = [s for s in slot_caps if s in pending]
        for slot in distinct_pending:
            cat = _slot_category(slot)
            eligible = [
                c for c in by_position.get(slot, [])
                if cat not in used[_identity(c)]
            ]
            if not eligible:
                continue
            top = eligible[0]
            second_pts = eligible[1]['position_pts'] if len(eligible) > 1 else 0
            gap = top['position_pts'] - second_pts
            if best_gap is None or gap > best_gap:
                best_gap = gap
                best_slot = slot
                best_player = top

        if best_slot is None:
            # No remaining slot can be filled. Emit empty rows for the
            # leftovers so the consumer sees the full lineup shape.
            for slot in pending:
                rank_counters[slot] += 1
                cap = int(slot_caps[slot])
                lineup.append({
                    'lineup_slot': slot,
                    'slot_rank': rank_counters[slot],
                    'slots_to_fill': cap,
                    'slot_label': slot_label(slot, rank_counters[slot], cap),
                    'player_id': None,
                    'player_name': None,
                    'display_name': None,
                    'pro_team': None,
                    'position_pts': None,
                    'platform_points': None,
                })
            break

        # Assign best_player to one instance of best_slot.
        rank_counters[best_slot] += 1
        cap = int(slot_caps[best_slot])
        out = dict(best_player)
        out['lineup_slot'] = best_slot
        out['slot_rank'] = rank_counters[best_slot]
        out['slots_to_fill'] = cap
        out['slot_label'] = slot_label(best_slot, rank_counters[best_slot], cap)
        # Renderer expects `platform_points`; surface position_pts under
        # both names so format_all_league_team_row works unchanged.
        #
        # Naming caveat: the value here is now calculated-points-sourced
        # (fct_player_position_pts switched from platform_*_pts to
        # total_*_stat_pts in v1.1.1). The field name is preserved to
        # avoid churning format_all_league_team_row + every cached
        # selected_rows shape; a rename to `optimal_team_pts` is
        # tracked in BRAINTHOUGHTS as a follow-up cleanup.
        out['platform_points'] = best_player.get('position_pts')
        lineup.append(out)

        used[_identity(best_player)].add(_slot_category(best_slot))
        pending.remove(best_slot)  # removes one instance

    # Final sort: canonical baseball-card order. The selection above
    # built `lineup` in gap-fill order (correct for selection, wrong
    # for display -- hitters and pitchers interleave). Sorting here
    # means every consumer of get_optimal_team_selections sees
    # display-ordered rows; no caller has to remember to re-sort.
    lineup.sort(key=lambda r: (
        _slot_sort_key(r.get('lineup_slot') or ''),
        int(r.get('slot_rank') or 1),
    ))
    return lineup


# v1.2 (#23): Home is a two-band dashboard. Left band (cols A-D) is a
# navigation hub + points glossary + the all-time All-League Team; right
# band (cols F+) is the All-League Team of the Week and Season-to-Date,
# each carrying two Total-Pts deviation columns. A blank spacer column (E)
# separates the bands.
_HOME_LEFT_WIDTH = 4

_HOME_SCORING_CALLOUT = (
    'All points use current-season scoring across every timeframe -- '
    "tell us if you'd rather see them as awarded at the time."
)

_HOME_GLOSSARY = [
    ('Total Points', 'All points a player produced -- active + inactive.'),
    ('Active Points', 'Produced while in an active lineup slot (not bench or IL).'),
    ('Inactive Points', 'Produced while on a bench or IL slot.'),
    (
        'Wasted Points',
        'Inactive points + the size of any negative active-game totals '
        '(points left on the bench, plus points actively lost).',
    ),
]


def build_home_tab_rows(weekly_rows, season_rows, weekly_all_rows,
                        season_all_rows, all_time_rows, season_year,
                        matchup_period, team_titles=None, league_id=None,
                        nav_targets=None):
    """Build the Home tab as a two-band dashboard (#23).

    LEFT band (cols A-D): navigation table + per-team link grid + points
    glossary + the all-time All-League Team. RIGHT band (cols F+):
    All-League Team of the Week and Season-to-Date, each with two
    Total-Pts deviation columns. The bands are built independently and
    zipped row-for-row (the shorter padded) so the spacer + right columns
    stay aligned.

    Data params arrive pre-fetched (see almanac_data.get_home_tab_data) so
    the preview path and the live-write path can't drift on what they
    query.

    nav_targets: optional {tab_title: gid} map. Provided on the live write
    -> nav cells become in-sheet =HYPERLINK formulas; None on the TSV
    preview -> plain tab-name text. Draft Recap is always plain (its tab
    isn't built yet).
    """
    banner = [
        ['Fantasy Beat Reporter Almanac'],
        [_HOME_SCORING_CALLOUT],
        [],
    ]
    right_rows, season_label_idx = _home_right_rows(
        weekly_rows, weekly_all_rows, season_rows, season_all_rows,
        season_year, matchup_period, league_id,
    )
    left_rows = _home_left_rows(
        all_time_rows, team_titles, nav_targets,
        align_alltime_to=season_label_idx,
    )
    right_width = len(HOME_HEADER) + 2
    return [
        *banner,
        *_merge_home_bands(left_rows, right_rows, _HOME_LEFT_WIDTH, right_width),
    ]


def _home_left_rows(all_time_rows, team_titles, nav_targets, align_alltime_to=None):
    """Left band (cols A-D): nav hub + per-team grid + glossary + all-time
    All-League Team. Rows are padded to _HOME_LEFT_WIDTH by the merge.

    align_alltime_to: the right band's Season-to-Date label index. The
    all-time block pads up to it and mirrors the season block's
    label / blank / header / rows shape, so the two lineups sit inline
    (#23 QA)."""
    rows = [['Navigate']]
    rows.append([
        home_nav_link('Records', RECORDS_TAB, nav_targets),
        'All-time & current-season record book.',
    ])
    rows.append([
        home_nav_link('Matchup History', TEAM_WEEKS_TAB, nav_targets),
        'Team-by-team week scoring archive.',
    ])
    rows.append([
        home_nav_link('Advanced Standings', ADVANCED_STANDINGS_TAB, nav_targets),
        'Standings + points by lineup slot.',
    ])
    rows.append(['Team Pages', 'Historic production by team.'])
    rows.extend(_home_team_grid_rows(team_titles, nav_targets))
    # Draft Recap is built, so it links live (gid resolved in the two-pass
    # write). The old "Slot Scoring -- coming soon" placeholder is now fulfilled
    # by the Advanced Standings tab linked above.
    rows.append([home_nav_link('Draft Recap', DRAFT_TAB, nav_targets),
                 'Draft board + best-value / bust picks.'])

    rows.append([])
    rows.append(['Points Glossary'])
    rows.extend([term, definition] for term, definition in _HOME_GLOSSARY)

    # Align the all-time block with the right-band Season-to-Date block so
    # the lineups sit inline: pad up to the season label's row (the pad's
    # last blank is the spacer above the title), then mirror its
    # label / header / rows shape -- title directly on header (Kyle 2026-07-15).
    if align_alltime_to is not None:
        while len(rows) < align_alltime_to:
            rows.append([])
    rows.append(['All-League Team: All-Time'])
    rows.append(list(HOME_ALLTIME_HEADER))
    rows.extend(format_all_league_thin_row(row) for row in all_time_rows)
    return rows


def _home_team_grid_rows(team_titles, nav_targets, per_row=2):
    """Per-team link grid: team tab titles laid out `per_row` across the
    left band, indented one column (col A blank) so the grid reads as a
    sub-list under the Team Pages nav heading. Each cell links to that
    team's tab (plain text in preview)."""
    titles = list(team_titles or [])
    grid = []
    for start in range(0, len(titles), per_row):
        chunk = titles[start:start + per_row]
        grid.append(['', *(home_nav_link(title, title, nav_targets) for title in chunk)])
    return grid


def _home_right_rows(weekly_rows, weekly_all_rows, season_rows,
                     season_all_rows, season_year, matchup_period, league_id):
    """Right band (cols F+): week + season All-League Teams, each row
    carrying the two Total-Pts deviation columns.

    Returns (rows, season_label_index). The index is the row where the
    Season-to-Date label sits; the left band aligns its all-time block to
    it so the two lineups sit inline (#23 QA)."""
    header = [*HOME_HEADER, HOME_DEVIATION_LABEL, '']
    week_dev = _deviation_by_slot(weekly_rows, weekly_all_rows)
    season_dev = _deviation_by_slot(season_rows, season_all_rows)

    # Each board's title sits directly on its header row. The FIRST board is
    # flush with the top of the band (row 4, aligned with the left band's
    # first row); later boards get a spacer blank above the title (Kyle
    # 2026-07-16). season_label_idx points at the title row so the left
    # band's All-Time block still aligns to it.
    rows = [
        [f'All-League Team of the Week: {season_year} Week {matchup_period}'],
        header,
    ]
    rows.extend(
        format_all_league_team_row_with_deviation(
            row, week_dev.get(row.get('slot_label')), league_id=league_id,
        )
        for row in weekly_rows
    )
    rows.append([])          # separator between the two boards
    rows.append([])          # spacer above the Season-to-Date title
    season_label_idx = len(rows)
    rows.append([f'All-League Team Season-to-Date: {season_year}'])
    rows.append(header)
    rows.extend(
        format_all_league_team_row_with_deviation(
            row, season_dev.get(row.get('slot_label')), league_id=league_id,
        )
        for row in season_rows
    )
    return rows, season_label_idx


def _deviation_by_slot(active_rows, all_rows):
    """Map slot_label -> the points_type='all' pick when it is a DIFFERENT
    player than the active pick at that slot (#23). Same player (just sat a
    game) -> no entry: the locked rule is player-only deltas, not
    points-only deltas.

    Behavior note (locked v1.2): the all-lens lineup is a full, independent
    optimal lineup -- gap-based selection re-optimized globally over
    active+inactive+FA -- and the deviation is read position-by-position
    against it. So a player can appear as BOTH an active pick at one slot
    AND another slot's total-pts deviation (e.g. Yordan starts at DH but is
    also the best LF by total points, because the all-lens lineup shuffles
    him to LF and someone else to DH). The column means "best at this slot
    incl. bench & FA," not "untapped value not already started elsewhere."
    """
    def _identity(row):
        # player_key where present (MLB-72: CBS ui-only synthetics carry
        # player_id NULL), else player_id -- same fallback as the selector.
        return row.get('player_key') or row.get('player_id')

    all_by_slot = {row.get('slot_label'): row for row in all_rows}
    deviations = {}
    for row in active_rows:
        label = row.get('slot_label')
        alt = all_by_slot.get(label)
        if (
            alt
            and _identity(alt) is not None
            and _identity(alt) != _identity(row)
        ):
            deviations[label] = alt
    return deviations


def _merge_home_bands(left_rows, right_rows, left_width, right_width):
    """Zip the two bands row-for-row into the full Home matrix. Each output
    row is [left band (left_width) | spacer | right band (right_width)],
    padding the shorter band with blank cells so columns stay aligned."""
    merged = []
    for index in range(max(len(left_rows), len(right_rows))):
        left = list(left_rows[index]) if index < len(left_rows) else []
        right = list(right_rows[index]) if index < len(right_rows) else []
        left = (left + [''] * left_width)[:left_width]
        right = (right + [''] * right_width)[:right_width]
        merged.append([*left, '', *right])
    return merged


def build_draft_tab_rows(board_rows, season_year, league_id=None):
    """Build the Draft Recap tab: side-by-side Best Value / Biggest Bust
    leaderboards above a keeper-sorted round x team draft board with
    per-row Min / Median / Max season points (draft tab).

    board_rows come from almanac_data.get_draft_board (one row per pick,
    value_delta attached). league_id is unused (no boxscore links here);
    accepted for signature symmetry with the other builders.
    """
    del league_id
    rows = [
        [f'Draft Recap: {season_year}'],
        ['Value = Overall pick minus Total Points rank. (K) = keeper.'],
        [],
    ]

    # Side-by-side leaderboards: Best Value (cols A-E) | spacer F | Biggest
    # Busts (cols G-K). Keepers' draft cost is re-ranked by the keeper-sort
    # so a team's 5th-best keeper counts as a late keeper, not a round-1 pick.
    ranked = [r for r in _draft_with_effective_picks(board_rows)
              if r.get('value_delta') is not None]
    best_value = sorted(ranked, key=lambda r: (-r['value_delta'], r['overall_pick']))[:10]
    biggest_bust = sorted(ranked, key=lambda r: (r['value_delta'], r['overall_pick']))[:10]

    rows.append(['Best Value Picks', '', '', '', '', '', 'Biggest Busts'])
    rows.append([*DRAFT_VALUE_HEADER, '', *DRAFT_VALUE_HEADER])
    blank = [''] * len(DRAFT_VALUE_HEADER)
    for index in range(max(len(best_value), len(biggest_bust))):
        left = format_draft_value_row(best_value[index]) if index < len(best_value) else list(blank)
        right = format_draft_value_row(biggest_bust[index]) if index < len(biggest_bust) else list(blank)
        rows.append([*left, '', *right])

    rows.append([])
    rows.append([])
    rows.append([f'Draft Board - {season_year}'])
    rows.extend(_draft_board_grid(board_rows))
    return rows


def _draft_sorted_columns(board_rows):
    """Return (team_order, team_abbrev, sorted_cols).

    Team columns are ordered by each team's round-1 pick -- the draft order,
    so the leftmost column is the overall #1 pick and the rightmost is the
    last. Within each column the picks are re-sorted: keepers first, ordered
    by season points (keepers are designated all at once, so their assigned
    round is arbitrary -- production is the meaningful order), then the
    drafted picks in draft order (overall_pick)."""
    by_team = defaultdict(list)
    round1_pick = {}
    team_abbrev = {}
    for r in board_rows:
        tid = r.get('team_id')
        if tid is None:
            continue
        by_team[tid].append(r)
        team_abbrev.setdefault(tid, r.get('team_abbrev') or str(tid))
        if r.get('round_num') == 1:
            round1_pick[tid] = r.get('round_pick')

    team_order = sorted(by_team, key=lambda tid: (round1_pick.get(tid) or 999, tid))
    sorted_cols = {}
    for tid, picks in by_team.items():
        keepers = sorted(
            (p for p in picks if p.get('keeper')),
            key=lambda p: (-(p.get('season_points') or 0), p.get('overall_pick')),
        )
        drafted = sorted(
            (p for p in picks if not p.get('keeper')),
            key=lambda p: p.get('overall_pick'),
        )
        sorted_cols[tid] = keepers + drafted
    return team_order, team_abbrev, sorted_cols


def _draft_with_effective_picks(board_rows):
    """Re-rank keepers' draft cost by the keeper-sort so leaderboard value is
    fair. A keeper's ESPN round is arbitrary (keepers are designated all at
    once), so within each team the keepers are sorted by season points and
    handed the team's keeper-slot pick numbers in order -- the best keeper
    gets the earliest keeper slot, the worst the latest. value_delta is then
    effective_overall_pick - points_rank. Drafted picks pass through
    unchanged (their pick + value are already meaningful)."""
    by_team = defaultdict(list)
    for r in board_rows:
        by_team[r.get('team_id')].append(r)

    augmented = []
    for picks in by_team.values():
        keepers = [p for p in picks if p.get('keeper')]
        # The (overall, round, round_pick) slots ESPN assigned this team's
        # keepers, earliest first.
        slots = sorted(
            (p.get('overall_pick'), p.get('round_num'), p.get('round_pick'))
            for p in keepers
        )
        keepers_by_points = sorted(
            keepers,
            key=lambda p: (-(p.get('season_points') or 0), p.get('overall_pick')),
        )
        for keeper, slot in zip(keepers_by_points, slots):
            effective = dict(keeper)
            effective['overall_pick'], effective['round_num'], effective['round_pick'] = slot
            effective['value_delta'] = slot[0] - (keeper.get('points_rank') or 0)
            augmented.append(effective)
        augmented.extend(p for p in picks if not p.get('keeper'))
    return augmented


def _draft_board_grid(board_rows):
    """Keeper-sorted round x team board with per-row Min / Median / Max of
    season points across the teams. Header row then one row per board slot."""
    team_order, team_abbrev, sorted_cols = _draft_sorted_columns(board_rows)
    max_slots = max((len(col) for col in sorted_cols.values()), default=0)

    grid = [['Rd', 'Min', 'Median', 'Max', *[team_abbrev[tid] for tid in team_order]]]
    for slot in range(max_slots):
        row_picks = [
            sorted_cols[tid][slot] if slot < len(sorted_cols[tid]) else None
            for tid in team_order
        ]
        pts = [float(p.get('season_points') or 0) for p in row_picks if p is not None]
        if pts:
            summary = [_one_decimal(min(pts)),
                       _one_decimal(statistics.median(pts)),
                       _one_decimal(max(pts))]
        else:
            summary = ['', '', '']
        grid.append([
            slot + 1, *summary,
            *[format_draft_board_cell(pick) for pick in row_picks],
        ])
    return grid


def build_draft_board_color_grid(board_rows):
    """Per-board-cell season points, aligned to _draft_board_grid's layout
    (same keeper-sort + team order). One list per board slot, each holding
    the teams' season points (None for an empty slot). The write layer maps
    these to the board's red->white->green color scale."""
    team_order, _, sorted_cols = _draft_sorted_columns(board_rows)
    max_slots = max((len(col) for col in sorted_cols.values()), default=0)
    return [
        [
            (float(sorted_cols[tid][slot].get('season_points') or 0)
             if slot < len(sorted_cols[tid]) else None)
            for tid in team_order
        ]
        for slot in range(max_slots)
    ]


def build_advanced_standings_tab_rows(standings_rows, slot_rows, stat_specs,
                                      season_year, acquisition_rows=None):
    """Build the Advanced Standings tab: the per-stat weekly-average
    standings (Table A) stacked over a team x active-lineup-slot points
    grid (Table B), then two acquisition-channel blocks (Active and Rostered
    lenses) when acquisition_rows is supplied (MLB-17).

    standings_rows come from almanac_data.get_team_standings (already
    ordered as a standings, with the per-week denominators on every row);
    slot_rows from almanac_data.get_team_slot_points (active slots only,
    pre-ordered by the roster dim's sort_order); stat_specs from
    get_team_week_stat_specs -- the same scored-stat set and order the
    Matchup History tab uses. The write layer paints the column gradients --
    this builder only lays out the cells. Both tables share the standings
    team order.
    """
    hitting_specs = _team_week_specs_for_category(stat_specs, 'hitting')
    pitching_specs = _team_week_specs_for_category(stat_specs, 'pitching')

    # The derived standard matchup length (modal regular-week gameplay
    # days), read off the standings rows for the subtitle; 7 only as the
    # fallback for an empty season.
    std_days = next(
        (int(r['standard_matchup_days']) for r in standings_rows
         if r.get('standard_matchup_days')),
        7,
    )
    rows = [
        [f'Advanced Standings: {season_year}'],
        [f'Regular season to date, shown as averages per {std_days} days of '
         'gameplay (one standard matchup; abnormal-length weeks normalize by '
         'their actual days with games). Offense / Defense / Total and '
         'Against are calculated points (Against = points conceded); W-L is '
         'the official ESPN record.'],
        [],
        ['Standings (Weekly Averages)'],
        standings_header(hitting_specs, pitching_specs),
    ]
    for rank, team in enumerate(standings_rows, start=1):
        rows.append(
            format_standings_row(rank, team, hitting_specs, pitching_specs)
        )

    # Slot columns in dim_roster_slot_counts.sort_order, carried on every
    # slot row -- no hardcoded slot list.
    slot_order = {}
    for r in slot_rows:
        if r.get('lineup_slot') is not None:
            slot_order.setdefault(r['lineup_slot'], r.get('sort_order') or 999)
    slot_cols = sorted(slot_order, key=lambda s: (slot_order[s], s))

    by_team = defaultdict(dict)
    for r in slot_rows:
        by_team[r['team_id']][r['lineup_slot']] = r['slot_pts']

    rows.append([])
    rows.append([])
    rows.append(['Points by Lineup Slot (Season Totals)'])
    # Indented one cell with Owner added so the grid's Team / Owner columns
    # sit directly under Table A's (column symmetry between the blocks).
    rows.append(['', 'Team', 'Owner', *slot_cols])
    for team in standings_rows:
        team_slots = by_team.get(team['team_id'], {})
        rows.append([
            '',
            team.get('team_abbrev') or '',
            team.get('owner_display') or '',
            *[team_slots.get(slot, '') for slot in slot_cols],
        ])

    # Acquisition-channel blocks (MLB-17): production by how each player was
    # acquired, and the production forfeited when they left, under two lenses.
    # Each block is ranked by its own Acquired total (the "rankings by
    # acquisition channel" deliverable), ties broken by abbrev for determinism.
    if acquisition_rows:
        rows.append([])
        rows.append([])
        rows.append(['Production by Acquisition Channel'])
        rows.append([
            "Points each team's roster produced, split by how each player was "
            "acquired (Acquired), against the points departed players went on "
            "to produce elsewhere (Lost). FA Net = FA-add acquired minus "
            "dropped lost; Trade Net = trade acquired minus traded-away lost."
        ])
        lens_blocks = (
            ('active',
             'Active Lens - started points only (Lost = production for other teams)'),
            ('rostered',
             'Rostered Lens - all points incl. bench/IL (Lost = other teams AND unowned)'),
        )
        for lens, label in lens_blocks:
            total_key = f'acquired_{lens}_pts'
            ranked = sorted(
                acquisition_rows,
                key=lambda r: (-float(r.get(total_key) or 0),
                               r.get('team_abbrev') or ''),
            )
            rows.append([])
            rows.append([label])
            rows.append(list(ACQUISITION_HEADER))
            for team in ranked:
                rows.append(format_acquisition_row(team, lens))

    return rows


def build_team_weeks_tab_rows(team_week_rows, stat_specs, league_id=None,
                              schedule_lookup=None):
    """Build the team-week matchup archive tab."""
    schedule_lookup = schedule_lookup or {}
    hitting_specs = _team_week_specs_for_category(stat_specs, 'hitting')
    pitching_specs = _team_week_specs_for_category(stat_specs, 'pitching')
    header = [
        *TEAM_WEEKS_BASE_HEADER,
        *_team_week_stat_headers(hitting_specs),
        '',
        *_team_week_stat_headers(pitching_specs),
        '',
        *TEAM_WEEKS_SCORE_HEADER,
    ]
    rows = [header]
    for row in team_week_rows:
        rows.append(format_team_week_row(
            row,
            hitting_specs,
            pitching_specs,
            league_id=league_id,
            schedule_lookup=schedule_lookup,
        ))
    return rows


def build_records_tab_rows(all_time_records, current_season_records, league_id=None,
                           display_map=None, schedule_lookup=None, record_specs=None):
    """Build the almanac Records tab as a side-by-side record book."""
    display_map = display_map or stat_catalog.get_display_map()
    schedule_lookup = schedule_lookup or records.load_schedule_lookup()
    record_specs = record_specs or [
        *SCORE_RECORD_SPECS,
        *get_scored_record_specs(),
        *RATE_RECORD_SPECS,
        *get_lineup_slot_record_specs(),
    ]

    all_time_index = _index_records(all_time_records)
    current_index = _index_records(current_season_records)

    # v1.1.1: thresholds come from dim_stat (via stat_catalog.get_rate_
    # qualifiers) rather than Python constants. If multiple rate stats
    # ever carry diverging qualifiers within the same category, this
    # rendering would need to grow.
    _rate_quals = stat_catalog.get_rate_qualifiers()
    _ab_min = max((m for q, m in _rate_quals.values() if q == 'ab'), default=0)
    _outs_min = max((m for q, m in _rate_quals.values() if q == 'outs'), default=0)
    _ip_min = _outs_min // 3
    rows = [
        ['League Records'],
        [
            'Counting Stats only look at standard-length matchups. '
            f'Pitching Rate stats require min {_ip_min} IP, '
            f'Hitting Rate stats require min {_ab_min} AB. '
            'Boxscore links go to the most recent instance of the record.'
        ],
        [
            'Current Season records set last week are italicized. '
            'All-Time records set this year are italicized. '
            'All-Time records set last week are italicized and highlighted.'
        ],
    ]

    for section_title, specs in _group_record_specs(record_specs):
        section_rows = []
        for spec in specs:
            if spec.get('spacer'):
                if section_rows and section_rows[-1] != []:
                    section_rows.append([])
                continue
            current_record = current_index.get(_spec_key(spec))
            all_time_record = all_time_index.get(_spec_key(spec))
            if _record_never_occurred(current_record):
                current_record = None
            if _record_never_occurred(all_time_record):
                all_time_record = None
            if current_record or all_time_record:
                section_rows.append(format_record_matrix_row(
                    spec,
                    current_record=current_record,
                    all_time_record=all_time_record,
                    league_id=league_id,
                    display_map=display_map,
                    schedule_lookup=schedule_lookup,
                ))

        if section_rows:
            rows.extend([
                _records_matrix_scope_header(section_title),
                RECORDS_MATRIX_DETAIL_HEADER,
            ])
            rows.extend(section_rows)
            rows.append([])

    if rows and rows[-1] == []:
        rows.pop()
    return rows


def _group_record_specs(record_specs):
    """Group record specs by section while preserving first-seen order."""
    grouped = []
    by_section = {}
    for spec in record_specs:
        section = spec.get('section') or 'Records'
        if section not in by_section:
            by_section[section] = []
            grouped.append((section, by_section[section]))
        by_section[section].append(spec)
    return grouped


def _spec_key(spec):
    if spec.get('spacer'):
        return None
    return (
        spec.get('grain'),
        spec.get('stat_name'),
        spec.get('direction'),
    )


def _record_never_occurred(record):
    """Suppress positive-event records whose top value is still zero."""
    if not record:
        return False
    return (
        record.get('record_direction') == 'most'
        and (record.get('stat_value') or 0) == 0
        and record.get('stat_name') not in {
            'CALCULATED_POINTS',
            'CALCULATED_HITTING_PTS',
            'CALCULATED_PITCHING_PTS',
            'PLATFORM_POINTS',
            'PLATFORM_HITTING_PTS',
            'PLATFORM_PITCHING_PTS',
        }
        and not str(record.get('stat_name') or '').startswith('LINEUP_SLOT_POINTS__')
    )


def _index_records(record_rows):
    """Index records by (grain, stat, direction) for curated lookup."""
    return {
        (
            row.get('entity_grain'),
            row.get('stat_name'),
            row.get('record_direction'),
        ): row
        for row in record_rows
    }


def build_team_roster_tabs(roster_rows, season_year, league_id=None, slot_caps=None):
    """Build one team active-stat roster tab per fantasy team."""
    grouped = defaultdict(list)
    for row in roster_rows:
        grouped[row.get('team_id')].append(row)

    tabs = []
    for team_id in sorted(grouped):
        team_rows = expand_team_roster_rows(grouped[team_id], slot_caps)
        first = team_rows[0]
        title = team_tab_title(first)
        scoring_period = first.get('latest_scoring_period')
        rows = [
            [first.get('team_name') or f'Team {team_id}'],
            [
                f"{season_year} roster snapshot"
                + (f" through scoring period {scoring_period}" if scoring_period else "")
            ],
            [],
            TEAM_ROSTER_HEADER,
        ]
        rows.extend([
            format_team_roster_row(row, league_id=league_id)
            for row in team_rows
        ])
        tabs.append((title, rows))

    return tabs


def expand_team_roster_rows(team_rows, slot_caps=None):
    """Add blank rows for configured roster slots with no current player."""
    if not slot_caps:
        return team_rows

    expanded = list(team_rows)
    by_slot = defaultdict(list)
    for row in team_rows:
        by_slot[row.get('lineup_slot')].append(row)

    template = team_rows[0] if team_rows else {}
    for slot in sorted(slot_caps, key=_slot_sort_key):
        capacity = slot_caps[slot]
        existing = len(by_slot.get(slot, []))
        for slot_rank in range(existing + 1, capacity + 1):
            expanded.append(_blank_roster_row(template, slot, slot_rank, capacity))

    return sorted(
        expanded,
        key=lambda r: (
            _slot_sort_key(r.get('lineup_slot')),
            int(r.get('slot_rank') or 1),
            r.get('display_name') or r.get('player_name') or '',
        ),
    )


def _blank_roster_row(template, slot, slot_rank, slots_to_fill):
    """Build an empty roster-slot placeholder row."""
    return {
        'season_year': template.get('season_year'),
        'latest_matchup_period': template.get('latest_matchup_period'),
        'latest_scoring_period': template.get('latest_scoring_period'),
        'latest_matchup_end_date': template.get('latest_matchup_end_date'),
        'team_id': template.get('team_id'),
        'team_name': template.get('team_name'),
        'team_abbrev': template.get('team_abbrev'),
        'owner_name': template.get('owner_name'),
        'lineup_slot': slot,
        'slot_rank': slot_rank,
        'slots_to_fill': slots_to_fill,
        'is_empty_slot': True,
    }


# Header-row column anchors (Kyle's gold standard, 2026-07-17): the
# points glossary at col H (overflowing rightward into empty cells),
# the "Lineup Data:" label right-aligned at R1, and the era lines at
# S1:S3 (merged S:X writer-side so auto-resize ignores them).
_GLOSS_COL = 7
_LINEUP_DATA_LABEL_COL = 17
_LINEUP_DATA_COL = 18


def _team_history_header_row(placements, width=TEAM_ROSTER_MATRIX_WIDTH):
    """Build one header row `width` cells wide (default TEAM_ROSTER_MATRIX_
    WIDTH) with text at specific column indices (everything else blank).
    Callers pass a wider width when the all-time side carries the trailing
    Years-of-Service column."""
    row = [''] * width
    for idx, text in placements.items():
        if 0 <= idx < width:
            row[idx] = text
    return row


def build_team_history_tabs(history_data, season_year, league_id=None, slot_caps=None,
                            optimal_team_fn=None, title_fn=None,
                            lineup_data=None, team_order=None,
                            best_seasons_fn=None):
    """Build side-by-side current-season/all-time best-lineup tabs.

    v1.1.1: Starters fill switched from days-active-at-slot greedy to
    get_optimal_team (calculated-points lens, gap-based selection). Bench
    fill switched from active_points to total rostered production
    (active + bench/IL points), so a player can land in Bench "because
    they were blocked by a better player" -- the missed-opportunity
    framing the user picked for Approach 1.

    The CBS almanac reuses this builder verbatim (Kyle 2026-07-16: team
    tabs identical across leagues, ESPN's shape wins) via league knobs,
    all defaulting to the ESPN behavior:
      optimal_team_fn  starters selector (see build_team_history_side)
      title_fn         team_meta -> worksheet title (default abbrev-based
                       team_tab_title; CBS keeps full team names)
      lineup_data      callable(team_id) -> up to 3 era lines for the
                       R1/S1:S3 "Lineup Data:" block (CBS provenance);
                       None = no block (ESPN)
      team_order       explicit team_id tab order; None = alphabetical by
                       title (both leagues -- Kyle 2026-07-17)
      best_seasons_fn  callable(team_id) -> {'candidates': [...],
                       'seasons': [...]} feeding the Best Individual
                       Seasons block under the Current Season readout
                       (Kyle 2026-07-17); None = no block
    """
    del league_id
    slot_caps = slot_caps or {}
    players = history_data.get('players') or []

    # Years-of-Service column (all-time side, trailing): render only for
    # leagues with >= 1 completed prior season -- otherwise it's a trivial
    # "1: <year>" for everyone (Kyle 2026-07-16). Detected from any active
    # season the all-time rows recorded before the current one.
    show_yos = any(
        y.strip() and int(y) < season_year
        for row in players
        if row.get('scope') == 'all_time'
        for y in str(row.get('service_years') or '').split(',')
    )
    all_time_detail = (TEAM_HISTORY_ALLTIME_DETAIL_HEADER if show_yos
                       else TEAM_HISTORY_DETAIL_HEADER)
    roster_header = [*TEAM_HISTORY_DETAIL_HEADER, '', *all_time_detail]
    matrix_width = len(roster_header)

    teams = {}
    players_by_team_scope = defaultdict(list)
    for row in players:
        team_id = row.get('team_id')
        scope = row.get('scope')
        if team_id is None or not scope:
            continue
        teams.setdefault(team_id, row)
        players_by_team_scope[(team_id, scope)].append(row)

    _title = title_fn or team_tab_title
    if team_order is not None:
        order_index = {tid: i for i, tid in enumerate(team_order)}
        sort_key = lambda tid: (order_index.get(tid, len(order_index)),)
    else:
        # Alphabetical by the DISPLAYED title (title_fn), not the default
        # abbrev title -- sorting CBS's full-name tabs by abbrev looked
        # scrambled (Kyle 2026-07-17).
        sort_key = lambda tid: (str(_title(teams[tid])).casefold(), tid)

    tabs = []
    for team_id in sorted(teams, key=sort_key):
        team_meta = teams[team_id]
        current_rows = build_team_history_side(
            players_by_team_scope[(team_id, 'current_season')],
            slot_caps,
            season_year=season_year,
            team_id=team_id,
            optimal_team_fn=optimal_team_fn,
        )
        all_time_rows = build_team_history_side(
            players_by_team_scope[(team_id, 'all_time')],
            slot_caps,
            season_year=None,
            team_id=team_id,
            optimal_team_fn=optimal_team_fn,
        )
        row_labels = _team_history_row_labels(current_rows, all_time_rows)
        period_end_date = _format_sheet_date(team_meta.get('latest_matchup_end_date'))
        # Kyle's gold-standard header (2026-07-17): a terse A2 dateline
        # (the "through" date = last completed matchup period on ESPN,
        # last captured date on CBS), a static A3 scoring note, the
        # points glossary inline at H1:H3, and the league-specific
        # Lineup Data block at R1/S1:S3.
        subtitle = (
            'Optimal Lineups'
            + (f', through {period_end_date}' if period_end_date else '')
        )
        team_name = team_meta.get('team_name') or f'Team {team_id}'
        team_abbrev = team_meta.get('team_abbrev')
        # Abbrev rides in the A1 cell as a size-10 non-bold parenthetical
        # (the writer styles the run); the tab itself stays abbrev-named.
        title_cell = f"{team_name} ({team_abbrev})" if team_abbrev else team_name
        era_lines = list(lineup_data(team_id) or ()) if lineup_data else []
        row1 = {0: title_cell, _GLOSS_COL: TEAM_HISTORY_GLOSS_LINES[0]}
        row2 = {0: subtitle, _GLOSS_COL: TEAM_HISTORY_GLOSS_LINES[1]}
        row3 = {0: "Points are calculated according to current season's "
                   'scoring.',
                _GLOSS_COL: TEAM_HISTORY_GLOSS_LINES[2]}
        if era_lines:
            row1[_LINEUP_DATA_LABEL_COL] = 'Lineup Data:'
            for placements, line in zip((row1, row2, row3), era_lines[:3]):
                placements[_LINEUP_DATA_COL] = line
        rows = [
            _team_history_header_row(row1, width=matrix_width),
            _team_history_header_row(row2, width=matrix_width),
            _team_history_header_row(row3, width=matrix_width),
            _team_history_scope_header(with_yos=show_yos),
            roster_header,
        ]
        for label in row_labels:
            rows.append(format_team_history_matrix_row(
                label,
                current_rows.get(label),
                all_time_rows.get(label),
                with_yos=show_yos,
            ))
        if best_seasons_fn is not None:
            _overlay_best_season_block(
                rows, best_seasons_fn(team_id) or {}, slot_caps,
                matrix_width=matrix_width,
            )
        tabs.append((_title(team_meta), rows))

    return tabs


def build_team_history_side(player_rows, slot_caps, *, season_year, team_id,
                            optimal_team_fn=None):
    """Arrange one scope of team/player history into best-lineup rows.

    v1.1.1: Starters come from get_optimal_team (calculated-points lens,
    gap-based selection); the days-active-at-slot greedy fill that
    previously drove this is gone. Bench/IL/Other still draw from the
    leftover-roster pool, but Bench sort is now total rostered production
    (active + bench/IL points) descending, per the user's Approach 1:
    "could've maybe made this team but didn't -- either misuse or
    blocked by a better player."

    Args:
      player_rows: list of player history rows (calculated-lens active +
        bench/IL points, rostered_days, il_days, stat tail, etc.) for
        this scope/team. From get_team_roster_history_stats.
      slot_caps:   dict {slot_code: starter_count} from
        get_slot_capacities.
      season_year: None for the all-time side, season int for the
        current-season side. Threaded into get_optimal_team.
      team_id:     this tab's team_id. Threaded into get_optimal_team
        so the Starters pool is scoped to players this team rostered.
      optimal_team_fn: optional starters selector for a non-ESPN league
        (the CBS almanac passes a get_best_lineup adapter). Called as
        fn(season_year=..., team_id=...) and must return rows carrying
        player_id + slot_label + lineup_slot, pre-sorted in slot order.
        None = ESPN's almanac_data.get_optimal_team (read at call time
        so tests can monkeypatch it).
    """
    players = {
        row.get('player_id'): row
        for row in player_rows
        if row.get('player_id') is not None
    }

    selected_ids = set()
    output = {}

    # Starters: best lineup this team could have built within
    # (season_year, team_id). For each picked (slot, player), use the
    # player's roster-context row from player_rows (active_points,
    # bench_il_points, rostered_days, active_games, stat tail) so the
    # display columns stay consistent with Bench/IL/Other and read
    # "this player's production across the window," not the position-
    # specific selection criterion.
    #
    # get_optimal_team returns rows pre-sorted in canonical SLOT_ORDER
    # (the selector itself sorts before returning), so the output dict
    # insertion order below is already correct for _team_history_row_
    # labels to read off.
    if optimal_team_fn is not None:
        optimal_rows = optimal_team_fn(season_year=season_year, team_id=team_id)
    else:
        optimal_rows = almanac_data.get_optimal_team(
            season_year=season_year,
            team_id=team_id,
            points_type='active',
        )
    for opt_row in optimal_rows:
        player_id = opt_row.get('player_id')
        if player_id is None:
            continue
        player = players.get(player_id)
        if not player:
            # Defensive: optimal-team selection couldn't be matched back
            # to roster history. Shouldn't happen since both queries scope
            # to (team_id) -- skip the row rather than fabricate display
            # context.
            continue
        slot_code = opt_row.get('lineup_slot') or ''
        label = opt_row.get('slot_label') or slot_code or ''
        if not label:
            continue
        # v1.2 fix: slot-decomposed active points. A two-way player
        # (Ohtani) otherwise shows his combined hitting+pitching total at
        # BOTH his DH and pitcher rows. Pull the player's own per-category
        # active points (from fct_player_season_performance, same source as
        # active_points) -- hitting pts at hitting slots, pitching pts at
        # pitching slots. Single-discipline players: the category total
        # equals active_points exactly (the other is 0), so their displayed
        # points don't move. The stat-line tail is already slot-decomposed
        # via display_slot.
        if str(slot_code).startswith(('SP', 'RP', 'P')):
            slot_points = player.get('active_pitching_points')
        else:
            slot_points = player.get('active_hitting_points')
        output[label] = _team_history_display_row(
            player,
            label,
            display_slot=label,
            active_points=slot_points,
        )
        selected_ids.add(player_id)

    remaining = [
        row for row in players.values()
        if row.get('player_id') not in selected_ids
    ]

    # Approach 1: Bench by total rostered production (active + bench/IL).
    # Surfaces "could've made the team but didn't -- misuse or blocked
    # by a better player" -- both lenses live on this team's history,
    # both are now calculated, so the sum is a coherent ranking.
    bench_count = int(slot_caps.get('BE') or 0)
    bench_candidates = sorted(
        remaining,
        key=lambda r: (
            -(float(r.get('active_points') or 0) + float(r.get('bench_il_points') or 0)),
            -int(r.get('rostered_days') or 0),
            r.get('display_name') or r.get('player_name') or '',
        ),
    )
    for slot_rank, row in enumerate(bench_candidates[:bench_count], 1):
        label = slot_label('BE', slot_rank, bench_count)
        position = _inactive_position_display(row)
        output[label] = _team_history_display_row(
            row,
            label,
            display_slot=_compact_inactive_slot('BE', position),
        )
        selected_ids.add(row.get('player_id'))

    remaining = [
        row for row in players.values()
        if row.get('player_id') not in selected_ids
    ]

    il_count = int(slot_caps.get('IL') or 0)
    il_candidates = [
        row for row in remaining
        if int(row.get('il_days') or 0) > 0
    ]
    il_candidates.sort(
        key=lambda r: (
            -int(r.get('il_days') or 0),
            -int(r.get('rostered_days') or 0),
            r.get('display_name') or r.get('player_name') or '',
        ),
    )
    for slot_rank in range(1, il_count + 1):
        label = slot_label('IL', slot_rank, il_count)
        if slot_rank <= len(il_candidates):
            row = il_candidates[slot_rank - 1]
            position = _inactive_position_display(row)
            output[label] = _team_history_display_row(
                row,
                label,
                display_slot=_compact_inactive_slot('IL', position),
            )
            selected_ids.add(row.get('player_id'))
        else:
            output[label] = _empty_team_history_display_row()

    remaining = [
        row for row in players.values()
        if row.get('player_id') not in selected_ids
    ]
    # Other only renders players who actually DID something for this team:
    # an active game played, or any points (active or inactive) while
    # rostered (Kyle 2026-07-16 -- LAW's 7-roster-day, all-zero Agustin
    # Ramirez row was pure noise). Bench and IL keep the unfiltered pool:
    # Bench ranks by production anyway, and IL tenancy is its own story.
    remaining = [
        row for row in remaining
        if int(row.get('active_games') or 0) > 0
        or float(row.get('active_points') or 0) != 0
        or float(row.get('bench_il_points') or 0) != 0
    ]

    def _total(r):
        return (float(r.get('active_points') or 0)
                + float(r.get('bench_il_points') or 0))

    # The franchise futility chair (Kyle 2026-07-17): the worst-ever
    # player by rostered_days - total_points ("the guy who dragged you
    # down" -- days+games-total was tested and only nosed toward
    # high-playing-time mediocrity; the hoarded-star class belongs to
    # the Wasted Hall of Shame, not here). Pulled OUT of the also-ran
    # ranking and pinned as the section's last row. All-time only --
    # a CURRENT-season worst-of makes no sense unless a team somehow
    # burns through 100+ players in one year (Kyle 2026-07-17), so the
    # current side only seats a chair when its cut actually fires.
    want_chair = (season_year is None
                  or len(remaining) > TEAM_HISTORY_OTHER_CAP)
    chair = max(
        remaining,
        key=lambda r: int(r.get('rostered_days') or 0) - _total(r),
        default=None,
    ) if want_chair else None
    pool = [r for r in remaining if r is not chair]

    # Other N uses the same total-rostered-production sort as Bench
    # (Approach 1) so the leftover-pool ordering is coherent across the
    # two sections -- they're conceptually the same pool with Bench just
    # the top BE-many rows. Capped at the top 100 (Kyle 2026-07-17);
    # the cut collapses into one honest summary line.
    pool.sort(
        key=lambda r: (
            -_total(r),
            -int(r.get('rostered_days') or 0),
            r.get('display_name') or r.get('player_name') or '',
        ),
    )
    also_rans = pool[:TEAM_HISTORY_OTHER_CAP]
    cut = pool[TEAM_HISTORY_OTHER_CAP:]
    for row_number, row in enumerate(also_rans, 1):
        label = f'Other {row_number}'
        position = _inactive_position_display(row)
        output[label] = _team_history_display_row(
            row,
            label,
            display_slot=_compact_inactive_slot('Other', position),
        )
    if cut:
        cutoff = _total(also_rans[-1])
        # The teasers are the nearest misses: the next 3 by total points
        # right below the cutoff (ranks 101-103).
        teasers = ', '.join(
            (r.get('display_name') or r.get('player_name') or '?')
            for r in cut[:3])
        summary = _empty_team_history_display_row()
        # The text rides in the Team cell (col D/T) -- it overflows
        # rightward across the blank numeric cells; the Player column
        # stays empty so its auto-resize doesn't fit to this line. The
        # trailing tease hands off to the chair row below (Kyle
        # 2026-07-17).
        summary['pro_team'] = (
            f'{len(cut)} other players under {cutoff:g} points, '
            f'including {teasers} and, worst of all...')
        output[TEAM_HISTORY_OTHER_MORE] = summary
    if chair is not None:
        position = _inactive_position_display(chair)
        output[TEAM_HISTORY_OTHER_WORST] = _team_history_display_row(
            chair,
            TEAM_HISTORY_OTHER_WORST,
            display_slot=_compact_inactive_slot('Worst', position),
        )

    return output


def _overlay_best_season_block(rows, data, slot_caps, *, matrix_width):
    """Write the Best Individual Seasons block into the LEFT side (cols
    A:O) below the Current Season readout (Kyle 2026-07-17): one buffer
    row, the navy banner, then the optimal lineup over PLAYER-SEASON
    candidates -- the same player may take several slots via different
    seasons, but a player-season is used once (synthetic key|season
    candidate ids make the shared selector enforce that for free).
    Starters + bench only; no Others. The right side's rows continue
    alongside untouched; extra full-width rows are appended if the block
    runs past them."""
    block = _best_season_block_rows(data, slot_caps)
    if not block:
        return
    # Last row with any left-side content, below the column header (the
    # first 5 rows are the tab header band).
    left_last = 4
    for i in range(5, len(rows)):
        if any(str(c).strip() for c in rows[i][:15]):
            left_last = i
    insert_at = left_last + 2   # +1 = the buffer row
    for offset, block_row in enumerate(block):
        idx = insert_at + offset
        while idx >= len(rows):
            rows.append([''] * matrix_width)
        row = rows[idx]
        if len(row) < matrix_width:
            row.extend([''] * (matrix_width - len(row)))
        row[:15] = block_row


def _best_season_block_rows(data, slot_caps):
    """The block's left-side cell rows: banner, hitter/pitcher stat
    sub-headers + starters, mixed sub-header + bench. Candidates carry
    (player, season, position, active pts); season stat rows carry the
    display fields at (player, season) grain."""
    candidates = []
    for cand in data.get('candidates') or ():
        row = dict(cand)
        season = int(row['season_year'])
        # Identity: player_id where present (numeric on ESPN; the CBS id
        # string doubles as player_key), player_key for the CBS ui-only
        # synthetics -- must match the seasons rows' player_id key.
        pid = row.get('player_id')
        base = str(pid) if pid is not None else str(row.get('player_key'))
        row['player_key'] = f'{base}|{season}'
        row['_base_key'] = base
        candidates.append(row)
    if not candidates:
        return []
    season_stats = {
        (str(r.get('player_id')), int(r['season_year'])): r
        for r in data.get('seasons') or ()
    }
    starter_caps = {slot: count for slot, count in slot_caps.items()
                    if slot not in ('BE', 'IL')}
    picks = get_optimal_team_selections(candidates, starter_caps)

    def _display_row(stat_row, season, label, display_slot, slot_code):
        row = dict(stat_row)
        name = row.get('display_name') or row.get('player_name') or ''
        row['display_name'] = f'{name} ({season})'
        # The Team column doubles as YEAR in this section (Kyle
        # 2026-07-17: pro_team is only season-accurate on the CBS side;
        # a Year column is honest on both leagues). The sub-headers
        # relabel the column.
        row['pro_team'] = season
        if str(slot_code).startswith(('SP', 'RP', 'P')):
            slot_points = row.get('active_pitching_points')
        else:
            slot_points = row.get('active_hitting_points')
        return _team_history_display_row(
            row, label, display_slot=display_slot, active_points=slot_points)

    def _side_cells(display_row):
        return _team_history_side_cells(display_row)

    def _stat_header(labels):
        side = [''] * len(TEAM_HISTORY_DETAIL_HEADER)
        side[3] = 'Year'   # the Team column's 1-off relabel, this section only
        side[10:] = labels
        return side

    hitters, pitchers = [], []
    used = set()
    for pick in picks:
        label = pick.get('slot_label') or pick.get('lineup_slot') or ''
        slot_code = pick.get('lineup_slot') or ''
        is_pitcher = str(slot_code).startswith(('SP', 'RP', 'P'))
        bucket = pitchers if is_pitcher else hitters
        base = pick.get('_base_key')
        if base is None:
            bucket.append(_side_cells({
                **_empty_team_history_display_row(), 'display_slot': label}))
            continue
        season = int(pick['season_year'])
        used.add((base, season))
        stat_row = season_stats.get((base, season), pick)
        bucket.append(_side_cells(
            _display_row(stat_row, season, label, label, slot_code)))

    rows = [[TEAM_HISTORY_BEST_SEASON_BANNER] + [''] * 14]
    rows.append(_stat_header(TEAM_HISTORY_HITTER_STATS))
    rows.extend(hitters)
    rows.append(_stat_header(TEAM_HISTORY_PITCHER_STATS))
    rows.extend(pitchers)

    bench_count = int(slot_caps.get('BE') or 0)
    if bench_count:
        def _season_total(r):
            return (float(r.get('active_points') or 0)
                    + float(r.get('bench_il_points') or 0))
        bench_pool = sorted(
            (r for key, r in season_stats.items() if key not in used),
            key=lambda r: (-_season_total(r), str(r.get('player_id')),
                           int(r['season_year'])),
        )
        bench_rows = []
        for r in bench_pool[:bench_count]:
            season = int(r['season_year'])
            position = _inactive_position_display(r)
            display_slot = _compact_inactive_slot('BE', position)
            bench_rows.append(_side_cells(
                _display_row(r, season, display_slot, display_slot,
                             r.get('position') or '')))
        if bench_rows:
            rows.append(_stat_header(TEAM_HISTORY_MIXED_STATS))
            rows.extend(bench_rows)
    return rows


def _team_history_row_labels(current_rows, all_time_rows):
    specials = (TEAM_HISTORY_OTHER_MORE, TEAM_HISTORY_OTHER_WORST)
    base_labels = [label for label in current_rows
                   if not label.startswith('Other ') and label not in specials]
    labels = list(base_labels)
    for label in all_time_rows:
        if (not label.startswith('Other ') and label not in specials
                and label not in labels):
            labels.append(label)
    labels = _insert_before_first(
        labels,
        TEAM_HISTORY_HITTER_HEADER,
        _is_hitter_team_history_label,
    )
    labels = _insert_before_first(
        labels,
        TEAM_HISTORY_PITCHER_HEADER,
        _is_pitcher_team_history_label,
    )
    labels = _insert_before_first(
        labels,
        TEAM_HISTORY_MIXED_HEADER,
        _is_mixed_team_history_label,
    )
    other_count = max(
        _max_other_index(current_rows),
        _max_other_index(all_time_rows),
    )
    tail_start = len(labels)
    labels.extend(f'Other {i}' for i in range(1, other_count + 1))
    # The section tail (Kyle 2026-07-17): the "...N more" summary line,
    # then the franchise futility chair as the very last row.
    for special in specials:
        if special in current_rows or special in all_time_rows:
            labels.append(special)
    if len(labels) > tail_start:
        labels.insert(tail_start, '')
    return labels


def _insert_before_first(labels, marker, predicate):
    for index, label in enumerate(labels):
        if predicate(label):
            return [*labels[:index], marker, *labels[index:]]
    return labels


def _is_hitter_team_history_label(label):
    return label and not _is_pitcher_team_history_label(label) and not _is_mixed_team_history_label(label)


def _is_pitcher_team_history_label(label):
    return str(label).startswith(('SP', 'RP', 'P '))


def _is_mixed_team_history_label(label):
    return str(label).startswith(('BE', 'IL'))


def _max_other_index(rows):
    max_index = 0
    for label in rows:
        if label.startswith('Other '):
            try:
                max_index = max(max_index, int(label.split(' ', 1)[1]))
            except (IndexError, ValueError):
                pass
    return max_index


def _team_sort_key(row):
    title = team_tab_title(row)
    try:
        team_id = int(row.get('team_id'))
    except (TypeError, ValueError):
        team_id = 9999
    return (title.casefold(), team_id)


def _candidate_sort_key(row):
    points = row.get('platform_points') or 0
    display_name = row.get('display_name') or row.get('player_name') or ''
    slot = row.get('lineup_slot') or ''
    return (-points, _slot_sort_key(slot), display_name)
