"""The live consent probe is sealed off from league data (MLB-209).

The probe exists so the consent measurement does not have to be taken by
publishing a real league. That is only true if it genuinely cannot reach
league data -- and "the author did not import db" is a promise, not a
guarantee, because a promise survives exactly until someone adds a
convenient import three months from now.

So the seal is tested two ways: the module's own import list is read from
its AST, and the module is then imported in a SUBPROCESS with a clean
interpreter, which catches anything pulled in transitively. Either check
alone would miss the case the other catches.

Nothing here reaches Google: the probe's client and publisher are stubs,
and the subprocess only imports.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

import sheets_auth
import sheets_workbook


_REPO_ROOT = Path(__file__).resolve().parents[1]
_PROBE_PATH = _REPO_ROOT / 'tools' / 'check_google_consent.py'

sys.path.insert(0, str(_REPO_ROOT / 'tools'))
import check_google_consent as probe          # noqa: E402


# Anything that reads league data, resolves a configured destination, or
# opens a warehouse. `db` covers Snowflake and DuckDB both.
FORBIDDEN_MODULES = (
    'db', 'sheets_target', 'extract', 'records', 'almanac_data',
    'almanac_sheets', 'almanac_write', 'cbs_almanac_sheets', 'sheets_writer',
    'generate_almanac_sheet', 'generate_records_report', 'stat_catalog',
    'slot_catalog', 'duckdb', 'snowflake',
)


# ---------------------------------------------------------------------------
# The seal
# ---------------------------------------------------------------------------

def _imported_names(path):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split('.')[0])
    return names


def test_the_probe_imports_nothing_that_can_reach_league_data():
    imported = _imported_names(_PROBE_PATH)
    assert not imported & set(FORBIDDEN_MODULES), (
        f'the probe imports {sorted(imported & set(FORBIDDEN_MODULES))}'
    )


def test_the_probe_imports_only_the_two_production_services():
    """Its whole point is exercising the shipped seam and nothing else."""
    project_imports = _imported_names(_PROBE_PATH) - {
        'argparse', 'sys', 'datetime', 'pathlib',
    }
    assert project_imports == {'sheets_auth', 'sheets_workbook'}


def test_importing_the_probe_pulls_in_no_league_module_transitively():
    """The AST check only sees this file. This one sees everything the
    import actually drags along."""
    script = (
        'import sys;'
        f'sys.path.insert(0, {str(_REPO_ROOT / "tools")!r});'
        'import check_google_consent;'
        'print("\\n".join(sorted(sys.modules)))'
    )
    proc = subprocess.run(
        [sys.executable, '-c', script],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr

    loaded = set(proc.stdout.split())
    offenders = loaded & set(FORBIDDEN_MODULES)
    assert not offenders, f'importing the probe loaded {sorted(offenders)}'


@pytest.fixture
def google_is_off_limits(monkeypatch):
    """Any attempt to authorize or publish is a test failure, not a
    browser window. Without this, a broken --run-live guard would make
    the suite itself open a consent screen."""
    def _explode(*a, **kw):
        raise AssertionError('a dry run reached out to Google')

    monkeypatch.setattr(sheets_auth, 'authorized_client', _explode)
    monkeypatch.setattr(sheets_workbook, 'publish_workbook', _explode)


@pytest.mark.parametrize('argv', [[], ['--title', 'x'], ['--force-new']])
def test_no_invocation_without_run_live_reaches_google(
        argv, google_is_off_limits):
    """--run-live is the only thing that makes this program act. Every
    other combination, including --help's neighbours, is inert."""
    assert probe.main(argv) == 0


def test_a_run_without_run_live_says_what_it_would_have_done(
        capsys, google_is_off_limits):
    assert probe.main([]) == 0
    out = capsys.readouterr().out
    assert 'nothing was done' in out
    assert 'drive.file' in out


# ---------------------------------------------------------------------------
# The content it would write
# ---------------------------------------------------------------------------

def test_the_rendered_content_is_a_fixed_synthetic_shape():
    rows = probe.consent_check_rows('2026-08-10T12:00:00+00:00')

    assert rows == [
        ['Fantasy League Almanac consent check'],
        ['Created (UTC)', '2026-08-10T12:00:00+00:00'],
        ['This is a scratch file created by a consent check.'],
        ['It contains no fantasy league data of any kind.'],
        ['Safe to delete.'],
    ]


def test_the_only_varying_cell_is_the_timestamp():
    a = probe.consent_check_rows('2026-08-10T12:00:00+00:00')
    b = probe.consent_check_rows('2027-01-01T00:00:00+00:00')

    differing = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    assert differing == [1]


def test_the_default_title_names_no_league_owner_or_season():
    title = probe.CONSENT_CHECK_TITLE
    assert title == 'Fantasy League Almanac consent check'
    assert not any(ch.isdigit() for ch in title)


# ---------------------------------------------------------------------------
# It uses the production seam
# ---------------------------------------------------------------------------

class _FakeWorksheet:
    def __init__(self):
        self.updates = []

    def update(self, rows, cell):
        self.updates.append((rows, cell))


class _FakeSpreadsheet:
    def __init__(self):
        self.worksheet = _FakeWorksheet()

    def get_worksheet(self, index):
        return self.worksheet


class _FakeClient:
    def __init__(self):
        self.spreadsheet = _FakeSpreadsheet()
        self.opened = []

    def open_by_key(self, spreadsheet_id):
        self.opened.append(spreadsheet_id)
        return self.spreadsheet


@pytest.fixture
def stub_publish():
    seen = {}

    def _publish(client, title, render, resume=True):
        seen.update(client=client, title=title, resume=resume)
        render('probe-sheet-id')
        return sheets_workbook.PublishResult(
            spreadsheet_id='probe-sheet-id', url='PROBE-URL', title=title,
            created=True, rendered=True, shared=True)

    seen['publish'] = _publish
    return seen


def test_the_probe_authorizes_through_the_public_profile(
        stub_publish, monkeypatch):
    asked = []
    monkeypatch.setattr(
        sheets_auth, 'authorized_client',
        lambda profile: asked.append(profile) or _FakeClient())

    probe.run_probe(publish=stub_publish['publish'])

    assert asked == [sheets_auth.PUBLIC]


def test_the_probe_writes_only_its_fixed_rows(stub_publish):
    client = _FakeClient()
    probe.run_probe(client=client, publish=stub_publish['publish'],
                    created_utc='2026-08-10T12:00:00+00:00')

    assert client.opened == ['probe-sheet-id']
    rows, cell = client.spreadsheet.worksheet.updates[0]
    assert cell == 'A1'
    assert rows == probe.consent_check_rows('2026-08-10T12:00:00+00:00')


def test_the_probe_reports_through_the_shared_success_rule(
        stub_publish, capsys):
    probe.run_probe(client=_FakeClient(), publish=stub_publish['publish'])

    out = capsys.readouterr().out
    assert sheets_workbook.SHARE_READY_LINE.format(url='PROBE-URL') in out


def test_the_probe_prints_no_url_when_sharing_was_not_verified(capsys):
    def _unverified(client, title, render, resume=True):
        render('probe-sheet-id')
        return sheets_workbook.PublishResult(
            spreadsheet_id='probe-sheet-id', url='PROBE-URL', title=title,
            created=True, rendered=True, shared=False,
            share_error='sharing could not be verified: no anyone permission',
            recovery=sheets_workbook.VERIFY_RECOVERY_MESSAGE)

    result = probe.run_probe(client=_FakeClient(), publish=_unverified)

    out = capsys.readouterr().out
    assert '-- share-ready.' not in out
    assert 'NOT share-ready' in out
    assert result.is_share_ready is False


def test_an_unverified_probe_run_exits_non_zero(monkeypatch):
    """A measurement harness has to be able to tell."""
    monkeypatch.setattr(
        sheets_auth, 'authorized_client', lambda profile: _FakeClient())
    monkeypatch.setattr(
        sheets_workbook, 'publish_workbook',
        lambda client, title, render, resume=True: (
            render('probe-sheet-id') or sheets_workbook.PublishResult(
                spreadsheet_id='probe-sheet-id', url='U', title=title,
                created=True, rendered=True, shared=False)))

    assert probe.main(['--run-live']) == 1
