"""Top-10 player-matchup divergence between net-negative-active and
gross-negative-active metrics. Investigation script for P1 #2 (whether
the doubly_wasted_pts metric should switch from week-net to per-day-rollup).

Reads int_player_daily and aggregates per (season_year, matchup_period,
player_id):
- total_player_points: sum of platform_points across ALL slots (active +
  inactive) for the matchup
- active_week_net: sum of platform_points across active slots only
  (week-net active total)
- active_gross_negative: sum of per-day negative_points across active
  slots only (the new metric)
- net_negative_active: GREATEST(0, -active_week_net) (the OLD metric)

Top-10 by gross-negative-active, with both metrics shown side-by-side.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "output"))

import db  # noqa: E402
db.init()
from db import query_snowflake  # noqa: E402


QUERY = """
WITH player_mp AS (
  SELECT
    season_year,
    matchup_period,
    player_id,
    MAX(display_name) AS display_name,
    SUM(platform_points) AS total_player_points,
    SUM(CASE WHEN is_active_slot THEN platform_points ELSE 0 END) AS active_week_net,
    SUM(CASE WHEN is_active_slot THEN negative_points ELSE 0 END) AS active_gross_negative
  FROM int_player_daily
  GROUP BY 1, 2, 3
)
SELECT
  season_year,
  matchup_period,
  display_name,
  ROUND(total_player_points, 1) AS total_pts,
  ROUND(active_week_net, 1) AS active_net,
  ROUND(GREATEST(0, -active_week_net), 1) AS old_metric_net_neg_active,
  ROUND(active_gross_negative, 1) AS new_metric_gross_neg_active,
  ROUND(active_gross_negative - GREATEST(0, -active_week_net), 1) AS divergence
FROM player_mp
WHERE active_gross_negative > 0
ORDER BY active_gross_negative DESC
LIMIT 10
"""


def main():
    rows = query_snowflake(QUERY)
    if not rows:
        print("(no rows returned)")
        return
    cols = list(rows[0].keys())
    widths = [max(len(c), max(len(str(r[c])) for r in rows)) for c in cols]
    header = "  ".join(c.ljust(w) for c, w in zip(cols, widths))
    sep = "  ".join("-" * w for w in widths)
    print(header)
    print(sep)
    for r in rows:
        print("  ".join(str(r[c]).ljust(w) for c, w in zip(cols, widths)))


if __name__ == "__main__":
    main()
