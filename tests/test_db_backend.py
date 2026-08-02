"""Backend selection and placeholder rewriting in output/db.py (MLB-10).

Pure by construction: nothing here opens a connection to either engine.
The DuckDB half of `query_snowflake` is exercised end-to-end by the
almanac byte-diff harness, which has a real warehouse to read; what
these cover is the logic that decides WHERE a query goes and how its
placeholders are spelled on the way.
"""

import subprocess
import sys
import textwrap

import pytest

import db


@pytest.fixture(autouse=True)
def _restore_backend():
    """Backend and dialect are process-global, so leaving either flipped
    would leak into whatever test ran next."""
    before = (db.backend(), db.dialect(), db._duck_path)
    yield
    db._BACKEND, db._duck_path = before[0], before[2]
    db.set_dialect(before[1])


# --- placeholder rewriting -------------------------------------------------

@pytest.mark.parametrize("sql,want", [
    # The ordinary case.
    ("select 1 where x = %s", "select 1 where x = ?"),
    ("select 1 where a = %s and b = %s", "select 1 where a = ? and b = ?"),
    # The case the naive str.replace gets wrong: a LIKE pattern whose text
    # happens to contain %s. Corrupting this produces a filter that silently
    # matches the wrong rows rather than an error.
    ("select 1 where n like '%save%' and x = %s",
     "select 1 where n like '%save%' and x = ?"),
    ("select 1 where n = '%s'", "select 1 where n = '%s'"),
    # Doubled quotes are the SQL escape for a quote, so the literal does not
    # end at the middle one.
    ("select 'it''s %s' , %s", "select 'it''s %s' , ?"),
    # Quoted identifiers are not string literals but are equally off-limits.
    ('select "odd%scol" from t where x = %s',
     'select "odd%scol" from t where x = ?'),
    # Comments, both kinds.
    ("-- %s stays\nselect %s", "-- %s stays\nselect ?"),
    ("/* %s stays */ select %s", "/* %s stays */ select ?"),
    # pyformat's escape for a literal percent sign.
    ("select 100 %% 7, %s", "select 100 % 7, ?"),
    # Nothing to do.
    ("select 1", "select 1"),
])
def test_to_qmark(sql, want):
    assert db._to_qmark(sql) == want


def test_to_qmark_tolerates_unterminated_literal():
    """A malformed statement must not hang or raise here -- it should reach
    the engine and fail there, with the engine's own message."""
    assert db._to_qmark("select 'oops %s") == "select 'oops %s"


# --- backend selection -----------------------------------------------------

def test_default_backend_is_snowflake():
    assert db.backend() == 'snowflake'


def test_use_duckdb_sets_backend_and_dialect():
    """One switch, not two. A DuckDB connection fed Snowflake spellings
    dies at the first LISTAGG, so the dialect follows the connection."""
    db.use_duckdb('some/where.duckdb')
    assert db.backend() == 'duckdb'
    assert db.dialect() == 'duckdb'
    assert db._duck_path == 'some/where.duckdb'


def test_use_duckdb_falls_back_to_env_then_default(monkeypatch):
    monkeypatch.setenv('DBT_DUCKDB_PATH', 'from/env.duckdb')
    db.use_duckdb()
    assert db._duck_path == 'from/env.duckdb'

    monkeypatch.delenv('DBT_DUCKDB_PATH')
    db.use_duckdb()
    assert db._duck_path == db.DEFAULT_DUCKDB_PATH


def test_init_honours_the_backend_env_var(monkeypatch):
    """The no-flag path. Scripts without a --duckdb flag -- and the golden
    byte-diff tests, which re-run the entry point in a subprocess with the
    environment inherited -- select the backend this way.

    init() is idempotent on _SNOWFLAKE_CONFIG, so it has to be un-warmed
    or the call under test is a no-op.
    """
    monkeypatch.setattr(db, '_SNOWFLAKE_CONFIG', None)
    monkeypatch.setenv('ALMANAC_DB_BACKEND', 'duckdb')
    db.init()
    assert db.backend() == 'duckdb'
    assert db.dialect() == 'duckdb'


def test_init_leaves_snowflake_alone_by_default(monkeypatch):
    """The live weekly run must not change behaviour because this exists."""
    monkeypatch.setattr(db, '_SNOWFLAKE_CONFIG', None)
    monkeypatch.delenv('ALMANAC_DB_BACKEND', raising=False)
    db.init()
    assert db.backend() == 'snowflake'
    assert db.dialect() == 'snowflake'


def test_missing_duckdb_file_names_the_path(tmp_path):
    """The BYO flow's most likely first error, so it should say what to do
    rather than surface a driver-level message."""
    db.use_duckdb(str(tmp_path / 'nope.duckdb'))
    with pytest.raises(FileNotFoundError, match='nope.duckdb'):
        db._get_duck_conn()


# --- the import contract that the BYO claim rides on -----------------------

def test_db_imports_without_the_snowflake_driver():
    """A clone that will only ever read DuckDB must not need the Snowflake
    connector installed. `import snowflake.connector` at module scope made
    it a hard dependency of the entire output layer; it is lazy now, and
    this is what keeps it that way.

    Run in a subprocess with the connector poisoned at import time, because
    the driver IS installed in this venv and cannot be unimported.
    """
    script = textwrap.dedent(
        """
        import sys
        class Blocker:
            def find_module(self, name, path=None):
                if name == 'snowflake' or name.startswith('snowflake.'):
                    raise ImportError('snowflake.connector is blocked')
                return None
        sys.meta_path.insert(0, Blocker())
        sys.path.insert(0, {output!r})
        sys.path.insert(0, {root!r})
        import db
        db.use_duckdb('x.duckdb')
        assert db.backend() == 'duckdb'
        assert 'snowflake.connector' not in sys.modules
        print('OK')
        """
    ).format(
        output=str(db.Path(db.__file__).resolve().parent),
        root=str(db.Path(db.__file__).resolve().parents[1]),
    )
    proc = subprocess.run([sys.executable, '-c', script],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert 'OK' in proc.stdout
