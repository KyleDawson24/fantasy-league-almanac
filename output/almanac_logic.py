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
    TEAM_HISTORY_HITTER_HEADER,
    TEAM_HISTORY_HITTER_STATS,
    TEAM_HISTORY_MIXED_HEADER,
    TEAM_HISTORY_MIXED_STATS,
    TEAM_HISTORY_PITCHER_HEADER,
    TEAM_HISTORY_PITCHER_STATS,
    TEAM_ROSTER_HEADER,
    TEAM_ROSTER_MATRIX_WIDTH,
    TEAM_WEEKS_BASE_HEADER,
    TEAM_WEEKS_SCORE_HEADER,
    TEAM_WEEKS_TAB,
    SLOT_ORDER,
    boxscore_formula,
    format_all_league_team_row,
    format_all_league_team_row_with_deviation,
    format_all_league_thin_row,
    format_draft_board_cell,
    format_draft_value_row,
    home_nav_link,
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
# int_player_position_pts already attributes pitching points to
# pitching positions and hitting points to hitting positions, so
# picking Shohei at both SP and DH sums to his real total production
# without double-counting either component. Same-category double
# picks (e.g., 1B and DH for a hitter who's eligible at both) WOULD
# double-count and are blocked.
# -------------------------------------------------------------------------


_PITCHING_SLOTS = frozenset({'SP', 'RP', 'P'})


def _slot_category(slot):
    """'pitching' for SP/RP/P, 'hitting' for everything else.

    Mirrors the CASE expression in int_player_position_pts that drives
    position_platform_pts -- keep these two in sync.
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
                if cat not in used[c['player_id']]
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
        # (int_player_position_pts switched from platform_*_pts to
        # total_*_stat_pts in v1.1.1). The field name is preserved to
        # avoid churning format_all_league_team_row + every cached
        # selected_rows shape; a rename to `optimal_team_pts` is
        # tracked in BRAINTHOUGHTS as a follow-up cleanup.
        out['platform_points'] = best_player.get('position_pts')
        lineup.append(out)

        used[best_player['player_id']].add(_slot_category(best_slot))
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
    rows.append(['Team Pages', 'Historic production by team.'])
    rows.extend(_home_team_grid_rows(team_titles, nav_targets))
    # Slot Scoring is still a planned tab (unlinked). Draft Recap is built
    # now, so it links live (gid resolved in the two-pass write).
    rows.append([home_nav_link('Slot Scoring', None, nav_targets), 'Coming soon.'])
    rows.append([home_nav_link('Draft Recap', DRAFT_TAB, nav_targets),
                 'Draft board + best-value / bust picks.'])

    rows.append([])
    rows.append(['Points Glossary'])
    rows.extend([term, definition] for term, definition in _HOME_GLOSSARY)

    # Align the all-time block with the right-band Season-to-Date block so
    # the lineups sit inline: pad up to the season label's row, then mirror
    # its label / blank / header / rows shape.
    if align_alltime_to is not None:
        while len(rows) < align_alltime_to:
            rows.append([])
    rows.append(['All-League Team: All-Time'])
    rows.append([])
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

    rows = [
        [f'All-League Team of the Week: {season_year} Week {matchup_period}'],
        [],
        header,
    ]
    rows.extend(
        format_all_league_team_row_with_deviation(
            row, week_dev.get(row.get('slot_label')), league_id=league_id,
        )
        for row in weekly_rows
    )
    rows.append([])
    season_label_idx = len(rows)
    rows.append([f'All-League Team Season-to-Date: {season_year}'])
    rows.append([])
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
    all_by_slot = {row.get('slot_label'): row for row in all_rows}
    deviations = {}
    for row in active_rows:
        label = row.get('slot_label')
        alt = all_by_slot.get(label)
        if (
            alt
            and alt.get('player_id') is not None
            and alt.get('player_id') != row.get('player_id')
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
        [],
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


# Column anchors for the team-tab header text (v1.1.2). Indices into a
# TEAM_ROSTER_MATRIX_WIDTH-wide row: the slot-fill explanation sits in
# the current-season side (col F), the points glossary over the all-time
# side (col Q). Both overflow rightward into the empty header cells.
_EXPLAIN_COL = 5
_GLOSSARY_COL = 16


def _team_history_header_row(placements):
    """Build one TEAM_ROSTER_MATRIX_WIDTH-wide header row with text placed
    at specific column indices (everything else blank)."""
    row = [''] * TEAM_ROSTER_MATRIX_WIDTH
    for idx, text in placements.items():
        if 0 <= idx < TEAM_ROSTER_MATRIX_WIDTH:
            row[idx] = text
    return row


def build_team_history_tabs(history_data, season_year, league_id=None, slot_caps=None):
    """Build side-by-side current-season/all-time best-lineup tabs.

    v1.1.1: Starters fill switched from days-active-at-slot greedy to
    get_optimal_team (calculated-points lens, gap-based selection). Bench
    fill switched from active_points to total rostered production
    (active + bench/IL points), so a player can land in Bench "because
    they were blocked by a better player" -- the missed-opportunity
    framing the user picked for Approach 1.
    """
    del league_id
    slot_caps = slot_caps or {}
    players = history_data.get('players') or []

    teams = {}
    players_by_team_scope = defaultdict(list)
    for row in players:
        team_id = row.get('team_id')
        scope = row.get('scope')
        if team_id is None or not scope:
            continue
        teams.setdefault(team_id, row)
        players_by_team_scope[(team_id, scope)].append(row)

    tabs = []
    for team_id in sorted(teams, key=lambda tid: _team_sort_key(teams[tid])):
        team_meta = teams[team_id]
        current_rows = build_team_history_side(
            players_by_team_scope[(team_id, 'current_season')],
            slot_caps,
            season_year=season_year,
            team_id=team_id,
        )
        all_time_rows = build_team_history_side(
            players_by_team_scope[(team_id, 'all_time')],
            slot_caps,
            season_year=None,
            team_id=team_id,
        )
        row_labels = _team_history_row_labels(current_rows, all_time_rows)
        period_end_date = _format_sheet_date(team_meta.get('latest_matchup_end_date'))
        subtitle = (
            f"Best Lineup -- current season + all-time"
            + (f", through {period_end_date}" if period_end_date else "")
        )
        rows = [
            _team_history_header_row({
                0: team_meta.get('team_name') or f'Team {team_id}',
                _GLOSSARY_COL: ('Total Points -- all points a player produced '
                                'while rostered by this team (active + bench/IL).'),
            }),
            _team_history_header_row({
                0: subtitle,
                _EXPLAIN_COL: ('Starting lineup: best Active Points at each '
                               'eligible position. Bench / IL / Other: most '
                               'Total Points while rostered.'),
                _GLOSSARY_COL: ('Active Points -- produced while in an active '
                                'lineup slot (not bench or IL).'),
            }),
            _team_history_header_row({
                _EXPLAIN_COL: ('Points use current-season scoring -- tell us if '
                               "you'd rather see them as awarded at the time."),
                _GLOSSARY_COL: ('Inactive Points -- produced while on this '
                                "team's bench or IL."),
            }),
            _team_history_scope_header(),
            TEAM_ROSTER_HEADER,
        ]
        for label in row_labels:
            rows.append(format_team_history_matrix_row(
                label,
                current_rows.get(label),
                all_time_rows.get(label),
            ))
        tabs.append((team_tab_title(team_meta), rows))

    return tabs


def build_team_history_side(player_rows, slot_caps, *, season_year, team_id):
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
    # Other N uses the same total-rostered-production sort as Bench
    # (Approach 1) so the leftover-pool ordering is coherent across the
    # two sections -- they're conceptually the same pool with Bench just
    # the top BE-many rows.
    remaining.sort(
        key=lambda r: (
            -(float(r.get('active_points') or 0) + float(r.get('bench_il_points') or 0)),
            -int(r.get('rostered_days') or 0),
            r.get('display_name') or r.get('player_name') or '',
        ),
    )
    for row_number, row in enumerate(remaining, 1):
        label = f'Other {row_number}'
        position = _inactive_position_display(row)
        output[label] = _team_history_display_row(
            row,
            label,
            display_slot=_compact_inactive_slot('Other', position),
        )

    return output


def _team_history_row_labels(current_rows, all_time_rows):
    base_labels = [label for label in current_rows if not label.startswith('Other ')]
    labels = list(base_labels)
    for label in all_time_rows:
        if not label.startswith('Other ') and label not in labels:
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
    labels.extend(f'Other {i}' for i in range(1, other_count + 1))
    if other_count:
        labels.insert(len(labels) - other_count, '')
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
