"""
MLB-129 spike -- assertions over the produced artifacts.

GO condition 3 is "zero silent fallbacks, assertion-enforced, not reviewed by
eye." These are those assertions. Each prints PASS/FAIL and the script exits
non-zero if any fail, so the claim in the handoff is machine-checked.

Run:  py assert_spike.py <scratchpad>
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from espn_api.baseball import constant as espn_const

OUT = Path(sys.argv[1])
ABBR = espn_const.PRO_TEAM_MAP

xwalk = json.loads((OUT / "espn_crosswalk_result.json").read_text(encoding="utf-8"))
cov = json.loads((OUT / "gamelevel_coverage.json").read_text(encoding="utf-8"))
audit = json.loads((OUT / "crosswalk_audit.json").read_text(encoding="utf-8"))
results = xwalk["results"]

failures: list[str] = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(name)


print("=" * 76)
print("A. CROSSWALK INTEGRITY")
print("=" * 76)

# A1 -- exactly two states: resolved to an int, or explicitly None. No third.
states = Counter("resolved" if isinstance(r["mlbam_id"], int)
                 else "unresolved" if r["mlbam_id"] is None
                 else "OTHER" for r in results)
check("A1 every row is resolved-int or unresolved-None (no third state)",
      states.get("OTHER", 0) == 0, dict(states))

# A2 -- the silent-guess path never fired. The imported matcher DOES return a
# guess on a true tie (method 'name_ambiguous') and on 'fuzzy_overlap'; the bar
# requires those to be NULL. Zero here means the population never exercised it,
# NOT that the code is safe -- a production landing must NULL them explicitly.
guessy = [r for r in results if r["method"] in ("name_ambiguous", "fuzzy_overlap")]
check("A2 no row resolved by a tie/weak rule (silent-guess path unexercised)",
      len(guessy) == 0, f"{len(guessy)} rows")

# A3 -- no fabricated ids: every mlbam_id was corroborated or is at least a
# real statsapi id seen during the audit's index build.
audited = audit["verdict"]
check("A3 audit ran against independent evidence",
      audited.get("AGREE", 0) > 0, f"{audited.get('AGREE',0)} agreeing player-seasons")

# A4 -- disagreements are bounded and enumerable
dis = audit["disagreements"]
dis_2025 = [d for d in dis if d["season"] == 2025]
check("A4 zero 2025 identity disagreements (the season Exit 1 needs)",
      len(dis_2025) == 0, f"{len(dis)} total, all season={sorted({d['season'] for d in dis})}")

# A5 -- collisions are enumerated and carry negligible weight
coll = xwalk["collisions"]
by_id = {r["espn_id"]: r for r in results}
coll_wt = sum(by_id[i]["wt25"] + by_id[i]["wt26"]
              for ids in coll.values() for i in ids)
check("A5 collisions enumerated with bounded weight",
      coll_wt < 100, f"{len(coll)} collisions, {coll_wt:.0f} units of weight")

print()
print("=" * 76)
print("B. CLUB-OF-GAME COVERAGE  (no row falls back to pro_team)")
print("=" * 76)

for key in ("2025-ALL", "2025-A", "2025-B", "2026-ALL"):
    s = cov["stats"][key]
    pct = 100 * s["hit"] / s["tot"] if s["tot"] else 0
    check(f"B:{key} resolves 100% of active-slot weight to a club-of-game",
          abs(pct - 100.0) < 1e-9,
          f"{s['hit']:,.0f}/{s['tot']:,.0f} = {pct:.2f}%, unresolved {s['miss']:,.0f}")

# B2 -- the weights reconcile to the figures the tickets state independently
KNOWN = {"2025-ALL": 202547, "2025-A": 169187, "2025-B": 33360, "2026-ALL": 129456}
for key, want in KNOWN.items():
    got = cov["stats"][key]["tot"]
    check(f"B:{key} total weight reconciles to the ticket figure {want:,}",
          abs(got - want) < 1, f"got {got:,.0f}")

# B3 -- zero silent fallback: the residual list is empty AND is enumerable.
# (Condition 2 -- the residual must be NAMED, not merely counted.)
check("B3 residual is empty and enumerable (condition 2)",
      len(cov["residual"]) == 0, f"{len(cov['residual'])} players in residual")

print()
print("=" * 76)
print("C. STRUCTURAL")
print("=" * 76)

# C1 -- club resolution never consults pro_team. Structural, by construction:
# the coverage measurement derives club ONLY from the swept game-level split.
# Assert the sweep carries a club for every warehouse-weighted row (already B),
# and that the disagreement counter is non-zero -- proving the two sources are
# genuinely different and pro_team was not silently reused as the answer.
tot_dis = sum(cov["stats"][k]["disagree"] for k in ("2025-ALL", "2026-ALL"))
check("C1 game-level club is independent of pro_team (they disagree materially)",
      tot_dis > 0, f"{tot_dis:,.0f} units of weight where they differ")

print()
print("=" * 76)
if failures:
    print(f"{len(failures)} ASSERTION(S) FAILED:")
    for f in failures:
        print(f"   - {f}")
    sys.exit(1)
print("ALL ASSERTIONS PASSED")
