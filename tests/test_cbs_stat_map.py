"""Unit tests for the CBS stat crosswalk seeds (MLB-60).

Pure-function scope: reads the two seed CSVs plus a committed fixture of
the 2026 scoring_rules categories (captured from the CBS_CONFIG snapshot)
-- no warehouse. The dbt side enforces the same invariants relationally
(seed schema tests + assert_cbs_stat_map_dispositions); this suite makes
them CI-visible without a Snowflake connection and pins the scoring
fixture so a future scoring_rules recapture that changes weights breaks
loudly here instead of silently drifting the seed.
"""

import csv
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SEEDS = REPO / "dbt_league" / "seeds"
FIXTURE = REPO / "tests" / "fixtures" / "cbs_scoring_rules_2026.json"


def _read_seed(name):
    with open(SEEDS / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _canonical_rows():
    return _read_seed("canonical_stats.csv")


def _map_rows():
    return _read_seed("cbs_stat_map.csv")


def _scored_categories():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["categories"]


def test_canonical_keys_unique_and_slug_shaped():
    rows = _canonical_rows()
    keys = [r["canonical_key"] for r in rows]
    assert len(keys) == len(set(keys)), "duplicate canonical_key"
    for k in keys:
        assert k and k == k.lower() and " " not in k, f"non-slug canonical_key: {k!r}"


def test_cbs_keys_unique():
    keys = [r["cbs_key"] for r in _map_rows()]
    assert len(keys) == len(set(keys)), "duplicate cbs_key"


def test_mapped_dispositions_have_valid_canonicals():
    canonicals = {r["canonical_key"] for r in _canonical_rows()}
    for r in _map_rows():
        if r["disposition"] == "mapped":
            assert r["canonical_key"] in canonicals, (
                f"{r['cbs_key']}: mapped to unknown canonical "
                f"{r['canonical_key']!r}")
        else:
            assert not r["canonical_key"], (
                f"{r['cbs_key']}: disposition {r['disposition']!r} "
                f"must not carry a canonical_key")


def test_every_scored_category_is_mapped_with_matching_points():
    """The ticket's coverage rule, pinned to the captured 2026 rules:
    all 16 scored categories exist in the map, disposition=mapped, and
    the seed's points_2026 matches the snapshot's weight."""
    by_key = {r["cbs_key"]: r for r in _map_rows()}
    for name, points in _scored_categories().items():
        assert name in by_key, f"scored category {name!r} missing from cbs_stat_map"
        row = by_key[name]
        assert row["disposition"] == "mapped", (
            f"scored category {name!r} has disposition {row['disposition']!r}")
        assert row["in_scoring_rules_2026"] == "true", (
            f"scored category {name!r} not flagged in_scoring_rules_2026")
        assert float(row["points_2026"]) == float(points), (
            f"{name!r}: seed points {row['points_2026']} != snapshot {points}")


def test_no_stray_scored_flags():
    """No map row claims to be scored beyond the snapshot's 16."""
    scored = set(_scored_categories())
    flagged = {r["cbs_key"] for r in _map_rows()
               if r["in_scoring_rules_2026"] == "true"}
    assert flagged == scored, (
        f"in_scoring_rules_2026 flags diverge from the snapshot: "
        f"extra={flagged - scored}, missing={scored - flagged}")


def test_batting_strikeout_and_pitching_strikeout_stay_distinct():
    """The collision that bit ESPN (HBP id 12 vs 42) in CBS form: the
    rules' scored K is PITCHING, the feeds' KO is BATTING. They must
    map to distinct canonicals forever."""
    by_key = {r["cbs_key"]: r for r in _map_rows()}
    assert by_key["K"]["canonical_key"] == "strikeouts_pitching"
    assert by_key["KO"]["canonical_key"] == "strikeouts_batting"


def test_no_canonical_double_mapping_within_category():
    """Two CBS keys must not map to the same canonical stat (a canonical
    represents one platform concept per platform)."""
    seen = {}
    for r in _map_rows():
        if r["disposition"] != "mapped":
            continue
        key = r["canonical_key"]
        assert key not in seen, (
            f"canonical {key!r} mapped from both {seen[key]!r} and "
            f"{r['cbs_key']!r}")
        seen[key] = r["cbs_key"]


def test_bref_alignment_is_honest():
    """Fantasy-layer constructs must NOT claim a bref identity; the
    known real-world stats should carry one. Spot-pins on both sides."""
    by_key = {r["canonical_key"]: r for r in _canonical_rows()}
    for fantasy_only in ("quality_starts", "holds",
                        "inherited_runners_stranded",
                        "platform_fantasy_points"):
        assert not by_key[fantasy_only]["bref_key"], (
            f"{fantasy_only} must not claim a bref_key")
    for real, expected in (("home_runs", "HR"), ("earned_runs", "ER"),
                           ("putouts", "PO"), ("stolen_bases", "SB")):
        assert by_key[real]["bref_key"] == expected
