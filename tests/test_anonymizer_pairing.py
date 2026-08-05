"""The screenshot anonymizer's mapping contract (MLB-202).

`tools/anonymize_sheet_copy.py` derives real -> twin name pairs by joining
the maintainer's league config against the published demo fixture. Two ways
that join has gone wrong, both covered here:

  ROOTS. The tool read both sides out of `dbt_league/seeds/`. The MLB-114
  split moved the real side to `league_config/` and the twin to `demo/`, and
  the tool kept opening the old paths -- so it raised before building a
  mapping and the release's screenshot workflow was simply dead. Nothing
  failed until someone tried to take a screenshot. `test_seed_roots_resolve`
  is the tripwire: the next root move breaks a test instead of the tool.

  POSITION. The join paired rows by index. Row order is not a key -- sort a
  file or regenerate the fixture and every pair silently shifts, producing a
  sheet that looks anonymized and is not. `test_pairing_is_position_
  independent` pins the fix by shuffling the input and demanding the same
  answer.

READING THE REAL SIDE: the maintainer's `dbt_league/league_config/*.csv`
working copies are real league data (skip-worktree; blank at HEAD). The
tests here that touch them assert on COUNTS ONLY and never put a cell value
in an assertion message -- a failure prints on Kyle's machine, and pytest
prints what you give it. Tests that need real data skip on a clone, where
those files are the blank templates.

Fast and pure: no warehouse, no network, no Sheets call.
"""
from __future__ import annotations

import csv
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REAL_DIR = "dbt_league/league_config"
TWIN_DIR = "demo/league_config"

# The five twinned identity files, named here rather than imported so that a
# rename in the tool has to be a deliberate two-file edit.
IDENTITY_FILES = (
    "cbs_franchises.csv",
    "cbs_team_owners.csv",
    "owner_alias.csv",
    "owner_nicknames.csv",
    "team_owner_by_year.csv",
)


def _tool():
    """The anonymizer module, or a skip if its Sheets deps are absent.

    Imported lazily: the root checks below are the load-bearing regression
    guard and must run even where gspread is not installed.
    """
    pytest.importorskip("gspread", reason="anonymizer needs the Sheets client")
    import importlib.util

    path = os.path.join(REPO, "tools", "anonymize_sheet_copy.py")
    spec = importlib.util.spec_from_file_location("anonymize_sheet_copy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _disk_rows(rel):
    with open(os.path.join(REPO, rel), newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _maintainer_side_populated():
    """True when league_config holds real data rather than blank templates."""
    try:
        return bool(_disk_rows(f"{REAL_DIR}/owner_nicknames.csv"))
    except OSError:
        return False


# --------------------------------------------------------------------------
# Roots -- the MLB-202 regression guard. No module import, no real data.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", IDENTITY_FILES)
def test_seed_roots_resolve(name):
    """Both sides of every identity pair exist where the tool looks.

    This is the check whose absence let the 114 split kill the anonymizer
    silently. It deliberately does not care what is IN the files.
    """
    for root in (REAL_DIR, TWIN_DIR):
        assert os.path.isfile(os.path.join(REPO, root, name)), (
            f"{root}/{name} is missing. The anonymizer joins {REAL_DIR} "
            f"against {TWIN_DIR}; if a seed root moves again, re-point "
            f"_REAL_DIR/_TWIN_DIR in tools/anonymize_sheet_copy.py -- the "
            f"tool cannot build a mapping without both sides."
        )


@pytest.mark.parametrize("name", IDENTITY_FILES)
def test_twin_side_is_published_and_populated(name):
    """The twin must carry data, not be a blank template.

    The failure mode this catches is subtle and was the actual MLB-202
    mechanism: point the twin at a path whose committed bytes are a header
    row, and the tool does not crash on a missing file -- it derives an
    empty mapping and anonymizes nothing.
    """
    rows = _disk_rows(f"{TWIN_DIR}/{name}")
    assert rows, (
        f"{TWIN_DIR}/{name} has no data rows. The twin side is the published "
        f"demo fixture -- if it reads as blank, the derived mapping is empty "
        f"and a 'successful' run would replace nothing at all."
    )


def test_tool_points_at_the_split_roots():
    """The tool's own constants agree with the layout above."""
    tool = _tool()
    assert tool._REAL_DIR == REAL_DIR
    assert tool._TWIN_DIR == TWIN_DIR
    assert set(tool.ALL_SEEDS) == set(IDENTITY_FILES)
    for name in tool.ALL_SEEDS:
        assert name in tool.SEED_KEYS, (
            f"{name} has no declared join key. Every identity file needs the "
            f"columns anonymization does not touch, or it falls back to being "
            f"unpairable."
        )


# --------------------------------------------------------------------------
# Position independence -- synthetic rows, so this runs anywhere.
# --------------------------------------------------------------------------

def _rows(*triples):
    return [
        {"league_key": lk, "franchise_id": fid, "franchise_name": nm}
        for lk, fid, nm in triples
    ]


def test_pairing_is_position_independent():
    """Reversing the input must not change a single pair.

    This is the property the old `zip(real, anon)` join lacked, and the one
    that matters: the two files are maintained by different processes and
    have no reason to stay in the same order forever.
    """
    tool = _tool()
    real = _rows(("cbs-bsb", "1", "Real One"),
                 ("cbs-bsb", "2", "Real Two"),
                 ("cbs-bsb", "3", "Real Three"))
    twin = _rows(("cbs-bsb", "1", "Twin One"),
                 ("cbs-bsb", "2", "Twin Two"),
                 ("cbs-bsb", "3", "Twin Three"))

    straight = tool._pair_by_key(tool.SEED_FRANCHISES, real, twin)
    shuffled = tool._pair_by_key(tool.SEED_FRANCHISES, list(reversed(real)),
                                 list(reversed(twin)))
    half = tool._pair_by_key(tool.SEED_FRANCHISES, real, list(reversed(twin)))

    def as_map(paired):
        return {r["franchise_id"]: t["franchise_name"] for r, t in paired}

    expected = {"1": "Twin One", "2": "Twin Two", "3": "Twin Three"}
    assert as_map(straight) == expected
    assert as_map(shuffled) == expected, "pairing changed when both sides were reordered"
    assert as_map(half) == expected, "pairing changed when only the twin was reordered"


def test_pairing_refuses_a_key_with_no_twin():
    """A real row whose key is absent from the twin is a partial mapping."""
    tool = _tool()
    real = _rows(("cbs-bsb", "1", "Real One"), ("cbs-bsb", "9", "Real Nine"))
    twin = _rows(("cbs-bsb", "1", "Twin One"))
    with pytest.raises(RuntimeError, match="no row in"):
        tool._pair_by_key(tool.SEED_FRANCHISES, real, twin)


def test_pairing_refuses_an_ambiguous_group_rather_than_guessing():
    """Two rows under one key and no resolver: refuse, never fall back to order."""
    tool = _tool()
    real = _rows(("cbs-bsb", "4", "Real A"), ("cbs-bsb", "4", "Real B"))
    twin = _rows(("cbs-bsb", "4", "Twin A"), ("cbs-bsb", "4", "Twin B"))
    with pytest.raises(RuntimeError, match="Row order is not"):
        tool._pair_by_key(tool.SEED_FRANCHISES, real, twin)


def test_pairing_refuses_mismatched_group_sizes():
    tool = _tool()
    real = _rows(("cbs-bsb", "4", "Real A"), ("cbs-bsb", "4", "Real B"))
    twin = _rows(("cbs-bsb", "4", "Twin A"))
    with pytest.raises(RuntimeError, match="cannot be aligned"):
        tool._pair_by_key(tool.SEED_FRANCHISES, real, twin)


def test_identity_diagnostics_do_not_echo_identifiers():
    """Refusal messages are pasted into tickets; they must not carry a name.

    A CBS owner id is a name-derived slug, so printing any slice of one
    prints part of a surname.
    """
    tool = _tool()
    handle = tool._ref("cbs-somebody-real")
    assert "somebody" not in handle and "real" not in handle
    assert len(handle) == 8 and handle == tool._ref("cbs-somebody-real")


# --------------------------------------------------------------------------
# The real mapping -- maintainer machines only.
# --------------------------------------------------------------------------

needs_real = pytest.mark.skipif(
    not _maintainer_side_populated(),
    reason=f"{REAL_DIR} holds blank templates (a clone); nothing to pair",
)


@needs_real
def test_every_identity_bearing_pair_resolves():
    """Every owner in the real config finds a twin, and the map builds.

    Counts only in the assertions: this test runs against real league data,
    and a failure message is printed output.
    """
    tool = _tool()
    crosswalk = tool._build_owner_crosswalk(verbose=False)

    owners = [r for r in _disk_rows(f"{REAL_DIR}/owner_nicknames.csv")
              if (r.get("owner_id") or "").strip()]
    unmapped = [r for r in owners
                if (r.get("owner_id") or "").strip() not in crosswalk]
    assert not unmapped, (
        f"{len(unmapped)} of {len(owners)} owners in {REAL_DIR}/"
        f"owner_nicknames.csv have no twin. Their real names would pass "
        f"through a screenshot unreplaced."
    )

    pairs, report = tool.build_mapping(verbose=False)
    assert pairs, "the derived mapping is empty"
    assert not report["unpaired_alias"], (
        f"{len(report['unpaired_alias'])} alias owner(s) had no twin and were "
        f"redacted rather than mapped"
    )
    # A mapping whose own output trips the residual audit is unverifiable.
    tool._assert_auditable(pairs)


@needs_real
def test_derived_mapping_is_deterministic():
    """Two derivations agree. Any ordering dependence shows up here."""
    tool = _tool()
    first, _ = tool.build_mapping(verbose=False)
    second, _ = tool.build_mapping(verbose=False)
    assert first == second, "the derived mapping is not stable across runs"
