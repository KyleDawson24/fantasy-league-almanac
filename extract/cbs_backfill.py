#!/usr/bin/env python3
"""CBS historical backfill: per-player gamelog sweep, 2004-2025 (MLB-45).

The real half of the 20-year archive: season-grain `league/stats?timeframe`
supplies each season's player universe (sparse 1-7 players 2004-2010, ~100+
2011-2019, ~450+ 2020+), and `players/gamelog?player_id&timeframe` supplies
authentic per-game stat lines in the league's own stat vocabulary (fielding
included, `.ytd` running counters). The fantasy layer — who rostered or
started anyone historically — is genuinely unrecoverable and is NOT here;
cbs_capture.py preserves it for 2026 only.

MUSEUM RULE (standing): read-only forever. This script reuses
cbs_capture's GET-only whitelisted Client — polite pacing, backoff, token
from the repo-root .env, never printed, params redacted everywhere.

Authenticity is judged BY CONTENT, never by HTTP 200: a gamelog is landed
only if every entry's game_date falls inside the requested timeframe year
(shape verified 2026-07-07: body.gamelog[*].game_date is YYYYMMDD; Votto
2015 = 159 entries, 2015-04-06 through 2015-10-04). A response claiming a
past season but carrying other-year dates is fake history and is rejected.

Landing (append-only, idempotent — rerun to resume):
    <repo-root>/data/cbs_raw/<league>/history/
        stats/<year>.json               one envelope per season universe
        gamelog/<year>/<player_id>.json one envelope per player-season
        backfill_manifest.jsonl         every call, params redacted
        verification_<stamp>.json       per-year content-verified summary

Usage:
    python extract/cbs_backfill.py --probe               # shapes only, lands nothing
    python extract/cbs_backfill.py --backfill            # full 2004-2025 sweep
    python extract/cbs_backfill.py --backfill --years 2015 2016
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from cbs_capture import (
    Client,
    envelope,
    find_repo_root,
    load_env,
    write_json,
)

FIRST_SEASON = 2004
LAST_SEASON = 2025          # 2026 is the live season; it gets swept at rollover


# --------------------------------------------------------------------------
# Payload readers (shapes probed 2026-07-07; hard-fail if CBS changes them)
# --------------------------------------------------------------------------

def universe_from_stats(payload) -> list[dict]:
    """body.league_stats.players -> [{id, name, ...stat columns}]."""
    try:
        players = payload["body"]["league_stats"]["players"]
    except (KeyError, TypeError):
        raise SystemExit("league/stats shape changed (no body.league_stats.players) "
                         "— re-probe before trusting anything.")
    if not isinstance(players, list):
        raise SystemExit("league/stats players is not a list — re-probe.")
    return [p for p in players if isinstance(p, dict) and p.get("id")]


def gamelog_entries(payload) -> list | None:
    """body.gamelog -> list of per-game entries, or None on shape mismatch."""
    node = payload.get("body", {}).get("gamelog") if isinstance(payload, dict) else None
    return node if isinstance(node, list) else None


def offyear_entries(entries: list, year: int) -> int:
    """Count entries dated outside the requested year.

    Null-date rows are postponed/cancelled-game placeholders (opponent
    and scores null too) and validate on `point` instead — 2021 alone
    has ~145 of them league-wide (the COVID-makeup era), and treating
    them as off-year rejected 112 authentic 2021 gamelogs on the first
    sweep. A dated entry from the wrong year is still fake history.
    """
    prefix = str(year)
    bad = 0
    for e in entries:
        if not isinstance(e, dict):
            bad += 1
            continue
        stamp = e.get("game_date")
        if stamp is None:
            stamp = e.get("point")
        if not str(stamp or "").startswith(prefix):
            bad += 1
    return bad


# --------------------------------------------------------------------------
# Landing
# --------------------------------------------------------------------------

def append_manifest(history_dir: Path, meta: dict, out_file: str | None) -> None:
    record = dict(meta)
    record["out_file"] = out_file
    history_dir.mkdir(parents=True, exist_ok=True)
    with (history_dir / "backfill_manifest.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_landed_payload(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))["payload"]


# --------------------------------------------------------------------------
# Modes
# --------------------------------------------------------------------------

def run_probe(client: Client) -> None:
    print("PROBE (nothing landed, params redacted):", flush=True)
    for year in ("2015", "2005"):
        payload, meta = client.request("stats", {"timeframe": year})
        if payload is None:
            print("  stats %s FAILED: HTTP %s %s"
                  % (year, meta.get("http_status"), meta.get("note", "")), flush=True)
            continue
        universe = universe_from_stats(payload)
        print("  stats %s: %d players; first: %s"
              % (year, len(universe),
                 [(p["id"], p.get("name")) for p in universe[:2]]), flush=True)
        if year == "2015" and universe:
            pid = universe[0]["id"]
            gl_payload, gl_meta = client.request("gamelog", {"player_id": pid, "timeframe": year})
            entries = gamelog_entries(gl_payload) if gl_payload else None
            if entries is None:
                print("  gamelog %s FAILED: HTTP %s" % (pid, gl_meta.get("http_status")), flush=True)
            else:
                print("  gamelog %s (%s) %s: %d games, %d off-year dates"
                      % (pid, universe[0].get("name"), year, len(entries),
                         offyear_entries(entries, int(year))), flush=True)


def run_backfill(client: Client, history_dir: Path, years: list[int], force: bool) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    league = client.league
    year_summaries = {}
    problems = []

    for year in years:
        stats_file = history_dir / "stats" / ("%d.json" % year)
        if stats_file.is_file() and stats_file.stat().st_size > 0 and not force:
            stats_payload = load_landed_payload(stats_file)
        else:
            stats_payload, meta = client.request("stats", {"timeframe": str(year)})
            if stats_payload is None:
                problems.append("%d: season stats fetch failed (%s)" % (year, meta.get("note")))
                append_manifest(history_dir, meta, None)
                year_summaries[year] = {"universe": None, "note": "stats fetch failed"}
                continue
            write_json(stats_file, envelope(stats_payload, meta, league, year))
            append_manifest(history_dir, meta, str(stats_file))

        universe = universe_from_stats(stats_payload)
        landed = skipped = empty = 0
        games_total = 0
        rejected, failed = [], []

        for i, player in enumerate(universe):
            pid = str(player["id"])
            out = history_dir / "gamelog" / str(year) / ("%s.json" % pid)
            if out.is_file() and out.stat().st_size > 0 and not force:
                skipped += 1
                continue
            payload, meta = client.request("gamelog", {"player_id": pid, "timeframe": str(year)})
            entries = gamelog_entries(payload) if payload is not None else None
            if entries is None:
                failed.append(pid)
                append_manifest(history_dir, meta, None)
            elif offyear_entries(entries, year):
                # Fake history must never enter the archive.
                rejected.append(pid)
                meta["note"] = "REJECTED: %d off-year game_date entries" % offyear_entries(entries, year)
                append_manifest(history_dir, meta, None)
            else:
                write_json(out, envelope(payload, meta, league, year))
                append_manifest(history_dir, meta, str(out))
                landed += 1
                games_total += len(entries)
                empty += 0 if entries else 1
            if (i + 1) % 50 == 0:
                print("  %d: %d/%d players..." % (year, i + 1, len(universe)), flush=True)

        year_summaries[year] = {
            "universe": len(universe), "landed": landed, "already_present": skipped,
            "empty_gamelogs": empty, "games_landed": games_total,
            "failed": failed, "rejected_fake_history": rejected,
        }
        if failed:
            problems.append("%d: %d gamelog fetches failed" % (year, len(failed)))
        if rejected:
            problems.append("%d: %d gamelogs REJECTED as fake history" % (year, len(rejected)))
        print("%d: universe=%d landed=%d present=%d empty=%d games=%d failed=%d rejected=%d"
              % (year, len(universe), landed, skipped, empty, games_total,
                 len(failed), len(rejected)), flush=True)

    summary = {
        "verified_at": stamp,
        "years": {str(y): year_summaries[y] for y in sorted(year_summaries)},
        "verdict": "PASS" if not problems else "FAIL: " + "; ".join(problems),
    }
    write_json(history_dir / ("verification_%s.json" % stamp), summary)
    print("VERIFY: %s" % summary["verdict"], flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--probe", action="store_true", help="shape check only; lands nothing")
    mode.add_argument("--backfill", action="store_true", help="stats + gamelog sweep + verify")
    ap.add_argument("--years", nargs="+", type=int,
                    default=list(range(FIRST_SEASON, LAST_SEASON + 1)))
    ap.add_argument("--force", action="store_true", help="re-fetch player-seasons already landed")
    args = ap.parse_args()

    root = find_repo_root(Path(__file__).resolve().parent)
    env = load_env(root / ".env")
    token, league = env.get("CBS_TOKEN"), env.get("CBS_LEAGUE", "bsb")
    if not token:
        raise SystemExit("CBS_TOKEN missing from .env")
    history_dir = root / "data" / "cbs_raw" / league / "history"
    client = Client(token, league)

    print("cbs_backfill: league=%s years=%d-%d landing=%s"
          % (league, min(args.years), max(args.years), history_dir), flush=True)
    if args.probe:
        run_probe(client)
    else:
        run_backfill(client, history_dir, sorted(args.years), args.force)
    print("done: %d API calls, read-only." % client.calls, flush=True)


if __name__ == "__main__":
    main()
