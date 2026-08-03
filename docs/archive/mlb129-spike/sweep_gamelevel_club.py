"""
MLB-129 spike -- sweep ESPN's per-scoring-period GAME-LEVEL club stamp.

extract.py already calls kona_player_info per scoring period and walks the
statSplitTypeId==5 splits for stats. It reads `stats` and `appliedTotal` and
DISCARDS `proTeamId` on the split -- which is the club of THAT GAME.

This sweep lifts that field for every (year, scoring_period, player) so the
coverage can be measured against the warehouse's active-slot weight.

Idempotent + resumable: completed (year, period) pairs are recorded in a
done-file and skipped on restart. Read-only against ESPN; writes only NDJSON
+ a log to the scratchpad. Touches no warehouse table.
"""
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(r"C:\Users\kyled\projects\espn-league-manager")
load_dotenv(ROOT / ".env")

ESPN_S2 = os.getenv("ESPN_S2")
SWID = os.getenv("SWID")
LEAGUE_ID = int(os.getenv("LEAGUE_ID"))
BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons"
COOKIES = {"swid": SWID, "espn_s2": ESPN_S2}

OUT = Path(sys.argv[1])
NDJSON = OUT / "gamelevel_club.ndjson"
DONE = OUT / "gamelevel_club.done"
LOG = OUT / "gamelevel_club.log"

# (year, first_period, last_period) -- from the warehouse's own period range
TARGETS = [(2025, 1, 195), (2026, 1, 131)]


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_done():
    if not DONE.exists():
        return set()
    out = set()
    for ln in DONE.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln:
            y, p = ln.split(",")
            out.add((int(y), int(p)))
    return out


def mark_done(year, period):
    with DONE.open("a", encoding="utf-8") as f:
        f.write(f"{year},{period}\n")


def fetch(year, period, attempt=0):
    url = f"{BASE}/{year}/segments/0/leagues/{LEAGUE_ID}"
    ffilter = {"players": {"limit": 1500,
                           "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}}
    try:
        r = requests.get(
            url,
            params={"view": "kona_player_info", "scoringPeriodId": period},
            cookies=COOKIES,
            headers={"x-fantasy-filter": json.dumps(ffilter)},
            timeout=60,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        if attempt < 3:
            time.sleep(3 * (attempt + 1))
            return fetch(year, period, attempt + 1)
        log(f"  !! {year} sp={period} FAILED after retries: {e}")
        return None


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    done = load_done()
    log(f"start; {len(done)} (year,period) pairs already done")

    with NDJSON.open("a", encoding="utf-8") as sink:
        for year, lo, hi in TARGETS:
            for period in range(lo, hi + 1):
                if (year, period) in done:
                    continue
                data = fetch(year, period)
                if data is None:
                    continue
                n_players = 0
                n_gl = 0
                for entry in data.get("players") or []:
                    p = entry.get("player") or {}
                    pid = p.get("id")
                    if pid is None:
                        continue
                    # game-level splits scoped to THIS scoring period
                    gl = []
                    for s in p.get("stats") or []:
                        if s.get("statSplitTypeId") != 5:
                            continue
                        if s.get("scoringPeriodId") != period:
                            continue
                        gl.append({
                            "proTeamId": s.get("proTeamId"),
                            "gameId": s.get("externalId"),
                            "statSourceId": s.get("statSourceId"),
                            "appliedTotal": s.get("appliedTotal"),
                        })
                    if not gl:
                        continue          # no appearance this period -> no row
                    n_players += 1
                    n_gl += len(gl)
                    sink.write(json.dumps({
                        "season_year": year,
                        "scoring_period": period,
                        "player_id": pid,
                        "player_name": p.get("fullName"),
                        "player_level_proTeamId": p.get("proTeamId"),
                        "game_splits": gl,
                    }) + "\n")
                sink.flush()
                mark_done(year, period)
                log(f"{year} sp={period:3d}: {n_players} players w/ game splits, {n_gl} splits")
    log("DONE")


if __name__ == "__main__":
    main()
