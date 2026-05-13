"""
output/records.py

Pure data-access functions for league records.

Both consumer scripts (generate_summary.py, generate_records_report.py)
read from here so the SQL and polarity-filter logic live in one place.
No formatting or display decisions in this module — those stay with the
consumers.

Phase 6.2 extraction: previously this code lived split across the two
consumer scripts. Team records used a direct fct_weekly_team_performance
query with Python max/min; player records used the leaderboard. New-record
detection had its own leaderboard reads + filter logic. This module
unifies all data access on mart_stat_leaderboard as the single source of
truth (the migrate-team-records-to-leaderboard backlog item is folded in).

Phase 6.3.3 chunk 3: bulk contributor fetches + leaderboard-dump
orchestrator. The records sections and Sheets Tab 3 (Leaderboard Dump)
need contributor lists for many records per script run; the bulk helpers
issue a single batched query rather than N round-trips.

Public API:
- get_all_time_records()              -- rank-1 records for all-time scope
- get_current_season_records()        -- rank-1 records for current_season
- get_records_set_this_week(s, mp)    -- new/tied records in just-recapped MP
- get_record_top_n(stat, ...)         -- top-N rank rows for one stat
- get_team_contributors(...)          -- per-player contribution list
- get_team_contributors_bulk(...)     -- batched variant (Phase 6.3.3)
- get_player_contributors_bulk(...)   -- top-N stats per player (Phase 6.3.3)
- get_records_with_contributors(...)  -- leaderboard-dump orchestrator
- load_schedule_lookup()              -- (season, mp) -> playoff metadata
- format_week_label(s, mp, lookup)    -- "Week N" or playoff round name
- get_stat_polarity()                 -- stat -> 'positive'|'negative'|'neutral'
- count_value_occurrences(...)        -- tie count for "Nth team" framing
- should_track_record(...)            -- polarity-aware filter rule
"""

from db import query_snowflake  # re-exported for league_notes.py et al


# Score-level stat_names in the leaderboard (calculated_*). Records section
# consumers query these specifically; the polarity filter treats them
# specially (always tracked at team grain in both directions).
SCORE_STAT_NAMES = (
    'CALCULATED_POINTS',
    'CALCULATED_HITTING_PTS',
    'CALCULATED_PITCHING_PTS',
)

# The seed (stg_scoring_settings, stat_classification) uses '1B' / '2B'
# / '3B' / '64' but the wide fct + leaderboard call those columns
# 'SINGLES' / 'DOUBLES' / 'TRIPLES' / 'SHO'. Translation needed so seed-
# driven lookups (polarity, always-tracked) land on leaderboard
# stat_names.
#
# Phase 6.3.3 chunk 4.5: '64' -> 'SHO' added. The seed kept stat ID 64 as
# the literal numeric stat_name (espn-api wrapper doesn't translate it),
# but chunk 1 aliased the leaderboard column to 'SHO'. Without this
# translation, polarity lookups for SHO returned None even though the
# league scores shutouts (5 pts).
_SEED_TO_LEADERBOARD = {
    '1B': 'SINGLES',
    '2B': 'DOUBLES',
    '3B': 'TRIPLES',
    '64': 'SHO',
}


# ---------- Bulk record fetches ----------

def get_all_time_records():
    """Rank-1 leaderboard rows for the all_time scope.

    Returns a list of dicts (raw leaderboard rows). Includes both
    entity_grain values (team, player), all stat_names the leaderboard
    tracks, and both record_directions (most, fewest). Consumers key by
    (entity_grain, stat_name, record_direction) to extract what they need.

    The polarity filter is NOT applied here -- consumers filter for their
    use case (the records sections want most+fewest for team scores but
    only most for player; the records report wants only 'most' direction).
    """
    return _fetch_rank1_records('all_time')


def get_current_season_records():
    """Rank-1 leaderboard rows for the current_season scope. Same shape
    as get_all_time_records()."""
    return _fetch_rank1_records('current_season')


def _fetch_rank1_records(scope):
    return query_snowflake("""
        SELECT entity_grain, stat_name, record_direction,
               team_id, team_name, team_abbrev, owner_name,
               player_id, player_name, display_name,
               season_year, matchup_period, stat_value
        FROM mart_stat_leaderboard
        WHERE record_scope = %s
          AND rank = 1
          AND performance_status = 'active'
    """, (scope,))


# ---------- Records report: top-N for one stat ----------

def get_record_top_n(stat_name, grain='team', direction='most',
                     scope='all_time', limit=5):
    """Top-N rank rows for one specific stat. Used by generate_records_report
    to render the multi-rank holders + tie tiers per stat.

    Phase 6.3.3: direction values are 'most' / 'fewest' (renamed from
    'best' / 'worst' at the mart layer). Default limit dropped to 5 to
    match the mart's new top-5 cap.
    """
    return query_snowflake("""
        SELECT rank, season_year, matchup_period, team_id, team_name,
               owner_name, stat_value
        FROM mart_stat_leaderboard
        WHERE entity_grain = %s
          AND record_scope = %s
          AND record_direction = %s
          AND stat_name = %s
          AND rank <= %s
          AND performance_status = 'active'
        ORDER BY rank
    """, (grain, scope, direction, stat_name, limit))


def get_tracked_team_stats():
    """Distinct stat_names present in the team-grain all-time 'most' leaderboard.
    Used by the records report to discover what stats to iterate."""
    rows = query_snowflake("""
        SELECT DISTINCT stat_name
        FROM mart_stat_leaderboard
        WHERE entity_grain = 'team'
          AND record_scope = 'all_time'
          AND record_direction = 'most'
          AND performance_status = 'active'
    """)
    return [r['stat_name'] for r in rows]


# ---------- Contributors ----------

def get_team_contributors(season_year, matchup_period, team_id, stat_column):
    """Per-player contribution to a specific stat for one team in one matchup.

    `stat_column` is interpolated directly into SQL. Safe ONLY because it
    comes from our own leaderboard's stat_name (an enumerated set of
    column names), NOT user input.
    """
    # Phase 7 D2: display_name added as tiebreak token. Without it, the
    # row order for tied stat_value entries is whatever Snowflake's scan
    # returns -- which flips when the fct gets re-materialized (e.g. on
    # --full-refresh). Same flake mode as the mart's ROW_NUMBER fix in
    # B1; deterministic by display_name keeps golden output stable AND
    # reads as "alphabetical within a tie" to a human.
    return query_snowflake(f"""
        SELECT display_name, {stat_column} AS stat_value
        FROM fct_weekly_player_active_performance
        WHERE season_year = %s
          AND matchup_period = %s
          AND team_id = %s
        ORDER BY {stat_column} DESC NULLS LAST, display_name
    """, (season_year, matchup_period, team_id))


# ---------- Polarity + filter rule ----------

def get_stat_polarity():
    """Map of leaderboard stat_name -> 'positive' | 'negative' | 'neutral'.

    Derived from sign of points_per_unit in stg_scoring_settings. Stats
    without a row in the seed are 'neutral' (zero-weighted).

    Translates seed names ('1B', '2B', '3B', '64') to leaderboard column
    names ('SINGLES', 'DOUBLES', 'TRIPLES', 'SHO') so consumer lookups
    land correctly.
    """
    rows = query_snowflake("""
        SELECT UPPER(stat_name) AS stat_name, points_per_unit
        FROM stg_scoring_settings
    """)
    polarity = {}
    for r in rows:
        name = _SEED_TO_LEADERBOARD.get(r['stat_name'], r['stat_name'])
        ppu = r['points_per_unit'] or 0
        if ppu > 0:
            polarity[name] = 'positive'
        elif ppu < 0:
            polarity[name] = 'negative'
        else:
            polarity[name] = 'neutral'
    return polarity


# Phase 6.3.3 chunk 6.5: implicit polarity for stats not present in
# stg_scoring_settings (the league doesn't directly score them) but that
# still surface as records via always_tracked, non-seed (rate stats /
# wasted_points), or derived (PA / SB-CS / W-L / SV-BLSV) paths. Used by
# get_effective_polarity() to fill in the gaps left by get_stat_polarity()
# so direction labels (Best/Worst) can be polarity-aware everywhere.
#
# The existing always_tracked seed flag covers WHICH stats surface as
# records; this map covers HOW their direction maps to good/bad outcome.
# Hitting "always_tracked" stats (H/TB/XBH/SF) are positive (more = good).
# Pitching "always_tracked" stats need more nuance: ER is the lone clear
# negative (more ER allowed = worse pitching). Rate stats split by
# convention: ERA/WHIP/HR-BB-allowed-per-9 negative; K/9, K/BB positive.
# WASTED_POINTS: more = worse roster decisions. Score stats: positive.
_IMPLICIT_POLARITY = {
    # Always-tracked counting stats not in scoring_settings (per is_always_tracked seed flag).
    'H':       'positive',
    'TB':      'positive',
    'XBH':     'positive',
    'SF':      'positive',
    'ER':      'negative',
    # Phase 6.3.3 chunk 2: rate stats (team-grain only)
    'ERA':      'negative',
    'WHIP':     'negative',
    'HR_PER_9': 'negative',
    'BB_PER_9': 'negative',
    'K_PER_9':  'positive',
    'K_PER_BB': 'positive',
    # Phase 6.3.3 chunk 2: derived counting stats
    'PA':       'positive',
    'SB_CS':    'positive',
    'W_L':      'positive',
    'SV_BLSV':  'positive',
    # Phase 6.3.3 chunk 2: roster waste (more = worse)
    'WASTED_POINTS': 'negative',
    # Score stats (also positive); explicit so neutral-from-seed doesn't bite
    'CALCULATED_POINTS':       'positive',
    'CALCULATED_HITTING_PTS':  'positive',
    'CALCULATED_PITCHING_PTS': 'positive',
    'PLATFORM_POINTS':         'positive',
    'PLATFORM_HITTING_PTS':    'positive',
    'PLATFORM_PITCHING_PTS':   'positive',
}


def get_effective_polarity():
    """Phase 6.3.3 chunk 6.5: stat -> 'positive' | 'negative' map that
    fills the gaps in get_stat_polarity() with _IMPLICIT_POLARITY.

    Stats present in stg_scoring_settings keep their seed-derived polarity
    (sign of points_per_unit). Stats marked 'neutral' or absent get
    overwritten from _IMPLICIT_POLARITY. Stats unknown to both default to
    'positive' (more = better) -- the more common case.
    """
    polarity = get_stat_polarity()
    for stat, pol in _IMPLICIT_POLARITY.items():
        if polarity.get(stat) in (None, 'neutral'):
            polarity[stat] = pol
    return polarity


def best_or_worst_label(stat_name, direction, effective_polarity):
    """Phase 6.3.3 chunk 6.5: uniform Best/Worst label, polarity-aware.

    For positive stats (HR, K, calculated_points, etc.):
      direction='most'   -> 'Best'  (more = better outcome)
      direction='fewest' -> 'Worst'

    For negative stats (ER, WHIP, GDP, wasted_points, etc.):
      direction='most'   -> 'Worst' (more = worse outcome)
      direction='fewest' -> 'Best'

    Unknown stats default to positive (most='Best'). The caller pre-builds
    the effective_polarity dict (via get_effective_polarity()) once per
    script run and passes it in; this fn does an O(1) lookup per row.

    Returns either the Best/Worst label or 'Best'/'Worst' as a string.
    """
    pol = effective_polarity.get(stat_name, 'positive')
    if pol == 'negative':
        return 'Best' if direction == 'fewest' else 'Worst'
    return 'Best' if direction == 'most' else 'Worst'


def get_always_tracked_stats():
    """Phase 6.3.3 chunk 4.5: stats flagged is_always_tracked=true in the
    stat_classification seed. These bypass the polarity filter at team
    grain (both directions surface as records) regardless of whether
    the league actually scores them.

    Use case: stats like H / TB / XBH / SF / ER that aren't scored
    directly but are universal counting events where 'Most X in a week'
    is a real, interesting record.

    Returns a frozenset of leaderboard stat_names (after seed -> leaderboard
    name translation). The seed is the source of truth -- adjust the
    flag in stat_classification.csv and reseed to add/remove members
    without code changes.
    """
    rows = query_snowflake("""
        SELECT UPPER(stat_name) AS stat_name
        FROM stat_classification
        WHERE is_always_tracked = TRUE
    """)
    return frozenset(
        _SEED_TO_LEADERBOARD.get(r['stat_name'], r['stat_name'])
        for r in rows
    )


def should_track_record(grain, stat_name, direction, polarity, always_tracked=None):
    """Phase 5 polarity-aware filter rules (direction values per Phase
    6.3.3 are 'most' / 'fewest'):
      - Player grain: only score-level stats; both directions surface
        (Phase 6.3.3 chunk 4.5: previously 'most' only -- the handoff
        doc's locked decision was to extend; enacted here)
      - Team grain, score columns: both directions
      - Team grain, always-tracked stat (per stat_classification seed):
        both directions, regardless of polarity (Phase 6.3.3 chunk 4.5)
      - Team grain, positive individual stat: both directions
      - Team grain, negative individual stat: 'most' (most-of-bad) only
      - Zero-weighted (neutral) stats: skipped entirely

    `always_tracked` defaults to None for backward-compat with callers
    that don't load the seed-driven set yet. Pass the result of
    get_always_tracked_stats() to enable the always-tracked override.
    """
    if grain == 'player':
        return stat_name in SCORE_STAT_NAMES
    # team grain
    if stat_name in SCORE_STAT_NAMES:
        return True
    if always_tracked and stat_name in always_tracked:
        return True
    pol = polarity.get(stat_name)
    if pol is None or pol == 'neutral':
        return False
    if pol == 'positive':
        return True
    return direction == 'most'  # negative-stat: most-of only


# ---------- New-record detection ----------

def get_records_set_this_week(season_year, matchup_period):
    """Records broken or tied in this matchup_period. Returns list of dicts:
      {
        'grain': 'team' | 'player',
        'stat_name': uppercase leaderboard name,
        'direction': 'most' | 'fewest',
        'new': leaderboard row holding rank 1,
        'prior': leaderboard row at rank 2 (None if no rank-2 row OR if tied),
        'is_tie': bool (True when rank-2 stat_value equals rank-1),
        'tie_count': int (only present when is_tie; total entities ever at this value),
      }
    Polarity filter applied. Floor-noise filter: tied records at value=0
    for individual stats (HBP, QS, SV, HLD, CG, etc. perfect-zero ties)
    are skipped.
    """
    polarity = get_stat_polarity()
    always_tracked = get_always_tracked_stats()

    candidates = query_snowflake("""
        SELECT *
        FROM mart_stat_leaderboard
        WHERE rank = 1
          AND record_scope = 'all_time'
          AND season_year = %s
          AND matchup_period = %s
          AND performance_status = 'active'
    """, (season_year, matchup_period))

    out = []
    for cand in candidates:
        grain = cand['entity_grain']
        stat = cand['stat_name']
        direction = cand['record_direction']
        if not should_track_record(grain, stat, direction, polarity, always_tracked):
            continue

        # Rank 2 = prior holder. With recency tiebreak, also tells us
        # whether we tied (rank-2 stat_value == rank-1).
        prior_rows = query_snowflake("""
            SELECT *
            FROM mart_stat_leaderboard
            WHERE entity_grain = %s
              AND stat_name = %s
              AND record_scope = 'all_time'
              AND record_direction = %s
              AND rank = 2
              AND performance_status = 'active'
        """, (grain, stat, direction))
        prior = prior_rows[0] if prior_rows else None

        is_tie = prior is not None and prior['stat_value'] == cand['stat_value']

        # Floor-noise filter: tied at value=0 for individual stats
        # (CG/HLD/SV/QS perfect-zero ties happen weekly with N in the
        # hundreds). Strict breaks at 0 can't happen for these (count
        # can't go negative), so this only affects ties.
        if is_tie and stat not in SCORE_STAT_NAMES and cand['stat_value'] == 0:
            continue

        rec = {
            'grain': grain,
            'stat_name': stat,
            'direction': direction,
            'new': cand,
            'prior': prior if not is_tie else None,
            'is_tie': is_tie,
        }
        if is_tie:
            rec['tie_count'] = count_value_occurrences(grain, stat, cand['stat_value'])
        out.append(rec)

    return _sort_new_records(out)


# Phase 6.3.3 chunk 2 derived counting stats live only in
# mart_stat_leaderboard, not on the fcts. count_value_occurrences
# computes them inline at the fct-row level when asked about one.
_DERIVED_STAT_FCT_EXPR = {
    'PA':      'ab + b_bb + hbp + sf',
    'SB_CS':   'sb - cs',
    'W_L':     'w - l',
    'SV_BLSV': 'sv - blsv',
}

# Stats with no fct-layer counterpart (or where exact-equality COUNT
# isn't reliable). count_value_occurrences returns None for these and
# callers fall back to whatever visible-row count they have.
_NON_FCT_COUNTABLE = frozenset({
    'WASTED_POINTS',                      # lives in mart_wasted_points
    'ERA', 'WHIP', 'K_PER_9', 'K_PER_BB', # floats: exact-equality
    'HR_PER_9', 'BB_PER_9',               # tie counts unreliable
})


def count_value_occurrences(grain, stat_name, value):
    """How many (entity, MP) tuples in fct_weekly_*_performance have this
    exact stat_value, excluding abnormal weeks. Used for 'Nth team/player'
    framing on tied records.

    Returns None if the stat has no fct-layer counterpart or its
    underlying type makes exact-equality COUNT unreliable (rate stats,
    WASTED_POINTS). Callers should fall back to visible tier size in
    that case.
    """
    return league_history_count(grain, stat_name, value, op='=')


# Phase 6.3.3 chunk 6.6: callouts in output/league_notes.py need to ask
# "how many times has stat_name compared with value over league history"
# to phrase records like "the Nth no-hitter ever" or "the 47th-49th
# zero-steal team-week". league_history_count is the generalization of
# count_value_occurrences (which now delegates here).
_VALID_HISTORY_OPS = frozenset({'=', '!=', '<', '<=', '>', '>='})


def league_history_count(grain, stat_name, value, op='='):
    """Count entity-weeks where stat_name's value compares with `value`
    using `op`, excluding abnormal weeks.

    grain:     'team' or 'player'
    stat_name: leaderboard stat_name (uppercase). Derived stats and
               column-name keys both work via _DERIVED_STAT_FCT_EXPR.
    value:     numeric threshold for the comparison
    op:        one of '=', '!=', '<', '<=', '>', '>='. Default '='.

    Returns int (count). Returns None for stats with no fct counterpart
    (rate stats, WASTED_POINTS). Callers should treat None as "I don't
    know" and not assume zero.
    """
    if op not in _VALID_HISTORY_OPS:
        raise ValueError(f"op must be one of {_VALID_HISTORY_OPS}, got {op!r}")
    if stat_name in _NON_FCT_COUNTABLE:
        return None
    fct = ('fct_weekly_team_performance' if grain == 'team'
           else 'fct_weekly_player_active_performance')
    col_expr = _DERIVED_STAT_FCT_EXPR.get(stat_name, stat_name.lower())
    rows = query_snowflake(f"""
        SELECT COUNT(*) AS n
        FROM {fct} f
        JOIN matchup_schedule s
          ON f.season_year = s.season_year
         AND f.matchup_period = s.matchup_period
        WHERE s.is_abnormal = false
          AND ({col_expr}) {op} %s
    """, (value,))
    return rows[0]['n'] if rows else 0


def ordinal(n):
    """1 -> '1st', 2 -> '2nd', 3 -> '3rd', 11 -> '11th', etc.
    Phase 6.3.3 chunk 6.6: moved here from generate_summary so league_notes
    can import without dragging in the summary script. generate_summary
    still imports from here for backward compat."""
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


# ============================================================
# Phase 6.3.3 chunk 3: bulk contributors + leaderboard dump
# ============================================================
#
# The records sections + Sheets Tab 3 need contributor lists for many
# (record × stat × team-or-player) tuples per script run. Bulk helpers
# below dedupe inputs, issue ONE batched SELECT, and slice in Python
# instead of N round-trips.
#
# Threshold filtering is intentionally absent: chunk 3 dropped player-
# grain rate stats from mart_stat_leaderboard entirely (Path A), so
# consumers don't need to gate small-sample noise. Team-grain rate stats
# stay in the mart -- a team's natural denominator accumulation keeps
# them meaningful without explicit thresholds.


# Stats that don't sit in the scoring-settings seed but are meaningful
# at team grain. Includes:
#  - rate stats: ERA / WHIP / K/9 / K/BB / HR/9 / BB/9
#  - WASTED_POINTS (from mart_wasted_points; team-only by construction)
#  - derived counting stats: PA / SB-CS / W-L / SV-BLSV
# These have no points_per_unit row in stg_scoring_settings, so
# get_stat_polarity() returns None for them and should_track_record()
# filters them out. The orchestrator below extends the filter rule to
# allow these at team grain in BOTH directions, while leaving
# should_track_record's existing rules untouched (so existing flows
# -- BBCode summary records section, Sheets tabs 1/2 -- behave the
# same as Phase 6.2).
_TEAM_NON_SEED_STATS = frozenset({
    'ERA', 'WHIP', 'K_PER_9', 'K_PER_BB', 'HR_PER_9', 'BB_PER_9',
    'WASTED_POINTS',
    'PA', 'SB_CS', 'W_L', 'SV_BLSV',
})

# Stats with no per-player breakdown story. Team-level rates aren't
# additive across players; WASTED_POINTS lives in a separate mart at
# different grain. get_team_contributors_bulk returns [] for these.
_NO_PLAYER_BREAKDOWN_STATS = frozenset({
    'ERA', 'WHIP', 'K_PER_9', 'K_PER_BB', 'HR_PER_9', 'BB_PER_9',
    'WASTED_POINTS',
})

# Leaderboard stat_name -> uppercase column-key list whose *_pts is the
# point contribution for player-grain top-N. Mirrors the wide *_pts
# columns on fct_weekly_player_active_performance. Hitter + pitcher pools both
# included since player records are typically score-level (calculated_*)
# where contributions can come from either side.
_PLAYER_CONTRIB_STATS = (
    # Hitting
    'H', 'AB', 'B_BB', 'B_SO', 'HBP', 'SF',
    'HR', 'R', 'RBI', 'SB', 'CS', 'TB',
    'SINGLES', 'DOUBLES', 'TRIPLES', 'XBH',
    'GDP', 'B_IBB',
    # Pitching
    'W', 'L', 'K', 'ER', 'OUTS', 'QS', 'SV', 'HLD',
    'P_H', 'P_BB', 'P_HR', 'P_R', 'CG', 'BLK', 'WP',
    'HBP_P', 'BLSV', 'NH', 'PG', 'PK', 'SHO',
)


def load_schedule_lookup():
    """Build a (season_year, matchup_period) -> {is_playoff, playoff_round}
    dict from matchup_schedule. One query per script run; pass the result
    to format_week_label() instead of re-querying for each record.
    """
    rows = query_snowflake("""
        SELECT season_year, matchup_period, is_playoff, playoff_round
        FROM matchup_schedule
    """)
    return {
        (r['season_year'], r['matchup_period']): {
            'is_playoff':    bool(r['is_playoff']),
            'playoff_round': r['playoff_round'],
        }
        for r in rows
    }


def format_week_label(season_year, matchup_period, schedule_lookup):
    """'Week N' for regular weeks, the playoff round name (e.g. 'Round 1',
    'Semi-Finals', 'Finals') for playoff weeks. Falls back to 'Week N' if
    the (season, mp) is missing -- defensive only; matchup_schedule is
    canonical for the current season."""
    info = schedule_lookup.get((season_year, matchup_period))
    if info and info.get('is_playoff') and info.get('playoff_round'):
        return info['playoff_round']
    return f"Week {matchup_period}"


def _player_stat_value(row, stat_name):
    """Compute the player's value for a leaderboard stat_name from their
    fct_weekly_player_active_performance row. Most stats are direct columns
    (lowercased); derived counting stats are inline expressions. Returns
    None for stats with no per-player contribution story."""
    if stat_name in _NO_PLAYER_BREAKDOWN_STATS:
        return None
    if stat_name == 'PA':
        return ((row.get('ab')  or 0) + (row.get('b_bb') or 0)
                + (row.get('hbp') or 0) + (row.get('sf')  or 0))
    if stat_name == 'SB_CS':
        return (row.get('sb') or 0) - (row.get('cs')   or 0)
    if stat_name == 'W_L':
        return (row.get('w')  or 0) - (row.get('l')    or 0)
    if stat_name == 'SV_BLSV':
        return (row.get('sv') or 0) - (row.get('blsv') or 0)
    return row.get(stat_name.lower()) or 0


def get_team_contributors_bulk(tuples, top_n=3):
    """Top-N player contributors per (season, mp, team_id, stat_name).

    Single batched fetch from fct_weekly_player_active_performance keyed by the
    distinct team-week tuples; ranking happens in Python. Stats with no
    meaningful per-player breakdown (rate stats, WASTED_POINTS) return
    [] for those input tuples.

    Returns dict[(season, mp, team_id, stat_name)] -> list of
    {display_name, stat_value} (top N by stat value descending,
    zero/None values dropped).
    """
    tuples = list(tuples)
    if not tuples:
        return {}

    team_weeks = sorted({(s, mp, tid) for (s, mp, tid, _) in tuples})
    placeholders = ", ".join(["(%s, %s, %s)"] * len(team_weeks))
    params = [v for tw in team_weeks for v in tw]
    rows = query_snowflake(f"""
        SELECT *
        FROM fct_weekly_player_active_performance
        WHERE (season_year, matchup_period, team_id) IN ({placeholders})
    """, params)

    by_team_week = {}
    for r in rows:
        key = (r['season_year'], r['matchup_period'], r['team_id'])
        by_team_week.setdefault(key, []).append(r)

    out = {}
    for (s, mp, tid, stat) in tuples:
        if stat in _NO_PLAYER_BREAKDOWN_STATS:
            out[(s, mp, tid, stat)] = []
            continue
        scored = []
        for r in by_team_week.get((s, mp, tid), []):
            v = _player_stat_value(r, stat)
            if v is None or v == 0:
                continue
            scored.append({'display_name': r['display_name'], 'stat_value': v})
        # Phase 7 D2: display_name as secondary key keeps tied stat_value
        # entries in deterministic order (alphabetical), same fix pattern
        # as the SQL contributor query above.
        scored.sort(key=lambda x: (-x['stat_value'], x['display_name']))
        out[(s, mp, tid, stat)] = scored[:top_n]
    return out


def get_player_contributors_bulk(tuples, top_n=3, positives_only=True):
    """Top-N stats by point contribution for each (season, mp, player_id).

    For player-grain leaderboard rows (typically score-level records), we
    surface what the player did to earn that score. Counts (not points)
    are the surfaced numbers per the locked v1 spec -- league members
    who know the weights can mentally convert.

    `positives_only=True` (default): rank by signed point_value desc,
    drop pts <= 0. Right for celebratory 'most' direction records.
    `positives_only=False`: rank by abs(point_value) desc. Right when
    surfacing 'fewest' / negative-impact records (not used in the v1
    pipeline since player-grain 'fewest' is filtered out, but the API
    is parameterized in case the rule loosens later).

    Returns dict[(season, mp, player_id)] -> list of
    {stat_name, count_value, point_value} (top N).
    """
    tuples = list(tuples)
    if not tuples:
        return {}

    unique = sorted({t for t in tuples})
    placeholders = ", ".join(["(%s, %s, %s)"] * len(unique))
    params = [v for t in unique for v in t]
    rows = query_snowflake(f"""
        SELECT *
        FROM fct_weekly_player_active_performance
        WHERE (season_year, matchup_period, player_id) IN ({placeholders})
    """, params)

    by_player = {(r['season_year'], r['matchup_period'], r['player_id']): r
                 for r in rows}

    out = {}
    for tup in tuples:
        row = by_player.get(tup)
        if row is None:
            out[tup] = []
            continue
        candidates = []
        for stat in _PLAYER_CONTRIB_STATS:
            col = stat.lower()
            count = row.get(col) or 0
            pts   = row.get(f'{col}_pts') or 0
            if pts == 0:
                continue
            if positives_only and pts <= 0:
                continue
            candidates.append({
                'stat_name':   stat,
                'count_value': count,
                'point_value': pts,
            })
        # Phase 7 D2: stat_name as secondary key keeps tied point_value
        # entries deterministic (alphabetical).
        if positives_only:
            candidates.sort(key=lambda c: (-c['point_value'], c['stat_name']))
        else:
            candidates.sort(key=lambda c: (-abs(c['point_value']), c['stat_name']))
        out[tup] = candidates[:top_n]
    return out


def _orchestrator_filter(grain, stat_name, direction, polarity, always_tracked):
    """Phase 6.3.3 chunk 3 layered filter for the leaderboard-dump
    orchestrator:
      - All should_track_record-passing rows pass (which now consults
        the always_tracked seed-driven set).
      - Plus team-grain stats in _TEAM_NON_SEED_STATS (rate stats /
        WASTED_POINTS / derived counts) in BOTH directions, since these
        carry no scoring-settings polarity but are meaningful at team
        grain (and aren't in the seed).
    """
    if should_track_record(grain, stat_name, direction, polarity, always_tracked):
        return True
    return grain == 'team' and stat_name in _TEAM_NON_SEED_STATS


# Mart caps each (grain, stat, direction) at this many ranks. The
# orchestrator pulls the full buffer (not just top_n) so collapse_ties
# can see whether a tier extends past the display cap and call
# count_value_occurrences() for accurate "N teams tied" totals.
_MART_TOP_N_BUFFER = 10


def get_records_with_contributors(scope, top_n=5):
    """Leaderboard-dump orchestrator. Pulls the mart's top-10 buffer per
    (entity_grain, stat_name, record_direction) for the given scope,
    applies the chunk-3 layered filter, runs tie-collapse trimmed to
    `top_n` display rows, then attaches contributor lists via the bulk
    helpers (one batched SELECT per grain). Contributors are fetched
    AFTER collapse so we don't pay round-trip cost for rows the collapse
    pass discards.

    `scope`: 'all_time' or 'current_season'.
    `top_n`: display cap per partition. Default 5 -- the mart's top-10
    cap is the visibility buffer so chunk-4 collapse detection can see
    tier extension past the display cap.

    Returns a list of dicts. Real leaderboard rows are annotated with a
    'contributors' key (team-grain: list of {display_name, stat_value};
    player-grain: list of {stat_name, count_value, point_value}).
    Synthetic collapsed rows carry 'is_collapsed': True with cleared
    identity fields and an empty contributors list.
    """
    polarity = get_stat_polarity()
    always_tracked = get_always_tracked_stats()

    rows = query_snowflake("""
        SELECT entity_grain, stat_name, record_direction, rank,
               season_year, matchup_period,
               team_id, team_name, team_abbrev, owner_name,
               player_id, player_name, display_name, stat_value
        FROM mart_stat_leaderboard
        WHERE record_scope = %s
          AND rank <= %s
          AND performance_status = 'active'
        ORDER BY entity_grain, stat_name, record_direction, rank
    """, (scope, _MART_TOP_N_BUFFER))

    surviving = [
        r for r in rows
        if _orchestrator_filter(
            r['entity_grain'], r['stat_name'],
            r['record_direction'], polarity, always_tracked,
        )
    ]

    # Phase 6.3.3 chunk 4: collapse first, contributor-stitch second.
    collapsed = collapse_ties(surviving, max_n=top_n)

    real_rows = [r for r in collapsed if not r.get('is_collapsed')]
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

    team_contribs   = get_team_contributors_bulk(team_tuples)     if team_tuples   else {}
    player_contribs = get_player_contributors_bulk(player_tuples) if player_tuples else {}

    for r in collapsed:
        if r.get('is_collapsed'):
            r['contributors'] = []
        elif r['entity_grain'] == 'team':
            key = (r['season_year'], r['matchup_period'],
                   r['team_id'], r['stat_name'])
            r['contributors'] = team_contribs.get(key, [])
        else:
            key = (r['season_year'], r['matchup_period'], r['player_id'])
            r['contributors'] = player_contribs.get(key, [])

    return collapsed


# ---------- Tie-collapse (Phase 6.3.3 chunk 4) ----------

def collapse_ties(records, max_n=5):
    """Walk leaderboard rows grouped by (entity_grain, stat_name,
    record_direction) and either pass a tier through unchanged or
    replace it with one synthetic 'N tied at value' row, depending on
    whether listing every member would push cumulative count past max_n.

    Same algorithm as format_contributors's max_n rule: a tier is kept
    in full only if its members fit under the cap; otherwise a single
    collapsed row is emitted and we stop processing the group. The cap
    target is the visible ranks count, not the underlying tie count.

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
        tie_count: N,                                 accurate via
                                                       count_value_occurrences
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
        out.extend(_collapse_one_group(group, max_n))
    return out


# Phase 6.3.3 chunk 6.6: when an overflow tier is small (<=
# INLINE_COLLAPSE_THRESHOLD members), the synthetic collapsed row keeps
# the underlying tier rows attached so the writer can comma-join their
# identities into one display row instead of just a "N tied at X"
# summary. Above the threshold, fall back to the count-only summary --
# the floor-zero NH/PG cases produce hundreds of ties per stat and
# inlining them all would be unhelpful clutter.
INLINE_COLLAPSE_THRESHOLD = 3


def _collapse_one_group(group, max_n):
    """Apply the chunk 4 rule to one pre-sorted (by rank asc) group of
    leaderboard rows. Returns a flat list (mix of original and at most
    one synthetic collapsed row)."""
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
        # rest is hidden behind the rank<=10 filter. Backfill via
        # count_value_occurrences (queries fct directly, accurate).
        # Otherwise visible tier_size is the truth. count returns None
        # for stats with no fct counterpart (WASTED_POINTS / rate stats);
        # we fall back to visible tier_size in that case -- still an
        # undercount when saturated, but those stats rarely saturate
        # at top-10 and the alternative is a runtime crash.
        saturated = tier[-1]['rank'] >= 10
        if saturated:
            counted = count_value_occurrences(grain, template['stat_name'], v)
            tie_count = counted if counted is not None else tier_size
        else:
            tie_count = tier_size

        recent = max(tier, key=lambda r: (r['season_year'], r['matchup_period']))

        # Phase 6.3.3 chunk 6.6: inline the tied identities for small
        # tiers. Carries enough per-row data for the writer to comma-join
        # team_abbrev / season / week into single cells. Above the
        # threshold (or when the tier is saturated and we don't actually
        # have all members in memory), holders=[] and the writer falls
        # back to the legacy "N teams" summary.
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
