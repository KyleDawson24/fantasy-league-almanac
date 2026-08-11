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
)

PUBLIC = Profile(
    name='public',
    scopes=(DRIVE_FILE_SCOPE,),
    client_env='GOOGLE_PUBLIC_OAUTH_CLIENT_PATH',
    token_path=_OUTPUT_DIR / '.sheets_public_oauth_token.json',
    exact_scopes=True,
    summary='the published tool; creates and shares its own workbooks only',
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
    client_path = client_config_path(profile)
    flow = InstalledAppFlow.from_client_secrets_file(
        client_path, profile.scope_list,
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
