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
    pytest tests/test_cbs_almanac_byte_diff.py -m warehouse

ANCHOR STATE (MLB-295)
  minted: 2026-09-18 at repo HEAD 15a1afd
  freeze: wk22-2026; frozen = YES
  input archive SHA256:
    8d214251df61302907ee2bc7e7deb4c083cd00d8e377f802e026a839e9adceb3
  horizon: CBS all history through period 24 / 2026-09-06; ESPN 2025
    plus 2026 MP1-22; MLB through 2026-09-06
  render anchor: Team of the Month window 2026-06-01 through 2026-06-30
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

Regenerate after a reviewed, intentional output change:
    REGENERATE_BASELINES=1 pytest tests/test_cbs_almanac_byte_diff.py -m warehouse
"""

import os
import shutil
from datetime import date
from pathlib import Path

import pytest

from tests.byte_diff_report import describe_drift

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
def cbs_duckdb(corpus_db, monkeypatch):
    """Restore all backend globals, including any previous cached connection."""
    import db
    for name in ('_BACKEND', '_duck_path', '_dialect', '_duck_conn'):
        monkeypatch.setattr(db, name, getattr(db, name))
    db._duck_conn = None
    db.use_duckdb(str(corpus_db))
    try:
        yield
    finally:
        if db._duck_conn is not None:
            db._duck_conn.close()


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
    # The Home tab's A3 "Updated ..." stamp is render-time state, so the
    # corpus is only stable when it renders blank (MLB-141). This render
    # is in-process, so the switch is monkeypatched into the environment
    # rather than passed as a subprocess env like the ESPN harness does.
    monkeypatch.setenv("SUPPRESS_UPDATED_STAMP", "1")

    tabs, _, _ = cbs_almanac_sheets.build_all_tabs()
    preview_tabs = [(title, rows) for title, rows, _ in tabs]
    generate_almanac_sheet._write_preview_dir(preview_tabs, str(out_dir))


def _list_tsv(d: Path) -> set:
    return {p.name for p in d.iterdir() if p.suffix == ".tsv"}



def test_cbs_almanac_tsv_matches_baseline(tmp_path, monkeypatch, cbs_duckdb, cbs_league):
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
            drifted.append((name, describe_drift(name, actual, expected)))

    if drifted:
        msg = ["CBS drift vs tests/fixtures/cbs_almanac/ (frozen fixture wk22-2026):"]
        for name, hint in drifted:
            msg.append(f"  {name}: {hint}")
        msg.append(
            "If intentional, regenerate with: REGENERATE_BASELINES=1 "
            "pytest tests/test_cbs_almanac_byte_diff.py -m warehouse"
        )
        pytest.fail("\n".join(msg))
