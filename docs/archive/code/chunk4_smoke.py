import sys
sys.path.insert(0, 'output')
import records as r
from collections import Counter

# Pull current-season records with collapse applied.
out = r.get_records_with_contributors('current_season', top_n=5)
print(f"current_season after collapse: {len(out)} rows total")

# Count collapsed vs non-collapsed.
collapsed = [row for row in out if row.get('is_collapsed')]
real = [row for row in out if not row.get('is_collapsed')]
print(f"  real rows: {len(real)}")
print(f"  collapsed synthetic rows: {len(collapsed)}")

# Sample a few collapsed rows to inspect.
print("\nSample collapsed rows:")
for row in collapsed[:8]:
    grain = row['entity_grain']
    stat = row['stat_name']
    direction = row['record_direction']
    val = row['stat_value']
    n = row['tie_count']
    sy = row['season_year']
    mp = row['matchup_period']
    print(f"  {grain:6} | {stat:25} | {direction:6} | value={val:6.2f} | tie_count={n:4} | recent={sy}-mp{mp}")

# Spot-check: a known floor-zero stat at team grain.
# In this league CG/HLD/SV/QS Fewest typically saturates at 0.
def find_group(grain, stat, direction):
    return [row for row in out
            if row['entity_grain'] == grain
            and row['stat_name'] == stat
            and row['record_direction'] == direction]

for stat in ['CG', 'HLD', 'NH', 'PG']:
    g = find_group('team', stat, 'fewest')
    print(f"\nteam | {stat} | fewest -> {len(g)} rows")
    for row in g:
        if row.get('is_collapsed'):
            print(f"  COLLAPSED: tie_count={row['tie_count']} value={row['stat_value']}")
        else:
            print(f"  rank {row['rank']}: {row['team_name']} W{row['matchup_period']} value={row['stat_value']}")

# Spot-check: a stat that probably doesn't tie at the cap.
# CALCULATED_POINTS team-grain should have 5 unique values for top-5.
g = find_group('team', 'CALCULATED_POINTS', 'most')
print(f"\nteam | CALCULATED_POINTS | most -> {len(g)} rows")
for row in g:
    if row.get('is_collapsed'):
        print(f"  COLLAPSED: tie_count={row['tie_count']} value={row['stat_value']:.2f}")
    else:
        print(f"  rank {row['rank']}: {row['team_name']} W{row['matchup_period']} value={row['stat_value']:.2f}")

# Group-count diagnostic: how many groups had collapse fire vs not?
from itertools import groupby
def group_key(row):
    return (row['entity_grain'], row['stat_name'], row['record_direction'])

groups_with_collapse = 0
groups_without_collapse = 0
for _, gi in groupby(sorted(out, key=group_key), key=group_key):
    rows = list(gi)
    if any(row.get('is_collapsed') for row in rows):
        groups_with_collapse += 1
    else:
        groups_without_collapse += 1

print(f"\ngroups with collapse fired: {groups_with_collapse}")
print(f"groups without collapse:    {groups_without_collapse}")
