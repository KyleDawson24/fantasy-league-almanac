"""
MLB-129 spike -- resolve ESPN player_id -> mlbam_id at PERSON grain.

Reuses extract/mlb_crosswalk.py's matcher verbatim (norm / initial_key /
build_mlb_index / learn_team_map / match). MLB-175's lesson is explicit that a
hand-rolled twin of an existing key is a known-bad path, so nothing here
re-implements name logic -- it imports it.

TEAM EVIDENCE -- the part the brief warned about, resolved differently.

The brief said ESPN's 2025 club labels are corrupt (0 of 1,236 player-seasons
carry >1 club) so 2025 team evidence must not be used, and tier B would need
MLBAM's own 2025 team as a tiebreak.

That is true of ESPN's PLAYER-LEVEL stamp. It is NOT true of the GAME-LEVEL
stamp on the per-scoring-period split, which this spike found ESPN also sends
and extract.py discards. So both tiers get real, period-accurate ESPN team
evidence, and 2025 evidence is admissible after all.

Person grain: one (espn_player_id -> mlbam_id) per human. Ambiguity resolves
to NULL by design -- never to a fallback.

Read-only. Writes a JSON result to the scratchpad. Lands nothing.
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
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

import extract.mlb_crosswalk as mc  # noqa: E402

OUT = Path(sys.argv[1])
SWEEP = OUT / "gamelevel_club.ndjson"

ESPN_TEAM_ABBR = espn_const.PRO_TEAM_MAP


def connect():
    return snowflake.connector.connect(
        account=os.getenv("SNOWFLAKE_ACCOUNT"), user=os.getenv("SNOWFLAKE_USER"),
        database=os.getenv("SNOWFLAKE_DATABASE"), schema=os.getenv("SNOWFLAKE_SCHEMA"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        private_key_file=os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH"),
        private_key_file_pwd=os.getenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"),
        password=os.getenv("SNOWFLAKE_PASSWORD"))


WEIGHT = ("(COALESCE(ab,0)+COALESCE(b_bb,0)+COALESCE(hbp,0)+COALESCE(sf,0)"
          "+COALESCE(outs,0)+COALESCE(p_h,0)+COALESCE(p_bb,0)+COALESCE(hbp_p,0))"
          "*COALESCE(active_weight,0)")


def espn_universe(cur):
    """Every distinct ESPN player, with seasons present and 2025 active-slot
    weight. Name is taken from the most recent season the player appears in."""
    # NOTE: the name pick is resolved to ONE row per player_id BEFORE the
    # join. Joining the per-(player,name,season) table straight back to the
    # fact fans the rows out and roughly doubles every SUM -- the first cut of
    # this query reported 392,188 units of 2025 weight against a known
    # 202,547, which is how the fan-out was caught.
    cur.execute(f"""
        WITH base AS (
          SELECT player_id, player_name, season_year, lineup_slot,
                 {WEIGHT} AS wt
          FROM ANALYTICS.FCT_PLAYER_DAILY_PERFORMANCE
          WHERE league_key='espn-main'
        ),
        agg AS (
          SELECT player_id,
                 MAX(CASE WHEN season_year=2025 THEN 1 ELSE 0 END) AS in25,
                 MAX(CASE WHEN season_year=2026 THEN 1 ELSE 0 END) AS in26,
                 MAX(CASE WHEN season_year=2026
                           AND lineup_slot NOT IN ('BE','IL','FA')
                          THEN 1 ELSE 0 END) AS anchor26,
                 SUM(CASE WHEN season_year=2025
                           AND lineup_slot NOT IN ('BE','IL','FA')
                          THEN wt ELSE 0 END) AS wt25,
                 SUM(CASE WHEN season_year=2026
                           AND lineup_slot NOT IN ('BE','IL','FA')
                          THEN wt ELSE 0 END) AS wt26
          FROM base GROUP BY player_id
        ),
        ranked AS (
          SELECT player_id, player_name,
                 ROW_NUMBER() OVER (PARTITION BY player_id
                                    ORDER BY season_year DESC, n DESC) AS rn
          FROM (SELECT player_id, player_name, season_year, COUNT(*) AS n
                FROM base GROUP BY player_id, player_name, season_year)
        ),
        nm AS (SELECT player_id, player_name FROM ranked WHERE rn=1)
        SELECT a.player_id, nm.player_name, a.in25, a.in26,
               a.anchor26, a.wt25, a.wt26
        FROM agg a JOIN nm ON nm.player_id = a.player_id
    """)
    rows = []
    for pid, name, in25, in26, anchor26, wt25, wt26 in cur.fetchall():
        rows.append({"espn_id": int(pid), "name": name,
                     "in25": bool(in25), "in26": bool(in26),
                     "anchor26": bool(anchor26),
                     "wt25": float(wt25 or 0), "wt26": float(wt26 or 0)})
    return rows


def game_level_evidence():
    """(espn_id, season) -> most-frequent GAME-LEVEL club abbrev, from the sweep.
    This is the period-accurate club, not the frozen player-level stamp."""
    per = defaultdict(Counter)
    seasons = defaultdict(set)
    if not SWEEP.exists():
        return {}, {}
    with SWEEP.open(encoding="utf-8") as f:
        for ln in f:
            r = json.loads(ln)
            pid, yr = r["player_id"], r["season_year"]
            for g in r["game_splits"]:
                tid = g.get("proTeamId")
                if tid:                      # 0 == FA/none, not a club
                    per[(pid, yr)][ESPN_TEAM_ABBR.get(tid, f"?{tid}")] += 1
                    seasons[pid].add(yr)
    ev = {k: c.most_common(1)[0][0] for k, c in per.items()}
    return ev, seasons


def main():
    print("MLB-129 spike -- ESPN -> MLBAM person-grain crosswalk", flush=True)

    ev_game, seasons_game = game_level_evidence()
    print(f"game-level evidence loaded: {len(ev_game)} (player,season) pairs", flush=True)

    conn = connect()
    cur = conn.cursor()
    players_raw = espn_universe(cur)
    print(f"ESPN distinct players: {len(players_raw)}", flush=True)

    # ESPN's window is 2025-2026 only -- index exactly that. A wider index
    # admits retired strangers the season-overlap check would then have to
    # reject; a narrower one can't see tier B at all.
    mc.FIRST_SEASON, mc.LAST_SEASON = 2025, 2026
    print("building MLB index (statsapi 2025-2026)...", flush=True)
    idx, ikey = mc.build_mlb_index()

    # Shape into the matcher's contract: seasons + evidence{yr: club_code}
    players = []
    for p in players_raw:
        seasons = set()
        if p["in25"]:
            seasons.add(2025)
        if p["in26"]:
            seasons.add(2026)
        evidence = {}
        for yr in (2025, 2026):
            club = ev_game.get((p["espn_id"], yr))
            if club:
                evidence[yr] = club.upper()
        players.append({**p, "seasons": seasons, "evidence": evidence})

    with_ev = sum(1 for p in players if p["evidence"])
    print(f"players carrying game-level team evidence: {with_ev}/{len(players)}", flush=True)

    team_map = mc.learn_team_map(players, idx)
    print(f"learned ESPN club -> MLBAM team map: {len(team_map)} codes", flush=True)
    unmapped = Counter(c for p in players for c in p["evidence"].values()
                       if c not in team_map)
    if unmapped:
        print(f"  unmapped codes: {dict(unmapped)}", flush=True)

    results = []
    for p in players:
        r = mc.match(p, idx, ikey, team_map)
        results.append({**p, **r, "seasons": sorted(p["seasons"])})

    # ---- collision check: two ESPN ids claiming one mlbam_id ----
    by_mlbam = defaultdict(list)
    for r in results:
        if r["mlbam_id"]:
            by_mlbam[r["mlbam_id"]].append(r)
    collisions = {m: v for m, v in by_mlbam.items() if len(v) > 1}

    out = OUT / "espn_crosswalk_result.json"
    out.write_text(json.dumps(
        {"results": results,
         "team_map": team_map,
         "collisions": {str(k): [x["espn_id"] for x in v] for k, v in collisions.items()}},
        indent=2), encoding="utf-8")
    print(f"\nwrote {out}", flush=True)

    # ---- coverage, by player count AND by 2025 active-slot weight ----
    def summarise(label, rows):
        tot_n = len(rows)
        tot_w = sum(r["wt25"] for r in rows)
        hit = [r for r in rows if r["mlbam_id"]]
        hit_w = sum(r["wt25"] for r in hit)
        print(f"\n{label}")
        print(f"  players : {len(hit)}/{tot_n} = "
              f"{100*len(hit)/tot_n if tot_n else 0:.2f}%")
        print(f"  2025 wt : {hit_w:,.0f}/{tot_w:,.0f} = "
              f"{100*hit_w/tot_w if tot_w else 0:.2f}%")
        meth = Counter(r["method"] for r in rows)
        for m, c in meth.most_common():
            print(f"     {m:28s} {c}")
        return hit_w, tot_w

    in25 = [r for r in results if r["in25"]]
    tierA = [r for r in in25 if r["anchor26"]]
    tierB = [r for r in in25 if not r["anchor26"]]

    print("\n" + "=" * 72)
    summarise("ALL 2025 players", in25)
    summarise("TIER A -- 2026-anchored", tierA)
    summarise("TIER B -- no 2026 anchor", tierB)
    print("=" * 72)
    print(f"\ncollisions (one mlbam_id claimed by >1 ESPN id): {len(collisions)}")
    for m, v in list(collisions.items())[:15]:
        print(f"   mlbam {m}: " + ", ".join(f"{x['espn_id']}({x['name']})" for x in v))


if __name__ == "__main__":
    main()
