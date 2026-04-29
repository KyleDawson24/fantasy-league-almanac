"""
dump_stats_map.py — One-shot helper to extract ESPN's stat ID → abbreviation
mapping from the espn-api package, then format it for pasting into a chat or
copying into stat_classification.csv.

Background: ESPN's raw API uses numeric stat IDs (5 = HR, 21 = RBI, etc.).
The espn-api wrapper translates them to human-readable names. This script
prints that mapping so the stat_classification seed's `espn_stat_id` column
can be backfilled.

Run once:
  py extract/dump_stats_map.py

Paste the output back into the chat (or directly into the seed) to populate
the espn_stat_id values for all standard stats. The "unknown" / internal
stats (22, 30, 61, 64, 78, 79, 80, 99, 42, 65, 66, 81) are already populated
in the seed with manual resolutions.
"""

from espn_api.baseball import constant


def find_stats_map():
    """Locate the stat_id → name dict in the espn-api package.

    Different versions name it differently (STATS_MAP, STAT_ID_TO_NAME, etc.)
    so we scan attributes for the largest dict that looks like a stat mapping.
    """
    candidates = []
    for attr in dir(constant):
        if attr.startswith('_'):
            continue
        val = getattr(constant, attr)
        if isinstance(val, dict) and len(val) > 30:
            candidates.append((attr, val))
    return candidates


def main():
    candidates = find_stats_map()

    if not candidates:
        print("Could not find STATS_MAP-like dict in espn_api.baseball.constant.")
        print("Available attributes on constant:")
        for attr in dir(constant):
            if not attr.startswith('_'):
                val = getattr(constant, attr)
                print(f"  {attr}: {type(val).__name__}"
                      f"{' (' + str(len(val)) + ' entries)' if hasattr(val, '__len__') else ''}")
        return

    for attr_name, stats_map in candidates:
        print(f"=== {attr_name} ({len(stats_map)} entries) ===")
        # Sort numerically when keys are int-like
        try:
            sorted_items = sorted(stats_map.items(),
                                  key=lambda x: int(x[0]) if str(x[0]).isdigit() else 999)
        except (TypeError, ValueError):
            sorted_items = sorted(stats_map.items())

        for stat_id, stat_name in sorted_items:
            print(f"  {stat_id}: {stat_name}")
        print()

    print("=" * 60)
    print("Paste the contents above so the espn_stat_id column in")
    print("stat_classification.csv can be backfilled for the standard stats.")
    print("=" * 60)


if __name__ == "__main__":
    main()
