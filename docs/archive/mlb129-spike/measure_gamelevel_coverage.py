"""
MLB-129 spike -- what share of the affinity chart's own metric can ESPN's
GAME-LEVEL club stamp actually resolve?

Joins the swept per-scoring-period game-level club against the warehouse's
active-slot weight, at the exact grain and with the exact weight expression
get_team_affinity_weights uses:

    weight = (ab + b_bb + hbp + sf + outs + p_h + p_bb + hbp_p) * active_weight
    filter = lineup_slot NOT IN ('BE','IL','FA'), league_key='espn-main'

Reports coverage by season, by tier, and enumerates the residual with a
reason per player -- GO condition 2 is that the residual is NAMED, not counted.

Read-only. Lands nothing.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

import snowflake.connector
from dotenv import load_dotenv
from espn_api.baseball import constant as espn_const

ROOT = Path(r"C:\Users\kyled\projects\espn-league-manager")
load_dotenv(ROOT / ".env")

OUT = Path(sys.argv[1])
SWEEP = OUT / "gamelevel_club.ndjson"
ABBR = espn_const.PRO_TEAM_MAP

WEIGHT = ("(COALESCE(ab,0)+COALESCE(b_bb,0)+COALESCE(hbp,0)+COALESCE(sf,0)"
          "+COALESCE(outs,0)+COALESCE(p_h,0)+COALESCE(p_bb,0)+COALESCE(hbp_p,0))"
          "*COALESCE(active_weight,0)")


def connect():
    return snowflake.connector.connect(
        account=os.getenv("SNOWFLAKE_ACCOUNT"), user=os.getenv("SNOWFLAKE_USER"),
        database=os.getenv("SNOWFLAKE_DATABASE"), schema=os.getenv("SNOWFLAKE_SCHEMA"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        private_key_file=os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH"),
        private_key_file_pwd=os.getenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"),
        password=os.getenv("SNOWFLAKE_PASSWORD"))


def warehouse_rows(cur):
    """One row per (season, scoring_period, player) carrying active-slot weight."""
    cur.execute(f"""
        SELECT season_year, scoring_period, player_id, ANY_VALUE(player_name),
               ANY_VALUE(pro_team), SUM({WEIGHT}) AS wt
        FROM ANALYTICS.FCT_PLAYER_DAILY_PERFORMANCE
        WHERE league_key='espn-main'
          AND lineup_slot NOT IN ('BE','IL','FA')
        GROUP BY season_year, scoring_period, player_id
        HAVING SUM({WEIGHT}) > 0
    """)
    return [{"season": int(y), "sp": int(sp), "pid": int(pid),
             "name": nm, "pro_team": pt, "wt": float(w)}
            for y, sp, pid, nm, pt, w in cur.fetchall()]


def load_sweep():
    """(season, sp, pid) -> {clubs: Counter, n_splits: int}"""
    out = {}
    if not SWEEP.exists():
        sys.exit("sweep NDJSON missing -- run sweep_gamelevel_club.py first")
    with SWEEP.open(encoding="utf-8") as f:
        for ln in f:
            r = json.loads(ln)
            key = (r["season_year"], r["scoring_period"], r["player_id"])
            clubs = Counter()
            for g in r["game_splits"]:
                tid = g.get("proTeamId")
                clubs[ABBR.get(tid, f"?{tid}") if tid else "FA/none"] += 1
            out[key] = {"clubs": clubs, "n": len(r["game_splits"]),
                        "player_level": ABBR.get(r.get("player_level_proTeamId"), "FA")}
    return out


def main():
    conn = connect()
    cur = conn.cursor()
    rows = warehouse_rows(cur)
    sweep = load_sweep()
    print(f"warehouse weighted rows: {len(rows):,}")
    print(f"swept (season,sp,player) keys: {len(sweep):,}\n")

    # tier membership: anchor = has a 2026 active-slot row
    cur.execute(f"""
        SELECT DISTINCT player_id FROM ANALYTICS.FCT_PLAYER_DAILY_PERFORMANCE
        WHERE league_key='espn-main' AND season_year=2026
          AND lineup_slot NOT IN ('BE','IL','FA')
    """)
    anchored = {int(r[0]) for r in cur.fetchall()}

    stats = defaultdict(lambda: {"tot": 0.0, "hit": 0.0, "miss": 0.0,
                                 "disagree": 0.0, "multi": 0.0})
    residual = defaultdict(lambda: {"wt": 0.0, "name": None, "reason": Counter(),
                                    "periods": 0})
    agreement = Counter()

    for r in rows:
        key = (r["season"], r["sp"], r["pid"])
        tier = "A" if r["pid"] in anchored else "B"
        buckets = [(r["season"], "ALL"), (r["season"], tier)]
        s = sweep.get(key)
        for b in buckets:
            stats[b]["tot"] += r["wt"]

        if s is None:
            for b in buckets:
                stats[b]["miss"] += r["wt"]
            e = residual[r["pid"]]
            e["name"] = r["name"]
            e["wt"] += r["wt"]
            e["periods"] += 1
            e["reason"]["no game-level split returned for this period"] += 1
            continue

        real = {c: n for c, n in s["clubs"].items() if c != "FA/none"}
        if not real:
            for b in buckets:
                stats[b]["miss"] += r["wt"]
            e = residual[r["pid"]]
            e["name"] = r["name"]
            e["wt"] += r["wt"]
            e["periods"] += 1
            e["reason"]["split present but proTeamId=0 (no club)"] += 1
            continue

        for b in buckets:
            stats[b]["hit"] += r["wt"]
            if len(real) > 1:
                stats[b]["multi"] += r["wt"]

        gl = max(real, key=real.get)
        pl = (r["pro_team"] or "FA")
        if gl.upper() != pl.upper():
            for b in buckets:
                stats[b]["disagree"] += r["wt"]
            agreement["game-level DISAGREES with stored pro_team"] += 1
        else:
            agreement["agrees"] += 1

    print("=" * 78)
    print(f"{'bucket':16s} {'total wt':>12s} {'resolved':>12s} {'cover%':>8s} "
          f"{'unresolved':>11s} {'disagree wt':>12s}")
    print("=" * 78)
    for k in sorted(stats):
        v = stats[k]
        cov = 100 * v["hit"] / v["tot"] if v["tot"] else 0
        print(f"{str(k):16s} {v['tot']:12,.0f} {v['hit']:12,.0f} {cov:7.2f}% "
              f"{v['miss']:11,.0f} {v['disagree']:12,.0f}")
    print("=" * 78)

    print(f"\nper-period agreement vs stored pro_team: {dict(agreement)}")
    tot_multi = sum(v['multi'] for k, v in stats.items() if k[1] == 'ALL')
    print(f"weight on periods with >1 distinct game-level club: {tot_multi:,.0f}")

    print(f"\n--- RESIDUAL: {len(residual)} players carrying unresolved weight ---")
    ranked = sorted(residual.items(), key=lambda kv: -kv[1]["wt"])
    print(f"{'espn_id':>9s} {'player':28s} {'unres wt':>9s} {'periods':>8s}  reason")
    for pid, e in ranked[:40]:
        top = e["reason"].most_common(1)[0][0]
        print(f"{pid:9d} {str(e['name'])[:28]:28s} {e['wt']:9,.0f} "
              f"{e['periods']:8d}  {top}")
    print(f"\ntotal unresolved weight across all residual players: "
          f"{sum(e['wt'] for e in residual.values()):,.0f}")

    (OUT / "gamelevel_coverage.json").write_text(json.dumps({
        "stats": {f"{k[0]}-{k[1]}": v for k, v in stats.items()},
        "residual": {str(p): {"name": e["name"], "wt": e["wt"],
                              "periods": e["periods"],
                              "reasons": dict(e["reason"])}
                     for p, e in ranked},
    }, indent=2), encoding="utf-8")
    print(f"\nwrote {OUT / 'gamelevel_coverage.json'}")


if __name__ == "__main__":
    main()
