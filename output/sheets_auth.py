"""
output/sheets_auth.py

OAuth profiles for the Google surfaces. One place that decides WHICH
scopes are requested, WHICH client config is used, and WHERE the
resulting grant is cached.

Two profiles exist, and they are deliberately not interchangeable:

  maintainer -- Kyle's own long-standing grant. Requests the sensitive
      `spreadsheets` scope and opens EXISTING workbooks by id. The
      standing dev/prod almanac books were created by hand, outside any
      app, so a `drive.file` client can never see them: an app only ever
      sees files it created or the user explicitly handed it through the
      Google picker. Narrowing this profile would strand those books.

  public -- the profile a stranger running the published tool would use.
      Requests ONLY `drive.file`, which Google classifies as
      non-sensitive: it authorizes creating a spreadsheet, writing to it,
      and setting its sharing permission, all scoped to files this app
      itself created. It cannot enumerate or read the rest of the Drive.
      This is the profile whose consent screen MLB-209 exists to measure.

The separation is enforced by construction rather than by care: distinct
scopes, distinct client-config env vars, distinct token caches. Selecting
the public profile cannot read, refresh, or overwrite the maintainer's
cached grant, because it never names that file.

A note on why the scope check reads the token FILE rather than the
credential object. `Credentials.from_authorized_user_file(path, scopes)`
STAMPS the scopes you pass onto the object it returns -- it does not
check them against what Google actually granted. Pass `['drive.file']`
while loading a cache that only ever held a `spreadsheets` grant and you
get a credential that claims `drive.file` and 403s on the first Drive
call. The granted set lives in the file's own `scopes` key, so that is
what gets compared.

Where the CLIENT comes from is the third axis, and the two profiles
differ there too. The maintainer profile is env-only: it names its own
env var and there is no fallback, so it can never end up consenting as
anybody else. The public profile prefers an explicit env override when
one is set -- maintainers and tests need that -- and otherwise uses the
identity this build SHIPS (`public_oauth_client`), because a stranger
running the published tool must not have to make a Google Cloud project
to be allowed to create their own spreadsheet.

No secret ever reaches stdout here. Errors name the env var and the path
they expected; they never echo client JSON, tokens, or account
addresses.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

import gspread
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

import public_oauth_client


_OUTPUT_DIR = Path(__file__).resolve().parent

SPREADSHEETS_SCOPE = 'https://www.googleapis.com/auth/spreadsheets'
DRIVE_FILE_SCOPE = 'https://www.googleapis.com/auth/drive.file'


@dataclass(frozen=True)
class Profile:
    """One OAuth identity: what it asks for, which client asks, and where
    the answer is cached.

    `exact_scopes` decides how a cached grant is judged. The maintainer
    profile is satisfied by any grant that CONTAINS its scopes (the
    pre-existing, permissive reading -- a token that also carries some
    older scope still works). The public profile demands an EXACT match,
    because the whole point of the measurement is that nothing broader
    than `drive.file` was ever granted; silently accepting a wider token
    would make the measurement a lie.
    """

    name: str
    scopes: tuple
    client_env: str
    token_path: Path
    exact_scopes: bool
    summary: str
    bundled_client: object = None

    @property
    def scope_list(self):
        """A fresh mutable list, the shape the Google libraries want."""
        return list(self.scopes)


MAINTAINER = Profile(
    name='maintainer',
    scopes=(SPREADSHEETS_SCOPE,),
    client_env='GOOGLE_OAUTH_CLIENT_PATH',
    token_path=_OUTPUT_DIR / '.sheets_oauth_token.json',
    exact_scopes=False,
    summary="the maintainer's own grant; opens existing workbooks by id",
    # Env-only, permanently. `None` here is the whole enforcement: the
    # resolver has no other source to reach for, so no edit to the public
    # side can make the maintainer profile consent as the shipped app.
    bundled_client=None,
)

PUBLIC = Profile(
    name='public',
    scopes=(DRIVE_FILE_SCOPE,),
    client_env='GOOGLE_PUBLIC_OAUTH_CLIENT_PATH',
    token_path=_OUTPUT_DIR / '.sheets_public_oauth_token.json',
    exact_scopes=True,
    summary='the published tool; creates and shares its own workbooks only',
    # A callable rather than the mapping itself, so the descriptor is
    # validated at the moment it is needed (and can be substituted in a
    # test) instead of being frozen into this module at import.
    bundled_client=public_oauth_client.bundled_client_config,
)

PROFILES = {p.name: p for p in (MAINTAINER, PUBLIC)}

# Guard the invariant the whole design rests on. If a future edit ever
# points two profiles at one cache, the import fails here rather than at
# the moment one grant silently overwrites the other.
assert len({p.token_path for p in PROFILES.values()}) == len(PROFILES), (
    'OAuth profiles must not share a token cache'
)
assert len({p.client_env for p in PROFILES.values()}) == len(PROFILES), (
    'OAuth profiles must not share a client config'
)
assert MAINTAINER.bundled_client is None, (
    'the maintainer profile must stay env-only and must never be able to '
    'fall through to the published identity'
)


def get_profile(name):
    """Look up a profile by name, failing with the valid names listed."""
    try:
        return PROFILES[name]
    except KeyError:
        known = ', '.join(sorted(PROFILES))
        raise RuntimeError(
            f"Unknown OAuth profile {name!r}. Known profiles: {known}."
        ) from None


def granted_scopes(token_path):
    """The scopes Google actually granted, read from the cache file.

    Returns an empty frozenset for a missing, unreadable, or malformed
    file -- every one of which means "we do not know what was granted",
    which must fail the check rather than pass it.
    """
    path = Path(token_path)
    if not path.exists():
        return frozenset()
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return frozenset()
    if not isinstance(payload, dict):
        return frozenset()
    scopes = payload.get('scopes')
    if isinstance(scopes, str):
        scopes = scopes.split()
    if not isinstance(scopes, (list, tuple)):
        return frozenset()
    return frozenset(str(s) for s in scopes)


def token_satisfies(profile, granted):
    """Does this granted scope set satisfy the profile?"""
    granted = frozenset(granted)
    if not granted:
        return False
    required = frozenset(profile.scopes)
    if profile.exact_scopes:
        return granted == required
    return required <= granted


def load_cached_credentials(profile):
    """Return cached credentials for `profile`, or None to force consent.

    None is returned -- rather than an exception -- for every "the cache
    cannot serve this profile" case, because the correct response to all
    of them is the same: run consent.
    """
    path = profile.token_path
    if not path.exists():
        return None

    granted = granted_scopes(path)
    if not token_satisfies(profile, granted):
        print(
            f"[sheets-auth] cached '{profile.name}' token does not carry the "
            f"scopes this profile requires; re-running consent"
        )
        return None

    try:
        return Credentials.from_authorized_user_file(
            str(path), profile.scope_list,
        )
    except (OSError, ValueError):
        print(
            f"[sheets-auth] cached '{profile.name}' token is unreadable; "
            f"re-running consent"
        )
        return None


def client_config_path(profile):
    """Resolve and validate the OAuth client JSON path for `profile`."""
    client_path = os.getenv(profile.client_env)
    if not client_path:
        raise RuntimeError(
            f"{profile.client_env} env var not set. It must point at the "
            f"OAuth desktop-client JSON for the '{profile.name}' profile "
            f"({profile.summary}). The file stays outside the repo."
        )
    if not Path(client_path).exists():
        raise RuntimeError(
            f"OAuth client config for the '{profile.name}' profile not found "
            f"at {client_path}. Check {profile.client_env} in .env."
        )
    return client_path


class BundledInstalledAppFlow(InstalledAppFlow):
    """The installed-app flow, with the client credentials in the BODY.

    One line of behavior, and it is the part that is easy to miss.

    Left alone, `requests_oauthlib.fetch_token` takes its "no auth
    supplied" branch and builds `HTTPBasicAuth(client_id, client_secret)`
    -- so the exchange goes out as `Authorization: Basic <base64 of
    id:secret>` with NO `client_id` in the body. That is a different wire
    shape from the one Google's Mobile & Desktop guide documents, and it
    moves the shipped secret into a header where a proxy, a log, or a
    header-dumping error handler is more likely to capture it than a form
    body would be.

    With `include_client_id=True`, `requests_oauthlib` skips the header
    and `prepare_request_body` puts `client_id` in the body, giving
    exactly Google's documented parameter set: `client_id`,
    `client_secret`, `code`, `code_verifier`, `grant_type`,
    `redirect_uri`. `tests/test_public_oauth_wire.py` asserts that at the
    adapter, on the bytes.

    PKCE rides along either way: `Flow.authorization_url` auto-generates
    a 128-character verifier and sends an S256 challenge, and
    `Flow.fetch_token` -- which this delegates to -- passes the verifier
    back. Google lists `code_verifier` as Required for this flow and the
    default satisfies it.

    A SUBCLASS RATHER THAN A KEYWORD AT THE CALL SITE because
    `run_local_server` calls `self.fetch_token(...)` itself and forwards
    its own `**kwargs` to `authorization_url`, not to the exchange. There
    is no seam to pass this through.

    A NOTE ON WHAT THIS IS NOT. An earlier revision of this class dropped
    the secret entirely and relied on PKCE alone, because Google's guide
    lists `client_secret` as Optional for installed apps. That was built
    and run live on 2026-08-11, and Google's token endpoint refused it
    after consent with `(invalid_request) client_secret is missing`. The
    accurate reading is narrow -- Google's token endpoint requires a
    client secret for THIS configured Desktop client -- and the code went
    back to sending one. Do not re-derive the secretless version from the
    documentation without re-running that probe; the documentation is not
    what decides it.
    """

    def fetch_token(self, **kwargs):
        kwargs.setdefault('include_client_id', True)
        return super().fetch_token(**kwargs)


@dataclass(frozen=True)
class ClientSource:
    """Which OAuth client this consent will run as, and where it came
    from.

    Two shapes, never both: a `path` to a client JSON on disk (the env
    override), or an in-memory `config` mapping (the identity this build
    ships). Kept as a small object rather than a bare string so the
    caller cannot accidentally treat a mapping as a path -- and so the
    CLI can say which one it is without re-deriving it.
    """

    kind: str            # 'env' | 'bundled'
    path: str = None
    config: dict = None


def resolve_client_source(profile):
    """Decide which client this profile consents as.

    PRECEDENCE, and each step is load-bearing:

      1. The env var, when SET. An explicit override wins, which is what
         keeps a maintainer or a test able to point at any client.
      2. A set-but-broken override is an ERROR, not a fallback. If the
         path does not exist, `client_config_path` raises and this stops.
         Silently substituting the shipped identity there would mean a
         developer who typo'd a path gets consented as the published app
         and never finds out.
      3. Otherwise the bundled identity -- for the profiles that HAVE
         one. The maintainer profile does not (`bundled_client is None`),
         so for it this reduces to today's env-only behavior, error
         message included.

    Everything that can fail here fails BEFORE any browser is launched.
    That is the point of resolving as a separate step: a descriptor
    problem should cost the user an error message, not a consent screen
    followed by a `KeyError`.
    """
    if os.getenv(profile.client_env) or profile.bundled_client is None:
        return ClientSource(kind='env', path=client_config_path(profile))
    return ClientSource(kind='bundled', config=profile.bundled_client())


def consent_disclosure(profile):
    """What this profile is about to ask the user for, in their words.

    Built FROM `profile.scopes` rather than written out beside them, so
    the sentence a stranger reads cannot drift away from the scope the
    code actually requests -- a disclosure that has to be kept in sync by
    hand is one edit from being false.
    """
    if profile.scopes == (DRIVE_FILE_SCOPE,):
        return (
            "Fantasy League Almanac is asking Google for per-file access "
            "(drive.file) so it can create this one workbook and write your "
            "almanac into it. It cannot list, open, or read anything else in "
            "your Google Drive -- only files it creates itself."
        )
    return (
        f"Fantasy League Almanac is asking Google for: "
        f"{', '.join(sorted(profile.scopes))}."
    )


def credential_scopes(creds):
    """The scopes a freshly returned credential actually carries.

    `granted_scopes` is what the token endpoint said it gave us, and it
    is the field to trust. It is populated only on some paths, so
    `scopes` -- the set that was REQUESTED -- is the deliberate fallback.
    The fallback is weaker on purpose and is why the cached-token check
    reads the token file instead: there, nothing is left to assume.

    `is None` rather than a truthiness test, and the difference matters:
    None means Google did not report a grant, which is the case the
    fallback exists for, while an empty collection means Google reported
    granting NOTHING. Truthiness conflates the two, and would answer "we
    were told nothing was granted" by substituting the scopes we asked
    for -- turning a refusal into an approval.
    """
    granted = getattr(creds, 'granted_scopes', None)
    if granted is not None:
        return frozenset(granted)
    return frozenset(getattr(creds, 'scopes', None) or ())


def run_consent_flow(profile):
    """Open a browser for a fresh consent and return new credentials.

    The grant is judged by the SAME rule a cached token is judged by,
    before it is handed back or written anywhere. Two ways this bites:

      - a user unchecks a permission on the consent screen, and the
        resulting token 403s on the first real call, several steps from
        the cause;
      - the flow comes back with MORE than was asked for (a previously
        granted scope carried forward on re-consent). For the public
        profile that is disqualifying rather than convenient -- an app
        running on a wider grant than the one being measured makes the
        measurement worthless, and caching it would make the next run
        inherit the lie.

    Raising here means the token cache is never written, so a refused
    grant leaves nothing behind to be picked up later.
    """
    source = resolve_client_source(profile)
    if source.kind == 'bundled':
        # Body-form client authentication for the shipped identity, so
        # the exchange matches Google's documented shape and the secret
        # never rides in a header. Deliberately NOT used for the env path
        # below, which stays byte-for-byte what maintainers already have.
        flow = BundledInstalledAppFlow.from_client_config(
            source.config, profile.scope_list,
        )
    else:
        # Unchanged for the env path. `from_client_secrets_file` is
        # `json.load` followed by `from_client_config`, so the two
        # branches converge immediately -- but going through the file
        # helper keeps the library owning how a downloaded client is
        # read, including any shape it learns to accept later.
        flow = InstalledAppFlow.from_client_secrets_file(
            source.path, profile.scope_list,
        )
    # port=0 picks an arbitrary free port for the redirect handler.
    creds = flow.run_local_server(port=0)

    granted = credential_scopes(creds)
    if not token_satisfies(profile, granted):
        required = frozenset(profile.scopes)
        missing = sorted(required - granted)
        extra = sorted(granted - required)
        detail = []
        if missing:
            detail.append(f"did not grant {missing}")
        if extra and profile.exact_scopes:
            detail.append(f"granted {extra} on top of what was requested")
        if not detail:
            detail.append('returned no usable scopes')

        message = (
            f"Consent for the '{profile.name}' profile "
            f"{' and '.join(detail)}. Nothing was cached."
        )
        if profile.exact_scopes:
            message += (
                f" This profile requires exactly {sorted(required)}. Re-run "
                f"with every requested permission left checked; if the extra "
                f"scope persists, revoke this app under the Google account's "
                f"security settings and consent again from clean."
            )
        else:
            message += ' Re-run and leave every requested permission checked.'
        raise RuntimeError(message)
    return creds


def save_credentials(profile, creds):
    """Cache a grant at the profile's own token path."""
    with open(profile.token_path, 'w') as f:
        f.write(creds.to_json())


def authorized_client(profile=MAINTAINER):
    """Return an authorized gspread client for `profile`.

    First run opens a browser for consent; later runs use the cached
    credentials and refresh transparently. When the cached refresh token
    has expired or been revoked (Google expires testing-mode tokens after
    ~7 days), fall back to a fresh consent flow instead of crashing the
    weekly run.
    """
    creds = load_cached_credentials(profile)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                # invalid_grant: refresh token expired/revoked -> re-consent
                # rather than hard-fail the run.
                creds = run_consent_flow(profile)
        else:
            creds = run_consent_flow(profile)
        save_credentials(profile, creds)

    return gspread.authorize(creds)
