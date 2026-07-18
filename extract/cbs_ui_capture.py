#!/usr/bin/env python3
"""CBS site-UI league-history capture (MLB-47).

The site UI serves fantasy-layer history the API denies under every
probed parameter: standings and transactions back to 2001, year-end
roster reports from 2003, draft results from 2017. This script lands
those pages as raw HTML — the owner story of a 26-season league.

MUSEUM RULE (standing): read-only forever. Only the GET URL templates
below are ever requested; polite pacing with backoff; the session
cookie comes from CBS_WEB_COOKIES in the repo-root .env and is never
printed, logged, or landed. Auth is verified BY CONTENT: a response
that bounces to the login page or lacks the league masthead is
recorded as auth-failed, never trusted because of an HTTP 200.

Surfaces and coverage (maintainer-verified in the UI, 2026-07-08):
    standings     /history/year-by-year/{year}          2001+
    transactions  /transactions/all/{filter}/{year}     2001+  (both the
                  all_but_lineup and all filters — bench/start moves ride
                  the log, and in a pure points league the active set is
                  the scoring lineup)
    rosters       /teams/roster-report/{team_id}/{year}/ 2003+ (the
                  Time Period pulldown's own option URLs; per-year team
                  ids are parsed from that year's standings page, with a
                  union-of-all-known-ids fallback)
    drafts        /draft/results/{year}:Pre-season:Pre-season/ 2017+
    team_overview /history/team-overview/{team_id}  one per franchise id —
                  the rename-continuity story (Aching Hippos = id 1)

Landing (append-only, idempotent — rerun to resume):
    <repo-root>/data/cbs_raw/<league>/history/ui/
        standings/<year>.html
        transactions/<filter>/<year>[_pN].html
        rosters/<year>/team_<id>.html
        drafts/<year>.html
        ui_manifest.jsonl, verification_<stamp>.json

Usage:
    python extract/cbs_ui_capture.py --probe     # auth + shape check, lands nothing
    python extract/cbs_ui_capture.py --capture   # full sweep + verify
"""

from __future__ import annotations

import argparse
import hashlib
import html as htmllib
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from cbs_capture import find_repo_root, load_env, write_json

BASE = "https://bsb.baseball.cbssports.com"
FIRST_SEASON = 2001
ROSTER_FLOOR = 2003          # roster reports render from 2003 per the UI
DRAFT_FLOOR = 2017
PACING_SECONDS = 0.75
RETRY_BACKOFF = [5, 15, 45]
TIMEOUT_SECONDS = 30
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
MASTHEAD = "Box Score Baseball"   # league name; present on every real page

# The GET-only URL templates — the museum rule for the site surface.
TEMPLATES = {
    "standings": BASE + "/history/year-by-year/{year}",
    "transactions": BASE + "/transactions/all/{filter}/{year}",
    "rosters": BASE + "/teams/roster-report/{team_id}/{year}/",
    "drafts": BASE + "/draft/results/{year}:Pre-season:Pre-season/",
    # Keyed draft results (2026-07-18): the {year}:Pre-season:Pre-season
    # guess above only hits drafts literally NAMED Pre-season — the real
    # keys live in the page's own sort <select> as option values
    # ('/draft/results/{year}:{period}:{title}/', period 'Pre-season' or a
    # supplemental-draft number; pre-2017 keys are two-part
    # '{year}:Pre-season'). {view} is the server-side sort: 'round' is the
    # only view carrying pick order; 'team' is the only one carrying the
    # Total/Active Fpts value columns (where CBS has them); '' = the
    # server's per-draft default.
    "drafts_keyed": BASE + "/draft/results/{key}/{view}",
    "team_overview": BASE + "/history/team-overview/{team_id}",
    # Message Board Archive (2026-07-18, the pre-2013 draft hunt): the
    # one league surface where DRAFT results could survive as human
    # posts. Feed first; thread template refined after the feed's own
    # hrefs reveal the real shape.
    "messages_feed": BASE + "/messages/feed",
}
TXN_FILTERS = ("all_but_lineup", "all")
DRAFT_VIEWS = ("round", "team")

# Per-surface content markers (any-of) — a page lands only if one is
# present. The roster-report family renders without the league masthead,
# so it validates on its own table furniture instead.
SURFACE_MARKERS = {
    "standings": (MASTHEAD,),
    "transactions": (MASTHEAD,),
    "drafts": (MASTHEAD,),
    "drafts_keyed": (MASTHEAD,),
    "team_overview": (MASTHEAD,),
    "messages_feed": (MASTHEAD,),
    "rosters": ("Own %", "Start %", "TOTALS"),
}


class UiClient:
    def __init__(self, cookies: str):
        self._cookies = cookies
        self.calls = 0

    def get(self, template_key: str, **kwargs):
        """GET a whitelisted page. Returns (html|None, meta). Never logs cookies."""
        if template_key not in TEMPLATES:
            raise ValueError("template %r is not whitelisted — refusing" % template_key)
        url = TEMPLATES[template_key].format(**kwargs)
        extra_page = kwargs.get("page")
        if extra_page:
            url += "?page=%s" % extra_page
        # start_row pagination (discovered by the maintainer 2026-07-12):
        # the transaction report's REAL pager. The bare URL equals
        # start_row=1 (newest first); offsets walk back through the season;
        # rows-per-page varies by year. The ?page= param above was the
        # older guess -- CBS ignores it (kept for manifest continuity).
        #
        # BETTER, DON'T-PAGINATE PATH (maintainer, 2026-07-14): the
        # transaction report honours ?print_rows=N -- print_rows=9999 returns
        # the ENTIRE season's log on one page (even this hyperactive league
        # stays well under 9999 rows/season), so a single GET replaces the
        # whole start_row walk. Maintainer-verified on 2001/all_but_lineup
        # AND on the `all` filter (2021 -- the filter carrying the lineup/
        # slot moves the walk-back needs). Left unwired here so the switch
        # is a deliberate change; adopt it on the next capture sweep.
        extra_start = kwargs.get("start_row")
        if extra_start and int(extra_start) > 1:
            url += "?start_row=%d" % int(extra_start)
        meta = {"url": url, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        last_err = None
        for backoff in [0] + RETRY_BACKOFF:
            if backoff:
                time.sleep(backoff)
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                       "Cookie": self._cookies})
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                    final, status = resp.geturl(), resp.status
                    body = resp.read().decode("utf-8", errors="replace")
                self.calls += 1
                time.sleep(PACING_SECONDS + random.uniform(0, 0.25))
                meta["http_status"] = status
                meta["bytes"] = len(body)
                if "/login" in final or "Sign In - CBSSports" in body:
                    meta["note"] = "AUTH-BOUNCED to login — cookie expired/invalid"
                    return None, meta
                if not any(m in body for m in SURFACE_MARKERS[template_key]):
                    meta["note"] = "no %s content markers — not landing" % template_key
                    return None, meta
                meta["sha256"] = hashlib.sha256(body.encode()).hexdigest()
                return body, meta
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

def land(path: Path, html: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(html, encoding="utf-8")
    tmp.replace(path)


def append_manifest(ui_dir: Path, meta: dict, out_file: str | None) -> None:
    record = dict(meta)
    record["out_file"] = out_file
    ui_dir.mkdir(parents=True, exist_ok=True)
    with (ui_dir / "ui_manifest.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------
# Content inspection (raw stays raw; these only judge authenticity)
# --------------------------------------------------------------------------

def team_ids_from_html(html: str) -> set[str]:
    """Team ids referenced by a page. History pages link each team as
    /history/team-overview/<id> (single-quoted hrefs); roster pulldowns
    use /teams/roster-report/<id>/<year>/."""
    ids = set(re.findall(r"/history/team-overview/(\d+)", html))
    ids |= set(re.findall(r"/teams/roster-report/(\d+)/", html))
    return ids


def year_marker_count(html: str, year: int) -> int:
    """Occurrences of the season year in date-ish contexts."""
    return len(re.findall(r"\b%d\b" % year, html)) + len(re.findall(r"/%02d/" % (year % 100), html))


def next_page_exists(html: str, current: int) -> bool:
    return bool(re.search(r'[?&]page=%d\b' % (current + 1), html))


# --------------------------------------------------------------------------
# Modes
# --------------------------------------------------------------------------

def run_probe(client: UiClient) -> None:
    print("PROBE (nothing landed, cookie never shown):", flush=True)
    html, meta = client.get("standings", year=2021)
    if html is None:
        raise SystemExit("standings/2021 failed: %s" % meta.get("note"))
    ids = team_ids_from_html(html)
    for marker in ("Aching Hippos", "Dugouts Wild"):
        print("  standings/2021: %r present=%s" % (marker, marker in html), flush=True)
    print("  standings/2021: %d team ids parsed: %s" % (len(ids), sorted(ids, key=int)[:20]), flush=True)

    html, meta = client.get("rosters", team_id="4", year=2021)
    if html is None:
        print("  roster-report/4/2021 FAILED: %s" % meta.get("note"), flush=True)
    else:
        for marker in ("Posey, Buster", "Rizzo, Anthony"):   # maintainer's 2021 paste
            print("  roster-report/4/2021: %r present=%s" % (marker, marker in html), flush=True)

    sizes = {}
    for f in TXN_FILTERS:
        html, meta = client.get("transactions", filter=f, year=2021)
        sizes[f] = meta.get("bytes") if html is not None else "FAILED:%s" % meta.get("note")
    print("  transactions/2021 sizes by filter: %s" % sizes, flush=True)

    html, meta = client.get("drafts", year=2017)
    print("  drafts/2017: %s (%s bytes)" % ("ok" if html else "FAILED", meta.get("bytes")), flush=True)


def run_capture(client: UiClient, ui_dir: Path, last_season: int, force: bool) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    results = {}   # (surface, year) -> "landed"|"present"|"failed: note"
    ids_by_year = {}

    def fetch_and_land(surface, out, **kwargs):
        if out.is_file() and out.stat().st_size > 0 and not force:
            return "present", out.read_text(encoding="utf-8", errors="replace")
        html, meta = client.get(surface, **kwargs)
        if html is None:
            append_manifest(ui_dir, meta, None)
            if "AUTH-BOUNCED" in str(meta.get("note", "")):
                raise SystemExit("Cookie no longer authenticates (%s) — re-extract "
                                 "CBS_WEB_COOKIES and rerun; landed pages are kept." % meta["url"])
            return "failed: %s" % meta.get("note"), None
        land(out, html)
        append_manifest(ui_dir, meta, str(out))
        return "landed", html

    # 1. Standings 2001..last — these also carry each year's team ids.
    for year in range(FIRST_SEASON, last_season + 1):
        status, html = fetch_and_land("standings", ui_dir / "standings" / ("%d.html" % year),
                                      year=year)
        results[("standings", year)] = status
        if html:
            ids_by_year[year] = team_ids_from_html(html)
    print("  standings: %s" % _tally(results, "standings"), flush=True)

    # 2. Transactions, both filters, with pagination.
    for f in TXN_FILTERS:
        for year in range(FIRST_SEASON, last_season + 1):
            page, status = 1, None
            while True:
                suffix = "%d.html" % year if page == 1 else "%d_p%d.html" % (year, page)
                out = ui_dir / "transactions" / f / suffix
                kwargs = {"filter": f, "year": year}
                if page > 1:
                    kwargs["page"] = page
                status, html = fetch_and_land("transactions", out, **kwargs)
                if html is None or not next_page_exists(html, page) or page >= 30:
                    break
                page += 1
            results[("transactions/" + f, year)] = status
    for f in TXN_FILTERS:
        print("  transactions/%s: %s" % (f, _tally(results, "transactions/" + f)), flush=True)

    # 3. Roster reports per (year, team id) — ids from that year's standings,
    # union-of-all-years as fallback for years whose parse came up empty.
    union_ids = set().union(*ids_by_year.values()) if ids_by_year else set()
    for year in range(max(ROSTER_FLOOR, FIRST_SEASON), last_season + 1):
        ids = ids_by_year.get(year) or union_ids
        landed = present = failed = 0
        for tid in sorted(ids, key=int):
            status, _ = fetch_and_land("rosters",
                                       ui_dir / "rosters" / str(year) / ("team_%s.html" % tid),
                                       team_id=tid, year=year)
            landed += status == "landed"
            present += status == "present"
            failed += status.startswith("failed")
        results[("rosters", year)] = "%d landed, %d present, %d failed of %d ids" % (
            landed, present, failed, len(ids))
        print("  rosters/%d: %s" % (year, results[("rosters", year)]), flush=True)

    # 4. Drafts.
    for year in range(DRAFT_FLOOR, last_season + 1):
        status, _ = fetch_and_land("drafts", ui_dir / "drafts" / ("%d.html" % year), year=year)
        results[("drafts", year)] = status
    print("  drafts: %s" % _tally(results, "drafts"), flush=True)

    # 5. Franchise overviews — one page per team id ever seen; these carry
    # the rename-continuity story (e.g. Aching Hippos = franchise id 1).
    for tid in sorted(union_ids, key=int):
        status, _ = fetch_and_land("team_overview",
                                   ui_dir / "team_overview" / ("team_%s.html" % tid),
                                   team_id=tid)
        results[("team_overview", tid)] = status
    print("  team_overview: %s" % _tally(results, "team_overview"), flush=True)

    verify(ui_dir, stamp, last_season)


def _tally(results: dict, surface: str) -> str:
    rows = [v for (s, _), v in results.items() if s == surface]
    return "%d landed, %d already present, %d failed of %d" % (
        sum(v == "landed" for v in rows), sum(v == "present" for v in rows),
        sum(str(v).startswith("failed") for v in rows), len(rows))


def verify(ui_dir: Path, stamp: str, last_season: int) -> None:
    """Content-based verdict over everything landed so far."""
    problems, summary = [], {"verified_at": stamp}

    st = sorted((ui_dir / "standings").glob("*.html"))
    summary["standings_years"] = len(st)
    if len(st) < last_season - FIRST_SEASON + 1:
        problems.append("standings: %d/%d years" % (len(st), last_season - FIRST_SEASON + 1))

    for f in TXN_FILTERS:
        pages = sorted((ui_dir / "transactions" / f).glob("*.html"))
        years = {p.stem.split("_")[0] for p in pages}
        summary["transactions_%s" % f] = {"pages": len(pages), "years": len(years)}
        if len(years) < last_season - FIRST_SEASON + 1:
            problems.append("transactions/%s: %d/%d years" % (f, len(years),
                                                              last_season - FIRST_SEASON + 1))

    r_years = sorted(p.name for p in (ui_dir / "rosters").glob("*") if p.is_dir())
    r_counts = {y: len(list((ui_dir / "rosters" / y).glob("team_*.html"))) for y in r_years}
    summary["roster_years"] = {y: r_counts[y] for y in r_years}
    thin = [y for y, n in r_counts.items() if int(y) >= 2003 and n < 8]
    if thin:
        problems.append("roster years with <8 teams landed: %s" % thin)

    summary["draft_years"] = len(list((ui_dir / "drafts").glob("*.html")))
    summary["team_overviews"] = len(list((ui_dir / "team_overview").glob("*.html")))

    # Ground truth from the maintainer's 2021 paste.
    gt = ui_dir / "rosters" / "2021" / "team_4.html"
    if gt.is_file():
        body = gt.read_text(encoding="utf-8", errors="replace")
        if not ("Posey" in body and "Rizzo" in body):
            problems.append("2021 team-4 roster fails the maintainer-paste ground truth")
    else:
        problems.append("2021 team-4 roster page missing")

    summary["verdict"] = "PASS" if not problems else "FAIL: " + "; ".join(problems)
    write_json(ui_dir / ("verification_%s.json" % stamp), summary)
    print("  VERIFY: %s" % json.dumps(summary), flush=True)


# Transaction rows across ALL eras, matched by EXCLUSION: every <tr> in
# the data table except the label header and footer/pagination furniture.
# Why not an include-list of zebra classes: CBS renders some real
# transaction rows as class="bgFan", and missing those makes the offset
# stride fall short of the server's page size -- every boundary then
# overlaps by the miscount (discovered on the 2026-07-12 sweep; the
# parser dedupes the overlaps this run left behind). Row markup also
# drifts by era (ids two-part 2021 / three-part 2015 / absent
# 2001-2008), so nothing here keys on ids; the duplicate-page clamp
# check compares the first row's cell content.
_TXN_TABLE_MARK = 'class="data borderTop'
_TXN_TR_RE = re.compile(r'<tr([^>]*)>(.*?)</tr>', re.DOTALL)
_TXN_SKIP_RE = re.compile(r'class="(?:label|footer)')


def _txn_rows(html: str) -> list:
    i = html.find(_TXN_TABLE_MARK)
    if i < 0:
        return []
    table = html[i:html.find('</table>', i)]
    return [m.group(2) for m in _TXN_TR_RE.finditer(table)
            if not _TXN_SKIP_RE.search(m.group(1))
            and m.group(2).count('<td') >= 4]


def run_transactions_sweep(client: UiClient, ui_dir: Path, last_season: int,
                           force: bool, filters=("all",)) -> None:
    """Full-history transaction capture via start_row pagination -- the real
    pager the maintainer found 2026-07-12 (the original capture's ?page=
    guess is ignored by CBS, which is why the archive held only each
    season's last ~30 moves). The bare URL equals start_row=1, newest
    first; the next offset is 1 + rows seen so far (rows-per-page varies
    by year); a page past the season's end renders zero transaction rows
    (or clamps to a repeat), which ends the year.

    Idempotent: present files are read and counted, not refetched, so an
    interrupted sweep resumes where it stopped. Filenames: {year}.html for
    start_row=1 (the original capture's name, so those pages are reused),
    {year}_r{start_row}.html beyond."""
    for f in filters:
        for year in range(FIRST_SEASON, last_season + 1):
            start_row, season_rows, pages, prev_first = 1, 0, 0, None
            while True:
                if pages >= 500:
                    print("  transactions/%s %d: RUNAWAY GUARD at 500 pages -- "
                          "inspect before trusting" % (f, year), flush=True)
                    break
                suffix = ("%d.html" % year if start_row == 1
                          else "%d_r%d.html" % (year, start_row))
                out = ui_dir / "transactions" / f / suffix
                if out.is_file() and out.stat().st_size > 0 and not force:
                    html = out.read_text(encoding="utf-8", errors="replace")
                else:
                    html, meta = client.get("transactions", filter=f, year=year,
                                            start_row=start_row)
                    if html is None:
                        append_manifest(ui_dir, meta, None)
                        if "AUTH-BOUNCED" in str(meta.get("note", "")):
                            raise SystemExit(
                                "Cookie no longer authenticates (%s) -- re-extract "
                                "CBS_WEB_COOKIES and rerun; the sweep resumes from "
                                "landed pages." % meta["url"])
                        print("  transactions/%s %d r%d: %s" % (
                            f, year, start_row, meta.get("note")), flush=True)
                        break
                    if not _txn_rows(html):
                        # Past the season's end: nothing to land, year done.
                        meta["note"] = "zero transaction rows (end of season)"
                        append_manifest(ui_dir, meta, None)
                        break
                    land(out, html)
                    append_manifest(ui_dir, meta, str(out))
                rows = _txn_rows(html)
                first = hash(rows[0]) if rows else None
                if not rows or first == prev_first:
                    break   # empty present file (defensive) / clamped offset
                prev_first = first
                pages += 1
                season_rows += len(rows)
                start_row += len(rows)
            print("  transactions/%s %d: %d moves across %d pages" % (
                f, year, season_rows, pages), flush=True)


# --------------------------------------------------------------------------
# Draft-results sweep (2026-07-18) — every draft the league ever recorded,
# by its EXACT key, in the two views that carry non-overlapping data.
# --------------------------------------------------------------------------

# Draft keys ride the sort <select> on every draft-results page: one
# option per draft ('/draft/results/{key}/', no inner slash) plus the
# current page's sort views ('{key}/round' — excluded here by [^"/]).
_DRAFT_OPTION_RE = re.compile(
    r'<option value="/draft/results/([^"/]+)/"[^>]*>([^<]*)</option>')


def draft_catalog(ui_dir: Path) -> list:
    """Union the draft dropdown across every captured draft page.

    The catalog is discovered from ARCHIVED pages, not guessed: the 10
    original {year}.html captures each embed the full list (verified
    identical apart from the current page's own sort options). Entries:
    key '{year}:{period}[:{title}]' with period 'Pre-season' or the
    supplemental-draft number; two-part pre-2017 keys have no title.
    Sorted into draft order within a year: Pre-season before period N —
    the stitching order for treating same-year drafts as one draft."""
    seen = {}
    for page in sorted((ui_dir / "drafts").glob("*.html")):
        html = page.read_text(encoding="utf-8", errors="replace")
        for key, label in _DRAFT_OPTION_RE.findall(html):
            parts = key.split(":")
            if not parts[0].isdigit():
                continue
            period = parts[1] if len(parts) > 1 else "Pre-season"
            period_order = 0 if period.lower() == "pre-season" else int(period)
            seen[key] = {"key": key, "year": int(parts[0]), "period": period,
                         "period_order": period_order,
                         "title": parts[2] if len(parts) > 2 else "",
                         "label": htmllib.unescape(label).strip()}
    return sorted(seen.values(), key=lambda d: (d["year"], d["period_order"]))


def _draft_fname(key: str, view: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9.-]+", "_", key)
    return "%s__%s.html" % (safe, view or "default")


def _draft_shape(html: str) -> dict:
    """Content evidence for the manifest: subtitles (Round N / team names),
    the label row's columns, and the pick-link count. Never trust a 200."""
    subtitles = re.findall(r'<tr class="subtitle"><td[^>]*>(.*?)</td></tr>', html)
    label = re.findall(r'<tr\s+class="label">(.*?)</tr>', html)
    cols = [re.sub(r"\s+", " ", c).strip()
            for c in re.findall(r"<th[^>]*>(.*?)</th>", label[0])] if label else []
    return {"subtitles": len(subtitles),
            "first_subtitle": re.sub(r"<[^>]+>", "", subtitles[0]).strip() if subtitles else None,
            "label_cols": cols,
            "player_links": len(re.findall(r"class='playerLink'", html))}


def run_drafts_sweep(client: UiClient, ui_dir: Path, force: bool) -> None:
    """Fetch every cataloged draft in both sort views, content-verified.

    ~21 drafts x 2 views at polite pacing. The original {year}.html
    captures stay untouched (append-only archive); keyed pages land at
    drafts/keyed/{key}__{view}.html. A page lands if the masthead is
    present; its pick-table shape is recorded in the manifest and the
    verification file either way, so configured-but-never-held drafts
    (the 2022/2024 Pre-season shells) are evidence, not surprises."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    catalog = draft_catalog(ui_dir)
    if not catalog:
        raise SystemExit("No draft options found under %s — run --capture first."
                         % (ui_dir / "drafts"))
    print("  catalog: %d drafts %d..%d" % (len(catalog), catalog[0]["year"],
                                           catalog[-1]["year"]), flush=True)
    report = []
    for d in catalog:
        entry = {"key": d["key"], "label": d["label"], "year": d["year"],
                 "period": d["period"], "views": {}}
        for view in DRAFT_VIEWS:
            out = ui_dir / "drafts" / "keyed" / _draft_fname(d["key"], view)
            if out.is_file() and out.stat().st_size > 0 and not force:
                html, status = out.read_text(encoding="utf-8", errors="replace"), "present"
            else:
                html, meta = client.get("drafts_keyed",
                                        key=urllib.parse.quote(d["key"], safe=":"),
                                        view=view)
                if html is None:
                    append_manifest(ui_dir, meta, None)
                    if "AUTH-BOUNCED" in str(meta.get("note", "")):
                        raise SystemExit("Cookie no longer authenticates (%s) — re-extract "
                                         "CBS_WEB_COOKIES and rerun; landed pages are kept."
                                         % meta["url"])
                    entry["views"][view] = {"status": "failed: %s" % meta.get("note")}
                    continue
                land(out, html)
                meta["shape"] = _draft_shape(html)
                append_manifest(ui_dir, meta, str(out))
                status = "landed"
            shape = _draft_shape(html)
            shape["status"] = status
            entry["views"][view] = shape
        report.append(entry)
        views = entry["views"]
        print("  %-38s %s" % (d["label"],
              " | ".join("%s: %s subs=%s links=%s cols=%s"
                         % (v, views[v].get("status"), views[v].get("subtitles"),
                            views[v].get("player_links"),
                            ",".join(views[v].get("label_cols") or []))
                         for v in views)), flush=True)
    write_json(ui_dir / ("verification_drafts_%s.json" % stamp),
               {"verified_at": stamp, "drafts": report})
    print("  VERIFY: report at verification_drafts_%s.json" % stamp, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--probe", action="store_true", help="auth + shape check; lands nothing")
    mode.add_argument("--capture", action="store_true", help="full sweep + verify")
    mode.add_argument("--transactions-sweep", action="store_true",
                      help="full-history transaction capture via start_row "
                           "pagination; pair with --last-season 2025 to leave "
                           "the live season to the API capture")
    mode.add_argument("--drafts-sweep", action="store_true",
                      help="every draft in the page catalog, round + team views")
    ap.add_argument("--last-season", type=int, default=2026)
    ap.add_argument("--txn-filters", default="all",
                    help="comma list for --transactions-sweep: all (includes the "
                         "activate/reserve lineup moves MLB-63 needs) and/or "
                         "all_but_lineup")
    ap.add_argument("--force", action="store_true", help="re-fetch pages already landed")
    args = ap.parse_args()

    root = find_repo_root(Path(__file__).resolve().parent)
    env = load_env(root / ".env")
    cookies = env.get("CBS_WEB_COOKIES")
    if not cookies:
        raise SystemExit("CBS_WEB_COOKIES missing from .env — copy the browser's cookie "
                         "request-header value (see MLB-47).")
    league = env.get("CBS_LEAGUE", "bsb")
    ui_dir = root / "data" / "cbs_raw" / league / "history" / "ui"
    client = UiClient(cookies)

    print("cbs_ui_capture: league=%s landing=%s" % (league, ui_dir), flush=True)
    if args.probe:
        run_probe(client)
    elif args.drafts_sweep:
        run_drafts_sweep(client, ui_dir, args.force)
    elif args.transactions_sweep:
        filters = tuple(x.strip() for x in args.txn_filters.split(",") if x.strip())
        unknown = [x for x in filters if x not in TXN_FILTERS]
        if unknown:
            raise SystemExit("unknown txn filters %s; known %s" % (unknown, TXN_FILTERS))
        run_transactions_sweep(client, ui_dir, args.last_season, args.force, filters)
    else:
        run_capture(client, ui_dir, args.last_season, args.force)
    print("done: %d GETs, read-only." % client.calls, flush=True)


if __name__ == "__main__":
    main()
