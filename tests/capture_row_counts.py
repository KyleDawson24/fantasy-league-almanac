"""Mart + intermediate row-count snapshot tool.

Two modes:
    python tests/capture_row_counts.py             # print current counts
    python tests/capture_row_counts.py --check     # diff vs baseline JSON
    python tests/capture_row_counts.py --regenerate # overwrite baseline JSON

Companion to test_golden_output.py. The BBCode tests catch behavioral
drift (what the user sees); this catches data-shape drift (which models
changed by how much). Used as a checkpoint diagnostic during the
Phase 7 Step 2 rearchitect — not a pytest test because most sub-chunks
will intentionally change row counts (new int_player_daily, new inactive
facts, dropped legacy models).

Baseline snapshot is the pre-refactor state. As sub-chunks land, expected
changes are documented; the --check output narrates them so we know when
the dust settles.
"""
import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "output"))
import db  # noqa: E402
from db import query_snowflake  # noqa: E402

FIXTURE = REPO / "tests" / "fixtures" / "mart_row_counts.json"

# All current intermediate + mart models. Update as the rearchitect adds/drops.
# Phase 7 H dropped: int_team_daily_scores, int_player_daily_scores,
# int_player_daily_stats, fct_weekly_player_scores, mart_wasted_points,
# and the transitional fct_weekly_team_performance compat view.
# v1.1.0 DAG cleanup: int_player_weekly_performance promoted to
# fct_weekly_player_performance. New dim_stat, dim_matchup_period,
# fct_player_daily_performance models added.
MODELS = [
    "int_player_daily",
    "dim_stat",
    "dim_matchup_period",
    "fct_player_daily_performance",
    "fct_weekly_player_performance",
    "fct_weekly_player_active_performance",
    "fct_weekly_player_inactive_performance",
    "fct_weekly_team_active_performance",
    "fct_weekly_team_inactive_performance",
    "mart_stat_leaderboard",
    "mart_league_weekly_benchmarks",
]


def snapshot() -> dict:
    db.init()
    out = {}
    for m in MODELS:
        try:
            out[m] = query_snowflake(f"SELECT COUNT(*) AS n FROM {m}")[0]["n"]
        except Exception as e:
            out[m] = f"<error: {type(e).__name__}: {e}>"
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--check", action="store_true", help="diff current vs baseline")
    p.add_argument("--regenerate", action="store_true", help="overwrite baseline")
    args = p.parse_args()

    current = snapshot()

    if args.regenerate:
        FIXTURE.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote baseline to {FIXTURE}")
        for m, n in current.items():
            print(f"  {m}: {n:,}" if isinstance(n, int) else f"  {m}: {n}")
        return

    if args.check:
        baseline = json.loads(FIXTURE.read_text(encoding="utf-8"))
        all_keys = sorted(set(baseline) | set(current))
        drift = False
        for m in all_keys:
            b = baseline.get(m, "<MISSING from baseline>")
            c = current.get(m, "<MISSING from current>")
            if b == c:
                print(f"  ok    {m}: {b:,}" if isinstance(b, int) else f"  ok    {m}: {b}")
            else:
                drift = True
                b_s = f"{b:,}" if isinstance(b, int) else str(b)
                c_s = f"{c:,}" if isinstance(c, int) else str(c)
                print(f"  DIFF  {m}: baseline={b_s}, current={c_s}")
        sys.exit(1 if drift else 0)

    # Default: just print current counts
    for m, n in current.items():
        print(f"  {m}: {n:,}" if isinstance(n, int) else f"  {m}: {n}")


if __name__ == "__main__":
    main()
