"""The output layer's record-eligibility decisions (MLB-235 rung 4A).

THE BUG THIS PINS. The Sheets writer decided "is this row a standard week"
with `not source_row.get('is_abnormal')`. That is fine while abnormality is
always known -- and fails OPEN the moment it is not, because `not None` is
True. Platform derivation declines for in-flight periods, malformed payloads
and seasons below the evidence floor, so unknown is now a real state, and the
old expression would have shaded every one of them as an ordinary week: banded
as standard, and eligible to be marked a record holder.

Credential-free by construction: these are the pure row-selection helpers, fed
dicts. No Sheets client, no warehouse, no network.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "output") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "output"))


@pytest.fixture(scope="module")
def writer():
    try:
        import almanac_write
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"almanac_write not importable ({exc})")
    return almanac_write


def _row(eligible, abnormal=None):
    """A fact row as the query hands it back."""
    row = {"is_record_eligible": eligible}
    if abnormal is not None:
        row["is_abnormal"] = abnormal
    return row


# ---------------------------------------------------------------------------
# Banding: which rows count as standard weeks
# ---------------------------------------------------------------------------
def test_known_normal_rows_are_standard(writer):
    ranges = writer._team_weeks_standard_data_ranges(
        1, 4, [_row(True, False), _row(True, False), _row(True, False)])

    assert ranges == [{"sheetId": 1, "startRowIndex": 1, "endRowIndex": 4}]


def test_an_abnormal_row_breaks_the_band(writer):
    ranges = writer._team_weeks_standard_data_ranges(
        1, 4, [_row(True, False), _row(False, True), _row(True, False)])

    assert ranges == [{"sheetId": 1, "startRowIndex": 1, "endRowIndex": 2},
                      {"sheetId": 1, "startRowIndex": 3, "endRowIndex": 4}]


def test_an_unknown_row_breaks_the_band_too(writer):
    """The regression. `is_abnormal` is None and `is_record_eligible` is
    False; the old `not source_row.get('is_abnormal')` returned True here and
    banded an unknown period as an ordinary week."""
    ranges = writer._team_weeks_standard_data_ranges(
        1, 4, [_row(True, False), _row(False, None), _row(True, False)])

    assert ranges == [{"sheetId": 1, "startRowIndex": 1, "endRowIndex": 2},
                      {"sheetId": 1, "startRowIndex": 3, "endRowIndex": 4}]


def test_a_row_missing_the_gate_entirely_is_not_standard(writer):
    """Defence in depth: a caller that forgot to select the column must not
    get the permissive answer."""
    ranges = writer._team_weeks_standard_data_ranges(1, 2, [{}])

    assert ranges == []


# ---------------------------------------------------------------------------
# The decision is spelled on the gate, not on the label
# ---------------------------------------------------------------------------
def test_the_banding_decision_reads_the_gate_not_the_flag(writer):
    """A row whose flag and gate disagree proves which one is consulted. This
    shape does not occur in practice -- it exists to make the source of the
    decision observable."""
    ranges = writer._team_weeks_standard_data_ranges(
        1, 2, [{"is_abnormal": False, "is_record_eligible": False}])

    assert ranges == [], "the writer is still deciding on is_abnormal"


def test_record_marking_skips_everything_not_eligible(writer):
    """_apply_team_weeks_record_formats' row filter, exercised through the
    same predicate: an unknown or abnormal row must never be marked as
    holding a record."""
    source = "".join(
        (REPO_ROOT / "output" / "almanac_write.py").read_text(encoding="utf-8").splitlines(True)
    )
    assert "if not source_row.get('is_record_eligible'):" in source, \
        "record marking no longer gates on eligibility"
    assert "is_standard = bool(source_row.get('is_record_eligible'))" in source


# ---------------------------------------------------------------------------
# The queries feeding those rows must select the gate
# ---------------------------------------------------------------------------
def test_the_team_weeks_query_selects_the_gate():
    """A Python-side gate is worthless if the column never arrives."""
    data = (REPO_ROOT / "output" / "almanac_data.py").read_text(encoding="utf-8")

    assert "is_record_eligible,\n" in data, \
        "the team-weeks payload does not carry is_record_eligible"


@pytest.mark.parametrize("module", [
    "almanac_data.py", "league_notes.py", "records_data.py",
    "generate_season_report.py",
])
def test_no_output_module_still_filters_records_on_the_raw_flag(module):
    """The inventory, reconciled after editing. `is_abnormal` may still be
    SELECTED for display -- what must not survive is a record-eligibility
    DECISION spelled on it."""
    text = (REPO_ROOT / "output" / module).read_text(encoding="utf-8")

    for bad in ("is_abnormal = false", "is_abnormal IS FALSE",
                "NOT is_abnormal", "not is_abnormal"):
        assert bad not in text, f"{module} still decides on `{bad}`"
