#!/usr/bin/env python3
"""CBS 2026 fantasy-layer capture (MLB-44).

Preserves the perishable fantasy layer of the CBS league before season
rollover (~2026-09-27): daily rosters with the deployed slot
(`roster_pos`), the rolling transaction window, standings, and the
league/scoring configuration. MLB stat lines are publicly
reconstructable; who rostered whom, where, is not — this is the
irreplaceable artifact.

MUSEUM RULE (standing): this league is read-only forever. This script
can only issue GET requests to the whitelisted endpoints below — there
is no code path that sends any other verb or any other path. Polite
pacing between calls, exponential backoff on throttling. The token is
loaded from the repo-root .env, is never printed, and never appears in
logs or landed artifacts (params are redacted everywhere).

History claims are verified BY CONTENT, never by HTTP 200: CBS serves
current-window data with a 200 for several "historical" queries. The
date-parameter discovery step and the post-sweep verification both
refuse to trust responses that don't actually differ across dates.

Landing: gitignored raw JSON under <repo-root>/data/cbs_raw/, one
envelope per response, append-only, plus a redacted capture manifest
(JSONL) and a verification summary. Adapter-shaped staging comes later
with MLB-43; nothing here reshapes the payloads.

Usage:
    python extract/cbs_capture.py --probe        # discovery, no landing
    python extract/cbs_capture.py --capture      # full sweep + verify
    python extract/cbs_capture.py --capture --start 2026-03-25 --end 2026-07-07
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BASE_URL = "https://api.cbssports.com/fantasy"
API_VERSION = "3.0"
SEASON_START = date(2026, 3, 25)
PACING_SECONDS = 0.75          # polite floor between calls
RETRY_BACKOFF = [5, 15, 45]    # seconds; 429/5xx/network only
TIMEOUT_SECONDS = 30
USER_AGENT = "espn-league-manager/cbs_capture (read-only archival; polite pacing)"

# The GET-only endpoint whitelist — the museum rule, enforced in code.
# `request()` refuses anything not in this dict, and urllib is only ever
# invoked without a data payload (i.e., GET).
WHITELIST = {
    "rosters": "league/rosters",
    "transactions": "league/transactions",
    "transactions_alt": "league/transaction-list/log",
    "standings": "league/standings/overall",
    "scoring_rules": "league/scoring/rules",
    "league_rules": "league/rules",
    "details": "league/details",
    "teams": "league/teams",
    "owners": "league/owners",
}

# Config snapshots taken once per run (endpoint key -> artifact name).
CONFIG_SNAPSHOTS = [
    ("details", "details"),
    ("teams", "teams"),
    ("owners", "owners"),
    ("league_rules", "league_rules"),
    ("scoring_rules", "scoring_rules"),
]

# Candidate spellings for the roster date parameter, tried in order
# during discovery; the accepted one must change response CONTENT.
DATE_PARAM_CANDIDATES = [
    ("date", "%Y-%m-%d"),
    ("date", "%Y%m%d"),
    ("point", "%Y%m%d"),
    ("period", "%Y%m%d"),
]


# --------------------------------------------------------------------------
# Environment
# --------------------------------------------------------------------------

def find_repo_root(start: Path) -> Path:
    """Upward search for the directory holding .env (main checkout root).

    Works from the worktree too: worktrees live under
    <main>/.claude/worktrees/<name>, so the walk reaches the main root.
    """
    for candidate in [start, *start.parents]:
        if (candidate / ".env").is_file():
            return candidate
    raise SystemExit("No .env found walking up from %s — run inside the repo." % start)


def load_env(env_path: Path) -> dict:
    """Tolerant .env parse: CRLF, stray spaces around '=', quotes, comments."""
    values = {}
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


# --------------------------------------------------------------------------
# HTTP (GET-only, redacted, paced)
# --------------------------------------------------------------------------

def redact(params: dict) -> dict:
    return {k: v for k, v in params.items() if k != "access_token"}


class Client:
    def __init__(self, token: str, league: str):
        self._token = token
        self.league = league
        self.calls = 0

    def request(self, endpoint_key: str, extra_params: dict | None = None):
        """GET a whitelisted endpoint. Returns (payload|None, meta dict)."""
        if endpoint_key not in WHITELIST:
            raise ValueError("endpoint %r is not whitelisted — refusing" % endpoint_key)
        params = {
            "version": API_VERSION,
            "response_format": "JSON",
            "league_id": self.league,
            "access_token": self._token,
        }
        if extra_params:
            params.update(extra_params)
        url = "%s/%s?%s" % (BASE_URL, WHITELIST[endpoint_key], urllib.parse.urlencode(params))
        meta = {
            "endpoint": WHITELIST[endpoint_key],
            "params": redact(params),
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        last_err = None
        for attempt, backoff in enumerate([0] + RETRY_BACKOFF):
            if backoff:
                time.sleep(backoff)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # GET
                with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                    raw = resp.read()
                    meta["http_status"] = resp.status
                    meta["bytes"] = len(raw)
                self.calls += 1
                time.sleep(PACING_SECONDS + random.uniform(0, 0.25))
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    meta["note"] = "non-JSON response"
                    return None, meta
                meta["sha256"] = hashlib.sha256(
                    json.dumps(payload, sort_keys=True).encode()
                ).hexdigest()
                return payload, meta
            except urllib.error.HTTPError as err:
                meta["http_status"] = err.code
                if err.code in (429, 500, 502, 503, 504):
                    last_err = err
                    continue
                meta["note"] = "HTTP %d" % err.code
                self.calls += 1
                time.sleep(PACING_SECONDS)
                return None, meta
            except (urllib.error.URLError, TimeoutError, OSError) as err:
                last_err = err
                continue
        meta["note"] = "gave up after retries: %s" % type(last_err).__name__
        return None, meta


# --------------------------------------------------------------------------
# Landing
# --------------------------------------------------------------------------

def envelope(payload, meta, league: str, season: int) -> dict:
    return {
        "captured_at": meta["ts"],
        "league": league,
        "season": season,
        "endpoint": meta["endpoint"],
        "params": meta["params"],          # already redacted
        "http_status": meta.get("http_status"),
        "payload": payload,
    }


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def append_manifest(season_dir: Path, meta: dict, out_file: str | None) -> None:
    record = dict(meta)
    record["out_file"] = out_file
    season_dir.mkdir(parents=True, exist_ok=True)
    with (season_dir / "capture_manifest.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------
# Content inspection (schema-light on purpose — we land raw)
# --------------------------------------------------------------------------

def describe(payload) -> dict:
    text = json.dumps(payload)
    info = {
        "bytes": len(text),
        "roster_pos_count": text.count('"roster_pos"'),
        "top_keys": sorted(payload.keys()) if isinstance(payload, dict) else None,
    }
    body = payload.get("body") if isinstance(payload, dict) else None
    if isinstance(body, dict):
        info["body_keys"] = sorted(body.keys())
        for key in ("rosters", "teams", "standings", "transactions"):
            node = body.get(key)
            if isinstance(node, dict) and isinstance(node.get("teams"), list):
                info["%s_teams" % key] = len(node["teams"])
            elif isinstance(node, list):
                info["%s_len" % key] = len(node)
    return info


def payload_hash(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

def discover_date_param(client: Client, season_dir: Path) -> tuple[str, str]:
    """Find the roster date parameter whose CONTENT actually changes.

    Compares an early-season date against the paramless (current)
    response; HTTP 200 with identical content = fake history = rejected.
    The accepted choice is cached in the season dir.
    """
    cache = season_dir / ".date_param.json"
    if cache.is_file():
        cached = json.loads(cache.read_text(encoding="utf-8"))
        return cached["param"], cached["fmt"]

    baseline, base_meta = client.request("rosters")
    if baseline is None:
        raise SystemExit("Baseline roster fetch failed (%s) — cannot discover." % base_meta.get("note"))
    base_hash = payload_hash(baseline)
    probe_day = SEASON_START + timedelta(days=7)

    for param, fmt in DATE_PARAM_CANDIDATES:
        payload, meta = client.request("rosters", {param: probe_day.strftime(fmt)})
        if payload is None:
            continue
        if payload_hash(payload) != base_hash and describe(payload)["roster_pos_count"] > 0:
            print("  date param accepted: %s=%s (content differs from current)"
                  % (param, probe_day.strftime(fmt)))
            write_json(cache, {"param": param, "fmt": fmt,
                               "verified_against": probe_day.isoformat()})
            return param, fmt
        print("  rejected %s (%s): HTTP %s but content identical/empty — fake history"
              % (param, fmt, meta.get("http_status")))
    raise SystemExit("No date parameter changed roster content — refusing to sweep. "
                     "Verify endpoint behavior before rerunning.")


# --------------------------------------------------------------------------
# Modes
# --------------------------------------------------------------------------

def run_probe(client: Client) -> None:
    print("PROBE (nothing landed, params redacted):")
    for key in ("details", "teams", "owners", "league_rules", "scoring_rules",
                "standings", "transactions", "transactions_alt", "rosters"):
        payload, meta = client.request(key)
        status = meta.get("http_status", "n/a")
        if payload is None:
            print("  %-22s HTTP %-4s %s" % (key, status, meta.get("note", "")))
        else:
            print("  %-22s HTTP %-4s %s" % (key, status, json.dumps(describe(payload))))


def run_capture(client: Client, season_dir: Path, start: date, end: date,
                season: int, force: bool) -> None:
    league = client.league

    # 1. Config + point-in-time snapshots (append-only, timestamped).
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    for key, name in CONFIG_SNAPSHOTS:
        payload, meta = client.request(key)
        out = None
        if payload is not None:
            out = season_dir / "config" / ("%s_%s.json" % (name, stamp))
            write_json(out, envelope(payload, meta, league, season))
        append_manifest(season_dir, meta, str(out) if out else None)
        print("  config/%-14s %s" % (name, "landed" if out else "FAILED: %s" % meta.get("note")))

    for key, sub in (("standings", "standings"), ("transactions", "transactions")):
        payload, meta = client.request(key)
        if payload is None and key == "transactions":
            key = "transactions_alt"
            payload, meta = client.request(key)
        out = None
        if payload is not None:
            out = season_dir / sub / ("snapshot_%s.json" % stamp)
            write_json(out, envelope(payload, meta, league, season))
        append_manifest(season_dir, meta, str(out) if out else None)
        print("  %-21s %s" % (sub, "landed" if out else "FAILED: %s" % meta.get("note")))

    # 2. Roster sweep — content-verified date parameter, idempotent.
    param, fmt = discover_date_param(client, season_dir)
    day, landed, skipped, failed = start, 0, 0, []
    while day <= end:
        out = season_dir / "rosters" / ("%s.json" % day.isoformat())
        if out.is_file() and out.stat().st_size > 0 and not force:
            skipped += 1
        else:
            payload, meta = client.request("rosters", {param: day.strftime(fmt)})
            if payload is not None and describe(payload)["roster_pos_count"] > 0:
                write_json(out, envelope(payload, meta, league, season))
                append_manifest(season_dir, meta, str(out))
                landed += 1
            else:
                failed.append(day.isoformat())
                append_manifest(season_dir, meta, None)
        day += timedelta(days=1)
    print("  rosters: %d landed, %d already present, %d failed%s"
          % (landed, skipped, len(failed), " -> " + ", ".join(failed[:8]) if failed else ""))

    # 3. Verification BY CONTENT (never trust the 200s).
    verify(season_dir, start, end, stamp)


def verify(season_dir: Path, start: date, end: date, stamp: str) -> None:
    roster_dir = season_dir / "rosters"
    files = sorted(roster_dir.glob("*.json")) if roster_dir.is_dir() else []
    hashes, pos_counts, team_counts = set(), [], []
    for f in files:
        env = json.loads(f.read_text(encoding="utf-8"))
        info = describe(env["payload"])
        hashes.add(payload_hash(env["payload"]))
        pos_counts.append(info["roster_pos_count"])
        team_counts.append(info.get("rosters_teams") or info.get("teams_teams"))
    expected = (end - start).days + 1
    summary = {
        "verified_at": stamp,
        "roster_files": len(files),
        "expected_dates": expected,
        "distinct_payload_hashes": len(hashes),
        "roster_pos_min": min(pos_counts) if pos_counts else 0,
        "roster_pos_max": max(pos_counts) if pos_counts else 0,
        "team_count_sample": team_counts[0] if team_counts else None,
        "verdict": None,
    }
    problems = []
    if len(files) < expected:
        problems.append("missing %d dates" % (expected - len(files)))
    if len(hashes) <= 1 and len(files) > 1:
        problems.append("all payloads identical — date param is a lie; sweep is NOT history")
    elif len(hashes) < max(2, len(files) // 10):
        problems.append("suspiciously few distinct payloads (%d/%d)" % (len(hashes), len(files)))
    if pos_counts and min(pos_counts) == 0:
        problems.append("some files carry no roster_pos")
    summary["verdict"] = "PASS" if not problems else "FAIL: " + "; ".join(problems)
    write_json(season_dir / ("verification_%s.json" % stamp), summary)
    print("  VERIFY: %s" % json.dumps(summary))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--probe", action="store_true", help="discovery only; lands nothing")
    mode.add_argument("--capture", action="store_true", help="config + sweep + verify")
    ap.add_argument("--start", type=date.fromisoformat, default=SEASON_START)
    ap.add_argument("--end", type=date.fromisoformat, default=date.today())
    ap.add_argument("--force", action="store_true", help="re-fetch dates already landed")
    args = ap.parse_args()

    root = find_repo_root(Path(__file__).resolve().parent)
    env = load_env(root / ".env")
    token, league = env.get("CBS_TOKEN"), env.get("CBS_LEAGUE", "bsb")
    if not token:
        raise SystemExit("CBS_TOKEN missing from .env")
    season = args.start.year
    season_dir = root / "data" / "cbs_raw" / league / str(season)
    client = Client(token, league)

    print("cbs_capture: league=%s season=%d landing=%s" % (league, season, season_dir))
    if args.probe:
        run_probe(client)
    else:
        run_capture(client, season_dir, args.start, args.end, season, args.force)
    print("done: %d API calls, read-only." % client.calls)


if __name__ == "__main__":
    main()
