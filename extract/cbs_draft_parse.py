#!/usr/bin/env python3
"""Captured CBS draft-results pages -> structured pick rows (NDJSON).

Parse half of the drafts sweep (cbs_ui_capture.py --drafts-sweep). Reads
the keyed pages under data/cbs_raw/bsb/history/ui/drafts/keyed/ and
emits one row per table row into parsed/draft_rows.ndjson, plus a
census/verification summary. No warehouse writes here — the NDJSON is
the evidence layer the RAW load will read when the modeling shape is
signed off (same staging-first doctrine as the other UI families).

What the pages actually hold (2026-07-18 census, in blood):
  - Views are server-side sorts, honoured only where the data exists.
    '/round' renders real round sections only for drafts with recorded
    order (2009, 2020 BSB, 2025x2, 2026x2); everywhere else it renders a
    broken team-ish skeleton with one EMPTY row per team — the TEAM view
    is the real record for those drafts.
  - Team views list each team's picks in order; 'Rnd/Pk' appears in some
    eras ('1/3', '1/' round-only, '/' = nothing).
  - 2009 records order with EMPTY player cells; 2010/2012 render nothing;
    2011 has plain-text names (no ids, no order).
  - Total Fpts / Active Fpts columns ride the 2022+ pages: CBS's own
    draft-season value in both lenses.
  - Fully-empty rows are furniture (the broken skeletons); rows with a
    pick/round but no player are EVIDENCE (2009) and land flagged.

Parsing is stdlib-regex on purpose, mirroring cbs_ui_parse.py: the
tables are machine-generated and rigidly regular, and the archive is
immutable so any rule can be re-verified against the verbatim HTML.

Usage:
  py extract/cbs_draft_parse.py            # parse + write NDJSON + census
  py extract/cbs_draft_parse.py --dry-run  # parse + census only
"""

from __future__ import annotations

import argparse
import html as htmllib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from cbs_capture import find_repo_root, write_json

LEAGUE_KEY = "cbs-bsb"

_TABLE_RE = re.compile(r"<table[^>]*>.*?</table>", re.DOTALL)
_TR_RE = re.compile(r'<tr\s+class="([^"]*)"[^>]*>(.*?)</tr>', re.DOTALL)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)
_TH_RE = re.compile(r"<th[^>]*>(.*?)</th>", re.DOTALL)
# First playerLink anchor in a cell is the pick; later ones are news/injury
# icon links inside playerIconsWrapper.
_PLAYER_A_RE = re.compile(
    r"<a class='playerLink'[^>]*href='/players/playerpage/(\d+)'[^>]*>(.*?)</a>",
    re.DOTALL)
_POS_TEAM_RE = re.compile(r'<span class="playerPositionAndTeam">(.*?)</span>', re.DOTALL)
_ROUND_SUBTITLE_RE = re.compile(r"^Round\s+(\d+)$", re.IGNORECASE)
_FNAME_RE = re.compile(r"^(?P<key>.+)__(?P<view>round|team|default)\.html$")


def _clean(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment or "")
    return re.sub(r"\s+", " ", htmllib.unescape(text)).strip()


def _num(raw: str):
    text = (raw or "").replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def draft_table_region(page_html: str) -> str:
    """Contiguous run of data tables after the draft sort <form>. The
    draft content is one machine-generated table (subtitle/label/rows);
    anything after the run (page furniture) never matches class="data"."""
    anchor = page_html.find("/draft/results/")
    if anchor < 0:
        return ""
    form_end = page_html.find("</form>", anchor)
    if form_end < 0:
        return ""
    chunks = []
    for m in _TABLE_RE.finditer(page_html, form_end):
        if 'class="data' in m.group(0)[:200]:
            chunks.append(m.group(0))
        elif chunks:
            break
    return "\n".join(chunks)


def parse_player_cell(cell: str):
    """(cbs_player_id|None, name|None, pos_team_raw|None). Plain-text-era
    cells carry the name before the position span with no anchor."""
    anchor = _PLAYER_A_RE.search(cell)
    pos = _POS_TEAM_RE.search(cell)
    pos_team = _clean(pos.group(1)) if pos else None
    if anchor:
        return anchor.group(1), _clean(anchor.group(2)) or None, pos_team
    text = cell[: pos.start()] if pos else cell
    return None, _clean(text) or None, pos_team


def parse_page(path: Path, rel: str, draft_meta: dict, view: str) -> tuple[list, dict]:
    page_html = path.read_text(encoding="utf-8", errors="replace")
    region = draft_table_region(page_html)
    rows, template, section = [], [], {"seq": 0, "kind": None, "label": None, "row_seq": 0}
    page_seq = 0
    for tr_class, tr_body in _TR_RE.findall(region):
        if "subtitle" in tr_class:
            label = _clean(tr_body)
            round_m = _ROUND_SUBTITLE_RE.match(label)
            section = {"seq": section["seq"] + 1,
                       "kind": "round" if round_m else "team",
                       "label": label,
                       "round_no": int(round_m.group(1)) if round_m else None,
                       "row_seq": 0}
            continue
        if "label" in tr_class:
            template = [_clean(th).lower() for th in _TH_RE.findall(tr_body)]
            continue
        cells = _TD_RE.findall(tr_body)
        if not cells or not template:
            continue
        named = dict(zip(template, cells))
        pid, pname, pos_team = parse_player_cell(named.get("player", ""))
        rnd = rnd_pick = None
        if "rnd/pk" in named:
            part = _clean(named["rnd/pk"]).split("/")
            rnd = int(part[0]) if part and part[0].isdigit() else None
            rnd_pick = int(part[1]) if len(part) > 1 and part[1].isdigit() else None
        if rnd is None and section.get("kind") == "round":
            rnd = section.get("round_no")
        pick_no_clean = _clean(named.get("pick", ""))
        pick_no = int(pick_no_clean) if pick_no_clean.isdigit() else None
        team_name = _clean(named.get("team", "")) or (
            section["label"] if section.get("kind") == "team" else None)
        row = {
            "league_key": LEAGUE_KEY,
            "source_path": rel,
            "draft_key": draft_meta["key"],
            "draft_label": draft_meta["label"],
            "season_year": draft_meta["year"],
            "period": draft_meta["period"],
            "period_order": draft_meta["period_order"],
            "view": view,
            "section_seq": section["seq"],
            "section_kind": section["kind"],
            "section_label": section["label"],
            "row_seq": None,          # assigned below for kept rows
            "page_seq": None,
            "pick_no": pick_no,
            "round_num": rnd,
            "round_pick": rnd_pick,
            "team_name_raw": team_name,
            "player_cbs_id": pid,
            "player_name_raw": pname,
            "pos_team_raw": pos_team,
            "elig_raw": _clean(named.get("elig", "")) or None,
            "salary_raw": _clean(named.get("salary", "")) or None,
            "elapsed_raw": _clean(named.get("elapsed time", "")) or None,
            "rank_raw": _clean(named.get("rank", "")) or None,
            "total_fpts": _num(_clean(named.get("total fpts", ""))),
            "active_fpts": _num(_clean(named.get("active fpts", ""))),
        }
        # Furniture: the broken skeletons render one all-empty row per
        # section, and team views end in a literal 'TEAM' totals row.
        # Order-without-player rows (2009) are kept, flagged.
        if pname is None and pid is None and pick_no is None and rnd is None:
            continue
        if pid is None and pname and pname.strip().upper() == "TEAM":
            continue
        section["row_seq"] += 1
        page_seq += 1
        row["row_seq"] = section["row_seq"]
        row["page_seq"] = page_seq
        row["is_playerless"] = pname is None and pid is None
        rows.append(row)
    census = {
        "rows": len(rows),
        "rows_with_player": sum(1 for r in rows if not r["is_playerless"]),
        "rows_with_id": sum(1 for r in rows if r["player_cbs_id"]),
        "rows_with_round": sum(1 for r in rows if r["round_num"] is not None),
        "rows_with_pickno": sum(1 for r in rows if r["pick_no"] is not None),
        "rows_with_fpts": sum(1 for r in rows if r["total_fpts"] is not None),
        "sections": section["seq"],
        "section_kind": rows[0]["section_kind"] if rows else None,
    }
    return rows, census


def catalog_from_capture(ui_dir: Path) -> dict:
    """Draft key -> metadata, via the capture module's own catalog."""
    import cbs_ui_capture
    return {d["key"]: d for d in cbs_ui_capture.draft_catalog(ui_dir)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="census only; write nothing")
    args = ap.parse_args()

    root = find_repo_root(Path(__file__).resolve().parent)
    ui_dir = root / "data" / "cbs_raw" / "bsb" / "history" / "ui"
    keyed_dir = ui_dir / "drafts" / "keyed"
    out_dir = ui_dir / "drafts" / "parsed"
    catalog = catalog_from_capture(ui_dir)

    all_rows, census_by_page = [], {}
    for path in sorted(keyed_dir.glob("*.html")):
        m = _FNAME_RE.match(path.name)
        if not m:
            continue
        key = next((k for k in catalog
                    if re.sub(r"[^A-Za-z0-9.-]+", "_", k) == m.group("key")), None)
        if key is None:
            print("  SKIP %s: no catalog entry" % path.name)
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        rows, census = parse_page(path, rel, catalog[key], m.group("view"))
        all_rows.extend(rows)
        census_by_page["%s | %s" % (catalog[key]["label"], m.group("view"))] = census
        print("  %-42s %s" % (path.name, json.dumps(census)))

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    summary = {"parsed_at": stamp, "pages": census_by_page, "total_rows": len(all_rows)}
    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / "draft_rows.ndjson"
        with out.open("w", encoding="utf-8") as fh:
            for row in all_rows:
                row["parsed_at"] = stamp
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        write_json(out_dir / "parse_summary.json", summary)
        print("wrote %d rows -> %s" % (len(all_rows), out))
    else:
        print("dry-run: %d rows parsed, nothing written" % len(all_rows))


if __name__ == "__main__":
    main()
