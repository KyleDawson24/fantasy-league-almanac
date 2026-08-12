"""The two OAuth profiles, and the wall between them (MLB-209).

The published tool must be able to ask for `drive.file` and nothing else.
The maintainer's long-standing `spreadsheets` grant -- the one that opens
the standing dev/prod almanac books, which no `drive.file` client can
ever see, because they were not created by an app -- has to keep working
exactly as it did.

The failure this file mostly exists to prevent is quiet rather than loud.
`Credentials.from_authorized_user_file(path, scopes)` STAMPS the scopes
you hand it onto the object it returns; it never compares them to what
Google granted. So loading the maintainer's `spreadsheets`-only cache
while asking for `drive.file` yields a credential that CLAIMS drive.file,
authorizes happily, and 403s on the first create -- and a measurement run
that got that far would have measured a consent screen nobody saw.

Account-free and fully synthetic: no client JSON, no token, no browser,
no network. Consent is a stub that returns a fake credential.
"""
from __future__ import annotations

import dataclasses
import json

import pytest

import sheets_auth
import sheets_writer
from google.auth.exceptions import RefreshError


SPREADSHEETS = sheets_auth.SPREADSHEETS_SCOPE
DRIVE_FILE = sheets_auth.DRIVE_FILE_SCOPE


class _FakeCreds:
    """Just enough credential to travel through authorized_client.

    `granted_scopes` defaults to None so the fallback to `scopes` is what
    most tests exercise; the tests that care set it explicitly.
    """

    def __init__(self, scopes, valid=True, expired=False, refresh_token=None,
                 granted_scopes=None):
        self.scopes = list(scopes)
        self.granted_scopes = (list(granted_scopes)
                               if granted_scopes is not None else None)
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token

    def to_json(self):
        return json.dumps({
            'token': 'FAKE',
            'refresh_token': 'FAKE',
            'client_id': 'FAKE',
            'client_secret': 'FAKE',
            'scopes': self.scopes,
        })


def _write_token(path, scopes):
    """A cache file in the shape google-auth writes."""
    path.write_text(json.dumps({
        'token': 'cached',
        'refresh_token': 'cached',
        'client_id': 'cached',
        'client_secret': 'cached',
        'scopes': list(scopes),
    }), encoding='utf-8')


@pytest.fixture
def secure_store(monkeypatch):
    """In-memory stand-in: unit tests must never touch Credential Locker."""
    state = {'token': None, 'events': []}

    def load():
        state['events'].append('load')
        return state['token']

    def store(serialized):
        state['events'].append('store')
        state['token'] = serialized

    def delete():
        state['events'].append('delete')
        existed = state['token'] is not None
        state['token'] = None
        return existed

    monkeypatch.setattr(sheets_auth.secure_token_store,
                        'load_public_token', load)
    monkeypatch.setattr(sheets_auth.secure_token_store,
                        'store_public_token', store)
    monkeypatch.setattr(sheets_auth.secure_token_store,
                        'delete_public_token', delete)
    return state


@pytest.fixture(autouse=True)
def no_real_secure_store(secure_store):
    return secure_store


@pytest.fixture
def profiles(tmp_path):
    """The real profiles, re-pointed at a temp directory.

    `dataclasses.replace` keeps every other field authentic -- scopes,
    exactness, client env -- so these tests exercise the shipped values
    and not a hand-built lookalike.
    """
    maintainer = dataclasses.replace(
        sheets_auth.MAINTAINER,
        token_path=tmp_path / '.sheets_oauth_token.json')
    public = dataclasses.replace(
        sheets_auth.PUBLIC,
        token_path=tmp_path / '.sheets_public_oauth_token.json')
    return maintainer, public


@pytest.fixture
def stub_consent(monkeypatch):
    """Replace the browser consent flow with a recorder."""
    calls = []

    def _consent(profile):
        calls.append(profile.name)
        return _FakeCreds(profile.scopes)

    monkeypatch.setattr(sheets_auth, 'run_consent_flow', _consent)
    monkeypatch.setattr(sheets_auth, 'gspread',
                        _StubGspread(), raising=True)
    return calls


class _StubGspread:
    """`gspread.authorize` without gspread."""

    @staticmethod
    def authorize(creds):
        return ('client-for', tuple(creds.scopes))


# ---------------------------------------------------------------------------
# The profiles are different things
# ---------------------------------------------------------------------------

def test_the_two_profiles_share_no_scope_client_or_token_path():
    m, p = sheets_auth.MAINTAINER, sheets_auth.PUBLIC

    assert set(m.scopes) != set(p.scopes)
    assert m.client_env != p.client_env
    assert m.token_path != p.token_path
    assert m.token_path.name != p.token_path.name


def test_public_profile_requests_only_drive_file():
    """The whole measurement is 'what does a drive.file-only app look
    like to a stranger'. One extra scope and the answer is worthless."""
    assert sheets_auth.PUBLIC.scopes == (DRIVE_FILE,)
    assert SPREADSHEETS not in sheets_auth.PUBLIC.scopes


def test_maintainer_profile_keeps_its_existing_scope_and_token_file():
    assert sheets_auth.MAINTAINER.scopes == (SPREADSHEETS,)
    assert sheets_auth.MAINTAINER.token_path.name == '.sheets_oauth_token.json'
    assert sheets_auth.MAINTAINER.client_env == 'GOOGLE_OAUTH_CLIENT_PATH'


def test_sheets_writer_is_pinned_to_the_maintainer_profile(monkeypatch):
    """Every existing almanac/records caller reaches Google through
    sheets_writer. It must not be reachable by the public profile."""
    seen = []
    monkeypatch.setattr(sheets_auth, 'authorized_client',
                        lambda profile: seen.append(profile) or 'client')

    assert sheets_writer._get_authorized_client() == 'client'
    assert seen == [sheets_auth.MAINTAINER]
    assert sheets_writer._OAUTH_SCOPES == [SPREADSHEETS]
    assert sheets_writer._TOKEN_PATH == sheets_auth.MAINTAINER.token_path


# ---------------------------------------------------------------------------
# Granted scopes are read from the file, never assumed
# ---------------------------------------------------------------------------

def test_granted_scopes_reports_what_the_file_says(tmp_path):
    path = tmp_path / 'tok.json'
    _write_token(path, [SPREADSHEETS])
    assert sheets_auth.granted_scopes(path) == frozenset({SPREADSHEETS})


@pytest.mark.parametrize('payload', ['', 'not json', '[]', '{}',
                                     '{"scopes": null}'])
def test_unknowable_grants_are_treated_as_no_grant(tmp_path, payload):
    """Missing, corrupt, or scope-less: all mean 'we cannot say what was
    granted', which must fail the check rather than pass it."""
    path = tmp_path / 'tok.json'
    path.write_text(payload, encoding='utf-8')
    assert sheets_auth.granted_scopes(path) == frozenset()


def test_a_spreadsheets_only_cache_does_not_satisfy_the_public_profile():
    """THE trap: this is the maintainer's real grant, and the naive load
    would stamp drive.file onto it and sail past."""
    assert not sheets_auth.token_satisfies(
        sheets_auth.PUBLIC, {SPREADSHEETS})


def test_a_token_missing_drive_file_does_not_satisfy_the_public_profile():
    assert not sheets_auth.token_satisfies(sheets_auth.PUBLIC, set())
    assert not sheets_auth.token_satisfies(
        sheets_auth.PUBLIC, {'https://www.googleapis.com/auth/drive.readonly'})


def test_the_public_profile_also_refuses_a_grant_that_is_too_WIDE():
    """A token carrying drive.file AND something sensitive would still
    'work'. Accepting it would make the measurement a lie -- the app
    would be running on a broader grant than the one being measured."""
    assert not sheets_auth.token_satisfies(
        sheets_auth.PUBLIC, {DRIVE_FILE, SPREADSHEETS})
    assert sheets_auth.token_satisfies(sheets_auth.PUBLIC, {DRIVE_FILE})


def test_the_maintainer_profile_keeps_its_permissive_reading():
    """Unchanged behavior: a grant that CONTAINS spreadsheets is fine."""
    assert sheets_auth.token_satisfies(
        sheets_auth.MAINTAINER, {SPREADSHEETS})
    assert sheets_auth.token_satisfies(
        sheets_auth.MAINTAINER, {SPREADSHEETS, DRIVE_FILE})
    assert not sheets_auth.token_satisfies(
        sheets_auth.MAINTAINER, {DRIVE_FILE})


# ---------------------------------------------------------------------------
# An unusable cache sends you to consent, not to a 403
# ---------------------------------------------------------------------------

def test_invalid_plaintext_public_cache_fails_closed_without_deleting_it(
        profiles, stub_consent):
    _, public = profiles
    _write_token(public.token_path, [SPREADSHEETS])

    with pytest.raises(RuntimeError, match='not migrated or deleted'):
        sheets_auth.authorized_client(public)

    assert public.token_path.exists()
    assert stub_consent == []


def test_public_profile_with_no_token_runs_consent(profiles, stub_consent):
    _, public = profiles
    sheets_auth.authorized_client(public)
    assert stub_consent == ['public']


def test_fresh_public_consent_is_saved_only_to_secure_storage(
        profiles, stub_consent, secure_store):
    _, public = profiles

    sheets_auth.authorized_client(public)

    assert sheets_auth.granted_scopes_json(secure_store['token']) == {
        DRIVE_FILE}
    assert not public.token_path.exists()


def test_secure_cached_authorization_avoids_fresh_consent(
        profiles, stub_consent, secure_store, monkeypatch):
    _, public = profiles
    secure_store['token'] = _FakeCreds([DRIVE_FILE]).to_json()
    monkeypatch.setattr(sheets_auth.Credentials, 'from_authorized_user_info',
                        staticmethod(lambda info, scopes: _FakeCreds(scopes)))

    sheets_auth.authorized_client(public)

    assert stub_consent == []


def test_expired_public_grant_refreshes_and_rewrites_secure_storage(
        profiles, stub_consent, secure_store, monkeypatch):
    _, public = profiles
    secure_store['token'] = _FakeCreds([DRIVE_FILE]).to_json()

    class _Refreshable(_FakeCreds):
        def __init__(self):
            super().__init__([DRIVE_FILE], valid=False, expired=True,
                             refresh_token='refresh')

        def refresh(self, request):
            self.valid = True
            self.expired = False

    monkeypatch.setattr(
        sheets_auth.Credentials, 'from_authorized_user_info',
        staticmethod(lambda info, scopes: _Refreshable()))

    sheets_auth.authorized_client(public)

    assert stub_consent == []
    assert secure_store['events'].count('store') == 1


def test_revoked_refresh_token_runs_fresh_consent_and_replaces_secure_grant(
        profiles, stub_consent, secure_store, monkeypatch):
    _, public = profiles
    secure_store['token'] = _FakeCreds([DRIVE_FILE]).to_json()

    class _Revoked(_FakeCreds):
        def __init__(self):
            super().__init__([DRIVE_FILE], valid=False, expired=True,
                             refresh_token='revoked')

        def refresh(self, request):
            raise RefreshError('invalid_grant')

    monkeypatch.setattr(
        sheets_auth.Credentials, 'from_authorized_user_info',
        staticmethod(lambda info, scopes: _Revoked()))

    sheets_auth.authorized_client(public)

    assert stub_consent == ['public']
    assert secure_store['events'].count('store') == 1


def test_plaintext_migration_writes_securely_before_removing_source(
        profiles, secure_store, monkeypatch):
    _, public = profiles
    _write_token(public.token_path, [DRIVE_FILE])
    events = []

    def _store(serialized):
        assert public.token_path.exists()
        events.append('secure-write')
        secure_store['token'] = serialized

    monkeypatch.setattr(sheets_auth.secure_token_store,
                        'store_public_token', _store)
    monkeypatch.setattr(sheets_auth.Credentials, 'from_authorized_user_info',
                        staticmethod(lambda info, scopes: _FakeCreds(scopes)))

    sheets_auth.load_cached_credentials(public)

    assert events == ['secure-write']
    assert secure_store['token'] is not None
    assert not public.token_path.exists()


def test_failed_secure_migration_keeps_plaintext_source_and_fails_closed(
        profiles, monkeypatch):
    _, public = profiles
    _write_token(public.token_path, [DRIVE_FILE])

    def _fail(serialized):
        raise sheets_auth.secure_token_store.SecureTokenStoreError(
            'locker unavailable')

    monkeypatch.setattr(sheets_auth.secure_token_store,
                        'store_public_token', _fail)

    with pytest.raises(RuntimeError, match='locker unavailable'):
        sheets_auth.load_cached_credentials(public)

    assert public.token_path.exists()


def test_local_deletion_forgets_secure_and_legacy_public_copies(
        profiles, secure_store):
    _, public = profiles
    secure_store['token'] = _FakeCreds([DRIVE_FILE]).to_json()
    _write_token(public.token_path, [DRIVE_FILE])

    assert sheets_auth.delete_cached_credentials(public) is True
    assert secure_store['token'] is None
    assert not public.token_path.exists()


def test_public_profile_with_a_good_token_does_not_run_consent(
        profiles, stub_consent, monkeypatch):
    _, public = profiles
    _write_token(public.token_path, [DRIVE_FILE])
    monkeypatch.setattr(sheets_auth.Credentials, 'from_authorized_user_info',
                        staticmethod(lambda info, scopes: _FakeCreds(scopes)))

    sheets_auth.authorized_client(public)

    assert stub_consent == []


# ---------------------------------------------------------------------------
# The wall
# ---------------------------------------------------------------------------

def test_the_public_profile_never_reads_the_maintainer_token(
        profiles, stub_consent, monkeypatch):
    maintainer, public = profiles
    _write_token(maintainer.token_path, [SPREADSHEETS])

    def _explode(path, scopes):
        raise AssertionError(f'the public path loaded a token file: {path}')

    monkeypatch.setattr(sheets_auth.Credentials, 'from_authorized_user_file',
                        staticmethod(_explode))

    sheets_auth.authorized_client(public)

    assert stub_consent == ['public']


def test_the_public_profile_never_overwrites_the_maintainer_token(
        profiles, stub_consent, secure_store):
    maintainer, public = profiles
    _write_token(maintainer.token_path, [SPREADSHEETS])
    before = maintainer.token_path.read_bytes()

    sheets_auth.authorized_client(public)

    assert maintainer.token_path.read_bytes() == before, (
        "the public consent flow rewrote the maintainer's cached grant"
    )
    assert secure_store['token'] is not None
    assert not public.token_path.exists()


def test_each_profile_writes_only_its_own_cache(profiles, stub_consent,
                                                secure_store):
    maintainer, public = profiles

    sheets_auth.authorized_client(maintainer)
    assert maintainer.token_path.exists()
    assert not public.token_path.exists()

    sheets_auth.authorized_client(public)
    assert sheets_auth.granted_scopes_json(secure_store['token']) == {DRIVE_FILE}
    assert sheets_auth.granted_scopes(maintainer.token_path) == {SPREADSHEETS}


# ---------------------------------------------------------------------------
# Consent that grants less than it was asked for
# ---------------------------------------------------------------------------

def _stub_flow(monkeypatch, returned_creds):
    monkeypatch.setenv('GOOGLE_PUBLIC_OAUTH_CLIENT_PATH', __file__)
    monkeypatch.setenv('GOOGLE_OAUTH_CLIENT_PATH', __file__)

    class _Flow:
        @staticmethod
        def from_client_secrets_file(path, scopes):
            return _Flow()

        def run_local_server(self, port=0):
            return returned_creds

    monkeypatch.setattr(sheets_auth, 'InstalledAppFlow', _Flow)


def test_a_consent_that_withholds_a_scope_fails_at_the_consent(monkeypatch):
    """A user who unchecks a box otherwise gets a token that 403s several
    steps later, nowhere near the cause."""
    _stub_flow(monkeypatch, _FakeCreds([]))          # granted nothing

    with pytest.raises(RuntimeError, match='did not grant'):
        sheets_auth.run_consent_flow(sheets_auth.PUBLIC)


def test_a_fresh_grant_carrying_an_EXTRA_scope_is_refused(monkeypatch):
    """The cached path already demanded exactness. The fresh path did
    not, so a re-consent that carried a previously granted `spreadsheets`
    forward would have been accepted and then CACHED -- and every later
    run would inherit a grant wider than the one being measured."""
    _stub_flow(monkeypatch, _FakeCreds(
        [DRIVE_FILE], granted_scopes=[DRIVE_FILE, SPREADSHEETS]))

    with pytest.raises(RuntimeError, match='on top of what was requested'):
        sheets_auth.run_consent_flow(sheets_auth.PUBLIC)


def test_a_refused_fresh_grant_is_never_written_to_the_token_cache(
        profiles, monkeypatch):
    _, public = profiles
    _stub_flow(monkeypatch, _FakeCreds(
        [DRIVE_FILE], granted_scopes=[DRIVE_FILE, SPREADSHEETS]))

    with pytest.raises(RuntimeError):
        sheets_auth.authorized_client(public)

    assert not public.token_path.exists(), (
        'a refused grant was cached, so the next run would inherit it'
    )


def test_the_granted_scopes_field_wins_over_the_requested_one(monkeypatch):
    """`scopes` is what we ASKED for; `granted_scopes` is what Google
    says it gave. Judging by the request would be judging our own input."""
    creds = _FakeCreds([DRIVE_FILE],
                       granted_scopes=[DRIVE_FILE, SPREADSHEETS])
    assert sheets_auth.credential_scopes(creds) == {DRIVE_FILE, SPREADSHEETS}


def test_credential_scopes_falls_back_to_requested_when_NOT_REPORTED():
    """google-auth does not populate granted_scopes on every path. None
    means 'we were not told', which is what the fallback is for."""
    creds = _FakeCreds([DRIVE_FILE], granted_scopes=None)
    assert sheets_auth.credential_scopes(creds) == {DRIVE_FILE}


@pytest.mark.parametrize('empty', [[], (), set()])
def test_an_explicitly_EMPTY_grant_is_not_replaced_by_what_we_asked_for(empty):
    """None and empty are different answers and must not be conflated.
    Empty means Google reported granting nothing; substituting the
    requested scopes there would turn a refusal into an approval."""
    creds = _FakeCreds([DRIVE_FILE], granted_scopes=empty)
    assert sheets_auth.credential_scopes(creds) == frozenset()
    assert not sheets_auth.token_satisfies(sheets_auth.PUBLIC,
                                           sheets_auth.credential_scopes(creds))


def test_a_consent_reporting_an_empty_grant_fails_closed(profiles,
                                                         monkeypatch):
    _, public = profiles
    _stub_flow(monkeypatch, _FakeCreds([DRIVE_FILE], granted_scopes=[]))

    with pytest.raises(RuntimeError, match='did not grant'):
        sheets_auth.authorized_client(public)

    assert not public.token_path.exists()


def test_an_exact_fresh_public_grant_is_accepted(monkeypatch):
    _stub_flow(monkeypatch, _FakeCreds(
        [DRIVE_FILE], granted_scopes=[DRIVE_FILE]))

    creds = sheets_auth.run_consent_flow(sheets_auth.PUBLIC)
    assert sheets_auth.credential_scopes(creds) == {DRIVE_FILE}


def test_the_maintainer_flow_still_tolerates_a_wider_grant(monkeypatch):
    """Exactness is the PUBLIC profile's promise. Narrowing the
    maintainer here would break a working setup for no gain."""
    _stub_flow(monkeypatch, _FakeCreds(
        [SPREADSHEETS], granted_scopes=[SPREADSHEETS, DRIVE_FILE]))

    creds = sheets_auth.run_consent_flow(sheets_auth.MAINTAINER)
    assert SPREADSHEETS in sheets_auth.credential_scopes(creds)


def test_a_missing_client_config_names_the_env_var_not_a_secret(monkeypatch):
    monkeypatch.delenv('GOOGLE_PUBLIC_OAUTH_CLIENT_PATH', raising=False)

    with pytest.raises(RuntimeError) as exc:
        sheets_auth.client_config_path(sheets_auth.PUBLIC)

    assert 'GOOGLE_PUBLIC_OAUTH_CLIENT_PATH' in str(exc.value)
    assert 'public' in str(exc.value)


def test_get_profile_rejects_an_unknown_name():
    with pytest.raises(RuntimeError, match='Unknown OAuth profile'):
        sheets_auth.get_profile('admin')
    assert sheets_auth.get_profile('public') is sheets_auth.PUBLIC
    assert sheets_auth.get_profile('maintainer') is sheets_auth.MAINTAINER


# ---------------------------------------------------------------------------
# Which client consents (MLB-209): the bundled identity and its override
# ---------------------------------------------------------------------------
#
# The public profile stopped being env-only when the published tool got an
# identity of its own. The maintainer profile did not, and the tests below
# are mostly about that asymmetry holding.

# The SHIPPED identity: client id and PKCE, no secret. Google marks
# client_secret optional for installed apps and code_verifier required,
# so this is the documented shape rather than a corner cut.
SYNTHETIC_BUNDLED_CLIENT = {
    'installed': {
        'client_id': '000000000000-synthetic.apps.googleusercontent.com',
        'client_secret': 'GOCSPX-' + ('SYNTHETICbundled' + '0' * 28)[:28],
        'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
        'token_uri': 'https://oauth2.googleapis.com/token',
    },
}

# A maintainer's OWN client, which may well carry a secret. The override
# path must keep working for these unchanged.
SYNTHETIC_BYO_CLIENT = {
    'installed': {
        **SYNTHETIC_BUNDLED_CLIENT['installed'],
        'client_secret': 'SYNTHETIC-not-a-real-secret',
    },
}


@pytest.fixture
def no_client_env(monkeypatch):
    """A clean environment. `.env` is loaded in some runs, so the vars
    are deleted rather than assumed absent."""
    monkeypatch.delenv('GOOGLE_PUBLIC_OAUTH_CLIENT_PATH', raising=False)
    monkeypatch.delenv('GOOGLE_OAUTH_CLIENT_PATH', raising=False)


@pytest.fixture
def bundled(monkeypatch):
    """Point the public profile at a synthetic bundled identity."""
    monkeypatch.setattr(
        sheets_auth.public_oauth_client, 'BUNDLED_PUBLIC_CLIENT',
        SYNTHETIC_BUNDLED_CLIENT)


def test_the_public_profile_needs_no_env_var_at_all(no_client_env, bundled):
    """THE POINT OF THE TICKET. Nothing set, no file on disk, and the
    published tool can still consent -- which is what makes 'you do not
    need a Google Cloud project' a true sentence."""
    source = sheets_auth.resolve_client_source(sheets_auth.PUBLIC)

    assert source.kind == 'bundled'
    assert source.path is None
    assert source.config['installed']['client_id'].endswith(
        '.apps.googleusercontent.com')


def test_an_explicit_public_override_still_wins(no_client_env, bundled,
                                                tmp_path, monkeypatch):
    """Maintainers and testers need to be able to consent as some other
    client without editing the repo."""
    byo = tmp_path / 'byo-client.json'
    byo.write_text(json.dumps(SYNTHETIC_BYO_CLIENT), encoding='utf-8')
    monkeypatch.setenv('GOOGLE_PUBLIC_OAUTH_CLIENT_PATH', str(byo))

    source = sheets_auth.resolve_client_source(sheets_auth.PUBLIC)

    assert source.kind == 'env'
    assert source.path == str(byo)


def test_a_broken_override_is_an_error_and_not_a_fallback(
        no_client_env, bundled, tmp_path, monkeypatch):
    """A set-but-wrong path must NOT quietly become the shipped identity.
    A developer who typo'd a path would otherwise be consented as the
    published app and never find out which client they were testing."""
    monkeypatch.setenv('GOOGLE_PUBLIC_OAUTH_CLIENT_PATH',
                       str(tmp_path / 'does-not-exist.json'))

    with pytest.raises(RuntimeError, match='not found'):
        sheets_auth.resolve_client_source(sheets_auth.PUBLIC)


def test_the_maintainer_profile_stays_env_only(no_client_env):
    """It has no bundled client and must never grow one: the maintainer's
    `spreadsheets` grant opens the standing dev/prod books, and the
    published identity has no business anywhere near them."""
    assert sheets_auth.MAINTAINER.bundled_client is None

    with pytest.raises(RuntimeError, match='GOOGLE_OAUTH_CLIENT_PATH'):
        sheets_auth.resolve_client_source(sheets_auth.MAINTAINER)


def test_the_maintainer_cannot_borrow_the_public_client(
        no_client_env, bundled, tmp_path, monkeypatch):
    """Even with the public side fully configured, the maintainer profile
    resolves nothing -- the two never cross."""
    byo = tmp_path / 'public-client.json'
    byo.write_text(json.dumps(SYNTHETIC_BYO_CLIENT), encoding='utf-8')
    monkeypatch.setenv('GOOGLE_PUBLIC_OAUTH_CLIENT_PATH', str(byo))

    with pytest.raises(RuntimeError, match='GOOGLE_OAUTH_CLIENT_PATH'):
        sheets_auth.resolve_client_source(sheets_auth.MAINTAINER)


def test_an_unusable_bundled_identity_fails_before_any_browser(
        no_client_env, monkeypatch):
    """A packaging failure has to cost an error message, not a consent
    screen followed by a KeyError. `InstalledAppFlow` is replaced with a
    tripwire: if resolution ever gets far enough to build a flow, the
    browser was one line away."""
    monkeypatch.setattr(
        sheets_auth.public_oauth_client, 'BUNDLED_PUBLIC_CLIENT',
        {'installed': {'client_id': ''}})

    class _Tripwire:
        @staticmethod
        def from_client_config(config, scopes):
            raise AssertionError('a flow was built from a broken descriptor')

        @staticmethod
        def from_client_secrets_file(path, scopes):
            raise AssertionError('the bundled path read a client file')

    monkeypatch.setattr(sheets_auth, 'InstalledAppFlow', _Tripwire)

    with pytest.raises(RuntimeError, match='packaging problem'):
        sheets_auth.run_consent_flow(sheets_auth.PUBLIC)


def _recording_flow(monkeypatch, returned_creds):
    """A flow that records which constructor the consent path used.

    BOTH flow names are replaced. The bundled path goes through
    `BundledInstalledAppFlow` and the env path through the stock
    `InstalledAppFlow`, and a recorder installed on only one of them
    would let the other reach a real browser.
    """
    used = {}

    class _Flow:
        @classmethod
        def from_client_config(cls, config, scopes):
            used['how'] = 'config'
            used['scopes'] = list(scopes)
            used['config'] = config
            return cls()

        @classmethod
        def from_client_secrets_file(cls, path, scopes):
            used['how'] = 'file'
            used['scopes'] = list(scopes)
            used['path'] = path
            return cls()

        def run_local_server(self, port=0):
            return returned_creds

    monkeypatch.setattr(sheets_auth, 'InstalledAppFlow', _Flow)
    monkeypatch.setattr(sheets_auth, 'BundledInstalledAppFlow', _Flow)
    return used


def test_the_bundled_identity_reaches_the_flow_in_memory(
        no_client_env, bundled, monkeypatch):
    """No temp file, no path -- the descriptor is handed to the library
    as a mapping, which is why there is nothing on disk for a stranger to
    lose, leak or have to install."""
    used = _recording_flow(
        monkeypatch, _FakeCreds([DRIVE_FILE], granted_scopes=[DRIVE_FILE]))

    sheets_auth.run_consent_flow(sheets_auth.PUBLIC)

    assert used['how'] == 'config'
    assert used['config'] == SYNTHETIC_BUNDLED_CLIENT
    assert used['scopes'] == [DRIVE_FILE]


def test_a_byo_override_cannot_widen_what_the_public_profile_requests(
        no_client_env, bundled, tmp_path, monkeypatch):
    """Scopes come from the PROFILE, never from the client file. A client
    JSON has no scope field to begin with, so the only way an override
    could widen the request is if someone wired one -- this is the test
    that would catch that."""
    byo = tmp_path / 'byo-client.json'
    byo.write_text(json.dumps({
        'installed': {**SYNTHETIC_BYO_CLIENT['installed'],
                      'scopes': [SPREADSHEETS]},   # ignored, and must be
    }), encoding='utf-8')
    monkeypatch.setenv('GOOGLE_PUBLIC_OAUTH_CLIENT_PATH', str(byo))

    used = _recording_flow(
        monkeypatch, _FakeCreds([DRIVE_FILE], granted_scopes=[DRIVE_FILE]))

    sheets_auth.run_consent_flow(sheets_auth.PUBLIC)

    assert used['how'] == 'file'
    assert used['scopes'] == [DRIVE_FILE]
    assert SPREADSHEETS not in used['scopes']


def test_the_bundled_public_grant_is_still_judged_exactly(
        profiles, no_client_env, bundled, monkeypatch):
    """Shipping the client changed WHO asks. It must not have changed
    what counts as an acceptable answer."""
    _, public = profiles
    _recording_flow(monkeypatch, _FakeCreds(
        [DRIVE_FILE], granted_scopes=[DRIVE_FILE, SPREADSHEETS]))

    with pytest.raises(RuntimeError, match='on top of what was requested'):
        sheets_auth.authorized_client(public)

    assert not public.token_path.exists()


# ---------------------------------------------------------------------------
# What the user is told before the browser opens
# ---------------------------------------------------------------------------

def test_the_public_disclosure_describes_per_file_access_and_no_enumeration():
    text = sheets_auth.consent_disclosure(sheets_auth.PUBLIC)

    assert 'drive.file' in text
    assert 'create this one workbook' in text
    assert 'cannot list, open, or read anything else' in text


def test_the_disclosure_is_derived_from_the_scopes_it_describes():
    """Written out beside the scopes rather than from them, this sentence
    would be one edit away from describing a request the code no longer
    makes. A profile asking for something else gets a different
    sentence."""
    wider = dataclasses.replace(
        sheets_auth.PUBLIC, scopes=(DRIVE_FILE, SPREADSHEETS))
    text = sheets_auth.consent_disclosure(wider)

    assert 'cannot list, open, or read anything else' not in text
    assert SPREADSHEETS in text
