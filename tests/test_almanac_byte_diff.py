"""Byte-diff regression test for the v1.1.1 almanac refactor.

Re-runs `output/generate_almanac_sheet.py --no-sheets --preview-dir <tmp>` for
the v1.1.0 anchor (2026 Week 7) and asserts every generated TSV is
byte-identical to the baseline at `tests/fixtures/almanac_v1_1_0/`. This is
the stop-the-line gate for the planned v1.1.1 refactor pass: any drift in
the relocated SQL or split Python layers must fail this test.

Marked `warehouse` -- skipped by the default `pytest tests/` suite. Run at
checkpoints during the refactor:
    pytest tests/ -m warehouse -k almanac_byte_diff

To regenerate after an intentional output change (rare during refactor;
typical when a new matchup_period has been loaded and the fixture should
track the new state):
    REGENERATE_BASELINES=1 pytest tests/test_almanac_byte_diff.py -m warehouse

SCOPED BY FILE PATH, NOT BY `-k almanac_byte_diff` (2026-08-16). That
selector also matches tests/test_cbs_almanac_byte_diff.py, whose harness
honours the same environment variable -- so the old spelling rewrote the
CBS corpus alongside this one. Harmless when both were drifting for the
same reviewed reason, and silent blessing of 21 unreviewed files when
they were not. Regenerate one corpus at a time, deliberately.

The season/matchup_period anchor is pinned because the entry script
auto-detects MAX(matchup_period) when run without args; pinning keeps the
test stable across weekly extracts during the refactor window.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.warehouse

REPO = Path(__file__).resolve().parents[1]
FIX_DIR = REPO / "tests" / "fixtures" / "almanac_v1_1_0"

if not FIX_DIR.exists():
    # The golden corpus is rendered from the real league, so it lives outside
    # the public repo (MLB-95). Local clones with the corpus run the full
    # byte-diff; everyone else skips.
    pytestmark = [pytest.mark.warehouse,
                  pytest.mark.skip(reason="private almanac golden corpus not present")]

# Anchor matches the fixture's `Home.tsv` header ("2026 Week 7"). Update
# both this constant and the fixture in the same commit if the anchor
# moves.
ANCHOR_SEASON = 2026
ANCHOR_MATCHUP_PERIOD = 7


def _run_almanac_preview(preview_dir: Path) -> None:
    proc = subprocess.run(
        [
            sys.executable, "output/generate_almanac_sheet.py",
            "--no-sheets",
            "--season-year", str(ANCHOR_SEASON),
            "--matchup-period", str(ANCHOR_MATCHUP_PERIOD),
            "--preview-dir", str(preview_dir),
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        # The Home tab's A3 "Updated ..." stamp is render-time state, so
        # the corpus is only stable when it renders blank (MLB-141) --
        # the same doctrine as SUPPRESS_LEAGUE_NOTES in test_golden_output,
        # on its own switch so the two suppressions can't entangle.
        env=dict(os.environ, SUPPRESS_UPDATED_STAMP="1"),
    )
    assert proc.returncode == 0, (
        f"generate_almanac_sheet.py failed (exit {proc.returncode}):\n"
        f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )


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
        byte_off = next(
            (i for i in range(min_len) if actual[i] != expected[i]),
            min_len,
        )
        return (
            f"byte {byte_off} differs; "
            f"actual {len(actual)} bytes, expected {len(expected)} bytes"
        )

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


def test_almanac_tsv_matches_baseline(tmp_path):
    _run_almanac_preview(tmp_path)

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
        msg = ["Almanac TSV drift vs tests/fixtures/almanac_v1_1_0/:"]
        for name, hint in drifted:
            msg.append(f"  {name}: {hint}")
        msg.append(
            "If intentional, regenerate with: "
            "REGENERATE_BASELINES=1 pytest tests/test_almanac_byte_diff.py "
            "-m warehouse"
        )
        pytest.fail("\n".join(msg))
