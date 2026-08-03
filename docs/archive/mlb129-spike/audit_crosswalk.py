"""
Independent audit of the ESPN -> MLBAM crosswalk.

1,227 of 1,236 matches came from `name_unique`, which checks a unique exact
name plus season overlap and does NOT look at team. A 100% coverage number off
a name-only bar has to be audited against evidence the matcher did not use.

The audit: for every matched player, compare
    ESPN's GAME-LEVEL club  (from the kona per-period split -- ESPN's own
                             account of who he played for)
against
    MLBAM's season team     (statsapi season listing currentTeam for the
                             matched mlbam_id)

These are two independent sources. A wrong match will usually disagree, because
a same-name stranger played for a different club. Agreement is corroboration
the matcher never consumed for those 1,227 rows.

Also measures homonym density -- how often a normalized name maps to more than
one mlbam inside the 2025-26 window -- so the 100% can be read as "the problem
is genuinely easy in a two-season window" rather than "the bar is loose."
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(r"C:\Users\kyled\projects\espn-league-manager")
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT / ".env")

import extract.mlb_crosswalk as mc  # noqa: E402
from espn_api.baseball import constant as espn_const  # noqa: E402

OUT = Path(sys.argv[1])
ABBR = espn_const.PRO_TEAM_MAP

res = json.loads((OUT / "espn_crosswalk_result.json").read_text(encoding="utf-8"))
results = res["results"]
team_map = res["team_map"]          # ESPN club abbrev (upper) -> MLBAM team id

mc.FIRST_SEASON, mc.LAST_SEASON = 2025, 2026
print("rebuilding MLB index for the audit (statsapi 2025-2026)...", flush=True)
idx, ikey = mc.build_mlb_index()

# mlbam_id -> {season: team_id}
teams_by_mlbam: dict[int, dict[int, int]] = defaultdict(dict)
name_by_mlbam: dict[int, str] = {}
homonym = Counter()
for key, cands in idx.items():
    homonym[len(cands)] += 1
    for mid, e in cands.items():
        name_by_mlbam[mid] = e["name"]
        for yr, tid in e["teams"].items():
            teams_by_mlbam[mid][yr] = tid

print("\n" + "=" * 74)
print("HOMONYM DENSITY inside the 2025-26 MLB window")
print("=" * 74)
tot = sum(homonym.values())
for k in sorted(homonym):
    print(f"  names mapping to {k} distinct mlbam_id: {homonym[k]:5d}"
          f"  ({100*homonym[k]/tot:.2f}%)")
ambiguous_names = sum(v for k, v in homonym.items() if k > 1)
print(f"  -> {ambiguous_names} genuinely ambiguous names of {tot}"
      f" ({100*ambiguous_names/tot:.2f}%)")

print("\n" + "=" * 74)
print("INDEPENDENT AUDIT -- ESPN game-level club vs MLBAM season team")
print("=" * 74)

# The comparison must use the FULL set of clubs ESPN saw the player with that
# season, not just the most frequent one. statsapi's season listing carries
# `currentTeam` -- for a traded player that is the club he ENDED with, while
# ESPN's modal club is usually the one he spent most of the year with. A first
# cut of this audit compared modal-vs-final and reported 188 "disagreements"
# that were, on inspection, all correct matches for players traded mid-season
# (Mullins BAL->NYM, O'Hearn BAL->SD, Helsley StL->NYM). Set membership is the
# question actually being asked: did MLBAM's club for that season appear
# anywhere in ESPN's own account of where he played?
clubs_by_player_season: dict[tuple[int, int], set[str]] = defaultdict(set)
for ln in (OUT / "gamelevel_club.ndjson").open(encoding="utf-8"):
    row = json.loads(ln)
    for g in row["game_splits"]:
        tid = g.get("proTeamId")
        if tid:
            clubs_by_player_season[(row["player_id"], row["season_year"])].add(
                ABBR.get(tid, f"?{tid}").upper())

verdict = Counter()
disagreements = []
for r in results:
    mid = r.get("mlbam_id")
    if not mid:
        verdict["unmatched"] += 1
        continue
    checked = False
    for yr in (2025, 2026):
        clubs = clubs_by_player_season.get((r["espn_id"], yr))
        mlbam_tid = teams_by_mlbam.get(mid, {}).get(yr)
        if not clubs or mlbam_tid is None:
            continue
        espn_tids = {team_map.get(c) for c in clubs}
        espn_tids.discard(None)
        if not espn_tids:
            continue
        checked = True
        if mlbam_tid in espn_tids:
            verdict["AGREE"] += 1
        else:
            verdict["DISAGREE"] += 1
            disagreements.append({
                "espn_id": r["espn_id"], "espn_name": r["name"],
                "season": yr, "espn_clubs": sorted(clubs),
                "mlbam_id": mid, "mlbam_name": name_by_mlbam.get(mid),
                "mlbam_team_id": mlbam_tid,
                "method": r["method"], "wt25": r["wt25"],
            })
    if not checked:
        verdict["no comparable season (evidence or MLBAM team missing)"] += 1

print()
for k, v in verdict.most_common():
    print(f"  {k:52s} {v}")

agree = verdict["AGREE"]
dis = verdict["DISAGREE"]
if agree + dis:
    print(f"\n  agreement rate on comparable player-seasons: "
          f"{100*agree/(agree+dis):.2f}%  ({agree}/{agree+dis})")

if disagreements:
    print(f"\n--- {len(disagreements)} DISAGREEMENTS (candidate bad matches) ---")
    disagreements.sort(key=lambda d: -d["wt25"])
    for d in disagreements[:40]:
        print(f"  {d['espn_id']:>8} {str(d['espn_name'])[:22]:22s} {d['season']} "
              f"espn={','.join(d['espn_clubs'])[:18]:18s} -> mlbam {d['mlbam_id']} "
              f"({str(d['mlbam_name'])[:18]:18s}) team={d['mlbam_team_id']} "
              f"[{d['method']}] wt={d['wt25']:.0f}")

(OUT / "crosswalk_audit.json").write_text(json.dumps({
    "homonym_density": {str(k): v for k, v in homonym.items()},
    "verdict": dict(verdict),
    "disagreements": disagreements,
}, indent=2), encoding="utf-8")
print(f"\nwrote {OUT / 'crosswalk_audit.json'}")
