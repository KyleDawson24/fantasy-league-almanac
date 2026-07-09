#!/usr/bin/env python3
"""Universal MLB stats extract (MLB-70): the baseball layer, sourced once.

After the 2026-07-09 pivot, player production comes from the free public MLB
Stats API (statsapi.mlb.com) rather than any platform's stats feed -- portable
(reusable across CBS/Yahoo/Fantrax) and complete (all MLB players, no FA-only
gap). Joined to platform membership downstream; feeds MLB-62's calculated_
recompute (universal stats x scoring rules, reconciled to platform_ FPTS).

Reads raw.CBS_MLBAM_CROSSWALK for the MLBAM ids to fetch. Per player:
  season/<mlbam>.json           yearByYear stats (all seasons, hitting+pitching)
  gamelog/<mlbam>/<yr>_<grp>.json  per-game lines (carries QS-deriving IP/ER,
                                   inheritedRunners, and the game's team --
                                   which also closes the 21 crosswalk
                                   same-name collisions)

Landing: gitignored JSON under <repo>/data/mlb_stats/, append-only, plus a
manifest. IDEMPOTENT -- a landed file is skipped, so rerun resumes (sleep-safe).
Public API, polite pacing (~0.35s), no key, no CBS token.

Usage:
    py extract/mlb_stats.py                 # full sweep (resumable)
    py extract/mlb_stats.py --limit 5       # smoke test: first 5 players
    py extract/mlb_stats.py --players 434378 592450
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import snowflake.connector
from dotenv import load_dotenv

API = "https://statsapi.mlb.com/api/v1"
PACING = 0.35
RETRY_BACKOFF = [3, 10, 30]
GROUPS = ("hitting", "pitching")


def find_repo_root(start: Path) -> Path:
    for c in [start, *start.parents]:
        if (c / ".env").is_file():
            return c
    raise SystemExit("no .env found from %s" % start)


def get(url: str):
    """GET JSON with polite pacing + backoff. None on give-up."""
    for attempt, backoff in enumerate([0] + RETRY_BACKOFF):
        if backoff:
            time.sleep(backoff)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "espn-league-manager/mlb_stats"})
            with urllib.request.urlopen(req, timeout=45) as r:
                raw = r.read()
            time.sleep(PACING + random.uniform(0, 0.15))
            return json.loads(raw)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
            continue
    return None


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def season_groups(year_by_year) -> dict:
    """{season: set(groups active)} from a yearByYear payload."""
    out: dict = {}
    for block in (year_by_year or {}).get("stats", []):
        grp = (block.get("group") or {}).get("displayName")
        for sp in block.get("splits", []):
            yr = sp.get("season")
            if yr and grp:
                out.setdefault(str(yr), set()).add(grp)
    return out


def crosswalk_ids(root: Path) -> list[dict]:
    load_dotenv(root / ".env")
    conn = snowflake.connector.connect(
        account=os.getenv("SNOWFLAKE_ACCOUNT"), user=os.getenv("SNOWFLAKE_USER"),
        database=os.getenv("SNOWFLAKE_DATABASE"), schema=os.getenv("SNOWFLAKE_SCHEMA"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        private_key_file=os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH"),
        private_key_file_pwd=os.getenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"),
        password=os.getenv("SNOWFLAKE_PASSWORD"))
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT mlbam_id, MAX(cbs_name) FROM RAW.CBS_MLBAM_CROSSWALK "
                "WHERE mlbam_id IS NOT NULL GROUP BY 1 ORDER BY 1")
    rows = [{"mlbam_id": int(r[0]), "name": r[1]} for r in cur.fetchall()]
    cur.close(); conn.close()
    return rows


def append_manifest(out_dir: Path, rec: dict) -> None:
    with (out_dir / "extract_manifest.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, help="only the first N crosswalk players")
    ap.add_argument("--players", nargs="+", type=int, help="explicit MLBAM ids")
    ap.add_argument("--force", action="store_true", help="re-fetch landed files")
    args = ap.parse_args()

    root = find_repo_root(Path(__file__).resolve().parent)
    out_dir = root / "data" / "mlb_stats"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")

    if args.players:
        players = [{"mlbam_id": p, "name": None} for p in args.players]
    else:
        players = crosswalk_ids(root)
        if args.limit:
            players = players[:args.limit]

    print("mlb_stats: %d players -> %s" % (len(players), out_dir), flush=True)
    calls = seasons_fetched = players_done = skipped = 0
    for i, pl in enumerate(players):
        mid = pl["mlbam_id"]
        # 1. Season (yearByYear) -- also tells us which (season, group) exist.
        sfile = out_dir / "season" / ("%d.json" % mid)
        if sfile.is_file() and sfile.stat().st_size > 0 and not args.force:
            yby = json.loads(sfile.read_text(encoding="utf-8"))["payload"]
        else:
            yby = get("%s/people/%d/stats?stats=yearByYear&group=hitting,pitching" % (API, mid))
            calls += 1
            if yby is None:
                append_manifest(out_dir, {"ts": stamp, "mlbam": mid, "note": "season fetch failed"})
                continue
            write_json(sfile, {"fetched_at": stamp, "mlbam_id": mid, "stat": "yearByYear",
                               "payload": yby})
        # 2. Game logs per active (season, group).
        for yr, grps in sorted(season_groups(yby).items()):
            for grp in grps:
                g = grp.lower()
                if g not in GROUPS:
                    continue
                gfile = out_dir / "gamelog" / str(mid) / ("%s_%s.json" % (yr, g))
                if gfile.is_file() and gfile.stat().st_size > 0 and not args.force:
                    skipped += 1
                    continue
                payload = get("%s/people/%d/stats?stats=gameLog&group=%s&season=%s"
                              % (API, mid, g, yr))
                calls += 1
                if payload is None:
                    append_manifest(out_dir, {"ts": stamp, "mlbam": mid, "season": yr,
                                              "group": g, "note": "gamelog fetch failed"})
                    continue
                write_json(gfile, {"fetched_at": stamp, "mlbam_id": mid, "season": yr,
                                   "group": g, "stat": "gameLog", "payload": payload})
                seasons_fetched += 1
        players_done += 1
        if (i + 1) % 100 == 0:
            print("  %d/%d players | %d calls | %d gamelog-seasons landed"
                  % (i + 1, len(players), calls, seasons_fetched), flush=True)

    print("done: %d players, %d gamelog-seasons landed, %d already-present, %d API calls."
          % (players_done, seasons_fetched, skipped, calls), flush=True)


if __name__ == "__main__":
    main()
