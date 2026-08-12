"""Where the published identity's secret goes -- and where it does not
(MLB-209).

This repository deliberately ships one credential: the Desktop OAuth
client secret for the Cloud project dedicated to the published tool.
Google's token endpoint requires it for this configured client (measured
live on 2026-08-11; a PKCE-only exchange was refused after consent with
`(invalid_request) client_secret is missing`), so it has to travel.

Shipping it makes exactly one property worth defending, and it is not
"the secret is hidden" -- it is in a public file by design. It is:

    THE SECRET GOES TO GOOGLE'S TOKEN ENDPOINT AND NOWHERE ELSE.

Not into a browser URL, not into an Authorization header, not into Drive
API traffic, not into stdout, not into an error message, not into the
workbook ledger, and not into a second file in this repo.

WHY IT IS ASSERTED AT THE ADAPTER. Every level above
`requests.adapters.HTTPAdapter.send` is a place where a credential can
still move. `requests_oauthlib` will happily relocate the client
credentials from the form body into `Authorization: Basic <base64>` if
not told otherwise -- a change invisible to anything inspecting the body
dict. What `HTTPAdapter.send` receives is the `PreparedRequest`: the
bytes and headers that would have gone out.

Account-free and fully synthetic. The transport is replaced before any
flow is built, and the secret used throughout is a canary -- never the
shipped one, except in the single test that checks where the shipped one
appears on disk, which reports counts and paths and never the value.
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import urllib.parse
from pathlib import Path

import pytest
import requests
from requests.adapters import HTTPAdapter

import public_oauth_client as poc
import sheets_auth
import sheets_workbook


DRIVE_FILE = sheets_auth.DRIVE_FILE_SCOPE
TOKEN_URI = 'https://oauth2.googleapis.com/token'
REPO_ROOT = Path(__file__).resolve().parents[1]

def synthetic_google_secret(tag):
    """A synthetic secret in Google's exact client-secret shape, padded
    programmatically -- hand-counting the 28-character body is a mistake
    that surfaces as a confusing validation error, not an obvious typo."""
    return 'GOCSPX-' + (tag + '0' * 28)[:28]


# Google's shape, so it exercises the same validation the real one does.
# Never the shipped value.
CANARY_SECRET = synthetic_google_secret('CANARYmustNotEscape')

CANARY_CONFIG = {
    'installed': {
        'client_id': '000000000000-synthetic.apps.googleusercontent.com',
        'client_secret': CANARY_SECRET,
        'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
        'token_uri': TOKEN_URI,
    },
}


@pytest.fixture
def wire(monkeypatch):
    """Capture every PreparedRequest; answer with a canned token body."""
    captured = []

    def _send(self, request, **kwargs):
        captured.append(request)
        response = requests.Response()
        response.status_code = 200
        response.headers['Content-Type'] = 'application/json'
        response.url = request.url
        response.request = request
        response._content = json.dumps({
            'access_token': 'synthetic-access',
            'refresh_token': 'synthetic-refresh',
            'expires_in': 3599,
            'scope': DRIVE_FILE,
            'token_type': 'Bearer',
        }).encode('utf-8')
        return response

    monkeypatch.setattr(HTTPAdapter, 'send', _send)
    return captured


def _exchange(config=None):
    """Run the real production flow class through a code exchange."""
    flow = sheets_auth.BundledInstalledAppFlow.from_client_config(
        json.loads(json.dumps(config or CANARY_CONFIG)), [DRIVE_FILE])
    flow.redirect_uri = 'http://localhost:8080/'
    auth_url, _ = flow.authorization_url()
    flow.fetch_token(code='synthetic-auth-code')
    return flow, auth_url


def _body(request):
    raw = request.body
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8')
    return urllib.parse.parse_qs(raw or '', keep_blank_values=True)


def _wire_text(request):
    """Everything that would go out: URL, body, and headers as one string.

    Header VALUES are decoded from base64 where they are Basic auth, so a
    credential smuggled into a header cannot pass a naive substring check.
    """
    raw = request.body or ''
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8')
    parts = [request.url, raw]
    for key, value in request.headers.items():
        parts.append(f'{key}: {value}')
        if isinstance(value, str) and value.startswith('Basic '):
            try:
                parts.append(base64.b64decode(value[6:]).decode('utf-8'))
            except Exception:                     # noqa: BLE001 -- best effort
                pass
    return '\n'.join(parts)


# ---------------------------------------------------------------------------
# Where it DOES go
# ---------------------------------------------------------------------------

def test_the_code_exchange_sends_the_secret_in_the_body(wire):
    """It has to travel -- Google's token endpoint requires it for this
    configured Desktop client. The body is where Google's Mobile &
    Desktop guide puts it."""
    _exchange()

    assert _body(wire[0])['client_secret'] == [CANARY_SECRET]


def test_the_body_matches_googles_documented_parameter_set(wire):
    _exchange()

    assert set(_body(wire[0])) == {
        'client_id', 'client_secret', 'code', 'code_verifier', 'grant_type',
        'redirect_uri'}


def test_the_exchange_goes_to_googles_token_endpoint(wire):
    _exchange()

    assert wire[0].url == TOKEN_URI
    assert wire[0].method == 'POST'


def test_pkce_still_rides_along(wire):
    """The secretless experiment is gone, but PKCE is not: Google lists
    `code_verifier` as Required for this flow, and it is what binds the
    code to the session that started it."""
    _, auth_url = _exchange()
    query = urllib.parse.parse_qs(urllib.parse.urlparse(auth_url).query)

    assert query['code_challenge_method'] == ['S256']
    assert _body(wire[0])['code_verifier'][0]


# ---------------------------------------------------------------------------
# Where it does NOT go
# ---------------------------------------------------------------------------

def test_the_secret_is_not_moved_into_an_authorization_header(wire):
    """THE ONE THAT IS EASY TO GET WRONG, and the reason
    `BundledInstalledAppFlow` exists at all. Without
    `include_client_id=True`, requests_oauthlib relocates the credentials
    into `Authorization: Basic <base64 of id:secret>` -- still correct
    OAuth, but it puts the shipped secret somewhere a proxy or a
    header-dumping error handler is likelier to capture, and it is not
    the shape Google documents. A body-only assertion would pass either
    way; this one does not."""
    _exchange()

    assert 'Authorization' not in wire[0].headers
    for value in wire[0].headers.values():
        if isinstance(value, str) and value.startswith('Basic '):
            decoded = base64.b64decode(value[6:]).decode('utf-8')
            assert CANARY_SECRET not in decoded


def test_the_secret_never_reaches_the_browser(wire):
    """The authorization URL is opened in the user's browser, lands in
    their history, and is logged by `run_local_server`. A client secret
    in a query string would be published to the user's own machine and
    every proxy in between."""
    _, auth_url = _exchange()

    assert CANARY_SECRET not in auth_url
    query = urllib.parse.parse_qs(urllib.parse.urlparse(auth_url).query)
    assert 'client_secret' not in query


def test_googles_token_endpoint_is_the_only_destination_that_gets_it(wire):
    """The whole claim, stated as a scan rather than a spot check: across
    every request the flow makes, the only one carrying the secret is the
    POST to Google's token endpoint."""
    _exchange()

    carriers = [r for r in wire if CANARY_SECRET in _wire_text(r)]

    assert [r.url for r in carriers] == [TOKEN_URI]


def test_drive_traffic_carries_a_bearer_token_and_no_secret(wire):
    """After the exchange, everything the app does with Drive rides on
    the access token. The client secret is finished the moment the code
    is redeemed and must not appear again."""
    from google.auth.transport.requests import AuthorizedSession

    flow, _ = _exchange()
    wire.clear()

    session = AuthorizedSession(flow.credentials)
    session.get('https://www.googleapis.com/drive/v3/files/synthetic-id')

    request = wire[-1]
    assert request.headers['Authorization'].startswith('Bearer ')
    assert CANARY_SECRET not in _wire_text(request)


def test_no_validation_message_reveals_the_secret():
    """Error text travels into logs, tracebacks and pasted bug reports.
    `describe_problem` may name fields; it may not quote them."""
    poisoned = json.loads(json.dumps(CANARY_CONFIG))
    poisoned['installed']['client_id'] = 'PASTE_HERE'

    problem = poc.describe_problem(poisoned)
    assert problem and CANARY_SECRET not in problem
    assert CANARY_SECRET not in poc.PACKAGING_ERROR.format(problem=problem)


def test_the_consent_disclosure_reveals_no_secret():
    """It is printed to the terminal immediately before consent."""
    text = (sheets_auth.consent_disclosure(sheets_auth.PUBLIC) + ' '
            + sheets_workbook.LINK_SHARING_DISCLOSURE)

    assert 'GOCSPX-' not in text
    assert 'client_secret' not in text


def test_the_workbook_ledger_holds_no_credential(tmp_path):
    """The ledger is local bookkeeping that survives between runs. It
    records what was created, never what created it."""
    ledger = sheets_workbook.WorkbookLedger(tmp_path / 'ledger.json')
    ledger.remember('sheet-1', 'Fantasy League Almanac', 'https://example')
    ledger.mark_rendered('sheet-1')

    text = (tmp_path / 'ledger.json').read_text(encoding='utf-8')
    assert 'GOCSPX-' not in text
    assert 'client_secret' not in text


# ---------------------------------------------------------------------------
# Which flow each source uses
# ---------------------------------------------------------------------------

def test_the_bundled_path_uses_the_body_form_flow(monkeypatch):
    monkeypatch.delenv('GOOGLE_PUBLIC_OAUTH_CLIENT_PATH', raising=False)
    monkeypatch.setattr(poc, 'BUNDLED_PUBLIC_CLIENT', CANARY_CONFIG)

    built = []

    class _Recorder(sheets_auth.BundledInstalledAppFlow):
        @classmethod
        def from_client_config(cls, config, scopes):
            built.append('bundled')
            raise RuntimeError('stop here -- construction is what is asserted')

    monkeypatch.setattr(sheets_auth, 'BundledInstalledAppFlow', _Recorder)

    with pytest.raises(RuntimeError, match='stop here'):
        sheets_auth.run_consent_flow(sheets_auth.PUBLIC)

    assert built == ['bundled']


def test_the_env_override_still_uses_the_stock_flow(monkeypatch, tmp_path):
    """A maintainer's own client keeps the behavior it has always had --
    this change is additive to the bundled path only."""
    byo = tmp_path / 'byo-client.json'
    byo.write_text(json.dumps(CANARY_CONFIG), encoding='utf-8')
    monkeypatch.setenv('GOOGLE_PUBLIC_OAUTH_CLIENT_PATH', str(byo))

    used = []

    class _Stock:
        @staticmethod
        def from_client_secrets_file(path, scopes):
            used.append('stock')
            raise RuntimeError('stop here')

    monkeypatch.setattr(sheets_auth, 'InstalledAppFlow', _Stock)

    with pytest.raises(RuntimeError, match='stop here'):
        sheets_auth.run_consent_flow(sheets_auth.PUBLIC)

    assert used == ['stock']


# ---------------------------------------------------------------------------
# The shipped value, on disk
# ---------------------------------------------------------------------------

_SECRET_LITERAL = re.compile(r'GOCSPX-[A-Za-z0-9_-]{28}')


def _tracked_files():
    """Every file git tracks, as repo-relative posix paths.

    `git ls-files` rather than a filesystem walk, deliberately: what
    matters is what can reach public history, and an ignored token cache
    on one machine is not that.
    """
    out = subprocess.run(['git', 'ls-files', '-z'], cwd=REPO_ROOT,
                         capture_output=True, text=True, check=True).stdout
    return [name for name in out.split(chr(0)) if name]


def test_no_tracked_file_contains_a_credential_shaped_literal():
    """THE GUARD THAT REPLACED A SCANNER CONFIG.

    The published client secret is real and it is required -- Google's
    token endpoint rejects this Desktop client without one. It is
    injected into a release bundle by `tools/build_release_bundle.py`
    and never committed, because GitHub's partner secret scanning reads
    public history out of band from anything this repo can configure:
    a credential pushed here would be reported to Google, and Google
    would decide whether it stays valid. History cannot be edited after
    the fact, so the only durable protection is that one never lands.

    This is that protection, and it is why `.gitleaks.toml` is gone --
    there is no longer any exception to make, only an absence to hold.
    Reports paths and counts; never a value.
    """
    offenders = {}
    for name in _tracked_files():
        path = REPO_ROOT / name
        try:
            blob = path.read_bytes()
        except OSError:
            continue
        count = len(_SECRET_LITERAL.findall(
            blob.decode('utf-8', errors='ignore')))
        if count:
            offenders[name] = count

    assert offenders == {}, (
        f'credential-shaped literals are tracked in: {sorted(offenders)}'
    )


def test_the_tracked_descriptor_ships_no_identity():
    """A plain clone is SUPPOSED to be credential-free, and to say so
    when asked to authorize rather than send anyone to Google Cloud."""
    fields = poc.BUNDLED_PUBLIC_CLIENT[poc.INSTALLED_KEY]

    assert fields['client_id'] == ''
    assert fields['client_secret'] == ''
    assert not poc.is_bundled_client_usable()

    with pytest.raises(RuntimeError) as exc:
        poc.bundled_client_config()
    assert 'packaging problem' in str(exc.value)
    assert 'do not need a Google Cloud project' in str(exc.value)
