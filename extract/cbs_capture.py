#!/usr/bin/env python3
"""CBS 2026 fantasy-layer capture (MLB-44).

Preserves the perishable fantasy layer of the CBS league before season
rollover (~2026-09-27): daily rosters with the deployed slot
(`roster_pos`) and the started/sat split (`roster_status` A/RS),
period-end standings, the rolling transaction window, and the
league/scoring configuration. MLB stat lines are publicly
reconstructable; who rostered whom, where, is not — this is the
irreplaceable artifact.

MUSEUM RULE (standing): this league is read-only forever. This script
can only issue GET requests to the whitelisted endpoints below — there
is no code path that sends any other verb or any other path. Polite
pacing between calls, exponential backoff on throttling. The token is
loaded from the repo-root .env, is never printed, and never appears in
logs or landed artifacts (params are redacted everywhere).

History claims are verified BY CONTENT, never by HTTP 200 — and never by
raw bytes either. Every endpoint earned its own proof (2026-07-07):

- Rosters: history rides `point=YYYYMMDD`, which maps the date to its
  scoring period (period-grain content; 624/624 transaction-log moves
  consistent). The `date` param is a decoy — it 200s with the CURRENT
  roster dressed in date-varying news headlines (byte-distinct, zero
  history), so roster history is judged by membership+deployed-slot
  fingerprints, never payload hashes. `roster_status` carries the
  started/sat split (A vs RS).
- Standings: history uses the scoring-period NUMBER, `period=N`, and
  each response echoes the period it served (asserted per file). Here
  `point` is the decoy: it echoes a period label over current totals
  (caught by the first sweep: 105 dates, 1 distinct state).
- Cross-season `point` values 400 or clamp to current — the fantasy
  layer really is current-season-only.

Full-league team coverage is required everywhere; league/rosters
without team_id=all silently returns only the token's own team.

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
    # league/transactions 404s for this league (probed 2026-07-07);
    # transaction-list/log is the live one, so it goes first.
    "transactions": "league/transaction-list/log",
    "transactions_alt": "league/transactions",
    "standings": "league/standings/overall",
    "scoring_rules": "league/scoring/rules",
    "league_rules": "league/rules",
    "details": "league/details",
    "teams": "league/teams",
    "owners": "league/owners",
    # The real 20-year archive, used by cbs_backfill.py (MLB-45): season
    # universes + per-game lines. Same GET-only client, same museum rule.
    "stats": "league/stats",
    "gamelog": "players/gamelog",
    # Draft-results probe (2026-07-18, the pre-2013 hunt): does the API
    # serve draft history the site UI lost? Probed with bare/season/year
    # params, verified by content. Same GET-only client, same museum rule.
    "draft_results": "league/draft/results",
    "draft_order": "league/draft/order",
    "draft_config": "league/draft/config",
}

# Config snapshots taken once per run (endpoint key -> artifact name).
CONFIG_SNAPSHOTS = [
    ("details", "details"),
    ("teams", "teams"),
    ("owners", "owners"),
    ("league_rules", "league_rules"),
    ("scoring_rules", "scoring_rules"),
]

# Candidate spellings for the roster history parameter, tried in order
# during discovery; the accepted one must change roster MEMBERSHIP, not
# just bytes. Probed 2026-07-07 against transaction-log ground truth:
# `point` (CBS's period key) serves real intra-season history; `date`
# (both formats) and `period` return the current roster dressed in
# date-varying headlines — distinct payload hashes with zero history,
# which is exactly why acceptance is fingerprint-based.
DATE_PARAM_CANDIDATES = [
    ("point", "%Y%m%d"),
    ("date", "%Y-%m-%d"),
    ("date", "%Y%m%d"),
    ("period", "%Y%m%d"),
]

# league/rosters WITHOUT team_id silently scopes to the token's own team
# (probed 2026-07-07: 1 team / 30 roster_pos vs 16 / 480 with team_id=all).
# Every roster call carries this, and sweep validity requires full-league
# team coverage so a scoping regression can never pass as history.
ROSTER_SCOPE = {"team_id": "all"}


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


def roster_fingerprint(payload) -> str | None:
    """Membership + deployed slot only — immune to the news/headline noise
    that makes whole-payload hashes lie about history. None on shape miss."""
    try:
        teams = payload["body"]["rosters"]["teams"]
        rows = sorted((str(t.get("id")), str(p.get("id")), str(p.get("roster_pos")))
                      for t in teams for p in t.get("players", []))
    except (KeyError, TypeError):
        return None
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def standings_rows(payload) -> list | None:
    """Flat (team_id, rank, points) rows from overall_standings; None on miss."""
    try:
        divisions = payload["body"]["overall_standings"]["divisions"]
        return sorted((str(t.get("id")), t.get("Total", {}).get("rank"),
                       t.get("Total", {}).get("points"))
                      for d in divisions for t in d.get("teams", []))
    except (KeyError, TypeError, AttributeError):
        return None


def standings_fingerprint(payload) -> str | None:
    rows = standings_rows(payload)
    if rows is None:
        return None
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

def discover_date_param(client: Client, season_dir: Path, expected_teams: int) -> tuple[str, str]:
    """Find the roster history parameter by MEMBERSHIP change, not bytes.

    The `date` param 200s with the current roster dressed in date-varying
    headlines, which defeats whole-payload comparisons (learned the hard
    way 2026-07-07: 105 dates, 2 distinct hashes, 0 membership changes).
    Acceptance demands, at two well-separated past dates: full team
    coverage, and membership+slot fingerprints that differ from current
    AND from each other. In an active league both probes differing from
    everything is what real history looks like; a league with genuinely
    frozen rosters would fail loudly here, which is the safe failure.
    The accepted choice is cached in the season dir.
    """
    cache = season_dir / ".date_param.json"
    if cache.is_file():
        cached = json.loads(cache.read_text(encoding="utf-8"))
        return cached["param"], cached["fmt"]

    baseline, base_meta = client.request("rosters", dict(ROSTER_SCOPE))
    if baseline is None:
        raise SystemExit("Baseline roster fetch failed (%s) — cannot discover." % base_meta.get("note"))
    base_fp = roster_fingerprint(baseline)
    if base_fp is None or describe(baseline).get("rosters_teams") != expected_teams:
        raise SystemExit("Baseline rosters cover %s teams, expected %d — scoping broke."
                         % (describe(baseline).get("rosters_teams"), expected_teams))
    span = max((date.today() - SEASON_START).days, 4)
    probe_days = [SEASON_START + timedelta(days=span // 4),
                  SEASON_START + timedelta(days=span // 2)]

    for param, fmt in DATE_PARAM_CANDIDATES:
        fps, coverage_ok = [], True
        for probe_day in probe_days:
            extra = dict(ROSTER_SCOPE)
            extra[param] = probe_day.strftime(fmt)
            payload, meta = client.request("rosters", extra)
            fp = roster_fingerprint(payload) if payload is not None else None
            info = describe(payload) if payload is not None else {}
            if (fp is None or info.get("roster_pos_count", 0) == 0
                    or info.get("rosters_teams") != expected_teams):
                coverage_ok = False
                break
            fps.append(fp)
        if coverage_ok and len(set(fps)) == len(probe_days) and base_fp not in fps:
            print("  history param accepted: %s (membership differs across %s and from current)"
                  % (param, " / ".join(d.isoformat() for d in probe_days)))
            write_json(cache, {"param": param, "fmt": fmt, "scope": ROSTER_SCOPE,
                               "teams": expected_teams,
                               "verified_against": [d.isoformat() for d in probe_days]})
            return param, fmt
        print("  rejected %s (%s): 200s but membership identical/partial — fake history"
              % (param, fmt))
    raise SystemExit("No parameter changed roster membership across past dates — refusing "
                     "to sweep. Verify endpoint behavior before rerunning.")


# --------------------------------------------------------------------------
# Modes
# --------------------------------------------------------------------------

def run_probe(client: Client) -> None:
    print("PROBE (nothing landed, params redacted):")
    for key, extra in (("details", None), ("teams", None), ("owners", None),
                       ("league_rules", None), ("scoring_rules", None),
                       ("standings", None), ("transactions", None),
                       ("transactions_alt", None), ("rosters", None),
                       ("rosters", dict(ROSTER_SCOPE))):
        payload, meta = client.request(key, extra)
        label = key if not extra else "%s %s" % (key, urllib.parse.urlencode(extra))
        status = meta.get("http_status", "n/a")
        if payload is None:
            print("  %-26s HTTP %-4s %s" % (label, status, meta.get("note", "")))
        else:
            print("  %-26s HTTP %-4s %s" % (label, status, json.dumps(describe(payload))))


def run_capture(client: Client, season_dir: Path, start: date, end: date,
                season: int, force: bool) -> None:
    league = client.league

    # 1. Config + point-in-time snapshots (append-only, timestamped).
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    config_payloads = {}
    for key, name in CONFIG_SNAPSHOTS:
        payload, meta = client.request(key)
        config_payloads[name] = payload
        out = None
        if payload is not None:
            out = season_dir / "config" / ("%s_%s.json" % (name, stamp))
            write_json(out, envelope(payload, meta, league, season))
        append_manifest(season_dir, meta, str(out) if out else None)
        print("  config/%-14s %s" % (name, "landed" if out else "FAILED: %s" % meta.get("note")))

    # The sweep's team-coverage target comes from the live teams list;
    # without it there is nothing trustworthy to verify coverage against.
    try:
        expected_teams = len(config_payloads["teams"]["body"]["teams"])
    except (KeyError, TypeError):
        raise SystemExit("teams snapshot missing/odd shape — refusing to sweep "
                         "without a team-coverage target")

    standings_now = None
    for key, sub in (("standings", "standings"), ("transactions", "transactions")):
        payload, meta = client.request(key)
        if payload is None and key == "transactions":
            key = "transactions_alt"
            payload, meta = client.request(key)
        if key == "standings":
            standings_now = payload
        out = None
        if payload is not None:
            out = season_dir / sub / ("snapshot_%s.json" % stamp)
            write_json(out, envelope(payload, meta, league, season))
        append_manifest(season_dir, meta, str(out) if out else None)
        print("  %-21s %s" % (sub, "landed" if out else "FAILED: %s" % meta.get("note")))

    # The standings period sweep needs to know how many periods exist so
    # far; the current snapshot carries it.
    try:
        current_period = int(standings_now["body"]["overall_standings"]["period"])
    except (KeyError, TypeError, ValueError):
        current_period = None

    # 2. Roster sweep — membership-verified history parameter, idempotent
    # per date. `point` maps each date to its scoring period, so files
    # inside one period repeat by construction; dedup is staging's job.
    param, fmt = discover_date_param(client, season_dir, expected_teams)
    day = start
    r_landed = r_skipped = 0
    failed = []
    while day <= end:
        out = season_dir / "rosters" / ("%s.json" % day.isoformat())
        if out.is_file() and out.stat().st_size > 0 and not force:
            r_skipped += 1
        else:
            extra = dict(ROSTER_SCOPE)
            extra[param] = day.strftime(fmt)
            payload, meta = client.request("rosters", extra)
            info = describe(payload) if payload is not None else {}
            if (payload is not None and info["roster_pos_count"] > 0
                    and info.get("rosters_teams") == expected_teams):
                write_json(out, envelope(payload, meta, league, season))
                append_manifest(season_dir, meta, str(out))
                r_landed += 1
            else:
                failed.append("%s rosters" % day.isoformat())
                append_manifest(season_dir, meta, None)
        day += timedelta(days=1)

    # 3. Standings period sweep — `period=N` (the scoring-period NUMBER)
    # is the standings history key; the `point` param echoes a period
    # label over CURRENT totals (caught by the first run: 105 dates, 1
    # distinct state). A response lands only if it echoes the period it
    # was asked for.
    s_landed = s_skipped = 0
    if current_period is None:
        failed.append("standings periods (current period unknown)")
    else:
        for n in range(1, current_period + 1):
            out = season_dir / "standings" / "periods" / ("period_%02d.json" % n)
            if out.is_file() and out.stat().st_size > 0 and not force:
                s_skipped += 1
                continue
            payload, meta = client.request("standings", {"period": str(n)})
            rows = standings_rows(payload) if payload is not None else None
            echoed = None
            if isinstance(payload, dict):
                echoed = payload.get("body", {}).get("overall_standings", {}).get("period")
            if rows is not None and len(rows) == expected_teams and str(echoed) == str(n):
                write_json(out, envelope(payload, meta, league, season))
                append_manifest(season_dir, meta, str(out))
                s_landed += 1
            else:
                failed.append("period %d standings" % n)
                append_manifest(season_dir, meta, None)
    print("  rosters: %d landed, %d already present; standings periods: %d landed, "
          "%d already present; %d failed%s"
          % (r_landed, r_skipped, s_landed, s_skipped, len(failed),
             " -> " + ", ".join(failed[:8]) if failed else ""))

    # 4. Verification BY CONTENT (never trust the 200s).
    verify(season_dir, start, end, stamp, expected_teams, current_period)


def verify(season_dir: Path, start: date, end: date, stamp: str,
           expected_teams: int, expected_periods: int | None) -> None:
    expected = (end - start).days + 1
    problems = []

    # Rosters: distinctness on membership+slot fingerprints, never raw
    # bytes — headline noise makes byte-hashes over-count, and the fake
    # `date` param proved byte-distinct payloads can carry zero history.
    roster_dir = season_dir / "rosters"
    files = sorted(roster_dir.glob("*.json")) if roster_dir.is_dir() else []
    fingerprints, pos_counts, team_counts = set(), [], []
    for f in files:
        payload = json.loads(f.read_text(encoding="utf-8"))["payload"]
        info = describe(payload)
        fingerprints.add(roster_fingerprint(payload))
        pos_counts.append(info["roster_pos_count"])
        team_counts.append(info.get("rosters_teams"))
    if len(files) < expected:
        problems.append("missing %d roster dates" % (expected - len(files)))
    if len(fingerprints) <= 1 and len(files) > 1:
        problems.append("all roster memberships identical — sweep is NOT history")
    elif len(fingerprints) < max(2, len(files) // 10):
        problems.append("suspiciously few distinct rosters (%d/%d)"
                        % (len(fingerprints), len(files)))
    if pos_counts and min(pos_counts) == 0:
        problems.append("some roster files carry no roster_pos")
    short = [c for c in team_counts if c != expected_teams]
    if short:
        problems.append("roster team coverage != %d on %d files" % (expected_teams, len(short)))

    # Standings: full coverage per period file, mutual distinctness
    # (cumulative period-end states can never repeat in a live league),
    # and every team's season total at the last period >= its first.
    # Strict monotonicity would be wrong — negative-scoring stats can
    # shrink a total across one bad period.
    period_dir = season_dir / "standings" / "periods"
    s_files = sorted(period_dir.glob("period_*.json")) if period_dir.is_dir() else []
    s_fps, first_totals, last_totals = set(), None, None
    for f in s_files:
        payload = json.loads(f.read_text(encoding="utf-8"))["payload"]
        rows = standings_rows(payload)
        if rows is None or len(rows) != expected_teams:
            problems.append("standings coverage/shape off in %s" % f.name)
            continue
        s_fps.add(standings_fingerprint(payload))
        totals = {}
        for tid, _, points in rows:
            try:
                totals[tid] = float(str(points).replace(",", ""))
            except (TypeError, ValueError):
                pass
        first_totals = first_totals or totals
        last_totals = totals
    if expected_periods and len(s_files) < expected_periods:
        problems.append("missing %d standings periods" % (expected_periods - len(s_files)))
    if len(s_files) > 1 and len(s_fps) < len(s_files):
        problems.append("standings periods not mutually distinct (%d/%d)"
                        % (len(s_fps), len(s_files)))
    if first_totals and last_totals:
        shrunk = [t for t in first_totals if last_totals.get(t, 0) < first_totals[t]]
        if shrunk:
            problems.append("season totals shrank first->last for teams %s" % shrunk)

    summary = {
        "verified_at": stamp,
        "expected_dates": expected,
        "expected_teams": expected_teams,
        "roster_files": len(files),
        "distinct_roster_fingerprints": len(fingerprints),
        "roster_pos_min": min(pos_counts) if pos_counts else 0,
        "roster_pos_max": max(pos_counts) if pos_counts else 0,
        "standings_periods": len(s_files),
        "expected_periods": expected_periods,
        "distinct_standings_states": len(s_fps),
        "verdict": "PASS" if not problems else "FAIL: " + "; ".join(problems),
    }
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
