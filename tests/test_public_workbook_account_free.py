"""The documented stranger command, run with no Google account setup
(MLB-209).

WHAT THIS FILE IS FOR. Every other test around this path proves one
seam. This one drives `generate_almanac_sheet.main()` from `sys.argv`,
exactly as QUICKSTART tells a stranger to, with **no
`GOOGLE_PUBLIC_OAUTH_CLIENT_PATH` set and no client JSON anywhere on
disk** -- and proves the whole thing still reaches a share-ready
workbook. That claim is the product outcome of the ticket, and it is the
one that cannot be checked by looking at any single function.

The pieces are all real: the argument parser, the flag validation, the
ESPN render dispatch, the public profile, the resolver, `publish_workbook`
with its create -> render -> share -> READ-BACK sequence, and the single
`is_share_ready` gate on the final line. Substituted, and only at the
edges where the network would be:

  - the OAuth flow (`InstalledAppFlow`), which would open a browser;
  - `gspread.authorize`, which would mint a real session;
  - the almanac data/render layer, which would need a warehouse.

Nothing here reads Kyle's DuckDB, league config, standing workbooks, or
external OAuth file, and no configured dev/prod sheet id is resolvable --
an attempt to resolve one is a hard failure, not a skip.
"""
from __future__ import annotations

import dataclasses
import json

import pytest
import requests

import generate_almanac_sheet as gas
import public_oauth_client
import sheets_auth
import sheets_workbook


DRIVE_FILE = sheets_auth.DRIVE_FILE_SCOPE

# The SHIPPED identity: client id and PKCE, no secret (Google marks
# client_secret optional for installed apps, code_verifier required).
SYNTHETIC_CLIENT = {
    'installed': {
        'client_id': '000000000000-synthetic.apps.googleusercontent.com',
        'client_secret': 'GOCSPX-' + ('SYNTHETICbundled' + '0' * 28)[:28],
        'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
        'token_uri': 'https://oauth2.googleapis.com/token',
    },
}

# A maintainer's OWN client, which may legitimately carry a secret.
SYNTHETIC_BYO_CLIENT = {
    'installed': {
        **SYNTHETIC_CLIENT['installed'],
        'client_secret': 'SYNTHETIC-not-a-real-secret',
    },
}

# What Drive returns for a correctly shared, app-created workbook.
LINK_VIEWER_PERMISSIONS = [
    {'id': 'owner-1', 'type': 'user', 'role': 'owner'},
    {'id': 'anyone-1', 'type': 'anyone', 'role': 'reader',
     'allowFileDiscovery': False},
]


# ---------------------------------------------------------------------------
# Fakes at the network edge
# ---------------------------------------------------------------------------

class _FakeCreds:
    def __init__(self, scopes):
        self.scopes = list(scopes)
        self.granted_scopes = list(scopes)
        self.valid = True
        self.expired = False
        self.refresh_token = 'synthetic'

    def to_json(self):
        return json.dumps({'token': 'synthetic', 'scopes': self.scopes})


class _FakeSpreadsheet:
    def __init__(self, spreadsheet_id, title):
        self.id = spreadsheet_id
        self.title = title
        self.url = f'https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit'


def _permission_response(permissions):
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps({'permissions': permissions}).encode('utf-8')
    return response


class _FakeHTTPClient:
    """gspread's authorized session. The sharing POST and the read-back
    GET both come through here, so the real Drive v3 wire shape is
    exercised rather than a convenience wrapper."""

    def __init__(self, owner):
        self._owner = owner

    def request(self, method, url, params=None, json=None, **kwargs):
        self._owner.events.append((method, url.rsplit('/', 2)[-1]))
        if method == 'post':
            self._owner.stored = list(LINK_VIEWER_PERMISSIONS)
            return _permission_response([])
        return _permission_response(self._owner.stored)


class _FakeGspreadClient:
    def __init__(self, events):
        self.events = events
        self.stored = []
        self.http_client = _FakeHTTPClient(self)

    def create(self, title, folder_id=None):
        self.events.append(('create', title))
        return _FakeSpreadsheet('app-created-id', title)


# ---------------------------------------------------------------------------
# The stranger's environment
# ---------------------------------------------------------------------------

@pytest.fixture
def events():
    """One ordered log across print, consent, create, render and share.

    Ordering is a real requirement here -- the disclosure has to come
    before consent, and sharing has to come after the render -- and
    reading it off a single list beats inferring it from three sources.
    """
    return []


def _tee_prints(monkeypatch, module, events):
    """Record what a module prints, in order, without losing the output.

    `monkeypatch.setattr(module, 'print', ...)` puts a name in the
    module's own globals, which Python resolves before the builtin. It is
    the least invasive way to get ordering across modules that each call
    the real `print`.
    """
    def _print(*args, **kwargs):
        events.append(('print', ' '.join(str(a) for a in args)))
    monkeypatch.setattr(module, 'print', _print, raising=False)


@pytest.fixture
def no_google_setup(monkeypatch, tmp_path, events):
    """A machine that has never been told anything about Google.

    Both client env vars deleted, both token caches redirected into a
    temp directory, and the ledger with them -- so nothing in this test
    can read or write a real credential, and the maintainer's cache
    cannot be touched even by accident.
    """
    monkeypatch.delenv('GOOGLE_PUBLIC_OAUTH_CLIENT_PATH', raising=False)
    monkeypatch.delenv('GOOGLE_OAUTH_CLIENT_PATH', raising=False)

    monkeypatch.setattr(
        sheets_auth, 'PUBLIC',
        dataclasses.replace(sheets_auth.PUBLIC,
                            token_path=tmp_path / 'public-token.json'))
    monkeypatch.setattr(sheets_workbook, 'LEDGER_PATH', tmp_path / 'ledger.json')
    monkeypatch.setattr(public_oauth_client, 'BUNDLED_PUBLIC_CLIENT',
                        SYNTHETIC_CLIENT)

    # Any read of the maintainer's cached grant is a failure, not a
    # tolerated side effect.
    read_paths = []

    def _load(path, scopes):
        read_paths.append(str(path))
        raise AssertionError(f'a cached token was read: {path}')

    monkeypatch.setattr(sheets_auth.Credentials, 'from_authorized_user_file',
                        staticmethod(_load))

    # Record which profiles ever ask for a client file on disk.
    real_client_config_path = sheets_auth.client_config_path
    asked = []

    def _client_config_path(profile):
        asked.append(profile.name)
        return real_client_config_path(profile)

    monkeypatch.setattr(sheets_auth, 'client_config_path', _client_config_path)

    _tee_prints(monkeypatch, gas, events)
    _tee_prints(monkeypatch, sheets_workbook, events)

    return {'token_reads': read_paths, 'client_paths_asked': asked,
            'tmp': tmp_path}


@pytest.fixture
def fake_google(monkeypatch, events):
    """The browser and the Google session, replaced at the last moment
    before either would exist."""
    client = _FakeGspreadClient(events)

    class _Flow:
        @classmethod
        def from_client_config(cls, config, scopes):
            events.append(('consent-from-config', tuple(scopes)))
            return cls()

        @classmethod
        def from_client_secrets_file(cls, path, scopes):
            events.append(('consent-from-file', tuple(scopes)))
            return cls()

        def run_local_server(self, port=0):
            return _FakeCreds([DRIVE_FILE])

    class _StubGspread:
        @staticmethod
        def authorize(creds):
            events.append(('authorize', tuple(creds.scopes)))
            return client

    monkeypatch.setattr(sheets_auth, 'InstalledAppFlow', _Flow)
    monkeypatch.setattr(sheets_auth, 'BundledInstalledAppFlow', _Flow)
    monkeypatch.setattr(sheets_auth, 'gspread', _StubGspread)
    return client


@pytest.fixture
def no_warehouse(monkeypatch, events):
    """The almanac data layer, stubbed wholesale.

    Swept by NAME rather than listed one by one on purpose. This file is
    about the CLI seam, and a hand-written list of twenty builders would
    break every time one of them gained an argument -- which would train
    people to weaken the test instead of fixing it. The three that have a
    load-bearing SHAPE are set explicitly afterwards.
    """
    import almanac_data
    import almanac_sheets

    for name in dir(almanac_sheets):
        if name.startswith(('get_', 'build_')) and callable(
                getattr(almanac_sheets, name)):
            monkeypatch.setattr(almanac_sheets, name,
                                lambda *a, **kw: [], raising=False)

    monkeypatch.setattr(almanac_sheets, 'get_latest_matchup_period',
                        lambda: (2026, 7))
    monkeypatch.setattr(almanac_sheets, 'get_home_tab_data',
                        lambda *a, **kw: {})
    monkeypatch.setattr(almanac_sheets, 'get_season_scoring_periods',
                        lambda *a, **kw: [])
    monkeypatch.setattr(almanac_data, 'team_best_seasons_fn',
                        lambda *a, **kw: (lambda *x, **y: []))

    written = []

    def _write_almanac(spreadsheet_id, **kwargs):
        events.append(('render', spreadsheet_id))
        written.append((spreadsheet_id, kwargs))

    monkeypatch.setattr(almanac_sheets, 'write_almanac', _write_almanac)

    monkeypatch.setattr(gas.cbs_almanac_sheets, 'is_points_league',
                        lambda: False)
    monkeypatch.setattr(gas.db, 'use_duckdb', lambda path=None: None)
    monkeypatch.setattr(gas.db, 'set_league', lambda key=None: None)

    # Resolving a configured workbook id on this path is the failure the
    # whole "creates its own destination" design exists to prevent.
    def _explode(*a, **kw):
        raise AssertionError(
            'the stranger path resolved a configured dev/prod Sheets target')

    monkeypatch.setattr(gas.sheets_target, 'resolve_sheets_target', _explode)
    return written


def _run(monkeypatch, *argv):
    monkeypatch.setattr('sys.argv', ['generate_almanac_sheet.py', *argv])
    return gas.main()


def _index(events, predicate):
    for i, event in enumerate(events):
        if predicate(event):
            return i
    return -1


# ---------------------------------------------------------------------------
# The documented command, on a machine with no Google setup
# ---------------------------------------------------------------------------

def test_the_documented_command_runs_with_no_oauth_client_anywhere(
        monkeypatch, events, no_google_setup, fake_google, no_warehouse):
    """THE PRODUCT OUTCOME. This is the exact command QUICKSTART gives,
    on a machine with no Google Cloud project, no downloaded client, and
    no env var -- and it ends on the share-ready line."""
    _run(monkeypatch, '--duckdb', '--new-public-workbook')

    assert ('consent-from-config', (DRIVE_FILE,)) in events, (
        'consent did not run as the identity this build ships'
    )
    assert sheets_workbook.SHARE_READY_LINE.format(
        url='https://docs.google.com/spreadsheets/d/app-created-id/edit'
    ) in [text for tag, text in events if tag == 'print']


def test_it_asks_google_for_drive_file_and_nothing_else(
        monkeypatch, events, no_google_setup, fake_google, no_warehouse):
    _run(monkeypatch, '--duckdb', '--new-public-workbook')

    consents = [e for e in events if e[0].startswith('consent-')]
    assert consents == [('consent-from-config', (DRIVE_FILE,))]
    assert ('authorize', (DRIVE_FILE,)) in events


def test_no_configured_workbook_id_is_resolved(
        monkeypatch, events, no_google_setup, fake_google, no_warehouse):
    """`resolve_sheets_target` is a tripwire in this fixture, so reaching
    it fails the run. Asserted separately anyway, because a future edit
    could catch the AssertionError."""
    _run(monkeypatch, '--duckdb', '--new-public-workbook')

    assert ('create', 'Fantasy League Almanac') in events


def test_no_maintainer_token_or_client_path_is_read(
        monkeypatch, events, no_google_setup, fake_google, no_warehouse):
    """The maintainer's `spreadsheets` grant opens the standing dev/prod
    books. The published path must not be able to see it, ask for it, or
    refresh it."""
    _run(monkeypatch, '--duckdb', '--new-public-workbook')

    assert no_google_setup['token_reads'] == []
    assert 'maintainer' not in no_google_setup['client_paths_asked']
    assert not (sheets_auth.MAINTAINER.token_path.name
                in {p.name for p in no_google_setup['tmp'].iterdir()})


def test_the_render_gets_the_app_created_id_and_the_same_client(
        monkeypatch, events, no_google_setup, fake_google, no_warehouse):
    """Under `drive.file` no other client may open the file, so the
    client that created it has to be the one that writes into it."""
    _run(monkeypatch, '--duckdb', '--new-public-workbook')

    assert len(no_warehouse) == 1
    spreadsheet_id, kwargs = no_warehouse[0]
    assert spreadsheet_id == 'app-created-id'
    assert kwargs['client'] is fake_google
    assert kwargs['season_year'] == 2026
    assert kwargs['matchup_period'] == 7


# ---------------------------------------------------------------------------
# Order
# ---------------------------------------------------------------------------

def test_the_disclosure_comes_before_consent_and_before_any_workbook(
        monkeypatch, events, no_google_setup, fake_google, no_warehouse):
    """A disclosure printed after the browser opened is not a disclosure.
    Both halves -- what is being requested, and that the result will be
    link-readable -- have to land while the user can still stop."""
    _run(monkeypatch, '--duckdb', '--new-public-workbook')

    scope_line = _index(events, lambda e: e[0] == 'print'
                        and 'drive.file' in e[1])
    sharing_line = _index(events, lambda e: e[0] == 'print'
                          and 'anyone-with-the-link VIEWER' in e[1])
    consent = _index(events, lambda e: e[0].startswith('consent-'))
    create = _index(events, lambda e: e[0] == 'create')

    assert 0 <= scope_line < consent
    assert 0 <= sharing_line < consent
    assert consent < create


def test_the_disclosure_says_it_cannot_enumerate_the_drive(
        monkeypatch, events, no_google_setup, fake_google, no_warehouse):
    _run(monkeypatch, '--duckdb', '--new-public-workbook')

    printed = ' '.join(text for tag, text in events if tag == 'print')
    assert 'cannot list, open, or read anything else' in printed
    assert 'nothing already in your Drive changes' in printed


def test_sharing_happens_last_and_is_read_back_before_the_success_line(
        monkeypatch, events, no_google_setup, fake_google, no_warehouse):
    """The order is load-bearing: a render that dies must never leave a
    publicly linked half-written book, and `share-ready` is only earned
    after the permission has been read back."""
    _run(monkeypatch, '--duckdb', '--new-public-workbook')

    create = _index(events, lambda e: e[0] == 'create')
    render = _index(events, lambda e: e[0] == 'render')
    share = _index(events, lambda e: e[0] == 'post')
    verify = _index(events, lambda e: e[0] == 'get')
    ready = _index(events, lambda e: e[0] == 'print'
                   and '-- share-ready.' in e[1])

    assert -1 < create < render < share < verify < ready


def test_an_unverifiable_permission_withholds_the_success_line(
        monkeypatch, events, no_google_setup, fake_google, no_warehouse):
    """The whole point of the read-back. Drive accepting the call is not
    evidence the file is link-viewable, and 'could not check' has to be
    treated exactly like 'wrong'."""
    monkeypatch.setattr(
        fake_google.http_client, 'request',
        lambda method, url, params=None, json=None, **kw: (
            events.append((method, 'permissions'))
            or _permission_response(
                [] if method == 'post'
                else [{'id': 'a', 'type': 'anyone', 'role': 'reader'}])))

    _run(monkeypatch, '--duckdb', '--new-public-workbook')

    printed = ' '.join(text for tag, text in events if tag == 'print')
    assert '-- share-ready.' not in printed
    assert 'NOT share-ready' in printed
    assert 'allowFileDiscovery' in printed


# ---------------------------------------------------------------------------
# When the identity did not ship
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('broken', [
    {},                                                  # no descriptor
    {'installed': {'client_id': ''}},                    # blank
    {'installed': {'client_id': 'x.apps.googleusercontent.com',
                   'auth_uri': 'https://a', 'token_uri': 'https://t',
                   'client_secret': 'a-secret-must-not-ship'}},
    {'web': SYNTHETIC_CLIENT['installed']},              # wrong client type
])
def test_a_broken_identity_exits_nonzero_before_the_browser(
        monkeypatch, events, no_google_setup, fake_google, no_warehouse,
        broken):
    """Including the one that is easiest to wave through: a descriptor
    that CARRIES a client secret. The published identity is secretless by
    design, so a secret in it is a credential in a public repo -- refused
    rather than silently used."""
    monkeypatch.setattr(public_oauth_client, 'BUNDLED_PUBLIC_CLIENT', broken)

    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, '--duckdb', '--new-public-workbook')

    assert exc.value.code == 2
    assert not [e for e in events if e[0].startswith('consent-')], (
        'a browser flow was constructed for a build with no usable identity'
    )
    assert not [e for e in events if e[0] == 'create']


def test_the_failure_blames_packaging_rather_than_the_users_google_account(
        monkeypatch, events, no_google_setup, fake_google, no_warehouse):
    """A stranger who hits this must not conclude they need to go and
    make a Google Cloud project -- removing that is the entire ticket."""
    monkeypatch.setattr(public_oauth_client, 'BUNDLED_PUBLIC_CLIENT', {})

    with pytest.raises(SystemExit):
        _run(monkeypatch, '--duckdb', '--new-public-workbook')

    printed = ' '.join(text for tag, text in events if tag == 'print')
    assert 'packaging problem' in printed
    assert 'do not need a Google Cloud project' in printed


# ---------------------------------------------------------------------------
# The advanced override
# ---------------------------------------------------------------------------

def test_an_explicit_client_path_still_overrides_the_bundled_identity(
        monkeypatch, events, no_google_setup, fake_google, no_warehouse,
        tmp_path):
    """The BYO route stays available for maintainers and testing. It is
    an override, not the mainline."""
    byo = tmp_path / 'byo-client.json'
    byo.write_text(json.dumps(SYNTHETIC_BYO_CLIENT), encoding='utf-8')
    monkeypatch.setenv('GOOGLE_PUBLIC_OAUTH_CLIENT_PATH', str(byo))

    _run(monkeypatch, '--duckdb', '--new-public-workbook')

    assert ('consent-from-file', (DRIVE_FILE,)) in events
    assert not [e for e in events if e[0] == 'consent-from-config']
    assert 'public' in no_google_setup['client_paths_asked']
