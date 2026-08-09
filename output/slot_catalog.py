"""output/slot_catalog.py

Lineup-slot metadata helpers. The Python half of the slot vocabulary that
MLB-222 F-1 moved into the `slot_classification` seed -- so "is this a
pitching slot?" is answered from the same dictionary the SQL layer uses
instead of from a frozenset literal maintained alongside it.

What this replaced was `almanac_logic._PITCHING_SLOTS`, whose own
docstring said "keep these two in sync" about the CASE expression in
fct_player_position_pts. Two hand-maintained lists agreeing by discipline
is the thing the seed exists to end.

Reads the SEED rather than a mart, which is where this deviates from
stat_catalog.py's "public outputs depend on a mart-layer model" note:
there is no consumer-facing dim_slot yet. Adding one belongs with MLB-6,
which already owns turning the slot dictionary into per-platform mapping
data.

Caching: `_load_catalog` is `@lru_cache(maxsize=1)`, so the fetch happens
once per process. Test code can call `slot_catalog._load_catalog.
cache_clear()` to force a reload (e.g. after a `dbt seed` between phases
of a test), exactly as stat_catalog documents.
"""

from functools import lru_cache

from db import query_snowflake

# The floor the SQL layer uses too (see stg_box_scores). NOT a guess: a
# category matching no stat_category downstream would delete the player's
# stats rather than misfile them, so an unknown slot defaults to the
# harmless side and the ALARM lives at build time --
# assert_slot_classification_covers_observed_slots fails the build naming
# any slot missing from the seed.
DEFAULT_SLOT_CATEGORY = 'hitting'


@lru_cache(maxsize=1)
def _load_catalog() -> tuple:
    """Fetch the slot catalog once per process. Tuple, not list, for
    hash-stability."""
    rows = query_snowflake("""
        SELECT platform, lineup_slot, slot_category, is_starting_slot,
               sort_order, notes
        FROM slot_classification
    """)
    return tuple(rows)


@lru_cache(maxsize=None)
def get_pitching_slots(platform: str = 'espn') -> frozenset:
    """Slot labels whose production is PITCHING production.

    ESPN: SP, RP and the generic P. A league that configures only P and
    never SP/RP is exactly the shape the old literal handled badly.
    """
    return frozenset(
        r['lineup_slot'] for r in _load_catalog()
        if r['platform'] == platform and r['slot_category'] == 'pitching'
    )


@lru_cache(maxsize=None)
def get_inactive_slots(platform: str = 'espn') -> frozenset:
    """Slot labels that are NOT a deployment -- bench, IL, and the
    extract's synthetic FA label for unrostered players."""
    return frozenset(
        r['lineup_slot'] for r in _load_catalog()
        if r['platform'] == platform and r['slot_category'] == 'inactive'
    )


def slot_category(slot, platform: str = 'espn') -> str:
    """'pitching' | 'hitting' | 'inactive' for one slot label.

    Falls back to DEFAULT_SLOT_CATEGORY for a slot with no seed row, and
    for a seeded slot carrying a NULL category (CBS ACT/U/EST, which
    cannot be classified from the slot alone -- MLB-226).
    """
    for r in _load_catalog():
        if r['platform'] == platform and r['lineup_slot'] == slot:
            return r['slot_category'] or DEFAULT_SLOT_CATEGORY
    return DEFAULT_SLOT_CATEGORY


def sql_in_list(slots) -> str:
    """Render a slot set as a SQL IN-list body, sorted.

    Sorted so the generated SQL is stable run to run -- a set's iteration
    order is not, and unstable SQL text makes a real diff impossible to
    see (the MLB-128 determinism instinct, applied to generated SQL).
    """
    return ', '.join(f"'{s}'" for s in sorted(slots))
