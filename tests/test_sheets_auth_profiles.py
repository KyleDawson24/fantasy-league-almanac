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

def test_public_profile_with_a_spreadsheets_token_runs_consent(
        profiles, stub_consent):
    _, public = profiles
    _write_token(public.token_path, [SPREADSHEETS])

    sheets_auth.authorized_client(public)

    assert stub_consent == ['public'], (
        'a spreadsheets-only cache was accepted for a drive.file profile'
    )
    assert sheets_auth.granted_scopes(public.token_path) == {DRIVE_FILE}


def test_public_profile_with_no_token_runs_consent(profiles, stub_consent):
    _, public = profiles
    sheets_auth.authorized_client(public)
    assert stub_consent == ['public']


def test_public_profile_with_a_good_token_does_not_run_consent(
        profiles, stub_consent, monkeypatch):
    _, public = profiles
    _write_token(public.token_path, [DRIVE_FILE])
    monkeypatch.setattr(sheets_auth.Credentials, 'from_authorized_user_file',
                        staticmethod(lambda path, scopes: _FakeCreds(scopes)))

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
        profiles, stub_consent):
    maintainer, public = profiles
    _write_token(maintainer.token_path, [SPREADSHEETS])
    before = maintainer.token_path.read_bytes()

    sheets_auth.authorized_client(public)

    assert maintainer.token_path.read_bytes() == before, (
        "the public consent flow rewrote the maintainer's cached grant"
    )
    assert public.token_path.exists()


def test_each_profile_writes_only_its_own_cache(profiles, stub_consent):
    maintainer, public = profiles

    sheets_auth.authorized_client(maintainer)
    assert maintainer.token_path.exists()
    assert not public.token_path.exists()

    sheets_auth.authorized_client(public)
    assert sheets_auth.granted_scopes(public.token_path) == {DRIVE_FILE}
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


@pytest.mark.parametrize('granted', [None, []])
def test_credential_scopes_falls_back_to_requested_when_unpopulated(granted):
    """google-auth does not populate granted_scopes on every path."""
    creds = _FakeCreds([DRIVE_FILE], granted_scopes=granted)
    assert sheets_auth.credential_scopes(creds) == {DRIVE_FILE}


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
