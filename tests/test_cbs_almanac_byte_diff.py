"""Byte-diff regression test for the CBS (points-league) almanac (MLB-110).

The CBS twin of test_almanac_byte_diff.py. Renders every tab and asserts each
is byte-identical to the corpus at `tests/fixtures/cbs_almanac/`. Until this
existed the CBS almanac -- 19 tabs across 26 seasons, the headline surface of
v1.5.0 -- was covered only by eyeball, while ESPN had a byte-diff net. It is
also the side that changes most.

TWO DIFFERENCES FROM THE ESPN HARNESS, both forced by the renderer:

1. It renders IN-PROCESS rather than by subprocess. The ESPN entry script
   pins its anchor with --season-year/--matchup-period, but those are H2H
   concepts the points path ignores by design ("the points almanac's horizon
   comes from the data"). There is no CLI anchor to pin, and the one live
   input has to be frozen from inside the process.

2. It pins the Team-of-the-Month window. `_month_window()` is documented as
   "the ONE deliberately-live board" (Kyle 2026-07-13) -- it reads TODAY'S
   date so the board turns over with the calendar, and its label renders the
   month into the Home tab. A golden over a live clock would drift on the 8th
   of every month and get blind-blessed, which is exactly the failure mode
   MLB-111 flagged on the recap baseline. Pinning it to a COMPLETED month
   makes the corpus depend on warehouse state alone. The live behaviour is
   unchanged in production and still covered by its own unit tests; what is
   pinned here is only the window this test renders through.

Marked `warehouse`. Run:
    pytest tests/ -m warehouse -k cbs_almanac_byte_diff

Regenerate after a reviewed, intentional output change:
    REGENERATE_BASELINES=1 pytest tests/ -m warehouse -k cbs_almanac_byte_diff
"""

import os
import shutil
from datetime import date
from pathlib import Path

import pytest

pytestmark = pytest.mark.warehouse

REPO = Path(__file__).resolve().parents[1]
FIX_DIR = REPO / "tests" / "fixtures" / "cbs_almanac"

if not FIX_DIR.exists():
    # Rendered from the real league, so the corpus lives outside the public
    # repo (MLB-95). Local clones with it run the byte-diff; everyone else
    # skips, matching the ESPN harness.
    pytestmark = [pytest.mark.warehouse,
                  pytest.mark.skip(reason="private CBS almanac corpus not present")]

CBS_LEAGUE_KEY = "cbs-bsb"

# A COMPLETED month, so the pinned window never depends on how far the
# current month has accrued. Move this and re-anchor in the same commit.
MONTH_WINDOW = (date(2026, 6, 1), date(2026, 6, 30))


@pytest.fixture
def cbs_league():
    """Point the shared db layer at CBS, then put it back.

    The league is process-global. Rendering in-process means leaking it would
    silently re-point any later test in the session at the wrong league, so
    the restore is not optional.
    """
    import db

    previous = db.league_key()
    db.set_league(CBS_LEAGUE_KEY)
    try:
        yield
    finally:
        db.set_league(previous)


def _render(out_dir: Path, monkeypatch) -> None:
    import cbs_almanac_sheets
    import generate_almanac_sheet

    monkeypatch.setattr(cbs_almanac_sheets, "_month_window",
                        lambda: MONTH_WINDOW)

    tabs, _, _ = cbs_almanac_sheets.build_all_tabs()
    preview_tabs = [(title, rows) for title, rows, _ in tabs]
    generate_almanac_sheet._write_preview_dir(preview_tabs, str(out_dir))


def _list_tsv(d: Path) -> set:
    return {p.name for p in d.iterdir() if p.suffix == ".tsv"}


def _first_diff_hint(actual: bytes, expected: bytes) -> str:
    if actual == expected:
        return "match"
    try:
        a_lines = actual.decode("utf-8").splitlines()
        e_lines = expected.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        min_len = min(len(actual), len(expected))
        byte_off = next((i for i in range(min_len) if actual[i] != expected[i]),
                        min_len)
        return (f"byte {byte_off} differs; actual {len(actual)} bytes, "
                f"expected {len(expected)} bytes")

    first_line = next(
        (i for i, (a, e) in enumerate(zip(a_lines, e_lines)) if a != e),
        min(len(a_lines), len(e_lines)),
    )
    return (
        f"first diff line {first_line + 1} "
        f"(actual {len(a_lines)} lines, expected {len(e_lines)} lines):\n"
        f"      expected: {e_lines[first_line] if first_line < len(e_lines) else '<EOF>'}\n"
        f"      actual:   {a_lines[first_line] if first_line < len(a_lines) else '<EOF>'}"
    )


def test_cbs_almanac_tsv_matches_baseline(tmp_path, monkeypatch, cbs_league):
    _render(tmp_path, monkeypatch)

    actual_names = _list_tsv(tmp_path)
    expected_names = _list_tsv(FIX_DIR)

    if os.environ.get("REGENERATE_BASELINES") == "1":
        for name in actual_names:
            shutil.copy2(tmp_path / name, FIX_DIR / name)
        for stale in expected_names - actual_names:
            (FIX_DIR / stale).unlink()
        return

    missing = expected_names - actual_names
    extra = actual_names - expected_names
    assert not missing, f"missing TSV outputs vs fixture: {sorted(missing)}"
    assert not extra, f"unexpected TSV outputs vs fixture: {sorted(extra)}"

    drifted = []
    for name in sorted(actual_names):
        actual = (tmp_path / name).read_bytes()
        expected = (FIX_DIR / name).read_bytes()
        if actual != expected:
            drifted.append((name, _first_diff_hint(actual, expected)))

    if drifted:
        msg = ["CBS almanac TSV drift vs tests/fixtures/cbs_almanac/:"]
        for name, hint in drifted:
            msg.append(f"  {name}: {hint}")
        msg.append(
            "If intentional, regenerate with: REGENERATE_BASELINES=1 "
            "pytest tests/ -m warehouse -k cbs_almanac_byte_diff"
        )
        pytest.fail("\n".join(msg))
