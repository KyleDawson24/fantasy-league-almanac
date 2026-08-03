"""Re-pin MLB-190's Baseball Reference case under game-level attribution.

BR: Curtis Mead played 1 game, 2 PA for Boston in 2026.
Warehouse today credits Boston with 20 units of his active-slot weight.
"""
import json
import os
import sys
from collections import Counter
from pathlib import Path

import snowflake.connector
from dotenv import load_dotenv
from espn_api.baseball import constant as espn_const

ROOT = Path(r"C:\Users\kyled\projects\espn-league-manager")
load_dotenv(ROOT / ".env")
OUT = Path(sys.argv[1])
ABBR = espn_const.PRO_TEAM_MAP

W = ("(coalesce(ab,0)+coalesce(b_bb,0)+coalesce(hbp,0)+coalesce(sf,0)"
     "+coalesce(outs,0)+coalesce(p_h,0)+coalesce(p_bb,0)+coalesce(hbp_p,0))"
     "*coalesce(active_weight,0)")

PLAYER, SEASON = 42360, 2026

gl = {}
for ln in (OUT / "gamelevel_club.ndjson").open(encoding="utf-8"):
    r = json.loads(ln)
    if r["season_year"] == SEASON and r["player_id"] == PLAYER:
        clubs = [ABBR.get(g["proTeamId"]) for g in r["game_splits"] if g.get("proTeamId")]
        if clubs:
            gl[r["scoring_period"]] = clubs[0]

cn = snowflake.connector.connect(
    account=os.getenv("SNOWFLAKE_ACCOUNT"), user=os.getenv("SNOWFLAKE_USER"),
    database=os.getenv("SNOWFLAKE_DATABASE"), schema=os.getenv("SNOWFLAKE_SCHEMA"),
    warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
    private_key_file=os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH"),
    private_key_file_pwd=os.getenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"),
    password=os.getenv("SNOWFLAKE_PASSWORD"))
cu = cn.cursor()
cu.execute(f"""
    select scoring_period, sum({W}) as w, any_value(pro_team) as pt
    from ANALYTICS.FCT_PLAYER_DAILY_PERFORMANCE
    where league_key='espn-main' and season_year={SEASON} and player_id={PLAYER}
      and lineup_slot not in ('BE','IL','FA')
    group by scoring_period
    having sum({W}) > 0
    order by scoring_period
""")

by_gl, by_stored = Counter(), Counter()
for sp, w, pt in cu.fetchall():
    by_gl[gl.get(sp, "(no club)")] += float(w)
    by_stored[pt or "FA"] += float(w)

print("Curtis Mead 2026 -- active-slot weight (PA+BF) attribution")
print(f"  periods with a game-level club: {len(gl)}")
print()
print(f"  TODAY  (stored pro_team)   : {dict(by_stored)}")
print(f"  GAME-LEVEL club-of-game    : {dict(by_gl)}")
print()
print("  MLB-190 pinned vs Baseball Reference: 1 game, 2 PA for Boston.")
print(f"  Boston today      : {by_stored.get('Bos', 0):.0f}")
print(f"  Boston game-level : {by_gl.get('Bos', 0):.0f}")
