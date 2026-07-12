#!/usr/bin/env python3
"""Build the CBS-player -> MLBAM-id crosswalk (MLB-70, the universal-stats spine).

After the 2026-07-09 pivot, the baseball layer is sourced from the universal
free MLB Stats API (statsapi.mlb.com), joined to CBS's fantasy layer. This
script builds the join key: a table mapping each CBS player id to its MLBAM
id, so per-game MLB stats can be attributed to CBS roster membership.

Method (name-based; elias_id is an Elias id, not MLBAM, and is null on stat
rows anyway): fetch the MLB Stats API's full player list per season
(2004-2026) -- each entry carries that season's team -- and index by
normalized name with seasons-active and team-per-season. Match each CBS
player (FA universe + current rosters) by normalized name, disambiguating
same-name collisions by season overlap AND team agreement.

Team evidence: the universe rows' TM column is the season-team ('SEASON'
placeholder pre-2014 rows are ignored); roster rows' pro_team is the current
team (the universe's pro_team is ALSO current-team-as-of-extract, stamped on
every historical row -- deliberately unused). CBS team codes are LEARNED from
the unique-name matches' co-occurrence with MLBAM season teams (argmax with a
3x dominance guard), not hardcoded -- survives code drift on either side.

Fuzzy (first-initial + last-name) matches must not contradict the team
evidence: a fuzzy candidate whose team disagrees in every comparable season
is rejected to unmatched rather than glued to a same-initial stranger
(the Joshua Baez -> Javier Baez failure mode).

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
from collections import Counter, defaultdict
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


def _entry():
    return {"name": None, "seasons": set(), "teams": {}}


def build_mlb_index():
    """Two indexes across all seasons, each key -> {mlbam_id: {name, seasons,
    teams{yr: teamId}}}: exact normname, and the forgiving initial key. The
    season listings' currentTeam is season-accurate (verified: Cole 2019 ->
    HOU 117), which is what makes team disambiguation free."""
    idx: dict = defaultdict(lambda: defaultdict(_entry))
    ikey: dict = defaultdict(lambda: defaultdict(_entry))
    for yr in range(FIRST_SEASON, LAST_SEASON + 1):
        people = get(f"{API}/sports/1/players?season={yr}").get("people", [])
        for p in people:
            team_id = (p.get("currentTeam") or {}).get("id")
            for tbl, k in ((idx, norm(p.get("fullName"))), (ikey, initial_key(p.get("fullName")))):
                e = tbl[k][p["id"]]
                e["name"] = p.get("fullName")
                e["seasons"].add(yr)
                if team_id:
                    e["teams"][yr] = team_id
        print("  MLB %d: %d players" % (yr, len(people)), flush=True)
        time.sleep(PACING)
    return idx, ikey


def cbs_players(cur) -> list[dict]:
    """Distinct CBS players with seasons of REAL production (universe) plus
    current-roster players, each with team evidence {yr: cbs_team_code}."""
    seasons = defaultdict(set)
    names = {}
    evidence = defaultdict(dict)
    # FA universe: non-empty seasons; TM = that season's team (2014+).
    cur.execute("""
        SELECT f.value:id::string, f.value:name::string, s.season_year,
               NULLIF(TRIM(COALESCE(f.value:TM::string, '')), '')
        FROM RAW.CBS_SEASON_STATS s,
             LATERAL FLATTEN(input => s.payload:body:league_stats:players) f
        WHERE s.league_key='cbs-bsb'
          AND TRIM(COALESCE(f.value:FPTS::string,'')) NOT IN ('', '0')
    """)
    for pid, name, yr, tm in cur.fetchall():
        seasons[pid].add(int(yr))
        names[pid] = name
        if tm and tm.upper() != "SEASON":
            evidence[pid][int(yr)] = tm.upper()
    # Current rosters: rostered players (absent from the FA universe);
    # pro_team here is the live current team -> current-season evidence.
    cur.execute("""
        WITH latest AS (SELECT MAX(roster_date) d FROM RAW.CBS_ROSTERS WHERE league_key='cbs-bsb')
        SELECT DISTINCT p.value:id::string, p.value:fullname::string,
               NULLIF(TRIM(COALESCE(p.value:pro_team::string, '')), '')
        FROM RAW.CBS_ROSTERS r, latest,
             LATERAL FLATTEN(input=>r.payload:body:rosters:teams) t,
             LATERAL FLATTEN(input=>t.value:players) p
        WHERE r.roster_date = latest.d
    """)
    for pid, name, pro in cur.fetchall():
        names.setdefault(pid, name)
        seasons.setdefault(pid, set())
        if pro:
            evidence[pid].setdefault(LAST_SEASON, pro.upper())
    return [{"cbs_id": pid, "name": names[pid], "seasons": seasons[pid],
             "evidence": dict(evidence.get(pid, {}))} for pid in names]


def learn_team_map(players: list[dict], idx: dict) -> dict:
    """CBS team code -> MLBAM team id, learned from unique-exact-name matches'
    (season, code) x (season, teamId) co-occurrence. A code only enters the
    map when its top team dominates the runner-up 3x -- a wrong entry would
    actively mis-split, so ambiguous codes are left out (those rows then
    contribute season evidence only)."""
    counts: dict = defaultdict(Counter)
    for p in players:
        cands = idx.get(norm(p["name"]), {})
        if len(cands) != 1:
            continue
        e = next(iter(cands.values()))
        for yr, code in p["evidence"].items():
            tid = e["teams"].get(yr)
            if tid:
                counts[code][tid] += 1
    team_map = {}
    for code, c in counts.items():
        top = c.most_common(2)
        if len(top) == 1 or top[0][1] >= 3 * top[1][1]:
            team_map[code] = top[0][0]
    return team_map


def _score(cands: dict, player: dict, team_map: dict):
    """Rank candidates by 3*team-season agreements + season overlaps.
    Returns the sorted list of (composite, team_hits, overlap, career, mid, entry)."""
    ps, ev = player["seasons"], player["evidence"]
    ranked = []
    for mid, e in cands.items():
        overlap = sum(1 for yr in ps if yr in e["seasons"])
        team_hits = sum(1 for yr, code in ev.items()
                        if team_map.get(code) is not None
                        and e["teams"].get(yr) == team_map[code])
        ranked.append((3 * team_hits + overlap, team_hits, overlap,
                       len(e["seasons"]), mid, e))
    ranked.sort(key=lambda t: (t[0], t[1], t[2], t[3]), reverse=True)
    return ranked


def match(player: dict, idx: dict, ikey: dict, team_map: dict) -> dict:
    """Resolve one CBS player to an MLBAM id. Exact normname first (team +
    season scored when ambiguous); then the forgiving initial-key fallback,
    which must not contradict the team evidence. Returns method for review.

    UNIQUE candidates still pass a season-overlap sanity check: a CBS
    production season (FPTS != 0) means the player appeared in MLB that
    year, so their true MLBAM id MUST be in that season's listing. A unique
    candidate sharing ZERO of the player's seasons is a different person
    wearing the same bare name -- the Michael A. Taylor class, where the
    true player's statsapi form carries a middle initial so the bare
    'Michael Taylor' key holds only a 2011-14 stranger. Exact-unique falls
    through to the initial-key pool (where team+season scoring can see the
    real candidate); a fuzzy-unique that fails the check rejects to
    unmatched."""
    out = {"mlbam_id": None, "mlbam_name": None, "method": "unmatched"}
    ev = player["evidence"]

    def impossible(e):
        return bool(player["seasons"]) and not any(
            yr in e["seasons"] for yr in player["seasons"])

    cands = idx.get(norm(player["name"]), {})
    if len(cands) == 1:
        mid, e = next(iter(cands.items()))
        if not impossible(e):
            # A bare-name index can narrow to a same-name stranger whose
            # career merely GRAZES the player's seasons (spring 40-man
            # listings) while the real player hides under a middle-initial
            # form in the initial-key pool. Before trusting exact-unique,
            # let the wider pool compete ON TEAM EVIDENCE: a rival wins
            # only by strictly beating the exact candidate on BOTH team
            # agreement and composite score -- when the exact candidate is
            # genuinely right, a rival would have to out-agree the player's
            # own team trail, which it can't.
            fc = ikey.get(initial_key(player["name"]), {})
            exact_rank = None
            if len(fc) > 1 and mid in fc:
                ranked = _score(fc, player, team_map)
                top = ranked[0]
                exact_rank = next((t for t in ranked if t[4] == mid), None)
                if (exact_rank is not None and top[4] != mid
                        and top[1] > exact_rank[1] and top[0] > exact_rank[0]):
                    return {**out, "mlbam_id": top[4], "mlbam_name": top[5]["name"],
                            "method": "name_evidence_override"}
            return {**out, "mlbam_id": mid, "mlbam_name": e["name"], "method": "name_unique"}
        # zero-overlap exact-unique: fall through to the initial-key pool
    if len(cands) > 1:
        ranked = _score(cands, player, team_map)
        top, runner = ranked[0], ranked[1]
        if top[0] > runner[0]:
            method = ("name_team_season" if top[1] > 0 else
                      "name_season_overlap" if player["seasons"] else "name_ambiguous")
        else:
            method = "name_ambiguous"   # true tie -- the collision flag guards it
        return {**out, "mlbam_id": top[4], "mlbam_name": top[5]["name"], "method": method}

    # Exact failed -> forgiving (first-initial, last-name).
    fc = ikey.get(initial_key(player["name"]), {})
    if len(fc) == 1:
        mid, e = next(iter(fc.items()))
        if impossible(e):
            return out   # zero season overlap: same-name stranger, reject
        comparable = [yr for yr, code in ev.items()
                      if team_map.get(code) is not None and e["teams"].get(yr)]
        if comparable and not any(
                e["teams"].get(yr) == team_map.get(code) for yr, code in ev.items()):
            return out   # team evidence exists and disagrees everywhere: reject
        return {**out, "mlbam_id": mid, "mlbam_name": e["name"], "method": "fuzzy_unique"}
    if len(fc) > 1:
        ranked = _score(fc, player, team_map)
        top = ranked[0]
        if top[1] >= 1 and top[0] > ranked[1][0]:
            return {**out, "mlbam_id": top[4], "mlbam_name": top[5]["name"],
                    "method": "fuzzy_team_season"}
        if player["seasons"] and top[2] > 0 and top[0] > ranked[1][0]:
            return {**out, "mlbam_id": top[4], "mlbam_name": top[5]["name"],
                    "method": "fuzzy_overlap"}
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

    team_map = learn_team_map(players, idx)
    print("learned team map (%d codes):" % len(team_map))
    for code in sorted(team_map):
        print("  %-4s -> %d" % (code, team_map[code]))
    unmapped = Counter(code for p in players for code in p["evidence"].values()
                       if code not in team_map)
    if unmapped:
        print("codes seen but NOT learned (season-only evidence):", dict(unmapped))

    rows, by_method = [], defaultdict(int)
    for p in players:
        m = match(p, idx, ikey, team_map)
        by_method[m["method"]] += 1
        rows.append({"cbs_player_id": p["cbs_id"], "cbs_name": p["name"], **m})

    # Flag remaining common-name collisions: distinct CBS ids resolving to ONE
    # MLBAM (beyond the legit Ohtani 900/901 two-way split) survived even the
    # team pass -- mark for review rather than silently trust.
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
    print("collision-flagged (need review):",
          sum(1 for r in rows if r["method"].endswith("_COLLISION")))
    print("mapped: %d / %d (%.1f%%)" % (len(mapped), len(rows), 100*len(mapped)/len(rows)))
    # Spot-check known ids, including the formerly-collided pairs.
    known = {"1893753": 543037, "1739608": 545361, "2071264": 592450, "530362": 434378,
             "2250617": 669257,   # Will Smith the catcher (LAD), not the reliever
             "1666825": 519293,   # Will Smith the reliever
             "2862994": 677651,   # Luis Garcia the Astros SP
             "2768551": 671277,   # Luis Garcia the Nationals infielder
             "2069487": 472610,   # Luis Garcia the veteran reliever
        "1954849": 572191,   # Michael A. Taylor -- NOT the bare-named 2011-14 Michael Taylor
        "29038651": 669796}  # Jose E. Hernandez the Pirates/Dodgers LHP -- not the 90s infielder
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
