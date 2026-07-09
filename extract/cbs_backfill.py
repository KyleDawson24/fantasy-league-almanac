#!/usr/bin/env python3
"""CBS historical backfill: per-player gamelog sweep, 2004-2025 (MLB-45).

The real half of the 20-year archive: season-grain `league/stats?timeframe`
supplies each season's player universe (sparse 1-7 players 2004-2010, ~100+
2011-2019, ~450+ 2020+), and `players/gamelog?player_id&timeframe` supplies
authentic per-game stat lines in the league's own stat vocabulary (fielding
included, `.ytd` running counters). The fantasy layer — who rostered or
started anyone historically — is genuinely unrecoverable and is NOT here;
cbs_capture.py preserves it for 2026 only.

The first sweep was hitters-only because the universe query was:
`league/stats` serves the HITTER table by default, and `position=P` is the
pitcher-universe key (probed 2026-07-08, evidence on MLB-45 — 2025 returns
594 pitchers with pitching columns INN/ERA/W/S/HA/BBI...). The decoys fail
SILENTLY: `stats_type=pitching` answers an empty 200, and five other
spellings answer the hitter default. So `--backfill-pitching` gates every
universe on pitching-vocabulary presence, validates the param's semantics
against the anchor year before trusting anything (an empty sparse-era
universe is only believable inside a run whose anchor proved position=P
still means pitchers), and confirms season-grain FPTS presence — the
MLB-62 reconciliation anchor. `players/gamelog` needs NO new param
(Wheeler 2025 verified: complete per-game pitching lines, PPos "SP"), so
pitcher gamelogs ride the exact same per-game date gate, and two-way
players already landed by the hitter sweep skip via the shared
gamelog/<year>/<player_id>.json idempotency.

Except there are no two-way players to overlap: CBS splits them into
two rosterable pseudo-players under sentinel ids that league/stats
history omits from BOTH position tables (so neither sweep's universe
can ever enumerate them), while players/gamelog serves them normally.
The pitching sweep fetches SENTINEL_PLAYERS explicitly — see the
constant for the evidence trail.

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
        stats/<year>.json               one envelope per season hitter universe
        stats_pitching/<year>.json      one envelope per season pitcher universe
        gamelog/<year>/<player_id>.json one envelope per player-season
        backfill_manifest.jsonl         every call, params redacted
        verification_<stamp>.json       per-year content-verified summary

Usage:
    python extract/cbs_backfill.py --probe               # shapes only, lands nothing
    python extract/cbs_backfill.py --backfill            # hitter sweep 2004-2025
    python extract/cbs_backfill.py --backfill-pitching   # pitcher sweep 2004-2025
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

# The pitcher-universe request key and its authenticity gate (MLB-45,
# probed 2026-07-08). INN/ERA are absent from the ENTIRE hitter
# vocabulary (74-key census), so a universe where no row carries either
# is the hitter-default decoy, not a pitcher table.
PITCHING_PARAM = {"position": "P"}
PITCHING_GATE_KEYS = ("INN", "ERA")
# Probe ground truth for validating position=P semantics at run start:
# 2025 answered 594 pitchers, pitching columns, season-grain FPTS.
PITCHING_ANCHOR_YEAR = 2025

# CBS models two-way players as TWO separately-rosterable pseudo-players
# with hand-allocated sentinel ids — league bsb has exactly one such
# pair (2026 roster scan). league/stats history omits the sentinels from
# BOTH position tables (probed 2026-07-09: absent from every landed
# universe, 2004-2025), so universe-driven enumeration can never find
# them — but players/gamelog serves them fine (901's 2021 log = his real
# 23 starts, batting included; 900's = schedule-grain hitting). The
# pitching sweep fetches them explicitly from their MLB debut season.
# Season-grain FPTS for the halves exists NOWHERE in league/stats — the
# MLB-62 anchor has a documented hole here.
SENTINEL_PLAYERS = {
    "900": ("Shohei Ohtani (Batter)", 2018),
    "901": ("Shohei Ohtani (Pitcher)", 2018),
}

# Player-seasons CBS's gamelog endpoint cannot serve — evidence in the
# backfill manifest. Counted as known_unavailable (not failures) so the
# verdict stays meaningful; --force retries them in case CBS heals.
KNOWN_UNAVAILABLE = {
    # Rostered prospect with zero MLB games (debut 2007-09); endpoint
    # answers persistent HTTP 500 (two runs, 8 attempts, 2026-07-07),
    # unlike Bergolla's same-shape 2006 which 200s with an empty log.
    ("2006", "547434"): "persistent HTTP 500; no MLB games that season",
    # The pre-2007 per-game void, pitcher side (MLB-45, 2026-07-09): the
    # gamelog endpoint serves NO daily rows before 2007 — hitters get an
    # empty 200 (Bergolla 2004/2005 landed empty despite real games),
    # pitchers get persistent HTTP 500. These seven are stars who
    # certainly pitched those seasons, so it's a CBS availability gap,
    # not "no games"; confirmed 500 across two runs (8 attempts each,
    # 2026-07-08 + 2026-07-09). Their season-grain universe rows DID
    # land (stats_pitching/), so the seasons aren't invisible. --force
    # retries in case CBS ever heals the early archive.
    ("2004", "390851"): "pre-2007 gamelog void; persistent HTTP 500 (Greinke)",
    ("2005", "390851"): "pre-2007 gamelog void; persistent HTTP 500 (Greinke)",
    ("2005", "555244"): "pre-2007 gamelog void; persistent HTTP 500 (Hill)",
    ("2005", "530362"): "pre-2007 gamelog void; persistent HTTP 500 (Verlander)",
    ("2006", "390851"): "pre-2007 gamelog void; persistent HTTP 500 (Greinke)",
    ("2006", "555244"): "pre-2007 gamelog void; persistent HTTP 500 (Hill)",
    ("2006", "530362"): "pre-2007 gamelog void; persistent HTTP 500 (Verlander)",
}


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


def universe_from_stats_pitching(payload) -> list[dict]:
    """Pitcher-table variant: tolerant of an EMPTY table, strict on shape.

    Sparse-era seasons retained 1-7 players TOTAL, so a season with no
    pitchers at all is plausible and must not kill the sweep; a missing
    players node under an intact league_stats envelope reads as that
    empty table. A missing league_stats is still a hard shape failure —
    emptiness and shape drift must never blur.
    """
    try:
        node = payload["body"]["league_stats"]
    except (KeyError, TypeError):
        raise SystemExit("league/stats shape changed (no body.league_stats) "
                         "— re-probe before trusting anything.")
    players = node.get("players") if isinstance(node, dict) else None
    if players is None:
        return []
    if not isinstance(players, list):
        raise SystemExit("league/stats players is not a list — re-probe.")
    return [p for p in players if isinstance(p, dict) and p.get("id")]


def pitcher_shaped(row: dict) -> bool:
    return any(k in row for k in PITCHING_GATE_KEYS)


def fpts_rows(universe: list[dict]) -> int:
    """Rows carrying a non-empty season FPTS — the MLB-62 anchor."""
    return sum(1 for p in universe if str(p.get("FPTS") or "").strip() != "")


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
    anchor = str(PITCHING_ANCHOR_YEAR)
    payload, meta = client.request("stats", {"timeframe": anchor, **PITCHING_PARAM})
    if payload is None:
        print("  stats %s position=P FAILED: HTTP %s %s"
              % (anchor, meta.get("http_status"), meta.get("note", "")), flush=True)
    else:
        universe = universe_from_stats_pitching(payload)
        shaped = sum(1 for p in universe if pitcher_shaped(p))
        print("  stats %s position=P: %d players, %d pitcher-shaped, FPTS on %d; first: %s"
              % (anchor, len(universe), shaped, fpts_rows(universe),
                 [(p["id"], p.get("name")) for p in universe[:2]]), flush=True)


def validate_pitching_anchor(client: Client, history_dir: Path, force: bool) -> None:
    """Prove position=P still means 'pitchers' before landing anything.

    The pitching decoys fail silently (empty 200 / hitter default), so an
    empty or hitter-shaped sparse-era universe is ambiguous on its own.
    The anchor year has probe-verified ground truth; only a run whose
    anchor comes back pitcher-shaped may trust emptiness elsewhere. The
    validated response lands (same file the sweep would write), so
    validation costs the run nothing.
    """
    year = PITCHING_ANCHOR_YEAR
    stats_file = history_dir / "stats_pitching" / ("%d.json" % year)
    if stats_file.is_file() and stats_file.stat().st_size > 0 and not force:
        payload = load_landed_payload(stats_file)
    else:
        payload, meta = client.request("stats", {"timeframe": str(year), **PITCHING_PARAM})
        if payload is None:
            raise SystemExit("pitching anchor %d fetch failed (HTTP %s %s) — token dead or "
                             "API down; nothing landed."
                             % (year, meta.get("http_status"), meta.get("note", "")))
        universe = universe_from_stats_pitching(payload)
        if not universe or not any(pitcher_shaped(p) for p in universe):
            append_manifest(history_dir, meta, None)
            raise SystemExit("pitching anchor %d: %d rows, 0 pitcher-shaped — position=P "
                             "no longer serves pitchers; refusing to sweep."
                             % (year, len(universe)))
        write_json(stats_file, envelope(payload, meta, client.league, year))
        append_manifest(history_dir, meta, str(stats_file))
    universe = universe_from_stats_pitching(payload)
    shaped = sum(1 for p in universe if pitcher_shaped(p))
    if not universe or shaped == 0:
        raise SystemExit("pitching anchor %d (landed file): %d rows, 0 pitcher-shaped — "
                         "re-probe before trusting the archive." % (year, len(universe)))
    print("anchor %d: %d pitchers, %d pitcher-shaped, FPTS on %d rows — position=P holds"
          % (year, len(universe), shaped, fpts_rows(universe)), flush=True)


def run_backfill(client: Client, history_dir: Path, years: list[int], force: bool,
                 pitching: bool = False) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    league = client.league
    year_summaries = {}
    problems = []

    stats_subdir = "stats_pitching" if pitching else "stats"
    read_universe = universe_from_stats_pitching if pitching else universe_from_stats
    if pitching:
        validate_pitching_anchor(client, history_dir, force)

    for year in years:
        stats_file = history_dir / stats_subdir / ("%d.json" % year)
        if stats_file.is_file() and stats_file.stat().st_size > 0 and not force:
            stats_payload = load_landed_payload(stats_file)
        else:
            extra = {"timeframe": str(year)}
            if pitching:
                extra.update(PITCHING_PARAM)
            stats_payload, meta = client.request("stats", extra)
            if stats_payload is None:
                problems.append("%d: season stats fetch failed (%s)" % (year, meta.get("note")))
                append_manifest(history_dir, meta, None)
                year_summaries[year] = {"universe": None, "note": "stats fetch failed"}
                continue
            if pitching:
                fetched = universe_from_stats_pitching(stats_payload)
                if fetched and not any(pitcher_shaped(p) for p in fetched):
                    # The hitter-default decoy: a 200 full of hitters
                    # claiming to be the pitcher table. Never landed.
                    problems.append("%d: pitcher universe REJECTED — 0/%d rows pitcher-shaped"
                                    % (year, len(fetched)))
                    meta["note"] = ("REJECTED: 0/%d rows carry pitching keys — "
                                    "hitter-default decoy" % len(fetched))
                    append_manifest(history_dir, meta, None)
                    year_summaries[year] = {"universe": len(fetched),
                                            "note": "rejected: hitter-default decoy"}
                    continue
            write_json(stats_file, envelope(stats_payload, meta, league, year))
            append_manifest(history_dir, meta, str(stats_file))

        universe = read_universe(stats_payload)
        landed = skipped = empty = unavailable = 0
        games_total = 0
        rejected, failed = [], []

        for i, player in enumerate(universe):
            pid = str(player["id"])
            out = history_dir / "gamelog" / str(year) / ("%s.json" % pid)
            if out.is_file() and out.stat().st_size > 0 and not force:
                skipped += 1
                continue
            if (str(year), pid) in KNOWN_UNAVAILABLE and not force:
                unavailable += 1
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

        # Split-player sentinels ride the pitching sweep: never in any
        # universe, so they get explicit fetches under the same gate.
        sentinels = {}
        if pitching:
            for pid, (label, debut) in sorted(SENTINEL_PLAYERS.items()):
                if year < debut:
                    continue
                out = history_dir / "gamelog" / str(year) / ("%s.json" % pid)
                if out.is_file() and out.stat().st_size > 0 and not force:
                    sentinels[pid] = "present"
                    continue
                if (str(year), pid) in KNOWN_UNAVAILABLE and not force:
                    sentinels[pid] = "known unavailable"
                    unavailable += 1
                    continue
                payload, meta = client.request("gamelog", {"player_id": pid, "timeframe": str(year)})
                entries = gamelog_entries(payload) if payload is not None else None
                if entries is None:
                    sentinels[pid] = "FAILED (HTTP %s)" % meta.get("http_status")
                    failed.append(pid)
                    append_manifest(history_dir, meta, None)
                elif offyear_entries(entries, year):
                    sentinels[pid] = "REJECTED (off-year dates)"
                    rejected.append(pid)
                    meta["note"] = ("REJECTED: %d off-year game_date entries"
                                    % offyear_entries(entries, year))
                    append_manifest(history_dir, meta, None)
                else:
                    write_json(out, envelope(payload, meta, league, year))
                    append_manifest(history_dir, meta, str(out))
                    sentinels[pid] = "landed (%d games)" % len(entries)
                    games_total += len(entries)

        year_summaries[year] = {
            "universe": len(universe), "landed": landed, "already_present": skipped,
            "empty_gamelogs": empty, "games_landed": games_total,
            "known_unavailable": unavailable,
            "failed": failed, "rejected_fake_history": rejected,
        }
        if pitching:
            # Texture for the verification record: rows missing pitching
            # keys (decoy residue would be ALL of them — gated above),
            # season FPTS coverage (the MLB-62 anchor), and the two-way
            # overlap with the landed hitter universe (structurally empty
            # under the split-player scheme; kept as the proof).
            if sentinels:
                year_summaries[year]["sentinel_gamelogs"] = sentinels
            year_summaries[year]["rows_missing_pitching_keys"] = sum(
                1 for p in universe if not pitcher_shaped(p))
            year_summaries[year]["fpts_rows"] = fpts_rows(universe)
            hitter_file = history_dir / "stats" / ("%d.json" % year)
            if hitter_file.is_file():
                hitter_ids = {str(p["id"]) for p in
                              universe_from_stats(load_landed_payload(hitter_file))}
                year_summaries[year]["two_way_overlap"] = [
                    {"id": str(p["id"]), "name": p.get("name")}
                    for p in universe if str(p["id"]) in hitter_ids]
        if failed:
            problems.append("%d: %d gamelog fetches failed" % (year, len(failed)))
        if rejected:
            problems.append("%d: %d gamelogs REJECTED as fake history" % (year, len(rejected)))
        print("%d: universe=%d landed=%d present=%d empty=%d games=%d failed=%d "
              "rejected=%d unavailable=%d"
              % (year, len(universe), landed, skipped, empty, games_total,
                 len(failed), len(rejected), unavailable), flush=True)

    summary = {
        "verified_at": stamp,
        "mode": "pitching" if pitching else "hitting",
        "years": {str(y): year_summaries[y] for y in sorted(year_summaries)},
        "verdict": "PASS" if not problems else "FAIL: " + "; ".join(problems),
    }
    write_json(history_dir / ("verification_%s.json" % stamp), summary)
    print("VERIFY: %s" % summary["verdict"], flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--probe", action="store_true", help="shape check only; lands nothing")
    mode.add_argument("--backfill", action="store_true", help="hitter stats + gamelog sweep + verify")
    mode.add_argument("--backfill-pitching", action="store_true",
                      help="pitcher universes (position=P) + gamelog sweep + verify")
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

    print("cbs_backfill: league=%s years=%d-%d mode=%s landing=%s"
          % (league, min(args.years), max(args.years),
             "probe" if args.probe else ("pitching" if args.backfill_pitching else "hitting"),
             history_dir), flush=True)
    if args.probe:
        run_probe(client)
    else:
        run_backfill(client, history_dir, sorted(args.years), args.force,
                     pitching=args.backfill_pitching)
    print("done: %d API calls, read-only." % client.calls, flush=True)


if __name__ == "__main__":
    main()
