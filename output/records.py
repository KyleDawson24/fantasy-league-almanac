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

Public API:
- get_all_time_records()              -- rank-1 records for all-time scope
- get_current_season_records()        -- rank-1 records for current_season
- get_records_set_this_week(s, mp)    -- new/tied records in just-recapped MP
- get_record_top_n(stat, ...)         -- top-N rank rows for one stat
- get_team_contributors(...)          -- per-player contribution list
- get_stat_polarity()                 -- stat -> 'positive'|'negative'|'neutral'
- count_value_occurrences(...)        -- tie count for "Nth team" framing
- should_track_record(...)            -- polarity-aware filter rule
"""

import os
from dotenv import load_dotenv
import snowflake.connector

load_dotenv()

SNOWFLAKE_CONFIG = {
    "account": os.getenv("SNOWFLAKE_ACCOUNT"),
    "user": os.getenv("SNOWFLAKE_USER"),
    "password": os.getenv("SNOWFLAKE_PASSWORD"),
    "database": os.getenv("SNOWFLAKE_DATABASE"),
    "schema": "ANALYTICS",
    "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE"),
}


# Score-level stat_names in the leaderboard (calculated_*). Records section
# consumers query these specifically; the polarity filter treats them
# specially (always tracked at team grain in both directions).
SCORE_STAT_NAMES = (
    'CALCULATED_POINTS',
    'CALCULATED_HITTING_PTS',
    'CALCULATED_PITCHING_PTS',
)

# The seed (stg_scoring_settings) uses '1B' / '2B' / '3B' but the wide
# fct + leaderboard call those columns 'SINGLES' / 'DOUBLES' / 'TRIPLES'.
# Translation needed so polarity lookups land on leaderboard stat_names.
_SEED_TO_LEADERBOARD = {
    '1B': 'SINGLES',
    '2B': 'DOUBLES',
    '3B': 'TRIPLES',
}


def query_snowflake(sql, params=None):
    """Run a query and return results as a list of dicts (cols lowercased).

    Each call opens and closes its own connection. Connection-management
    consolidation is queued for Phase 7 polish; for now we accept the
    handshake cost since per-script query counts are modest (~10-20).
    """
    conn = snowflake.connector.connect(**SNOWFLAKE_CONFIG)
    cursor = conn.cursor()
    try:
        cursor.execute(sql, params or ())
        columns = [desc[0].lower() for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        cursor.close()
        conn.close()


# ---------- Bulk record fetches ----------

def get_all_time_records():
    """Rank-1 leaderboard rows for the all_time scope.

    Returns a list of dicts (raw leaderboard rows). Includes both
    entity_grain values (team, player), all stat_names the leaderboard
    tracks, and both record_directions (best, worst). Consumers key by
    (entity_grain, stat_name, record_direction) to extract what they need.

    The polarity filter is NOT applied here -- consumers filter for their
    use case (the records sections want best+worst for team scores but
    only best for player; the records report wants only 'best' direction).
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
    """, (scope,))


# ---------- Records report: top-N for one stat ----------

def get_record_top_n(stat_name, grain='team', direction='best',
                     scope='all_time', limit=10):
    """Top-N rank rows for one specific stat. Used by generate_records_report
    to render the multi-rank holders + tie tiers per stat."""
    return query_snowflake("""
        SELECT rank, season_year, matchup_period, team_id, team_name,
               owner_name, stat_value
        FROM mart_stat_leaderboard
        WHERE entity_grain = %s
          AND record_scope = %s
          AND record_direction = %s
          AND stat_name = %s
          AND rank <= %s
        ORDER BY rank
    """, (grain, scope, direction, stat_name, limit))


def get_tracked_team_stats():
    """Distinct stat_names present in the team-grain all-time best leaderboard.
    Used by the records report to discover what stats to iterate."""
    rows = query_snowflake("""
        SELECT DISTINCT stat_name
        FROM mart_stat_leaderboard
        WHERE entity_grain = 'team'
          AND record_scope = 'all_time'
          AND record_direction = 'best'
    """)
    return [r['stat_name'] for r in rows]


# ---------- Contributors ----------

def get_team_contributors(season_year, matchup_period, team_id, stat_column):
    """Per-player contribution to a specific stat for one team in one matchup.

    `stat_column` is interpolated directly into SQL. Safe ONLY because it
    comes from our own leaderboard's stat_name (an enumerated set of
    column names), NOT user input.
    """
    return query_snowflake(f"""
        SELECT display_name, {stat_column} AS stat_value
        FROM fct_weekly_player_performance
        WHERE season_year = %s
          AND matchup_period = %s
          AND team_id = %s
        ORDER BY {stat_column} DESC NULLS LAST
    """, (season_year, matchup_period, team_id))


# ---------- Polarity + filter rule ----------

def get_stat_polarity():
    """Map of leaderboard stat_name -> 'positive' | 'negative' | 'neutral'.

    Derived from sign of points_per_unit in stg_scoring_settings. Stats
    without a row in the seed are 'neutral' (zero-weighted).

    Translates seed names ('1B', '2B', '3B') to leaderboard column names
    ('SINGLES', 'DOUBLES', 'TRIPLES') so consumer lookups land correctly.
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


def should_track_record(grain, stat_name, direction, polarity):
    """Phase 5 polarity-aware filter rules:
      - Player grain: only score-level stats, only 'best' direction
      - Team grain, score columns: both directions
      - Team grain, positive individual stat: both directions
      - Team grain, negative individual stat: 'best' (most-of) only
      - Zero-weighted (neutral) stats: skipped entirely
    """
    if grain == 'player':
        return stat_name in SCORE_STAT_NAMES and direction == 'best'
    # team grain
    if stat_name in SCORE_STAT_NAMES:
        return True
    pol = polarity.get(stat_name)
    if pol is None or pol == 'neutral':
        return False
    if pol == 'positive':
        return True
    return direction == 'best'  # negative-stat: most-of only


# ---------- New-record detection ----------

def get_records_set_this_week(season_year, matchup_period):
    """Records broken or tied in this matchup_period. Returns list of dicts:
      {
        'grain': 'team' | 'player',
        'stat_name': uppercase leaderboard name,
        'direction': 'best' | 'worst',
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

    candidates = query_snowflake("""
        SELECT *
        FROM mart_stat_leaderboard
        WHERE rank = 1
          AND record_scope = 'all_time'
          AND season_year = %s
          AND matchup_period = %s
    """, (season_year, matchup_period))

    out = []
    for cand in candidates:
        grain = cand['entity_grain']
        stat = cand['stat_name']
        direction = cand['record_direction']
        if not should_track_record(grain, stat, direction, polarity):
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


def count_value_occurrences(grain, stat_name, value):
    """How many (entity, MP) tuples in fct_weekly_*_performance have this
    exact stat_value, excluding abnormal weeks. Used for 'Nth team/player'
    framing on tied records.
    """
    fct = ('fct_weekly_team_performance' if grain == 'team'
           else 'fct_weekly_player_performance')
    col = stat_name.lower()
    rows = query_snowflake(f"""
        SELECT COUNT(*) AS n
        FROM {fct} f
        JOIN matchup_schedule s
          ON f.season_year = s.season_year
         AND f.matchup_period = s.matchup_period
        WHERE s.is_abnormal = false
          AND {col} = %s
    """, (value,))
    return rows[0]['n'] if rows else 0


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

        direction_rank = 0 if rec['direction'] == 'best' else 1
        return (grain_rank, stat_rank, direction_rank)

    return sorted(records, key=sort_key)
