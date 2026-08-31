"""Byte-diff regression test for the SEASON-POINTS almanac (MLB-263).

The sibling harness, tests/test_almanac_byte_diff.py, pins the HEAD-TO-HEAD
book against the real league on Snowflake. This one pins the OTHER book --
the season-long points workbook a points-format league receives -- and it
had no baseline at all until now. That gap mattered the moment MLB-249 X-1
went near `espn_points_data.window_lineup`: the Team-of-the-Month board is
rendered by the points book, so a change there moved output that nothing
was watching.

WHY A LOCAL DuckDB RATHER THAN THE WAREHOUSE. There is no season-points
league in Snowflake to render -- `espn-main` there is head-to-head. The
only points-format dataset that exists is the first-year stranger
rehearsal captured 2026-08-13 (the MLB-243 league: drafted July 31, never
completed a matchup, so `max(matchup_period)` is 1). It lives in a local
DuckDB file, which also makes this corpus FROZEN: unlike the H2H corpus,
weekly extracts cannot move it, so a diff here is always code.

THE DATABASE IS NOT THE SHIPPED SNAPSHOT. The 1.9.0-era file the rehearsal
left behind was built by 2026-08-13 model code, and the chain has moved
since: `stg_draft_settings` and `stg_transaction_coverage` did not exist
yet, and `mart_draft_board` had no `draft_type` column. Rendering current
code against it fails outright. So CORPUS_DB is a COPY, rebuilt with
`dbt run` at current model code (97/97) -- the shipped snapshot under
scratchpad/type5-local-rehearsal-20260813/ is left untouched.

Rebuilding it needed one seam that is worth stating plainly:
RAW.TRANSACTION_COVERAGE is created EMPTY. That is not a stand-in for
missing data -- it is the state stg_transaction_coverage documents as
normal ("coverage rows only exist for extracts run after this model
shipped, so a league captured earlier has none"), and the model's own
fallback then treats staged transaction rows as proof the board was read.
An 08-13 capture genuinely has no coverage verdict.

==========================================================================
ANCHOR STATE -- the WAREHOUSE this corpus was minted against, not just the
matchup period. Recorded because "2026 MP1" alone does not identify a
render: the same anchor over different data is a different corpus.
==========================================================================

  minted            2026-08-31, at repo HEAD b3ee31c (pre-MLB-264/X-1)
  database          copy of scratchpad/type5-local-rehearsal-20260813/
                    fantasy-league-almanac-1.9.0/data/duckdb/ESPN_FANTASY.duckdb
                    -> scratchpad/points_corpus/ESPN_FANTASY.duckdb
  rebuilt           dbt seed + dbt run at current model code, 97/97 PASS
                    (--target duckdb --profiles-dir dbt_league/profiles)
  RAW adjustment    RAW.TRANSACTION_COVERAGE created EMPTY (see above)
  league            espn-main, format 'points' (dim_league_format)
  season            2026; matchup_period max 1; scoring_period max 142
  teams             14 tabs + Home / Advanced-Standings / Draft-Recap /
                    Records = 14 TSVs
  frozen            YES -- this database is a snapshot, so weekly extracts
                    cannot move this corpus. A diff here is always code.

Marked `warehouse` -- skipped by the default `pytest tests/` suite, like
both sibling corpora. Run it deliberately:
    pytest tests/test_points_almanac_byte_diff.py -m warehouse

To regenerate after an intentional output change:
    REGENERATE_BASELINES=1 pytest tests/test_points_almanac_byte_diff.py -m warehouse

SCOPE BY FILE PATH, NOT BY `-k byte_diff`. That selector matches all three
byte-diff harnesses, and every one of them honours REGENERATE_BASELINES --
so the loose spelling silently rewrites corpora nobody reviewed. This is
the same trap tests/test_almanac_byte_diff.py records having sprung on
2026-08-16. Regenerate one corpus at a time, deliberately.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.warehouse

REPO = Path(__file__).resolve().parents[1]
FIX_DIR = REPO / "tests" / "fixtures" / "points_almanac_v2_1"

# The rebuilt rehearsal database. Overridable so the corpus can be pointed
# at a relocated copy without editing the test; the default is where the
# MLB-263 mint left it (under the gitignored scratchpad/, like the data it
# came from -- it is real league output and never belongs in the repo).
CORPUS_DB = Path(
    os.getenv("POINTS_CORPUS_DB", REPO / "scratchpad" / "points_corpus" / "ESPN_FANTASY.duckdb")
)

# Anchor. The rehearsal league never completed a matchup, so the points
# book's own anchor is matchup_period 1 -- not an arbitrary pin like the
# H2H corpus's Week 7, but the only period this league has. Season totals
# run through scoring_period 142.
ANCHOR_SEASON = 2026
ANCHOR_MATCHUP_PERIOD = 1

if not FIX_DIR.exists() or not CORPUS_DB.exists():
    # Same doctrine as the sibling corpora (MLB-95): rendered from a real
    # league, so it lives locally and on the private remote only. A public
    # clone has neither the fixtures nor the database and skips.
    pytestmark = [
        pytest.mark.warehouse,
        pytest.mark.skip(reason="private points-almanac corpus not present"),
    ]


def _run_points_preview(preview_dir: Path) -> None:
    proc = subprocess.run(
        [
            sys.executable, "output/generate_almanac_sheet.py",
            "--no-sheets",
            "--season-year", str(ANCHOR_SEASON),
            "--matchup-period", str(ANCHOR_MATCHUP_PERIOD),
            "--preview-dir", str(preview_dir),
            "--duckdb", str(CORPUS_DB),
            "--league", "espn-main",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        # Same render-time stamp suppression the H2H corpus needs
        # (MLB-141): Home's "Updated ..." cell is wall-clock state and
        # would defeat the byte comparison on every run.
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


def test_points_almanac_tsv_matches_baseline(tmp_path):
    _run_points_preview(tmp_path)

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

    assert not drifted, "points almanac output drifted from baseline:\n" + "\n".join(
        f"  {name}: {hint}" for name, hint in drifted
    )
