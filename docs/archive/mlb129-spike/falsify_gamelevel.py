"""
Try to FALSIFY the claim that ESPN's game-level proTeamId is club-of-game.

MLB-159's own confirming query is the test: a player traded mid-season must
show >1 club in a season where the club signal is real, and exactly 1 where it
is frozen. The warehouse's player-level stamp gives 0 of 1,236 for 2025 and 66
of 1,208 for 2026.

If the game-level stamp is genuinely club-of-game, 2025 must show a realistic
crop of multi-club players -- comparable to 2026's 66. If it shows ~0, the
game-level field is the same frozen stamp wearing a different key and the
finding is wrong.

Also re-pins the two cases the tickets pinned against Baseball Reference:
Curtis Mead 2026 (BR: 1 game, 2 PA for Boston; warehouse credits 20) and the
four 2025 mis-stamps.
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from espn_api.baseball import constant as espn_const

OUT = Path(sys.argv[1])
SWEEP = OUT / "gamelevel_club.ndjson"
ABBR = espn_const.PRO_TEAM_MAP

PINNED = {
    32082: ("Sonny Gray", 2025, "StL all season; warehouse stamps Bos"),
    32151: ("Tyler Anderson", 2025, "LAA all season; warehouse stamps FA"),
    31668: ("Marcell Ozuna", 2025, "Atl all season; warehouse stamps FA"),
    28976: ("Max Scherzer", 2025, "Tor all season; warehouse stamps FA"),
    42360: ("Curtis Mead", 2026, "BR: 1 G / 2 PA for Bos; warehouse credits 20"),
}


def main():
    per_season = defaultdict(lambda: defaultdict(Counter))   # yr -> pid -> clubs
    names = {}
    player_level = {}
    for ln in SWEEP.open(encoding="utf-8"):
        r = json.loads(ln)
        yr, pid = r["season_year"], r["player_id"]
        names[pid] = r["player_name"]
        player_level[(pid, yr)] = ABBR.get(r.get("player_level_proTeamId"), "FA")
        for g in r["game_splits"]:
            tid = g.get("proTeamId")
            if tid:
                per_season[yr][pid][ABBR.get(tid, f"?{tid}")] += 1

    print("=" * 78)
    print("FALSIFIER -- distinct GAME-LEVEL clubs per player-season")
    print("(warehouse player-level stamp: 2025 = 0 of 1,236 ; 2026 = 66 of 1,208)")
    print("=" * 78)
    for yr in sorted(per_season):
        counts = Counter(len(c) for c in per_season[yr].values())
        multi = sum(n for k, n in counts.items() if k > 1)
        print(f"\n{yr}: {len(per_season[yr])} players with game-level clubs")
        print(f"     distribution of distinct clubs per player: {dict(sorted(counts.items()))}")
        print(f"     >1 club (mid-season movers): {multi}")

    print("\n" + "=" * 78)
    print("Multi-club players in 2025 -- these are the trades the stored")
    print("player-level stamp erased entirely")
    print("=" * 78)
    movers = [(pid, c) for pid, c in per_season[2025].items() if len(c) > 1]
    movers.sort(key=lambda kv: -sum(kv[1].values()))
    for pid, c in movers[:30]:
        stored = player_level.get((pid, 2025), "?")
        clubs = ", ".join(f"{k}({v})" for k, v in c.most_common())
        print(f"  {pid:>8} {str(names.get(pid))[:24]:24s} stored={stored:4s}  game-level: {clubs}")
    print(f"\n  ... {len(movers)} multi-club players in 2025 total")

    print("\n" + "=" * 78)
    print("PINNED CASES")
    print("=" * 78)
    for pid, (nm, yr, note) in PINNED.items():
        c = per_season[yr].get(pid, Counter())
        stored = player_level.get((pid, yr), "?")
        print(f"\n{nm} ({pid}) {yr} -- {note}")
        print(f"   stored player-level : {stored}")
        print(f"   game-level clubs    : {dict(c.most_common())}")


if __name__ == "__main__":
    main()
