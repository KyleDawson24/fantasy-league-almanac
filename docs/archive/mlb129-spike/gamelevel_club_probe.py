"""
Does ESPN's GAME-LEVEL split (statSplitTypeId==5) carry club-of-game for the
BACKFILLED 2025 season -- or is it, like the player-level stamp, frozen?

Falsifiable, using MLB-159's own named cases:
  Sonny Gray 32082   2025 truth = STL   (warehouse stamps 'Bos')
  Tyler Anderson 32151 2025 truth = LAA (warehouse stamps 'FA')
  Marcell Ozuna 31668  2025 truth = ATL (warehouse stamps 'FA')
  Max Scherzer 28976   2025 truth = TOR (warehouse stamps 'FA')

If the game-level proTeamId reads STL/LAA/ATL/TOR, ESPN serves club-of-game
directly and MLB-159 Exit 1 needs no MLBAM crosswalk at all.
If it reads BOS/FA, the game-level field is the same frozen stamp and the
crosswalk is the only path.

Read-only.
"""
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

import requests
from dotenv import load_dotenv
from espn_api.baseball import constant as espn_const

ROOT = Path(r"C:\Users\kyled\projects\espn-league-manager")
load_dotenv(ROOT / ".env")

ESPN_S2 = os.getenv("ESPN_S2")
SWID = os.getenv("SWID")
LEAGUE_ID = int(os.getenv("LEAGUE_ID"))
BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons"
COOKIES = {"swid": SWID, "espn_s2": ESPN_S2}

PRO_TEAM_MAP = espn_const.PRO_TEAM_MAP

CASES_2025 = {
    32082: ("Sonny Gray", "StL", "warehouse says Bos"),
    32151: ("Tyler Anderson", "LAA", "warehouse says FA"),
    31668: ("Marcell Ozuna", "Atl", "warehouse says FA"),
    28976: ("Max Scherzer", "Tor", "warehouse says FA"),
}
CASES_2026 = {
    42360: ("Curtis Mead", "Bos late / Wsh earlier", "warehouse credits Bos 20 PA vs BR 2"),
}


def playercard(year, ids, scoring_period=None):
    url = f"{BASE}/{year}/segments/0/leagues/{LEAGUE_ID}"
    ffilter = {"players": {"filterIds": {"value": list(ids)}}}
    params = {"view": "kona_playercard"}
    if scoring_period is not None:
        params["scoringPeriodId"] = scoring_period
    r = requests.get(url, params=params, cookies=COOKIES,
                     headers={"x-fantasy-filter": json.dumps(ffilter)}, timeout=45)
    r.raise_for_status()
    return r.json()


def kona_period(year, scoring_period, limit=1500):
    url = f"{BASE}/{year}/segments/0/leagues/{LEAGUE_ID}"
    ffilter = {"players": {"limit": limit,
                           "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}}
    r = requests.get(url, params={"view": "kona_player_info",
                                  "scoringPeriodId": scoring_period},
                     cookies=COOKIES,
                     headers={"x-fantasy-filter": json.dumps(ffilter)}, timeout=45)
    r.raise_for_status()
    return r.json()


def abbr(tid):
    return PRO_TEAM_MAP.get(tid, f"?{tid}")


def show(year, cases, data, source):
    print(f"\n{'=' * 78}\n{source}  year={year}\n{'=' * 78}")
    for entry in data.get("players") or []:
        p = entry.get("player") or {}
        pid = p.get("id")
        if pid not in cases:
            continue
        name, truth, note = cases[pid]
        print(f"\n{name}  (espn id {pid})")
        print(f"   TRUTH {year}: {truth}   [{note}]")
        print(f"   player-level proTeamId = {p.get('proTeamId')} -> {abbr(p.get('proTeamId'))}")
        game_clubs = Counter()
        periods = defaultdict(set)
        n = 0
        for s in p.get("stats") or []:
            if s.get("statSplitTypeId") != 5:
                continue
            n += 1
            tid = s.get("proTeamId")
            game_clubs[abbr(tid)] += 1
            periods[s.get("scoringPeriodId")].add(abbr(tid))
        print(f"   game-level splits (statSplitTypeId==5): {n}")
        if game_clubs:
            print(f"   game-level club distribution: {dict(game_clubs)}")
        else:
            print("   -- no game-level splits returned at this call shape --")


def main():
    print("MLB-159/129 -- is ESPN's GAME-LEVEL club real for the backfilled season?")

    # 1) playercard, whole season, no scoringPeriod pin
    for year, cases in ((2025, CASES_2025), (2026, CASES_2026)):
        try:
            show(year, cases, playercard(year, cases.keys()), "kona_playercard (no period pin)")
        except Exception as e:
            print(f"[warn] playercard {year}: {e}")

    # 2) playercard pinned to a mid-season period (2025 sp=120 ~ late July)
    for year, cases, sp in ((2025, CASES_2025, 120), (2026, CASES_2026, 120)):
        try:
            show(year, cases, playercard(year, cases.keys(), sp),
                 f"kona_playercard (scoringPeriodId={sp})")
        except Exception as e:
            print(f"[warn] playercard {year} sp={sp}: {e}")

    # 3) the exact call the extract makes, several 2025 periods
    for sp in (60, 90, 120, 150):
        try:
            data = kona_period(2025, sp)
            hit = {}
            for entry in data.get("players") or []:
                p = entry.get("player") or {}
                if p.get("id") in CASES_2025:
                    hit[p["id"]] = p
            print(f"\n-- kona_player_info 2025 sp={sp}: {len(hit)}/{len(CASES_2025)} cases present")
            for pid, p in hit.items():
                name, truth, _ = CASES_2025[pid]
                splits = [s for s in (p.get("stats") or [])
                          if s.get("statSplitTypeId") == 5
                          and s.get("scoringPeriodId") == sp]
                gl = [abbr(s.get("proTeamId")) for s in splits]
                print(f"     {name:16s} truth={truth:4s}  player-level={abbr(p.get('proTeamId')):4s}"
                      f"  game-level={gl}")
        except Exception as e:
            print(f"[warn] kona 2025 sp={sp}: {e}")


if __name__ == "__main__":
    main()
