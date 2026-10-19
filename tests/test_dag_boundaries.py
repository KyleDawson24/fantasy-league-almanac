"""The DAG layer contract, enforced on every run (MLB-158 Phase C).

Not warehouse-marked on purpose: it runs in CI's unit-tests job. It parses the
dbt project against the placeholder CI profile (.github/ci/profiles.yml: parse
never opens a connection), then checks the manifest it wrote with
tools/dag_boundaries.py -- the same functions the CLI and the CI dbt-parse
job use. The rule, the register and the reasons live in
docs/dag-boundaries.md and dbt_league/dag_exceptions.yml.
"""
import os
import shutil
import subprocess
import sys
import time
import warnings
from pathlib import Path

import pytest

from tools import dag_boundaries as db

REPO = Path(__file__).resolve().parents[1]
REGISTER = REPO / 'dbt_league' / 'dag_exceptions.yml'
README = REPO / 'README.md'
DOC = REPO / 'docs' / 'dag-boundaries.md'
TARGET = 'target/boundaries'   # relative to the dbt project, so it lands in gitignored dbt_league/target/


def _dbt():
    """The dbt next to this interpreter (the venv locally, setup-python's bin
    in CI), else the one on PATH."""
    here = Path(sys.executable).with_name('dbt.exe' if os.name == 'nt' else 'dbt')
    return str(here) if here.exists() else shutil.which('dbt')


@pytest.fixture(scope='module')
def manifest():
    dbt = _dbt()
    assert dbt, 'dbt is not installed in this environment'
    started = time.monotonic()
    proc = subprocess.run(
        [dbt, 'parse', '--project-dir', 'dbt_league', '--profiles-dir', '.github/ci',
         '--target-path', TARGET, '--no-partial-parse'],
        cwd=REPO, capture_output=True, text=True, timeout=600)
    elapsed = time.monotonic() - started
    assert proc.returncode == 0, f'dbt parse failed ({proc.returncode}):\n{proc.stdout[-4000:]}\n{proc.stderr[-2000:]}'
    print(f'dbt parse for the boundary test took {elapsed:.1f}s')
    return db.load_manifest(REPO / 'dbt_league' / TARGET / 'manifest.json')


def test_every_model_has_a_layer(manifest):
    assert db.missing_layers(manifest) == [], 'models without a valid meta.dag_layer'


def test_no_backward_edges(manifest):
    assert db.backward_edges(manifest) == [], 'a model reads a later layer'


def test_reporting_to_staging_edges_equal_the_register(manifest):
    entries = db.load_register(REGISTER)
    assert db.register_problems(entries, manifest) == []
    unregistered, retired = db.compare_register(manifest, entries)
    assert unregistered == [], 'reporting reads staging with no row in dbt_league/dag_exceptions.yml'
    assert retired == [], 'register rows whose edge is gone: retire them'


def test_no_gap_has_expired(manifest):
    expired, active = db.gaps(manifest, db.load_register(REGISTER))
    for reader, dep, target in active:
        warnings.warn(f'exception until {target}: {reader} -> {dep}', UserWarning)
    assert expired == [], 'a gap exception has expired: its model exists, so the reader moves onto it'


def test_doc_register_matches_the_yaml():
    assert db.compare_doc(db.doc_register(DOC), db.load_register(REGISTER)) == []


def test_readme_numbers_match_the_manifest(manifest):
    assert db.compare_readme(manifest, db.readme_numbers(README)) == []
