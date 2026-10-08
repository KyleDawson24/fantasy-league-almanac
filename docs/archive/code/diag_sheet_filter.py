"""Diagnose why certain stats / directions aren't appearing in the sheets."""
import sys
sys.path.insert(0, 'output')
import records as r

polarity = r.get_stat_polarity()
always_tracked = r.get_always_tracked_stats()
print(f"always_tracked stats from seed: {sorted(always_tracked)}\n")

# 1. What stats does the polarity map have, and which are None?
print("=" * 60)
print("STAT POLARITY MAP")
print("=" * 60)
all_seed_stats = r.query_snowflake("""
    SELECT DISTINCT stat_name FROM mart_stat_leaderboard
    WHERE entity_grain = 'team' AND record_scope = 'current_season'
    ORDER BY stat_name
""")
for row in all_seed_stats:
    s = row['stat_name']
    pol = polarity.get(s, 'NOT_IN_MAP')
    print(f"  {s:25} -> polarity={pol}")

# 2. What's in current_season rank=1, and what survives should_track_record?
print()
print("=" * 60)
print("CURRENT_SEASON rank=1 records")
print("=" * 60)
rows = r.query_snowflake("""
    SELECT entity_grain, stat_name, record_direction, stat_value
    FROM mart_stat_leaderboard
    WHERE record_scope = 'current_season' AND rank = 1
    ORDER BY entity_grain, stat_name, record_direction
""")

passed = 0
filtered = 0
filter_reasons = {}
for row in rows:
    grain = row['entity_grain']
    stat = row['stat_name']
    direction = row['record_direction']
    keep = r.should_track_record(grain, stat, direction, polarity, always_tracked)
    if keep:
        passed += 1
    else:
        filtered += 1
        # Why was it filtered?
        if grain == 'player':
            if stat not in r.SCORE_STAT_NAMES:
                reason = "player grain + non-score stat"
            elif direction != 'most':
                reason = "player grain + non-most direction"
            else:
                reason = "??"
        else:
            if stat in r.SCORE_STAT_NAMES:
                reason = "team score + ?"
            else:
                pol = polarity.get(stat)
                if pol is None:
                    reason = "team + polarity=None (not in scoring_settings)"
                elif pol == 'neutral':
                    reason = "team + polarity=neutral (zero-weighted)"
                elif pol == 'negative' and direction != 'most':
                    reason = "team + negative + fewest (most-of-bad rule)"
                else:
                    reason = "??"
        filter_reasons[reason] = filter_reasons.get(reason, 0) + 1

print(f"\nPassed filter: {passed} rows")
print(f"Filtered out:  {filtered} rows")
for reason, n in sorted(filter_reasons.items(), key=lambda x: -x[1]):
    print(f"  {n:3}  {reason}")

# 3. Specifically: where does 'H' (Hits) land?
print()
print("=" * 60)
print("Hits and chunk 1 stats specifically:")
print("=" * 60)
focus_stats = ['H', 'TB', 'XBH', 'AB', 'SF', 'ER', 'PA',
               'GDP', 'B_IBB', 'HBP_P', 'BLSV', 'NH', 'PG', 'PK', 'SHO']
for stat in focus_stats:
    pol = polarity.get(stat, 'NOT_IN_MAP')
    keep_most = r.should_track_record('team', stat, 'most', polarity, always_tracked)
    keep_fewest = r.should_track_record('team', stat, 'fewest', polarity, always_tracked)
    in_seed = stat in always_tracked
    print(f"  {stat:8} polarity={str(pol):8} always={str(in_seed):5} most={keep_most}  fewest={keep_fewest}")

# 4. For CALCULATED_POINTS: are 'fewest' direction rows in current_season?
print()
print("=" * 60)
print("CALCULATED_POINTS current_season rank=1 (BOTH directions):")
print("=" * 60)
calc_rows = r.query_snowflake("""
    SELECT entity_grain, stat_name, record_direction,
           team_name, display_name, season_year, matchup_period, stat_value
    FROM mart_stat_leaderboard
    WHERE record_scope = 'current_season' AND rank = 1
      AND stat_name IN ('CALCULATED_POINTS','CALCULATED_HITTING_PTS','CALCULATED_PITCHING_PTS')
    ORDER BY entity_grain, stat_name, record_direction
""")
for row in calc_rows:
    holder = row['team_name'] if row['entity_grain'] == 'team' else row['display_name']
    print(f"  {row['entity_grain']:7} | {row['stat_name']:25} | {row['record_direction']:7} | {holder:35} | mp{row['matchup_period']} | {row['stat_value']:.2f}")
