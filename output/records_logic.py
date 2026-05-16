"""
output/records_logic.py

Pure-function consumer-side rules and presentation helpers for the
records pipeline. No SQL originates here, and no imports from
records_data -- the module is import-pure with respect to the data
layer so its rules can be unit-tested without a warehouse round-trip.

The one place the algorithm needs to query the warehouse
(_collapse_one_group's saturated-tier backfill: when a tied tier hits
the mart's top-10 cap, the visible row count is an undercount of the
true tie cohort) is handled via dependency injection: callers pass a
`count_fn` argument and this module remains ignorant of where the
count comes from. v1.x DI cleanup; previously this module imported
count_value_occurrences directly, which was the only logic->data
import in the project.

Public API:
- SCORE_STAT_NAMES                      -- score-level stat_names tuple
- INLINE_COLLAPSE_THRESHOLD             -- tier inline-vs-summary cutoff
- should_track_record(...)              -- polarity-aware consumer filter
- best_or_worst_label(...)              -- polarity-aware "Best"/"Worst"
- format_week_label(...)                -- "Week N" or playoff round name
- ordinal(n)                            -- "1st" / "2nd" / etc.
- collapse_ties(records, max_n=5,       -- tie-collapse algorithm; pass
                count_fn=None)             count_fn=count_value_occurrences
                                           in production for accurate
                                           saturated-tier counts
"""


# Score-level stat_names in the leaderboard. The records-section
# consumers and the rendering helpers treat these specially:
#   - should_track_record: short-circuits to True at team grain; at
#     player grain ONLY these stats surface (other player records like
#     "Most HR by a player in a week" intentionally don't bubble up to
#     the records section).
#   - fmt_stat_value_with_unit + make_record_label: render as "XX.X pts"
#     with "Best/Worst Team|Player Foo Points" labels rather than the
#     individual-stat "{value} {abbrev}" + "Most/Fewest {label}" form.
#
# Phase 7 G2+G4: PLATFORM_* added. Pre-Phase-7 records.SCORE_STAT_NAMES
# was narrow (CALCULATED_* only) because today's recap+report only
# surface CALCULATED_* records (Phase 5 design). But formatters._SCORE_
# STAT_KEYS already had PLATFORM_* for value-rendering; the misalignment
# rendered "86.4 PLATFORM_HITTING_PTS" instead of "86.4 pts" when
# PLATFORM_* records did surface (latent before G2, real after G2's
# polarity-source change). Unifying here -- PLATFORM_* records will now
# surface in the recap's new-records section alongside CALCULATED_* and
# render with proper "Best Team Platform Hitting Points: 86.4 pts" shape.
SCORE_STAT_NAMES = (
    'CALCULATED_POINTS',
    'CALCULATED_HITTING_PTS',
    'CALCULATED_PITCHING_PTS',
    'PLATFORM_POINTS',
    'PLATFORM_HITTING_PTS',
    'PLATFORM_PITCHING_PTS',
)


# ---------- Polarity-aware filter rules ----------
#
# The polarity merge logic that used to live in records.py moved to the
# seed: per-stat polarity is stored directly in stat_classification.polarity
# and read via stat_catalog.get_polarity_map(). The seed-driven auto-
# tracked set is read via stat_catalog.get_auto_tracked().
#
# What stays here:
#   - best_or_worst_label    -- pure presentation helper (takes injected
#                               polarity dict, returns Best/Worst string)
#   - should_track_record    -- consumer-side filter (grain x direction
#                               matrix using injected polarity + auto-
#                               tracked sets). Caller injects the dicts;
#                               keeps the signature testable without
#                               monkeypatching.

def best_or_worst_label(stat_name, direction, effective_polarity):
    """Phase 6.3.3 chunk 6.5: uniform Best/Worst label, polarity-aware.

    For positive stats (HR, K, calculated_points, etc.):
      direction='most'   -> 'Best'  (more = better outcome)
      direction='fewest' -> 'Worst'

    For negative stats (ER, WHIP, GDP, wasted_points, etc.):
      direction='most'   -> 'Worst' (more = worse outcome)
      direction='fewest' -> 'Best'

    Unknown stats default to positive (most='Best'). The caller pre-builds
    the effective_polarity dict (via stat_catalog.get_polarity_map()) once
    per script run and passes it in; this fn does an O(1) lookup per row.

    Returns either the Best/Worst label or 'Best'/'Worst' as a string.
    """
    pol = effective_polarity.get(stat_name, 'positive')
    if pol == 'negative':
        return 'Best' if direction == 'fewest' else 'Worst'
    return 'Best' if direction == 'most' else 'Worst'


def should_track_record(grain, stat_name, direction, polarity, auto_tracked=None):
    """Polarity-aware filter rules (direction values are 'most' / 'fewest'):
      - Player grain: only score-level stats; both directions surface.
      - Team grain, score columns: both directions.
      - Team grain, auto_tracked stat (per stat_classification seed):
        both directions, regardless of polarity. These are stats tracked
        regardless of the league's scoring settings -- universal counting
        stats and project-defined derivations that lack a points_per_unit
        weight.
      - Team grain, positive individual stat: both directions.
      - Team grain, negative individual stat: 'most' (most-of-bad) only.
      - Zero-weighted (neutral) stats: skipped entirely.

    `auto_tracked` defaults to None for callers that don't load the seed-
    driven set. Pass the result of stat_catalog.get_auto_tracked() to
    enable the bypass for stats outside scoring settings.
    """
    if grain == 'player':
        return stat_name in SCORE_STAT_NAMES
    # team grain
    if stat_name in SCORE_STAT_NAMES:
        return True
    if auto_tracked and stat_name in auto_tracked:
        return True
    pol = polarity.get(stat_name)
    if pol is None or pol == 'neutral':
        return False
    if pol == 'positive':
        return True
    return direction == 'most'  # negative-stat: most-of only


# Stats that don't sit in the scoring-settings seed but are meaningful
# at team grain. Includes:
#  - rate stats: ERA / WHIP / K/9 / K/BB / HR/9 / BB/9
#  - WASTED_POINTS (derived from the inactive facts; team-only by construction)
#  - derived counting stats: PA / SB-CS / W-L / SV-BLSV
# These have no points_per_unit row in stg_scoring_settings, so
# stat_catalog.get_polarity_map() doesn't carry an entry for them and
# should_track_record() filters them out. The orchestrator filter below
# extends the rule to allow these at team grain in BOTH directions,
# while leaving should_track_record's existing rules untouched (so
# existing flows -- BBCode summary records section, Sheets tabs 1/2 --
# behave the same as Phase 6.2).
_TEAM_NON_SEED_STATS = frozenset({
    'ERA', 'WHIP', 'K_PER_9', 'K_PER_BB', 'HR_PER_9', 'BB_PER_9',
    'WASTED_POINTS',
    'PA', 'SB_CS', 'W_L', 'SV_BLSV',
})


def _orchestrator_filter(grain, stat_name, direction, polarity, auto_tracked):
    """Layered filter for the leaderboard-dump orchestrator:
      - All should_track_record-passing rows pass (which consults the
        auto_tracked seed-driven set).
      - Plus team-grain stats in _TEAM_NON_SEED_STATS (rate stats /
        WASTED_POINTS / derived counts) in BOTH directions, since these
        carry no scoring-settings polarity but are meaningful at team
        grain (and aren't in the seed).
    """
    if should_track_record(grain, stat_name, direction, polarity, auto_tracked):
        return True
    return grain == 'team' and stat_name in _TEAM_NON_SEED_STATS


# ---------- Presentation helpers ----------

def format_week_label(season_year, matchup_period, schedule_lookup):
    """'Week N' for regular weeks, the playoff round name (e.g. 'Round 1',
    'Semi-Finals', 'Finals') for playoff weeks. Falls back to 'Week N' if
    the (season, mp) is missing -- defensive only; matchup_schedule is
    canonical for the current season."""
    info = schedule_lookup.get((season_year, matchup_period))
    if info and info.get('is_playoff') and info.get('playoff_round'):
        return info['playoff_round']
    return f"Week {matchup_period}"


def ordinal(n):
    """1 -> '1st', 2 -> '2nd', 3 -> '3rd', 11 -> '11th', etc.
    Phase 6.3.3 chunk 6.6: moved here from generate_summary so league_notes
    can import without dragging in the summary script. generate_summary
    still imports from records (re-exported) for backward compat."""
    if 11 <= n % 100 <= 13:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f"{n}{suffix}"


# ---------- Sort helper for new records (display order) ----------

# STAT_DISPLAY-style ordering from the records report. Used only as the
# tiebreaker when sorting new-records for display; not exposed publicly
# since output formatters control their own labels.
_DISPLAY_ORDER = [
    'CALCULATED_POINTS', 'CALCULATED_HITTING_PTS', 'CALCULATED_PITCHING_PTS',
    'H', 'AB', 'B_BB', 'B_SO', 'HBP', 'SF',
    'HR', 'R', 'RBI', 'SB', 'CS', 'TB',
    'SINGLES', 'DOUBLES', 'TRIPLES', 'XBH',
    'W', 'L', 'K', 'ER', 'OUTS', 'QS', 'SV', 'HLD',
    'P_H', 'P_BB', 'P_HR', 'P_R', 'CG', 'BLK', 'WP',
]


def _sort_new_records(records):
    """Stable visual order for new-record display: player records first,
    then team score records, then team stat records. Within each group,
    Best before Worst. Score columns ordered Total -> Hitting -> Pitching;
    individual stats by _DISPLAY_ORDER position.
    """
    score_order = {'CALCULATED_POINTS': 0, 'CALCULATED_HITTING_PTS': 1,
                   'CALCULATED_PITCHING_PTS': 2}

    def sort_key(rec):
        if rec['grain'] == 'player':
            grain_rank = 0
        elif rec['stat_name'] in SCORE_STAT_NAMES:
            grain_rank = 1
        else:
            grain_rank = 2

        if rec['stat_name'] in SCORE_STAT_NAMES:
            stat_rank = score_order.get(rec['stat_name'], 99)
        else:
            try:
                stat_rank = _DISPLAY_ORDER.index(rec['stat_name'])
            except ValueError:
                stat_rank = 99

        direction_rank = 0 if rec['direction'] == 'most' else 1
        return (grain_rank, stat_rank, direction_rank)

    return sorted(records, key=sort_key)


# ---------- Tie-collapse (Phase 6.3.3 chunk 4) ----------
#
# Phase 6.3.3 chunk 6.6: when an overflow tier is small (<=
# INLINE_COLLAPSE_THRESHOLD members), the synthetic collapsed row keeps
# the underlying tier rows attached so the writer can comma-join their
# identities into one display row instead of just a "N tied at X"
# summary. Above the threshold, fall back to the count-only summary --
# the floor-zero NH/PG cases produce hundreds of ties per stat and
# inlining them all would be unhelpful clutter.
INLINE_COLLAPSE_THRESHOLD = 3


def collapse_ties(records, max_n=5, count_fn=None):
    """Walk leaderboard rows grouped by (entity_grain, stat_name,
    record_direction) and either pass a tier through unchanged or
    replace it with one synthetic 'N tied at value' row, depending on
    whether listing every member would push cumulative count past max_n.

    Same algorithm as format_contributors's max_n rule: a tier is kept
    in full only if its members fit under the cap; otherwise a single
    collapsed row is emitted and we stop processing the group. The cap
    target is the visible ranks count, not the underlying tie count.

    `count_fn`, when supplied, is a callable(grain, stat_name, value)
    used to backfill the synthetic row's tie_count when an overflow
    tier saturates the mart's top-10 cap (visible tier_size is then an
    undercount of the true tie cohort). In production this is
    records_data.count_value_occurrences. When count_fn is None or
    returns None (rate stats, WASTED_POINTS -- no fct counterpart),
    tie_count falls back to the visible tier_size, an honest undercount.

    Input expected to be ordered by (entity_grain, stat_name,
    record_direction, rank) -- which is what get_records_with_contributors
    produces. Records carry a 'rank' field; collapsed synthetic rows use
    the marker string 'collapsed' for that field so consumers can tell
    them apart from real-rank rows.

    Synthetic row shape:
      {
        entity_grain, stat_name, record_direction,    inherited
        rank: 'collapsed',                            marker
        is_collapsed: True,
        tie_count: N,                                 accurate via count_fn
                                                       when mart top-10 saturates
        stat_value: tied value,
        season_year, matchup_period: most recent
            occurrence in the visible tier (context),
        team_id / team_name / team_abbrev / owner_name: None
        player_id / player_name / display_name:        None
        contributors: []
      }
    """
    if not records:
        return []

    from itertools import groupby

    def group_key(r):
        return (r['entity_grain'], r['stat_name'], r['record_direction'])

    out = []
    for _, group_iter in groupby(records, key=group_key):
        group = sorted(group_iter, key=lambda r: r['rank'])
        out.extend(_collapse_one_group(group, max_n, count_fn=count_fn))
    return out


def _collapse_one_group(group, max_n, count_fn=None):
    """Apply the collapse rule to one pre-sorted (by rank asc) group of
    leaderboard rows. Returns a flat list (mix of original and at most
    one synthetic collapsed row). See collapse_ties for count_fn
    semantics."""
    out = []
    used = 0
    i = 0
    while i < len(group) and used < max_n:
        v = group[i]['stat_value']
        # Find this tier: consecutive rows with identical stat_value.
        j = i
        while j < len(group) and group[j]['stat_value'] == v:
            j += 1
        tier = group[i:j]
        tier_size = len(tier)

        if used + tier_size <= max_n:
            out.extend(tier)
            used += tier_size
            i = j
            continue

        # Tier overflows the cap. Emit one synthetic collapsed row for
        # the whole tier and stop processing this group.
        template = tier[0]
        grain = template['entity_grain']

        # If the group runs to the mart's top-10 cap, the visible tier
        # is potentially an undercount of the true tie cohort -- the
        # rest is hidden behind the rank<=10 filter. Backfill via the
        # injected count_fn (records_data.count_value_occurrences in
        # production, accurate via direct fct query). Otherwise visible
        # tier_size is the truth. count_fn None / count returns None
        # for stats with no fct counterpart (WASTED_POINTS / rate stats);
        # we fall back to visible tier_size in that case -- still an
        # undercount when saturated, but those stats rarely saturate
        # at top-10 and the alternative is a runtime crash.
        saturated = tier[-1]['rank'] >= 10
        if saturated and count_fn is not None:
            counted = count_fn(grain, template['stat_name'], v)
            tie_count = counted if counted is not None else tier_size
        else:
            tie_count = tier_size

        recent = max(tier, key=lambda r: (r['season_year'], r['matchup_period']))

        # Inline the tied identities for small tiers. Carries enough
        # per-row data for the writer to comma-join team_abbrev / season /
        # week into single cells. Above the threshold (or when the tier
        # is saturated and we don't actually have all members in memory),
        # holders=[] and the writer falls back to the legacy "N teams"
        # summary.
        if tier_size <= INLINE_COLLAPSE_THRESHOLD and not saturated:
            holders = [
                {
                    'team_id':        r.get('team_id'),
                    'team_name':      r.get('team_name'),
                    'team_abbrev':    r.get('team_abbrev'),
                    'owner_name':     r.get('owner_name'),
                    'player_id':      r.get('player_id'),
                    'player_name':    r.get('player_name'),
                    'display_name':   r.get('display_name'),
                    'season_year':    r.get('season_year'),
                    'matchup_period': r.get('matchup_period'),
                }
                for r in tier
            ]
        else:
            holders = []

        out.append({
            'entity_grain':     grain,
            'stat_name':        template['stat_name'],
            'record_direction': template['record_direction'],
            'rank':             'collapsed',
            'is_collapsed':     True,
            'tie_count':        tie_count,
            'holders':          holders,
            'stat_value':       v,
            'season_year':      recent['season_year'],
            'matchup_period':   recent['matchup_period'],
            'team_id':       None, 'team_name':    None,
            'team_abbrev':   None, 'owner_name':   None,
            'player_id':     None, 'player_name':  None,
            'display_name':  None,
            'contributors':  [],
        })
        break
    return out
