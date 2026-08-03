"""
MLB-129 Step 0 -- does ESPN send ANY external player id (MLBAM / bbref / retro)?

Read-only. Hits the same kona_player_info endpoint extract.py already calls
weekly, plus the playercard view, and enumerates EVERY key ESPN returns on a
player object -- because extract.py:307-315 hand-picks 7 keys and discards the
rest, so RAW cannot answer this question.

Writes nothing to the warehouse. Dumps findings to stdout + a JSON sample.
"""
import json
import os
import sys
from collections import Counter
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(r"C:\Users\kyled\projects\espn-league-manager")
load_dotenv(ROOT / ".env")

ESPN_S2 = os.getenv("ESPN_S2")
SWID = os.getenv("SWID")
LEAGUE_ID = int(os.getenv("LEAGUE_ID"))
BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons"
COOKIES = {"swid": SWID, "espn_s2": ESPN_S2}

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")

# MLBAM ids for players certain to be in the pool -- if ESPN carries MLBAM
# anywhere, one of these integers will appear verbatim in the payload.
KNOWN_MLBAM = {
    660271: "Ohtani",
    592450: "Judge",
    665742: "Soto",
    677951: "Bobby Witt Jr.",
    808967: "Yamamoto",
    669373: "Tarik Skubal",
}
# bbref ids are strings like 'ohtansh01'; retro like 'ohtas001'.
EXTERNAL_HINTS = (
    "mlbam", "mlbid", "mlb_id", "bbref", "baseball_reference", "retro",
    "externalid", "external_id", "sportradar", "statsapi", "gsis",
    "universalid", "universal_id", "personid", "person_id", "globalid",
)


def walk(obj, path="", keys=None, depth=0, maxdepth=8):
    """Collect every key path in a nested structure."""
    if keys is None:
        keys = Counter()
    if depth > maxdepth:
        return keys
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            keys[p] += 1
            walk(v, p, keys, depth + 1, maxdepth)
    elif isinstance(obj, list):
        for v in obj[:3]:  # sample -- shapes repeat
            walk(v, f"{path}[]", keys, depth + 1, maxdepth)
    return keys


def find_ints(obj, targets, path="", hits=None, depth=0):
    """Locate any occurrence of the known MLBAM integers, at any depth."""
    if hits is None:
        hits = []
    if depth > 10:
        return hits
    if isinstance(obj, dict):
        for k, v in obj.items():
            find_ints(v, targets, f"{path}.{k}", hits, depth + 1)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            find_ints(v, targets, f"{path}[{i}]", hits, depth + 1)
    else:
        try:
            if isinstance(obj, bool):
                return hits
            n = int(obj)
            if n in targets:
                hits.append((path, n, targets[n]))
        except (TypeError, ValueError):
            if isinstance(obj, str) and len(obj) >= 6:
                low = obj.lower()
                # bbref/retro shaped: letters then digits
                if any(h in low for h in ("bbref", "retro")):
                    hits.append((path, obj, "string-hint"))
    return hits


def probe_kona(year, scoring_period, limit=1500):
    url = f"{BASE}/{year}/segments/0/leagues/{LEAGUE_ID}"
    ffilter = {"players": {"limit": limit,
                           "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}}
    r = requests.get(
        url,
        params={"view": "kona_player_info", "scoringPeriodId": scoring_period},
        cookies=COOKIES,
        headers={"x-fantasy-filter": json.dumps(ffilter)},
        timeout=45,
    )
    r.raise_for_status()
    return r.json()


def probe_playercard(year, player_ids):
    """The playercard view -- richer player object than kona."""
    url = f"{BASE}/{year}/segments/0/leagues/{LEAGUE_ID}"
    ffilter = {"players": {"filterIds": {"value": list(player_ids)}}}
    r = requests.get(
        url,
        params={"view": "kona_playercard"},
        cookies=COOKIES,
        headers={"x-fantasy-filter": json.dumps(ffilter)},
        timeout=45,
    )
    r.raise_for_status()
    return r.json()


def report(label, data):
    print(f"\n{'=' * 72}\n{label}\n{'=' * 72}")
    players = data.get("players") or []
    print(f"players returned: {len(players)}")
    if not players:
        print("  (empty)")
        return None

    # Full key inventory across a sample of player objects
    keys = Counter()
    for entry in players[:40]:
        walk(entry, "", keys)
    print(f"\n-- distinct key paths (sample of 40 entries): {len(keys)}")
    for k in sorted(keys):
        if k.count("[]") <= 1 and k.count(".") <= 3:
            print(f"   {k}")

    # Anything whose NAME hints at an external id
    hinted = [k for k in keys if any(h in k.lower() for h in EXTERNAL_HINTS)]
    print(f"\n-- key paths hinting at an external id: {len(hinted)}")
    for k in sorted(hinted):
        print(f"   *** {k}")

    # Anything whose VALUE is a known MLBAM integer
    hits = []
    for entry in players:
        hits.extend(find_ints(entry, KNOWN_MLBAM))
    print(f"\n-- occurrences of known MLBAM ids as VALUES: {len(hits)}")
    for path, val, who in hits[:25]:
        print(f"   *** {path} = {val}  ({who})")

    return players


def main():
    print("MLB-129 Step 0 -- probing ESPN for external player ids")
    print(f"league={LEAGUE_ID}")

    samples = {}
    for year, sp in ((2026, 120), (2025, 120)):
        try:
            data = probe_kona(year, sp)
            players = report(f"kona_player_info  year={year} scoringPeriod={sp}", data)
            if players:
                samples[f"kona_{year}"] = players[0]
        except Exception as e:
            print(f"\n[warn] kona {year} sp={sp} failed: {e}")

    # playercard for a handful of well-known players
    try:
        data = probe_kona(2026, 120, limit=25)
        ids = [(e.get("player") or {}).get("id") for e in (data.get("players") or [])]
        ids = [i for i in ids if i][:8]
        if ids:
            pc = probe_playercard(2026, ids)
            players = report(f"kona_playercard  year=2026 ids={ids}", pc)
            if players:
                samples["playercard_2026"] = players[0]
    except Exception as e:
        print(f"\n[warn] playercard failed: {e}")

    if samples:
        p = OUT / "step0_samples.json"
        p.write_text(json.dumps(samples, indent=2)[:2_000_000], encoding="utf-8")
        print(f"\n\nsample objects written to {p}")


if __name__ == "__main__":
    main()
