"""
output/public_oauth_client.py

The project-owned OAuth identity the PUBLISHED tool consents under
(MLB-209).

WHY THIS FILE EXISTS. Before it, running the stranger path meant
creating a Google Cloud project, enabling APIs, configuring a consent
screen, downloading a Desktop OAuth client, and pointing
`GOOGLE_PUBLIC_OAUTH_CLIENT_PATH` at it -- five console steps before the
first spreadsheet. That is not a setup instruction, it is a wall. The
published tool ships its own identity instead, which is what every other
installed Google app does.

WHY THE SECRET SHIPS, AND WHAT WAS ACTUALLY MEASURED. Google's OAuth 2.0
for Mobile & Desktop Apps guide lists `client_secret` as Optional on the
authorization-code exchange, and lists `code_verifier` as Required. A
PKCE-only exchange was therefore built and run live on 2026-08-11 --
client id and code verifier, no secret, no Authorization header. Google's
token endpoint refused it, after consent, with:

    InvalidClientIdError: (invalid_request) client_secret is missing.

The narrow and accurate statement of that result: GOOGLE'S TOKEN ENDPOINT
REQUIRES A CLIENT SECRET FOR THIS CONFIGURED DESKTOP CLIENT. It is not a
claim that the documentation is wrong in general, nor that every
installed-app client behaves this way -- other client types and other
configurations were not tested. What follows from it is only this: for
the client this project ships, the secret has to travel with it.

So it does, deliberately, and Google's installed-app guidance accepts
that a native app cannot keep client credentials confidential. Three
things make that a contained decision rather than a resigned one:

  - the client lives in a Cloud project created for nothing else. It is
    not the maintainer's project and not the maintainer's client, so
    abuse of it cannot reach the maintainer's `spreadsheets` grant or the
    standing dev/prod workbooks;
  - it requests only `drive.file`, so the worst a consenting user can
    grant it is per-file access to files it creates itself;
  - it is rotatable. Rotation costs strangers a re-consent and costs the
    maintainer nothing.

BUT IT DOES NOT SHIP IN GIT. "An installed app may embed its client
credentials" is a statement about DISTRIBUTED BINARIES, and a public git
repository is not one. GitHub's partner secret scanning reports supported
credentials found in public history straight to Google, out of band from
any scanner config this repo could carry and out of band from a
push-protection bypass -- so the credential would be Google's to revoke,
on Google's schedule, with the repo unable to opt out. The two values
below therefore stay EMPTY in source and are injected into a temporary
tree by `tools/build_release_bundle.py` at release time. Consumers
download that bundle; a clone is the developer path.

What none of this covers is TOKENS. A client identifies the APP; a token
is somebody's granted access to their own Drive. This file holds the
first and refuses to hold the second -- `describe_problem` rejects a
descriptor carrying token material outright, so the invariant is enforced
at load rather than remembered.

NOT YET PUBLICLY USABLE. The project is External and in Google's TESTING
mode. Only accounts added as test users can complete consent, and their
refresh tokens expire after about a week. Moving it to Production needs
homepage, privacy-policy, terms and branding work that is a separate
release gate (MLB-241/242). Nothing here should be described as ready for
strangers until that lands.

WHY IT IS PYTHON AND NOT JSON, which looks like the obvious shape for a
thing Google hands you as a .json file:

  1. `.gitignore` has a blanket `*.json` with four narrow re-includes
     (`dbt_league/**`, `config/`, `archive/`, `tests/fixtures/`). A
     descriptor written to `output/public_oauth_client.json` is ignored
     -- `git add` refuses it without `-f`, and a clone never receives it.
     That is the exact false-complete failure this ticket had to avoid:
     docs saying "no client file needed" over a repo that ships no
     client. A `.py` file beside `sheets_auth.py` is tracked normally.
  2. There is no packaging shape to lean on. No setup.py, no
     pyproject.toml, no MANIFEST.in -- this project is distributed as a
     source checkout, so `package_data` is not a mechanism that exists
     here. Modules in `output/` are, definitionally, what ships.
  3. A mapping is directly importable and testable, and the validation
     below lives next to the thing it validates.

WHAT THE FIELDS ARE. Exactly the four the pinned flow consumes, and no
more. Measured against google-auth-oauthlib 1.3.1 rather than assumed:

  - `client_id`, `auth_uri`, `token_uri` are `_REQUIRED_CONFIG_KEYS` in
    `google_auth_oauthlib.helpers.session_from_client_config`; a config
    missing any of them raises "Client secrets is not in the correct
    format" when the flow is CONSTRUCTED, before a browser can open.
  - `client_secret` is NOT in that set, and is the trap. `Flow.fetch_token`
    reads `self.client_config["client_secret"]` unconditionally, so a
    descriptor without it builds a flow happily, OPENS THE BROWSER, takes
    the user through consent, and dies on a bare `KeyError` on the way
    back. `describe_problem` requires it for that reason, and the
    resolver calls this before anything can launch a browser.

A downloaded Desktop client JSON also carries `project_id`,
`auth_provider_x509_cert_url` and `redirect_uris`. None is used by this
flow: `run_local_server` supplies its own redirect, and the other two are
console metadata that would only publish which Cloud project the identity
came from. They are deliberately not copied.
"""

import copy
import re


INSTALLED_KEY = 'installed'

# The four fields the pinned installed-app flow actually consumes.
REQUIRED_FIELDS = ('client_id', 'client_secret', 'auth_uri', 'token_uri')

# Token material, named so it can be REFUSED. A client descriptor and a
# cached grant are different classes of thing, and the only reason they
# are ever confused is that both arrive as JSON with a `client_id` and a
# `client_secret` in them. If one of these keys is present, whatever this
# is, it is not a client descriptor and must not be committed.
TOKEN_FIELDS = ('token', 'access_token', 'refresh_token', 'id_token',
                'expiry', 'account')

# Google's own endpoints, spelled out rather than copied from a download
# so the two constants have a single source.
GOOGLE_AUTH_URI = 'https://accounts.google.com/o/oauth2/auth'
GOOGLE_TOKEN_URI = 'https://oauth2.googleapis.com/token'

# Every Google OAuth client id ends this way. Checking it is not
# pedantry: it is what stops a placeholder ('PASTE_HERE', '', 'TODO')
# from shipping as though it were an identity, which is the specific way
# this file could quietly become a lie.
CLIENT_ID_SUFFIX = '.apps.googleusercontent.com'

# The Google OAuth client-secret FORMAT -- anchored and length-exact.
# Used for the same reason as the suffix above: it is what the release
# builder validates an injected value against, so a truncated paste or a
# placeholder cannot ship as though it were a credential. If Google ever
# changes the format this fails loudly rather than admitting something
# unrecognised.
#
# A PATTERN, NOT A VALUE. It does not match itself -- the bracket
# expression is not in its own character class -- so this constant is not
# a Google-shaped literal and no secret scanner sees a credential here.
# `tests/test_public_oauth_client.py` asserts the whole tracked tree
# contains no such literal.
GOOGLE_SECRET_SHAPE = re.compile(r'^GOCSPX-[A-Za-z0-9_-]{28}$')


# ---------------------------------------------------------------------------
# The bundled identity
# ---------------------------------------------------------------------------
#
# EMPTY IN SOURCE, ON PURPOSE, AND THAT IS THE WHOLE DISTRIBUTION MODEL.
#
# `tools/build_release_bundle.py` fills these two lines in a TEMPORARY
# tree built from a git ref, zips the result, and throws the tree away.
# The credential exists in the released archive and in the maintainer's
# own `.gcp` folder. It is never committed, so it never enters git
# history, where nothing could remove it after the fact.
#
# The reason is specific rather than general caution. GitHub's partner
# secret scanning runs on public repositories independently of any
# scanner config in the repo and independently of a push-protection
# bypass. A supported credential pushed to public history is reported
# to Google directly, and Google -- not this project -- then decides
# whether it stays valid. A scanner exemption cannot opt out of that,
# because it is not GitHub's scanner being exempted. So the credential
# does not go in the repository at all.
#
# UNPOPULATED IS THE FAIL-CLOSED STATE, not a soft default, and it is the
# state a plain clone is SUPPOSED to be in. `describe_problem` reports the
# missing fields, `bundled_client_config` raises, and the stranger path
# exits nonzero BEFORE any browser opens. It must never degrade into "ask
# the user for their own client file", because that is the wall this
# ticket exists to remove: a source checkout is the developer path, and a
# consumer is meant to download the prepared release bundle instead.
#
# Developers who need the live path from a checkout set
# GOOGLE_PUBLIC_OAUTH_CLIENT_PATH, which overrides this entirely.
BUNDLED_PUBLIC_CLIENT = {
    INSTALLED_KEY: {
        'client_id': '',
        'client_secret': '',
        'auth_uri': GOOGLE_AUTH_URI,
        'token_uri': GOOGLE_TOKEN_URI,
    },
}


PACKAGING_ERROR = (
    "This build did not ship a usable Fantasy League Almanac OAuth identity "
    "({problem}). This is a packaging problem with the copy of the tool you "
    "are running, NOT something to fix in your Google account -- you do not "
    "need a Google Cloud project, an API to enable, or an OAuth client of "
    "your own. Re-install from a released copy, or report it. (Maintainers "
    "and testers can point GOOGLE_PUBLIC_OAUTH_CLIENT_PATH at a client JSON "
    "to override this.)"
)


def describe_problem(config):
    """Why this client descriptor is unusable, or None when it is fine.

    A sentence rather than a boolean, because the caller has to be able
    to say WHAT is wrong without the reader opening this file.

    NAMES ONLY, NEVER VALUES. Every branch below reports field names and
    structure. None of them interpolates a value, so this function cannot
    leak a client secret into a log, a traceback, a test report, or a
    GitHub issue -- which is the whole reason the checks are here and not
    inline at the call site. `tests/test_public_oauth_client.py` asserts
    that with a canary.
    """
    if not isinstance(config, dict):
        return 'the descriptor is not a JSON object'

    if INSTALLED_KEY not in config:
        if 'web' in config:
            # A web client cannot run this flow: it needs a registered
            # redirect and a real callback host, which is precisely the
            # hosted service this journey does not have.
            return ("the descriptor is a 'web' OAuth client; the published "
                    "tool needs an 'installed' (Desktop app) client")
        return f"the descriptor has no {INSTALLED_KEY!r} section"

    fields = config[INSTALLED_KEY]
    if not isinstance(fields, dict):
        return f"the {INSTALLED_KEY!r} section is not a JSON object"

    carried = sorted(f for f in TOKEN_FIELDS if f in fields)
    if carried:
        # Deliberately loud. Reaching here means somebody pasted a token
        # cache where a client descriptor goes, and shipping that would
        # publish a real person's granted access to their own Drive.
        return (f"the descriptor carries token material ({', '.join(carried)}); "
                f"a client descriptor is not a credential and must never "
                f"contain one")

    missing = [f for f in REQUIRED_FIELDS
               if not isinstance(fields.get(f), str) or not fields[f].strip()]
    if missing:
        return f"required field(s) not set: {', '.join(missing)}"

    if not fields['client_id'].endswith(CLIENT_ID_SUFFIX):
        return f"client_id does not look like a Google client id (no "\
               f"{CLIENT_ID_SUFFIX} suffix)"

    if not GOOGLE_SECRET_SHAPE.match(fields['client_secret']):
        return ('client_secret is not in Google\'s client-secret format '
                '(GOCSPX- followed by 28 characters)')

    for field in ('auth_uri', 'token_uri'):
        if not fields[field].startswith('https://'):
            return f'{field} is not an https URL'

    return None


def is_bundled_client_usable():
    """Did this build actually ship an identity?"""
    return describe_problem(BUNDLED_PUBLIC_CLIENT) is None


def bundled_client_config():
    """The bundled descriptor, validated, in the shape the flow wants.

    A deep copy, so a caller that hands it to a Google library cannot
    mutate the module-level constant for the rest of the process.
    """
    problem = describe_problem(BUNDLED_PUBLIC_CLIENT)
    if problem:
        raise RuntimeError(PACKAGING_ERROR.format(problem=problem))
    return copy.deepcopy(BUNDLED_PUBLIC_CLIENT)
