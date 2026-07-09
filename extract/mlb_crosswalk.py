#!/usr/bin/env python3
"""Build the CBS-player -> MLBAM-id crosswalk (MLB-70, the universal-stats spine).

After the 2026-07-09 pivot, the baseball layer is sourced from the universal
free MLB Stats API (statsapi.mlb.com), joined to CBS's fantasy layer. This
script builds the join key: a table mapping each CBS player id to its MLBAM
id, so per-game MLB stats can be attributed to CBS roster membership.

Method (name-based; elias_id is an Elias id, not MLBAM, and is null on stat
rows anyway): fetch the MLB Stats API's full player list per season
(2004-2026), index by normalized name with the set of seasons each MLBAM id
was active, then match each CBS player (from the FA universe + current
rosters) by normalized name, disambiguating same-name collisions by
season-of-activity overlap. Verify by content -- spot-check known ids.

CBS empty pre-debut phantom rows (FPTS=0, all-null; e.g. Scherzer's 2007 row)
carry no seasons of real production and simply don't match -- intended.

Public API, not the CBS museum: polite pacing, no token. Lands
raw.CBS_MLBAM_CROSSWALK (idempotent full rebuild). Read-only vs CBS.

Usage:  py extract/mlb_crosswalk.py            # build + land + verify
        py extract/mlb_crosswalk.py --dry-run  # build + verify, land nothing
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import unicodedata
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

import snowflake.connector
from dotenv import load_dotenv

FIRST_SEASON, LAST_SEASON = 2004, 2026
API = "https://statsapi.mlb.com/api/v1"
PACING = 0.3


def norm(n: str) -> str:
    n = unicodedata.normalize("NFKD", str(n or "")).encode("ascii", "ignore").decode()
    n = re.sub(r"\(.*?\)", "", n)  # drop CBS's two-way suffix: "Ohtani (Batter)"
    n = n.lower().replace(".", "").replace("'", "").replace("-", " ")
    n = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", n)
    return re.sub(r"\s+", " ", n).strip()


def initial_key(n: str) -> str:
    """(first-initial, last-name) key -- forgives nickname/middle-initial forms
    (Nate vs Nathaniel Lowe, Josh H. Smith vs Josh Smith)."""
    parts = norm(n).split()
    return (parts[0][:1] + " " + parts[-1]) if len(parts) >= 2 else norm(n)


def get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "espn-league-manager/mlb_crosswalk"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.loads(r.read())


def build_mlb_index():
    """Two indexes across all seasons, each key -> {mlbam_id: {name, seasons}}:
    exact normname, and the forgiving (first-initial, last-name) key."""
    idx: dict = defaultdict(lambda: defaultdict(lambda: {"name": None, "seasons": set()}))
    ikey: dict = defaultdict(lambda: defaultdict(lambda: {"name": None, "seasons": set()}))
    for yr in range(FIRST_SEASON, LAST_SEASON + 1):
        people = get(f"{API}/sports/1/players?season={yr}").get("people", [])
        for p in people:
            for tbl, k in ((idx, norm(p.get("fullName"))), (ikey, initial_key(p.get("fullName")))):
                e = tbl[k][p["id"]]
                e["name"] = p.get("fullName")
                e["seasons"].add(yr)
        print("  MLB %d: %d players" % (yr, len(people)), flush=True)
        time.sleep(PACING)
    return idx, ikey


def cbs_players(cur) -> list[dict]:
    """Distinct CBS players with the seasons they show REAL production (universe)
    plus current-roster players (who are absent from the FA universe)."""
    # FA universe: non-empty seasons (FPTS present) per player.
    cur.execute("""
        SELECT f.value:id::string, f.value:name::string, s.season_year
        FROM RAW.CBS_SEASON_STATS s,
             LATERAL FLATTEN(input => s.payload:body:league_stats:players) f
        WHERE s.league_key='cbs-bsb'
          AND TRIM(COALESCE(f.value:FPTS::string,'')) NOT IN ('', '0')
    """)
    seasons = defaultdict(set)
    names = {}
    for pid, name, yr in cur.fetchall():
        seasons[pid].add(int(yr))
        names[pid] = name
    # Current rosters: rostered players (not in the FA universe). No historical
    # seasons known here -> match on name alone / current season.
    cur.execute("""
        WITH latest AS (SELECT MAX(roster_date) d FROM RAW.CBS_ROSTERS WHERE league_key='cbs-bsb')
        SELECT DISTINCT p.value:id::string, p.value:fullname::string
        FROM RAW.CBS_ROSTERS r, latest,
             LATERAL FLATTEN(input=>r.payload:body:rosters:teams) t,
             LATERAL FLATTEN(input=>t.value:players) p
        WHERE r.roster_date = latest.d
    """)
    for pid, name in cur.fetchall():
        names.setdefault(pid, name)
        seasons.setdefault(pid, set())
    return [{"cbs_id": pid, "name": names[pid], "seasons": seasons[pid]} for pid in names]


def match(player: dict, idx: dict, ikey: dict) -> dict:
    """Resolve one CBS player to an MLBAM id. Exact normname first; then a
    (first-initial, last-name) fallback for nickname/middle-initial forms,
    disambiguated by season-of-activity overlap. Returns method for review."""
    ps = player["seasons"]
    out = {"mlbam_id": None, "mlbam_name": None, "method": "unmatched"}
    cands = idx.get(norm(player["name"]), {})
    if len(cands) == 1:
        mid, e = next(iter(cands.items()))
        return {**out, "mlbam_id": mid, "mlbam_name": e["name"], "method": "name_unique"}
    if len(cands) > 1:
        best = max(cands.items(), key=lambda kv: (len(kv[1]["seasons"] & ps), len(kv[1]["seasons"])))
        return {**out, "mlbam_id": best[0], "mlbam_name": best[1]["name"],
                "method": "name_season_overlap" if ps else "name_ambiguous"}
    # Exact failed -> forgiving (first-initial, last-name).
    fc = ikey.get(initial_key(player["name"]), {})
    if len(fc) == 1:
        mid, e = next(iter(fc.items()))
        return {**out, "mlbam_id": mid, "mlbam_name": e["name"], "method": "fuzzy_unique"}
    if len(fc) > 1 and ps:
        best = max(fc.items(), key=lambda kv: (len(kv[1]["seasons"] & ps), len(kv[1]["seasons"])))
        if best[1]["seasons"] & ps:  # require a real season overlap to accept
            return {**out, "mlbam_id": best[0], "mlbam_name": best[1]["name"], "method": "fuzzy_overlap"}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    load_dotenv()

    conn = snowflake.connector.connect(
        account=os.getenv("SNOWFLAKE_ACCOUNT"), user=os.getenv("SNOWFLAKE_USER"),
        database=os.getenv("SNOWFLAKE_DATABASE"), schema=os.getenv("SNOWFLAKE_SCHEMA"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        private_key_file=os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH"),
        private_key_file_pwd=os.getenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"),
        password=os.getenv("SNOWFLAKE_PASSWORD"))
    cur = conn.cursor()

    print("building MLB player index (statsapi, %d-%d)..." % (FIRST_SEASON, LAST_SEASON), flush=True)
    idx, ikey = build_mlb_index()
    players = cbs_players(cur)
    print("CBS distinct players to map: %d" % len(players), flush=True)

    rows, by_method = [], defaultdict(int)
    for p in players:
        m = match(p, idx, ikey)
        by_method[m["method"]] += 1
        rows.append({"cbs_player_id": p["cbs_id"], "cbs_name": p["name"], **m})

    # Flag common-name collisions: distinct CBS ids resolving to ONE MLBAM
    # (beyond legit two-way splits, which share an MLBAM) are same-name
    # confusions -- season overlap wasn't enough, they need team-level
    # disambiguation (natural when the game-log extract pulls team-per-season,
    # MLB-70). Mark for review rather than silently trust.
    SPLIT_OK = {660271}  # Shohei Ohtani -- the 900/901 split legitimately shares one id
    claims = defaultdict(int)
    for r in rows:
        if r["mlbam_id"]:
            claims[r["mlbam_id"]] += 1
    for r in rows:
        if r["mlbam_id"] and claims[r["mlbam_id"]] > 1 and r["mlbam_id"] not in SPLIT_OK:
            r["method"] += "_COLLISION"
            by_method[r["method"]] += 1

    mapped = [r for r in rows if r["mlbam_id"]]
    print("\nmatch methods:", dict(by_method))
    print("collision-flagged (need team disambiguation):",
          sum(1 for r in rows if r["method"].endswith("_COLLISION")))
    print("mapped: %d / %d (%.1f%%)" % (len(mapped), len(rows), 100*len(mapped)/len(rows)))
    # Spot-check known ids.
    known = {"1893753": 543037, "1739608": 545361, "2071264": 592450, "530362": 434378}
    print("spot checks (cbs_id -> expected MLBAM):")
    for r in rows:
        if r["cbs_player_id"] in known:
            ok = str(r["mlbam_id"]) == str(known[r["cbs_player_id"]])
            print("  %-9s %-20s -> %s expected %s %s" % (
                r["cbs_player_id"], r["cbs_name"], r["mlbam_id"], known[r["cbs_player_id"]],
                "OK" if ok else "MISMATCH"))

    unmatched = [r["cbs_name"] for r in rows if not r["mlbam_id"]]
    print("unmatched (%d):" % len(unmatched), sorted(unmatched))
    if args.dry_run:
        print("\ndry run -- nothing landed.")
        return
    cur.execute("""CREATE OR REPLACE TABLE RAW.CBS_MLBAM_CROSSWALK (
        cbs_player_id VARCHAR, cbs_name VARCHAR, mlbam_id INTEGER,
        mlbam_name VARCHAR, method VARCHAR, loaded_at TIMESTAMP_NTZ)""")
    loaded_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cur.executemany(
        "INSERT INTO RAW.CBS_MLBAM_CROSSWALK "
        "(cbs_player_id, cbs_name, mlbam_id, mlbam_name, method, loaded_at) "
        "VALUES (%(cbs_player_id)s, %(cbs_name)s, %(mlbam_id)s, %(mlbam_name)s, %(method)s, '" + loaded_at + "')",
        [r for r in rows if r["mlbam_id"]])
    conn.commit()
    print("\nlanded %d rows -> RAW.CBS_MLBAM_CROSSWALK" % len(mapped))
    cur.close(); conn.close()


if __name__ == "__main__":
    main()
