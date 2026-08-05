"""tools/demo.sh must never build or render against the real warehouse (MLB-198).

THE ACCEPTANCE HERE IS PROPAGATION, NOT REFUSAL, and the distinction is the
whole reason this file exists. The original T6 guard was verified by setting
DBT_DUCKDB_PATH to the real warehouse and watching the script refuse -- which
it did. What nobody checked was the ordinary case: leave the variable unset,
and where does the work actually go? `DUCKDB_PATH` was a local shell variable
the script never exported, while both dbt's profile and `output/db.py` read
the ENV var and default to the REAL warehouse when it is missing. So the
script printed the demo path and then seeded the fixture into, and rendered
from, the maintainer's real database.

A guard that only fires on the case you thought to test is theater. Every
test below therefore asserts on the RESOLVED value the script exports, not
merely on its exit code.

`DEMO_SELF_CHECK=1` runs the script's resolution and guards and then stops,
printing what it resolved -- so these cases cost milliseconds instead of a
twenty-minute build, and no test here writes a database or a TSV.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO_SH = os.path.join(REPO, "tools", "demo.sh")

REAL_WAREHOUSE = os.path.join(REPO, "data", "duckdb", "ESPN_FANTASY.duckdb")
DEMO_WAREHOUSE = os.path.join(REPO, "data", "duckdb", "demo", "ESPN_FANTASY.duckdb")
FIXTURE_CONFIG = "../demo/league_config"

BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="demo.sh needs bash")

TIMEOUT = 120


def run_demo(**env_overrides):
    """Run demo.sh in self-check mode. Returns (returncode, stdout, resolved).

    `resolved` maps the selfcheck keys to their values. PY_BIN is pinned to
    the interpreter running the tests so the canonicalizer is deterministic
    rather than whatever venv happens to be installed.
    """
    env = dict(os.environ)
    # A stray value in the developer's own shell would otherwise decide the
    # result of the very thing under test.
    for key in ("DBT_DUCKDB_PATH", "DBT_LEAGUE_CONFIG", "DEMO_ALLOW_REAL_WAREHOUSE",
                "DEMO_ALLOW_REAL_LEAGUE_CONFIG", "DEMO_ALLOW_UNSTAMPED"):
        env.pop(key, None)
    env["DEMO_SELF_CHECK"] = "1"
    env["PY_BIN"] = sys.executable
    for key, value in env_overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value

    proc = subprocess.run(
        [BASH, DEMO_SH], cwd=REPO, env=env, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT,
    )
    resolved = {}
    for line in proc.stdout.splitlines():
        marker = "[demo] selfcheck "
        if line.startswith(marker) and "=" in line:
            key, _, value = line[len(marker):].partition("=")
            resolved[key] = value.strip()
    return proc.returncode, proc.stdout + proc.stderr, resolved


def same_file(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


# --------------------------------------------------------------------------
# The case the original guard never covered.
# --------------------------------------------------------------------------

def test_unset_env_propagates_the_demo_path():
    """THE regression test. Env unset is the normal case and was the broken one."""
    code, out, resolved = run_demo()
    assert code == 0, f"self-check failed with the env unset:\n{out}"
    assert "DBT_DUCKDB_PATH" in resolved, (
        f"the script exported no warehouse path at all:\n{out}"
    )
    assert same_file(resolved["DBT_DUCKDB_PATH"], DEMO_WAREHOUSE), (
        f"with DBT_DUCKDB_PATH unset the demo resolved to "
        f"{resolved['DBT_DUCKDB_PATH']}, expected the demo warehouse at "
        f"{DEMO_WAREHOUSE}."
    )
    assert not same_file(resolved["DBT_DUCKDB_PATH"], REAL_WAREHOUSE), (
        "the demo resolved to the REAL warehouse -- this is the MLB-198 bug: "
        "the guard checked an unexported local variable while dbt and "
        "output/db.py read the env var and defaulted to the real database."
    )


def test_unset_env_pins_the_fixture_league_config():
    code, out, resolved = run_demo()
    assert code == 0, out
    assert resolved.get("DBT_LEAGUE_CONFIG") == FIXTURE_CONFIG, (
        f"league config resolved to {resolved.get('DBT_LEAGUE_CONFIG')!r}, "
        f"expected the fixture {FIXTURE_CONFIG!r}"
    )


def test_exported_path_is_absolute():
    """Children cd to the repo root, but an absolute path cannot be misread."""
    code, out, resolved = run_demo()
    assert code == 0, out
    assert os.path.isabs(resolved["DBT_DUCKDB_PATH"]), (
        f"exported {resolved['DBT_DUCKDB_PATH']!r}, which is relative -- its "
        f"meaning then depends on each child's working directory"
    )


# --------------------------------------------------------------------------
# Refusal, across every spelling of the same file.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("spelling", [
    "data/duckdb/ESPN_FANTASY.duckdb",              # plain relative
    "./data/duckdb/ESPN_FANTASY.duckdb",            # dot-prefixed
    "data/duckdb/demo/../ESPN_FANTASY.duckdb",      # via a parent hop
    "data/./duckdb/ESPN_FANTASY.duckdb",            # redundant separator
])
def test_refuses_every_relative_spelling_of_the_real_warehouse(spelling):
    """String equality was never enough: these all name one file."""
    code, out, resolved = run_demo(DBT_DUCKDB_PATH=spelling)
    assert code != 0, (
        f"demo.sh accepted {spelling!r}, which resolves to the real "
        f"warehouse. It exported {resolved.get('DBT_DUCKDB_PATH')!r}."
    )
    assert "refusing to run" in out


def test_refuses_the_absolute_real_warehouse():
    code, out, resolved = run_demo(DBT_DUCKDB_PATH=REAL_WAREHOUSE)
    assert code != 0, f"demo.sh accepted the absolute real warehouse path:\n{out}"
    assert "refusing to run" in out


def test_refuses_a_symlink_to_the_real_warehouse(tmp_path):
    """A link is a different name for the same bytes."""
    link = tmp_path / "looks_innocent.duckdb"
    try:
        os.symlink(REAL_WAREHOUSE, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"cannot create a symlink here: {exc}")
    code, out, resolved = run_demo(DBT_DUCKDB_PATH=str(link))
    assert code != 0, (
        f"demo.sh accepted a symlink to the real warehouse and exported "
        f"{resolved.get('DBT_DUCKDB_PATH')!r}"
    )
    assert "refusing to run" in out


def test_typed_override_is_honoured():
    """Refusal must stay overridable, or the maintainer routes around it."""
    code, out, resolved = run_demo(
        DBT_DUCKDB_PATH=REAL_WAREHOUSE, DEMO_ALLOW_REAL_WAREHOUSE="1")
    assert code == 0, f"the explicit override did not work:\n{out}"
    assert same_file(resolved["DBT_DUCKDB_PATH"], REAL_WAREHOUSE)


# --------------------------------------------------------------------------
# A demo path that is not the real warehouse still has to propagate.
# --------------------------------------------------------------------------

def test_relative_demo_path_propagates(tmp_path):
    code, out, resolved = run_demo(
        DBT_DUCKDB_PATH="data/duckdb/demo/ESPN_FANTASY.duckdb")
    assert code == 0, out
    assert same_file(resolved["DBT_DUCKDB_PATH"], DEMO_WAREHOUSE)


def test_absolute_elsewhere_path_propagates(tmp_path):
    target = tmp_path / "scratch" / "ESPN_FANTASY.duckdb"
    code, out, resolved = run_demo(DBT_DUCKDB_PATH=str(target))
    assert code == 0, out
    assert same_file(resolved["DBT_DUCKDB_PATH"], str(target)), (
        f"a caller-chosen warehouse must still be what propagates; got "
        f"{resolved['DBT_DUCKDB_PATH']!r}"
    )


# --------------------------------------------------------------------------
# The league config is the other half of the isolation.
# --------------------------------------------------------------------------

def test_refuses_an_ambient_league_config():
    """A shell left pointing at the real league must not silently win.

    Seeds decide what the demo's marts are built FROM; the old
    default-if-unset let an inherited value through without a word.
    """
    code, out, resolved = run_demo(DBT_LEAGUE_CONFIG="../dbt_league/league_config")
    assert code != 0, (
        f"demo.sh accepted an ambient league config and resolved "
        f"{resolved.get('DBT_LEAGUE_CONFIG')!r}"
    )
    assert "refusing to run" in out


def test_league_config_override_is_honoured():
    code, out, resolved = run_demo(
        DBT_LEAGUE_CONFIG="../dbt_league/league_config",
        DEMO_ALLOW_REAL_LEAGUE_CONFIG="1")
    assert code == 0, out
    assert resolved.get("DBT_LEAGUE_CONFIG") == "../dbt_league/league_config"


def test_fixture_value_passed_explicitly_is_accepted():
    """Naming the fixture is agreement, not an override."""
    code, out, resolved = run_demo(DBT_LEAGUE_CONFIG=FIXTURE_CONFIG)
    assert code == 0, out
    assert resolved.get("DBT_LEAGUE_CONFIG") == FIXTURE_CONFIG


# --------------------------------------------------------------------------
# Provenance. A path says where a database is, not what is in it.
# --------------------------------------------------------------------------

def _duckdb_python():
    """An interpreter that can import duckdb, or None."""
    for candidate in (r"C:\Users\kyled\.venvs\mlb10-duckdb\Scripts\python.exe",
                      sys.executable):
        if not os.path.exists(candidate):
            continue
        probe = subprocess.run([candidate, "-c", "import duckdb"],
                               capture_output=True, timeout=TIMEOUT)
        if probe.returncode == 0:
            return candidate
    return None


def _make_db(path, stamped):
    py = _duckdb_python()
    script = "import duckdb,sys; c=duckdb.connect(sys.argv[1]);"
    if stamped:
        script += (
            "c.execute('create schema if not exists DEMO_META');"
            "c.execute('create table DEMO_META.PROVENANCE"
            "(marker varchar, league_config varchar, stamped_at timestamp)');"
            "c.execute(\"insert into DEMO_META.PROVENANCE values"
            "('tools/demo.sh','../demo/league_config', now())\");"
        )
    else:
        script += "c.execute('create schema if not exists ANALYTICS');"
    script += "c.close()"
    subprocess.run([py, "-c", script, str(path)], check=True, timeout=TIMEOUT)
    return py


needs_duckdb = pytest.mark.skipif(
    _duckdb_python() is None, reason="no interpreter here can import duckdb")


@needs_duckdb
def test_refuses_an_existing_database_with_no_demo_marker(tmp_path):
    """The skip-the-build path is exactly where a stray database gets rendered.

    A file at the demo path is not evidence of demo provenance -- a copy of
    the real warehouse would satisfy every check the script had before this.
    """
    db = tmp_path / "ESPN_FANTASY.duckdb"
    py = _make_db(db, stamped=False)
    code, out, resolved = run_demo(DBT_DUCKDB_PATH=str(db), PY_BIN=py)
    assert code != 0, f"demo.sh trusted an unmarked database:\n{out}"
    assert "provenance marker" in out
    assert resolved.get("STAMP_STATE") in (None, "unstamped")


@needs_duckdb
def test_accepts_a_stamped_database(tmp_path):
    db = tmp_path / "ESPN_FANTASY.duckdb"
    py = _make_db(db, stamped=True)
    code, out, resolved = run_demo(DBT_DUCKDB_PATH=str(db), PY_BIN=py)
    assert code == 0, f"demo.sh rejected a properly stamped database:\n{out}"
    assert resolved.get("STAMP_STATE") == "stamped"


@needs_duckdb
def test_unstamped_override_is_honoured(tmp_path):
    db = tmp_path / "ESPN_FANTASY.duckdb"
    py = _make_db(db, stamped=False)
    code, out, resolved = run_demo(
        DBT_DUCKDB_PATH=str(db), PY_BIN=py, DEMO_ALLOW_UNSTAMPED="1")
    assert code == 0, out
    assert resolved.get("STAMP_STATE") == "unstamped"


@needs_duckdb
def test_the_real_warehouse_carries_no_demo_marker():
    """Belt to the path guard's braces: even reached under the typed
    override, the real warehouse is not stamped, so it is not mistakable
    for a demo build by anything that reads provenance."""
    py = _duckdb_python()
    if not os.path.exists(REAL_WAREHOUSE):
        pytest.skip("no real warehouse on this machine")
    probe = subprocess.run([py, "-c", """
import duckdb, sys
try:
    c = duckdb.connect(sys.argv[1], read_only=True)
    n = c.execute("select count(*) from DEMO_META.PROVENANCE").fetchone()[0]
except Exception:
    n = 0
sys.exit(0 if n else 1)
""", REAL_WAREHOUSE], capture_output=True, timeout=TIMEOUT)
    assert probe.returncode != 0, (
        "the REAL warehouse carries a demo provenance marker. Something "
        "stamped it, and the marker no longer distinguishes the two."
    )


# --------------------------------------------------------------------------
# Fail-closed on a broken interpreter: no canonical paths, no run.
# --------------------------------------------------------------------------

def test_refuses_when_paths_cannot_be_canonicalized():
    """If the check cannot be trusted, refuse -- do not fall back to strings.

    Reaching this needs an interpreter that EXISTS and is executable but
    cannot run the canonicalizer: a name that is merely wrong falls back to
    `python` on PATH by design, so it never gets here.
    """
    not_python = shutil.which("false")
    if not_python is None:
        pytest.skip("no executable-but-useless binary available to stand in")
    code, out, _ = run_demo(PY_BIN=not_python)
    assert code != 0, f"demo.sh ran without being able to canonicalize:\n{out}"
    assert "canonicalize" in out
