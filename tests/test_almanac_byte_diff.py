"""Byte-diff regression test for the v1.1.1 almanac refactor.

Re-runs `output/generate_almanac_sheet.py --no-sheets --preview-dir <tmp>` for
the v1.1.0 anchor (2026 Week 7) and asserts every generated TSV is
byte-identical to the baseline at `tests/fixtures/almanac_v1_1_0/`. This is
the stop-the-line gate for the planned v1.1.1 refactor pass: any drift in
the relocated SQL or split Python layers must fail this test.

Marked `warehouse` -- skipped by the default `pytest tests/` suite. Run at
checkpoints during the refactor:
    pytest tests/ -m warehouse -k almanac_byte_diff

ANCHOR STATE (MLB-295)
  minted: 2026-09-18 at repo HEAD 15a1afd
  freeze: wk22-2026; frozen = YES
  input archive SHA256:
    8d214251df61302907ee2bc7e7deb4c083cd00d8e377f802e026a839e9adceb3
  horizon: ESPN 2025 plus 2026 MP1-22; CBS history through period 24,
    2026-09-06; MLB through 2026-09-06
  render anchor: ESPN 2026 matchup period 7
  cache: data/fixtures/corpus-wk22-2026/cache/ESPN_FANTASY.duckdb
  rebuilt by: tools/corpus_fixture.py build (103 models passed)
  Snowflake parity validation is deferred; this anchor records the local lane.

FROZEN INPUT (MLB-295). This corpus reads corpus-wk22-2026, the frozen
week-22 RAW and projected league-config snapshot, never the live warehouse.
tools/corpus_fixture.py builds its separate DuckDB cache and rebuilds when
models, macros, reference seeds, project/profile config, RAW loader or adapter
versions change. Fetch private inputs with tools/fetch_corpus_fixture.py.
A byte diff means logic changed, not that a week passed. Regenerate only
after reviewing the cause, one corpus at a time by explicit test-file path.
Moving the freeze is a declared pass: freeze a new id, prepare private assets,
update DEFAULT_FREEZE_ID and re-anchor the affected corpora together.

To regenerate after a reviewed logic change:
    REGENERATE_BASELINES=1 pytest tests/test_almanac_byte_diff.py -m warehouse

SCOPED BY FILE PATH, NOT BY `-k almanac_byte_diff` (2026-08-16). That
selector also matches tests/test_cbs_almanac_byte_diff.py, whose harness
honours the same environment variable -- so the old spelling rewrote the
CBS corpus alongside this one. Harmless when both were drifting for the
same reviewed reason, and silent blessing of 21 unreviewed files when
they were not. Regenerate one corpus at a time, deliberately.

The season/matchup_period anchor is pinned because the entry script
auto-detects MAX(matchup_period) when run without args; pinning keeps the
render anchor stable within the frozen input bundle.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.byte_diff_report import describe_drift

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


def _run_almanac_preview(preview_dir: Path, corpus_db: Path) -> None:
    proc = subprocess.run(
        [
            sys.executable, "output/generate_almanac_sheet.py",
            "--no-sheets",
            "--season-year", str(ANCHOR_SEASON),
            "--matchup-period", str(ANCHOR_MATCHUP_PERIOD),
            "--preview-dir", str(preview_dir),
            "--duckdb", str(corpus_db),
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



def test_almanac_tsv_matches_baseline(tmp_path, corpus_db):
    _run_almanac_preview(tmp_path, corpus_db)

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
            drifted.append((name, describe_drift(name, actual, expected)))

    if drifted:
        msg = ["ESPN drift vs tests/fixtures/almanac_v1_1_0/ (frozen fixture wk22-2026):"]
        for name, hint in drifted:
            msg.append(f"  {name}: {hint}")
        msg.append(
            "If intentional, regenerate with: "
            "REGENERATE_BASELINES=1 pytest tests/test_almanac_byte_diff.py "
            "-m warehouse"
        )
        pytest.fail("\n".join(msg))
