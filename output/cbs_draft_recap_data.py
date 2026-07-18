"""CBS draft-recap data provider (stopgap seam, 2026-07-18).

Assembles the CBS-bsb draft history from the scraped draft-results pages
(cbs_ui_capture.py --drafts-sweep -> cbs_draft_parse.py NDJSON) and the
warehouse's calculated season points, into pick rows the Draft Recap tab
builder consumes.

STOPGAP BOUNDARY, on purpose: the parsed NDJSON is read from
data/cbs_raw/.../drafts/parsed/ directly instead of a mart because the
draft rows aren't landed in RAW/staging yet -- the modeling shape (a
stg_cbs__draft branch under the ESPN chain's mart_draft_board, per
HANDOFF_CBS_DRAFT_RECAP reuse doctrine) needs Kyle's sign-off first.
Everything downstream of build_picks() is written against the row
contract that mart will emit, so the swap is a data-source change, not a
rewrite. Season points DO come from the warehouse (the record book's
calculated lens), not from the pages.

What the pages hold per year -- and therefore what stitching does -- was
established by content analysis (see DRAFT_RECAP_PROGRESS.md):

  * 2025/2026 run two real drafts (Mini then Mega, disjoint players);
    Kyle's continuation rule applies: the Mega's rounds renumber to
    follow the Mini's (2026 = Mini R1-8 + Mega R1-22 -> rounds 1-30).
  * 2020/2021 ALSO list two drafts each, but they are the SAME player
    set recorded twice (240/240 and 433/433 overlap) -- duplicates, not
    continuations. One part is used; the other is documented.
  * 2024 is split-brain: 'BSB DRAFT-A-GANZA' holds a 432-slot pick-order
    skeleton with NO players; '2024 - 2' holds 420 players in per-team
    lists with NO order. zip_2024() marries them (team's k-th listed
    player -> team's k-th skeleton slot) -- EXPERIMENTAL, flagged.
  * 2011-2023 offline-entered drafts carry per-team lists only. The
    tempting 'k-th listed player = round k' shortcut is PROVEN for
    online drafts (2025/2026: 100%) and DISPROVEN for offline imports
    (2020: 9/240, lists ride roster order) -- so no round is invented
    for these years; they contribute to no-order-needed surfaces only.
  * 2009 recorded order but zero players; 2010/2012 recorded nothing.

Value lenses (kept switchable end-to-end):
  * calc_total  -- warehouse CALCULATED_POINTS for the draft season
    (the record book's lens; complete for crosswalked players; the
    almanac's standing choice after the week-12 platform-points bug).
  * page_total / page_active -- CBS's own Total/Active Fpts columns
    (2022+ pages only). Landed for QA cross-checks and because ACTIVE
    points pre-walk-back exist nowhere else; not the default lens.

Identity: id-first (pages carry CBS player ids everywhere except 2011),
then dim_player_identity name-key fallback (platform='cbs'), ambiguous
flagged and NEVER guessed, per the identity doctrine.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from db import query_snowflake, league_predicate

FIRST_DRAFT_SEASON = 2011
NDJSON_REL = Path("data") / "cbs_raw" / "bsb" / "history" / "ui" / "drafts" / "parsed" / "draft_rows.ndjson"

# Per-year assembly plan. 'parts' fetch (draft_key, view) in stitch order;
# 'order' grades the pick-sequence evidence:
#   true           -- round + pick number straight from the page
#   rounds_suspect -- rounds recorded but shaped oddly (2020's offline
#                     import holds same-team runs inside one round)
#   zip            -- order skeleton married to per-team lists (2024)
#   none           -- per-team lists only; no round exists anywhere
DRAFT_SOURCES = {
    2011: {"parts": [("2011:Pre-season", "team")], "order": "none",
           "note": "plain-text names, no CBS ids; 15 teams"},
    2013: {"parts": [("2013:Pre-season", "team")], "order": "none"},
    2015: {"parts": [("2015:Pre-season", "team")], "order": "none"},
    2017: {"parts": [("2017:Pre-season:Pre-season", "team")], "order": "none",
           "note": "96-pick top-up draft"},
    2018: {"parts": [("2018:Pre-season:Pre-season", "team")], "order": "none"},
    2019: {"parts": [("2019:Pre-season:Pre-season", "team")], "order": "none",
           "note": "96-pick top-up draft"},
    2020: {"parts": [("2020:Pre-season:BSB 2020 Draft", "round")], "order": "rounds_suspect",
           "note": "'2020 - 2' is the same 240 players recorded twice; deduped"},
    2021: {"parts": [("2021:Pre-season:Pre-season", "team")], "order": "none",
           "note": "'2021 - 2' is the same 433 players recorded twice; deduped"},
    2022: {"parts": [("2022:2:MEGA-DRAFT", "team")], "order": "none"},
    2023: {"parts": [("2023:Pre-season:Pre-season", "team")], "order": "none",
           "note": "160-pick top-up draft"},
    2024: {"zip": [("2024:Pre-season:BSB DRAFT-A-GANZA", "round"), ("2024:2:2", "team")],
           "order": "zip",
           "note": "order skeleton + player lists married on (team, k); the player "
                   "side rides roster order, so the marriage is NOT draft order "
                   "(Ohtani landed in round 19) -- excluded from ordered surfaces"},
    2025: {"parts": [("2025:Pre-season:Mini Draft", "round"), ("2025:2:Mega Draft", "round")],
           "order": "true"},
    2026: {"parts": [("2026:Pre-season:Mini Draft", "round"), ("2026:2:Mega Draft", "round")],
           "order": "true"},
}


def _repo_root() -> Path:
    for candidate in [Path(__file__).resolve().parent, *Path(__file__).resolve().parents]:
        if (candidate / ".env").is_file():
            return candidate
    raise FileNotFoundError("no .env found walking up from output/ -- run inside the repo")


def load_draft_rows() -> list[dict]:
    path = _repo_root() / NDJSON_REL
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def _view_rows(rows, draft_key, view, players_only=True):
    picked = [r for r in rows if r["draft_key"] == draft_key and r["view"] == view]
    if players_only:
        picked = [r for r in picked if not r.get("is_playerless")]
    return sorted(picked, key=lambda r: r["page_seq"])


def _pick(base_row, year, part_label, rnd, rnd_pick, overall, list_seq, order_tier):
    return {
        "season_year": year,
        "draft_label": part_label,
        "round_num": rnd,
        "round_pick": rnd_pick,
        "overall_pick": overall,
        "list_seq": list_seq,
        "team_name_raw": base_row["team_name_raw"],
        "player_cbs_id": base_row["player_cbs_id"],
        "player_name_raw": base_row["player_name_raw"],
        "pos_team_raw": base_row["pos_team_raw"],
        "page_total_fpts": base_row["total_fpts"],
        "page_active_fpts": base_row["active_fpts"],
        "order_tier": order_tier,
    }


def zip_2024(rows, skeleton_key, players_key):
    """Marry the A-GANZA order skeleton with the '2024 - 2' team lists.

    The skeleton gives every (round, pick-in-round, team) slot; the team
    lists give each team's players in list order. Slot k of team T gets
    T's k-th listed player; slots beyond a team's list stay empty (12 of
    432 -- passed late picks) and are dropped with a count."""
    skeleton = [r for r in rows if r["draft_key"] == skeleton_key and r["view"] == "round"
                and r["pick_no"] is not None]
    skeleton.sort(key=lambda r: (r["round_num"], r["pick_no"]))
    team_lists = defaultdict(list)
    for r in _view_rows(rows, players_key, "team"):
        team_lists[r["team_name_raw"]].append(r)
    picks, slot_idx, unfilled = [], defaultdict(int), 0
    overall = 0
    for slot in skeleton:
        team = slot["team_name_raw"]
        k = slot_idx[team]
        slot_idx[team] += 1
        if k >= len(team_lists[team]):
            unfilled += 1
            continue
        overall += 1
        picks.append(_pick(team_lists[team][k], 2024, "BSB DRAFT-A-GANZA + 2",
                           slot["round_num"], slot["pick_no"], overall, k + 1, "zip"))
    leftovers = sum(len(v) for v in team_lists.values()) - sum(slot_idx.values()) + unfilled
    return picks, {"unfilled_slots": unfilled, "unslotted_players": max(leftovers, 0)}


def build_picks() -> tuple[list[dict], dict]:
    """All years' picks + per-year assembly report. Round numbers renumber
    across stitched parts (the continuation rule); overall_pick is the
    stitched sequence where order exists, None where it doesn't."""
    rows = load_draft_rows()
    picks, report = [], {}
    for year, plan in sorted(DRAFT_SOURCES.items()):
        if "zip" in plan:
            year_picks, zip_report = zip_2024(rows, plan["zip"][0][0], plan["zip"][1][0])
            picks.extend(year_picks)
            report[year] = {"picks": len(year_picks), "order": plan["order"],
                            "note": plan.get("note"), **zip_report}
            continue
        year_picks, round_offset, overall = [], 0, 0
        for draft_key, view in plan["parts"]:
            part_rows = _view_rows(rows, draft_key, view)
            label = draft_key.split(":")[-1] if draft_key.count(":") == 2 else "Pre-season"
            max_round_seen = 0
            team_seq = defaultdict(int)
            for r in part_rows:
                rnd = r["round_num"]
                if rnd is not None:
                    max_round_seen = max(max_round_seen, rnd)
                    rnd += round_offset
                team_seq[r["team_name_raw"]] += 1
                if plan["order"] in ("true", "rounds_suspect"):
                    overall += 1
                    year_picks.append(_pick(r, year, label, rnd,
                                            r["pick_no"], overall,
                                            team_seq[r["team_name_raw"]], plan["order"]))
                else:
                    year_picks.append(_pick(r, year, label, None, None, None,
                                            team_seq[r["team_name_raw"]], plan["order"]))
            round_offset += max_round_seen
        picks.extend(year_picks)
        report[year] = {"picks": len(year_picks), "order": plan["order"],
                        "rounds": round_offset or None, "note": plan.get("note")}
    return picks, report


# ---------------------------------------------------------------------------
# Identity + value attachment (id-first, name fallback, never guess)
# ---------------------------------------------------------------------------

_SUFFIX_RE = re.compile(r" (jr|sr|ii|iii|iv)$")
_SPLIT_MARKER_RE = re.compile(r"\((batter|pitcher)\)$")


def name_key(raw: str) -> str:
    """Python port of the cbs_name_key macro: periods dropped (no space,
    'B.J.' == 'BJ'), lowercased, whitespace collapsed, generational
    suffix stripped. Two-way '(batter)'/'(pitcher)' markers survive
    deliberately. Draft pages never use the packed 'Last, First POS TEAM'
    form, so the comma steps are not ported."""
    text = (raw or "").replace(".", "").lower()
    text = re.sub(r"\s+", " ", text).strip()
    return _SUFFIX_RE.sub("", text)


def fetch_season_points(first_season=FIRST_DRAFT_SEASON, last_season=2026) -> dict:
    """(cbs_player_id, season_year) -> calculated point lenses."""
    sql = f"""
        select cbs_player_id, season_year,
               max(case when stat_name = 'CALCULATED_POINTS'       then stat_value end) as calc_total,
               max(case when stat_name = 'CALCULATED_HITTING_PTS'  then stat_value end) as calc_hitting,
               max(case when stat_name = 'CALCULATED_PITCHING_PTS' then stat_value end) as calc_pitching
        from int_cbs__player_season_stats
        where {league_predicate()}
          and season_year between %s and %s
          and stat_name in ('CALCULATED_POINTS', 'CALCULATED_HITTING_PTS', 'CALCULATED_PITCHING_PTS')
        group by cbs_player_id, season_year
    """
    return {(str(r["cbs_player_id"]), int(r["season_year"])): r
            for r in query_snowflake(sql, (first_season, last_season))}


def fetch_identity(first_season=FIRST_DRAFT_SEASON, last_season=2026) -> tuple[dict, dict]:
    """Two read-side maps from dim_player_identity (platform='cbs'):
    (name_key, season) -> identity row, and the mlbam inversion
    (mlbam_id, season) -> {platform_player_id, ...} -- every CBS id any
    name form resolved to that season, which is exactly the set of split
    pseudo-ids a two-way player's production hides under."""
    sql = """
        select name_key, season_year, platform_player_id, mlbam_id, is_ambiguous,
               stat_group_scope
        from dim_player_identity
        where platform = 'cbs' and season_year between %s and %s
    """
    by_name, ids_by_mlbam = {}, defaultdict(lambda: {"scoped": set(), "all": set()})
    for r in query_snowflake(sql, (first_season, last_season)):
        year = int(r["season_year"])
        by_name[(r["name_key"], year)] = r
        if r.get("mlbam_id") and r.get("platform_player_id"):
            entry = ids_by_mlbam[(int(r["mlbam_id"]), year)]
            entry["all"].add(str(r["platform_player_id"]))
            if r.get("stat_group_scope"):
                entry["scoped"].add(str(r["platform_player_id"]))
    return by_name, ids_by_mlbam


def _mlbam_value_ids(ids_by_mlbam, mlbam, year):
    """The CBS ids whose points rows sum to this mlbam's season production.
    Discipline-scoped ids (the two-way split pseudo-ids) are the halves;
    when they exist the unified id is EXCLUDED so a unified row that also
    carries points can never double-count a half."""
    entry = ids_by_mlbam.get((int(mlbam), year))
    if not entry:
        return []
    return sorted(entry["scoped"] or entry["all"])


def _sum_points(points, ids, year):
    rows = [points[(i, year)] for i in ids if (i, year) in points]
    if not rows:
        return None
    return {
        "calc_total": sum(float(r["calc_total"] or 0) for r in rows),
        "calc_hitting": sum(float(r["calc_hitting"] or 0) for r in rows),
        "calc_pitching": sum(float(r["calc_pitching"] or 0) for r in rows),
        "n_ids": sum(1 for i in ids if (i, year) in points),
    }


def attach_values(picks: list[dict]) -> dict:
    """Mutates picks in place: resolution + calc lenses. Returns QA tallies.

    Value resolution walks the mlbam spine: a pick naming a SPLIT half
    ('... (Batter)') is credited that half's points only (the id the page
    carries IS the split pseudo-id); a unified name sums every CBS id its
    mlbam maps to that season -- one id for everyone except two-ways,
    both halves for Ohtani-class players (the 2023/2024 unified-row
    lesson). Ambiguous names stay flagged and unvalued, never guessed."""
    points = fetch_season_points()
    identity, ids_by_mlbam = fetch_identity()
    tally = defaultdict(int)
    for p in picks:
        year = p["season_year"]
        pid = p["player_cbs_id"]
        key = name_key(p["player_name_raw"])
        is_split = bool(_SPLIT_MARKER_RE.search(key))
        ident = identity.get((key, year)) if key else None
        unambiguous = ident if ident and not ident.get("is_ambiguous") else None
        resolution, value = None, None
        if is_split:
            # The page id is the split pseudo-id; that half's points only.
            if pid and (pid, year) in points:
                resolution, value = "id", _sum_points(points, [pid], year)
            elif unambiguous and unambiguous.get("platform_player_id"):
                mapped = str(unambiguous["platform_player_id"])
                if (mapped, year) in points:
                    resolution, value = "name", _sum_points(points, [mapped], year)
        else:
            # Unified name: production may hide under split pseudo-ids, so
            # a resolvable mlbam beats the page id (Ohtani-class). A page
            # id is authoritative identity, so an ambiguous NAME never
            # blocks it -- ambiguity only bites name-only resolution.
            mlbam = unambiguous.get("mlbam_id") if unambiguous else None
            if mlbam:
                value = _sum_points(points, _mlbam_value_ids(ids_by_mlbam, mlbam, year), year)
                if value:
                    resolution = "id" if pid and (pid, year) in points else "name"
            if value is None and pid and (pid, year) in points:
                resolution, value = "id", _sum_points(points, [pid], year)
            if value is None and resolution is None and not pid and ident is not None \
                    and ident.get("is_ambiguous"):
                resolution = "ambiguous"
        if value:
            p["calc_total"] = value["calc_total"]
            p["calc_hitting"] = value["calc_hitting"]
            p["calc_pitching"] = value["calc_pitching"]
            p["twoway_sum"] = value["n_ids"] > 1
        else:
            # Drafted, resolvable to nobody who played: a genuine zero only
            # when the player is identified (never-played pick, the ESPN
            # mart's COALESCE-0 precedent); otherwise honestly unresolved.
            resolution = resolution or ("zero_fill" if pid or ident else "unresolved")
            zero = resolution == "zero_fill"
            p["calc_total"] = 0.0 if zero else None
            p["calc_hitting"] = 0.0 if zero else None
            p["calc_pitching"] = 0.0 if zero else None
            p["twoway_sum"] = False
        p["resolution"] = resolution
        tally["%d_%s" % (year, resolution)] += 1
    return dict(tally)


def get_draft_history() -> tuple[list[dict], dict]:
    """The provider entrypoint: enriched picks + assembly/QA report."""
    picks, report = build_picks()
    tallies = attach_values(picks)
    for year in report:
        year_tallies = {k.split("_", 1)[1]: v for k, v in tallies.items()
                        if int(k.split("_", 1)[0]) == year}
        report[year]["resolution"] = year_tallies
    return picks, report


# ---------------------------------------------------------------------------
# QA harness (python output/cbs_draft_recap_data.py) -- prints the evidence
# the morning review needs; writes nothing anywhere.
# ---------------------------------------------------------------------------

def _spearman(xs, ys):
    def rank(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        ranks = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                ranks[order[k]] = avg
            i = j + 1
        return ranks
    rx, ry = rank(xs), rank(ys)
    n = len(rx)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    return cov / (vx * vy) if vx and vy else 0.0


def main() -> None:
    import db
    db.set_league("cbs-bsb")
    picks, report = get_draft_history()
    print("== assembly + resolution by year ==")
    for year, info in sorted(report.items()):
        print("  %d: %s" % (year, json.dumps(info)))

    both = [p for p in picks if p.get("calc_total") is not None
            and p.get("page_total_fpts") is not None]
    if both:
        deltas = sorted(abs(p["calc_total"] - p["page_total_fpts"]) for p in both)
        within2 = sum(1 for d in deltas if d <= 2)
        print("\n== page Total Fpts vs calculated (%d picks) ==" % len(both))
        print("  within +/-2: %d (%.1f%%); median delta %.1f; p90 %.1f; max %.1f"
              % (within2, 100 * within2 / len(both), deltas[len(deltas) // 2],
                 deltas[int(len(deltas) * 0.9)], deltas[-1]))
        worst = sorted(both, key=lambda p: -abs(p["calc_total"] - p["page_total_fpts"]))[:8]
        for p in worst:
            print("    %d %-24s calc=%.1f page=%.1f (%s)"
                  % (p["season_year"], p["player_name_raw"], p["calc_total"],
                     p["page_total_fpts"], p["draft_label"]))

    print("\n== list-order vs value (no-order years; rho ~1 would mean lists are draft-ordered) ==")
    for year in sorted(DRAFT_SOURCES):
        year_picks = [p for p in picks if p["season_year"] == year
                      and p["order_tier"] == "none" and p.get("calc_total") is not None]
        by_team = defaultdict(list)
        for p in year_picks:
            by_team[p["team_name_raw"]].append(p)
        xs, ys = [], []
        for team_picks in by_team.values():
            for p in team_picks:
                xs.append(p["list_seq"])
                ys.append(-p["calc_total"])
        if xs:
            print("  %d: rho=%.2f over %d picks" % (year, _spearman(xs, ys), len(xs)))

    print("\n== unresolved / ambiguous samples ==")
    misses = [p for p in picks if p["resolution"] in ("unresolved", "ambiguous")]
    for p in misses[:12]:
        print("  %d %-10s %-28s id=%s (%s)" % (p["season_year"], p["resolution"],
                                               p["player_name_raw"], p["player_cbs_id"],
                                               p["team_name_raw"]))
    print("  total misses: %d of %d" % (len(misses), len(picks)))

    twoway = [p for p in picks if p.get("twoway_sum")]
    print("\n== two-way mlbam sums: %d picks ==" % len(twoway))
    for p in twoway[:6]:
        print("  %d %-28s calc=%.1f page=%s" % (p["season_year"], p["player_name_raw"],
                                                p["calc_total"], p["page_total_fpts"]))

    gaps = [p for p in picks if p.get("calc_total") == 0.0
            and (p.get("page_total_fpts") or 0) >= 10]
    print("\n== crosswalk-gap candidates (calc=0 but page credits >=10) ==")
    for p in sorted(gaps, key=lambda p: -(p.get("page_total_fpts") or 0))[:15]:
        print("  %d %-28s page=%.0f id=%s" % (p["season_year"], p["player_name_raw"],
                                              p["page_total_fpts"], p["player_cbs_id"]))
    print("  total: %d" % len(gaps))


if __name__ == "__main__":
    main()
