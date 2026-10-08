import sys
sys.path.insert(0, 'output')
import records as r
from collections import Counter

sl = r.load_schedule_lookup()
print(f"schedule_lookup: {len(sl)} entries")
print(f"  (2025, 24): {sl.get((2025, 24))}")
print(f"  format_week_label(2025, 24): {r.format_week_label(2025, 24, sl)}")
print(f"  format_week_label(2025, 1):  {r.format_week_label(2025, 1, sl)}")

out = r.get_records_with_contributors('current_season', top_n=5)
print(f"\nget_records_with_contributors('current_season', top_n=5): {len(out)} rows")

breakdown = Counter((row['entity_grain'], row['record_direction']) for row in out)
for k, v in sorted(breakdown.items()):
    print(f"  {k}: {v}")

team_rec = next((row for row in out
                 if row['entity_grain'] == 'team'
                 and row['stat_name'] == 'HR'
                 and row['rank'] == 1
                 and row['record_direction'] == 'most'), None)
if team_rec:
    print(f"\nMost HR (team, current season, rank 1):")
    print(f"  {team_rec['team_name']} W{team_rec['matchup_period']} HR={team_rec['stat_value']}")
    print(f"  contributors: {team_rec['contributors']}")

player_rec = next((row for row in out
                   if row['entity_grain'] == 'player'
                   and row['stat_name'] == 'CALCULATED_POINTS'
                   and row['rank'] == 1), None)
if player_rec:
    print(f"\nMost CALCULATED_POINTS (player, current season, rank 1):")
    print(f"  {player_rec['display_name']} W{player_rec['matchup_period']} {player_rec['stat_value']:.2f}")
    for c in player_rec['contributors']:
        print(f"    {c['stat_name']}: {c['count_value']} count, {c['point_value']:+.1f} pts")

wp_rec = next((row for row in out
               if row['stat_name'] == 'WASTED_POINTS'
               and row['rank'] == 1
               and row['record_direction'] == 'most'), None)
if wp_rec:
    print(f"\nMost WASTED_POINTS (team, current season, rank 1):")
    print(f"  {wp_rec['team_name']} W{wp_rec['matchup_period']} {wp_rec['stat_value']:.2f}")
    print(f"  contributors: {wp_rec['contributors']} (expect empty)")

era_rec = next((row for row in out
                if row['stat_name'] == 'ERA'
                and row['rank'] == 1
                and row['record_direction'] == 'fewest'), None)
if era_rec:
    print(f"\nFewest ERA (team, current season, rank 1):")
    print(f"  {era_rec['team_name']} W{era_rec['matchup_period']} ERA={era_rec['stat_value']:.2f}")
    print(f"  contributors: {era_rec['contributors']} (expect empty -- rate stat)")
