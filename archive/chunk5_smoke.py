"""Smoke-test chunk 5: build all 3 tabs locally without touching Sheets."""
import sys
sys.path.insert(0, 'output')

import records as r
import sheets_writer as sw

schedule_lookup = r.load_schedule_lookup()
effective_polarity = r.get_effective_polarity()

at_rank1  = r.get_records_with_contributors('all_time',       top_n=1)
cs_rank1  = r.get_records_with_contributors('current_season', top_n=1)
at_top5   = r.get_records_with_contributors('all_time',       top_n=5)
cs_top5   = r.get_records_with_contributors('current_season', top_n=5)

at_rows = sw._build_rows('All-Time',       at_rank1, schedule_lookup, effective_polarity)
cs_rows = sw._build_rows('Current Season', cs_rank1, schedule_lookup, effective_polarity)
dump_rows = (sw._build_rows('All-Time',       at_top5, schedule_lookup, effective_polarity) +
             sw._build_rows('Current Season', cs_top5, schedule_lookup, effective_polarity))

print(f"Tab 1 (All-Time):       {len(at_rows)} rows")
print(f"Tab 2 (Current Season): {len(cs_rows)} rows")
print(f"Tab 3 (Leaderboard Dump): {len(dump_rows)} rows")
print(f"Header has {len(sw._HEADER)} cols: {sw._HEADER}")

# Verify row width matches header
mismatches = [r for r in (at_rows + cs_rows + dump_rows) if len(r) != len(sw._HEADER)]
print(f"Row-width mismatches: {len(mismatches)}")

# Show one team row, one player row, one collapsed row from tab 3
print("\nSample team row (Most HR all-time):")
for row in dump_rows:
    if row[0] == 'All-Time' and row[1] == 'Team' and row[2] == 'Home Runs' and row[3] == 'Most' and row[4] == 1:
        for h, v in zip(sw._HEADER, row):
            print(f"  {h:14}: {v}")
        break

print("\nSample player row (Best Total Points all-time):")
for row in dump_rows:
    if row[0] == 'All-Time' and row[1] == 'Player' and row[2] == 'Total Points' and row[3] == 'Best' and row[4] == 1:
        for h, v in zip(sw._HEADER, row):
            print(f"  {h:14}: {v}")
        break

print("\nSample collapsed row (any):")
collapsed = [row for row in dump_rows if row[4] == 'collapsed']
if collapsed:
    for h, v in zip(sw._HEADER, collapsed[0]):
        print(f"  {h:14}: {v}")
    print(f"  (total collapsed rows: {len(collapsed)})")

# Check playoff week label rendering (2025 mp24 should be 'Round 1')
print("\nPlayoff week labels (current_season 2026 playoffs not played yet, "
      "so checking all-time for 2025 mp24):")
playoff_rows = [row for row in dump_rows
                if row[9] == 2025 and 'Round' in str(row[10])]
print(f"  found {len(playoff_rows)} rows with 'Round' in Week column")
if playoff_rows:
    print(f"  example: stat={playoff_rows[0][2]} week={playoff_rows[0][10]}")

# === Chunk 6.5 spot-checks ===
print("\n=== Chunk 6.5: OUTS->IP + polarity-aware Best/Worst ===")

# OUTS Value column: should be baseball IP notation
outs_rows = [row for row in dump_rows if row[2] == 'Innings Pitched']
print(f"\nInnings Pitched rows (Value column samples):")
for row in outs_rows[:5]:
    # Header: Scope | Grain | Stat | Direction | Rank | Holder | ...Value(8)...
    print(f"  rank={row[4]} value={row[8]!r} (was raw outs pre-6.5)")

# Player record Best Pitching Points: contributor count for IP should be IP notation
pp_rows = [row for row in dump_rows
           if row[1] == 'Player' and row[2] == 'Pitching Points'
           and row[4] == 1 and row[3] == 'Best']
print(f"\nBest Pitching Points (player, rank 1) - Innings Pitched contributor:")
for row in pp_rows[:3]:
    holder = row[5]
    # Contributor1 at index 11, Count1 at 12
    if 'Innings Pitched' in (row[11], row[13], row[15]):
        idx = [row[11], row[13], row[15]].index('Innings Pitched')
        count = [row[12], row[14], row[16]][idx]
        print(f"  {holder} season={row[9]}: IP contributor count={count!r}")

# === Chunk 6.6 spot-checks: inline-collapsed + sort + callouts ===
print("\n=== Chunk 6.6: inline-tier expansion + new sort + callouts ===")

# Inline-collapsed rows: expect <=3 holders to show comma-joined,
# >3 to fall back to "N teams"
inline_rows = [row for row in dump_rows
               if row[4] == 'collapsed' and ',' in str(row[5])]
print(f"\nInline-collapsed rows (Holder has comma): {len(inline_rows)}")
for row in inline_rows[:3]:
    print(f"  {row[2]:25} | dir={row[3]:5} | holder={row[5]:50} | season={row[9]} | week={row[10]}")

count_only_rows = [row for row in dump_rows
                   if row[4] == 'collapsed' and ' teams' in str(row[5]) and ',' not in str(row[5])]
print(f"\nCount-only collapsed rows (legacy 'N teams'): {len(count_only_rows)}")
for row in count_only_rows[:3]:
    print(f"  {row[2]:25} | dir={row[3]:5} | holder={row[5]:15} | value={row[8]}")

# Tab 1 collapsed rows (top_n=1 tier overflow)
tab1_collapsed = [row for row in at_rows if row[4] == 'collapsed']
tab1_inline    = [row for row in tab1_collapsed if ',' in str(row[5])]
print(f"\nTab 1 (All-Time rank=1) collapsed: {len(tab1_collapsed)} total, "
      f"{len(tab1_inline)} inline (small tier), "
      f"{len(tab1_collapsed) - len(tab1_inline)} count-only (4+ tied)")
if tab1_inline:
    print(f"  example: {tab1_inline[0][2]} {tab1_inline[0][3]} -> {tab1_inline[0][5]}")

# Sort verification: check first 10 rows of Tab 3 are Best Scores cohort
print(f"\nTab 3 first 12 rows (expect Best Scores cohort first):")
for row in dump_rows[:12]:
    print(f"  dir={row[3]:5} stat={row[2]:25} scope={row[0]:15} grain={row[1]:6} rank={row[4]}")

# Direction column: Best/Worst polarity check
print("\nDirection labels by polarity (chunk 6.5):")
checks = [
    ('Earned Runs', 'most'),    # negative polarity -> 'most' should be 'Worst'
    ('Earned Runs', 'fewest'),  # negative polarity -> 'fewest' should be 'Best'
    ('Home Runs', 'most'),       # positive -> 'most' = 'Best'
    ('Home Runs', 'fewest'),     # positive -> 'fewest' = 'Worst'
    ('ERA', 'most'),             # negative (rate) -> 'most' = 'Worst'
    ('ERA', 'fewest'),           # negative (rate) -> 'fewest' = 'Best'
    ('Wasted Points', 'most'),   # negative -> 'most' = 'Worst'
    ('Wasted Points', 'fewest'), # negative -> 'fewest' = 'Best'
    ('Hits', 'most'),            # always-tracked positive -> 'most' = 'Best'
    ('Hits', 'fewest'),
]
# Direction pre-translated by sheets writer; we look at what label landed.
# Build a (stat_label, mart_direction) -> sheet_direction map by reading
# the orchestrator's record_direction alongside the rendered label.
mart_dir_by_stat_label = {}
for rec in at_top5:
    label = sw.STAT_DISPLAY.get(rec['stat_name'], rec['stat_name'])
    key = (label, rec['record_direction'])
    if key not in mart_dir_by_stat_label:
        mart_dir_by_stat_label[key] = r.best_or_worst_label(
            rec['stat_name'], rec['record_direction'], effective_polarity,
        )
for stat_label, mart_dir in checks:
    label = mart_dir_by_stat_label.get((stat_label, mart_dir))
    print(f"  {stat_label:18} mart='{mart_dir}' -> sheet label='{label}'")
