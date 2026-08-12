"""The identity the published tool ships with (MLB-209).

A stranger must not need a Google Cloud project to render their own
league, so this repo carries Fantasy League Almanac's own Desktop OAuth
client. That is ordinary for an installed app -- Google's native-app
guidance says the client id and secret may be embedded in distributed
source -- and it introduces exactly one new way to be badly wrong:
shipping something that LOOKS like an identity and is not, or shipping
something that is not an identity at all but a person's granted access.

So the assertions here fall into three groups:

  - CLASS SEPARATION. A client descriptor identifies the app. A token is
    somebody's access to their own Drive. They arrive in similar-looking
    JSON, and confusing them is the only genuinely dangerous mistake
    available here.
  - NO PLACEHOLDER SHIPS. A blank or `PASTE_HERE` descriptor would let
    the docs claim "no OAuth client needed" over a build that cannot
    authorize anything. It has to fail loudly rather than plausibly.
  - NO VALUE EVER REACHES A MESSAGE. Errors about the descriptor travel
    into logs, tracebacks and bug reports. They may name fields; they may
    not quote them.

Account-free and fully synthetic. Every descriptor below is made up.
"""
from __future__ import annotations

import pytest

import public_oauth_client as poc


# Deliberately NOT in Google's client-secret format, so it doubles as the
# malformed-secret fixture and can never be mistaken for a real value.
CANARY = 'GOCSPX-CANARY-VALUE-DO-NOT-PRINT'

def synthetic_google_secret(tag):
    """A synthetic secret in Google's exact client-secret shape.

    Padded programmatically because hand-counting the 28-character body
    is a mistake that has now been made three times in this work, and it
    fails as a confusing validation error rather than an obvious typo.
    """
    return 'GOCSPX-' + (tag + '0' * 28)[:28]


SYNTHETIC_SECRET = synthetic_google_secret('SYNTHETICdescriptor')


def _synthetic(**overrides):
    """A well-formed descriptor made of obviously fake values."""
    fields = {
        'client_id': '000000000000-synthetic.apps.googleusercontent.com',
        'client_secret': SYNTHETIC_SECRET,
        'auth_uri': poc.GOOGLE_AUTH_URI,
        'token_uri': poc.GOOGLE_TOKEN_URI,
    }
    fields.update(overrides)
    return {'installed': fields}


# ---------------------------------------------------------------------------
# The happy shape
# ---------------------------------------------------------------------------

def test_a_well_formed_installed_descriptor_is_accepted():
    assert poc.describe_problem(_synthetic()) is None


def test_the_accepted_shape_is_the_one_the_pinned_flow_actually_takes():
    """Not asserted from the docs -- asserted against the library that
    ships in this venv, because the required-key set is an implementation
    detail of google-auth-oauthlib and could move under a bump."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_config(
        _synthetic(), [poc.GOOGLE_TOKEN_URI])          # scope value is irrelevant
    assert flow.client_type == 'installed'


# ---------------------------------------------------------------------------
# A client descriptor is not a credential
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('field', poc.TOKEN_FIELDS)
def test_a_descriptor_carrying_token_material_is_refused(field):
    """THE dangerous confusion. A token cache also has a `client_id` in
    it, so 'it looks about right' is not a check. Shipping one would
    publish a real person's granted access to their own Drive."""
    problem = poc.describe_problem(_synthetic(**{field: 'whatever'}))

    assert problem is not None
    assert 'token material' in problem
    assert field in problem


def test_the_bundled_descriptor_carries_no_token_material():
    fields = poc.BUNDLED_PUBLIC_CLIENT[poc.INSTALLED_KEY]
    for forbidden in poc.TOKEN_FIELDS:
        assert forbidden not in fields, forbidden


def test_the_bundled_descriptor_carries_nothing_but_the_four_needed_fields():
    """A downloaded Desktop client also carries `project_id`,
    `auth_provider_x509_cert_url` and `redirect_uris`. This flow uses
    none of them, and `project_id` in particular would publish which
    Cloud project the identity came from for no benefit at all."""
    assert set(poc.BUNDLED_PUBLIC_CLIENT) == {poc.INSTALLED_KEY}
    assert set(poc.BUNDLED_PUBLIC_CLIENT[poc.INSTALLED_KEY]) == set(
        poc.REQUIRED_FIELDS)
    assert set(poc.REQUIRED_FIELDS) == {
        'client_id', 'client_secret', 'auth_uri', 'token_uri'}


def test_the_bundled_descriptor_points_at_googles_own_endpoints():
    fields = poc.BUNDLED_PUBLIC_CLIENT[poc.INSTALLED_KEY]
    assert fields['auth_uri'] == 'https://accounts.google.com/o/oauth2/auth'
    assert fields['token_uri'] == 'https://oauth2.googleapis.com/token'


# ---------------------------------------------------------------------------
# Nothing half-built gets through
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('field', poc.REQUIRED_FIELDS)
@pytest.mark.parametrize('bad', ['', '   ', None])
def test_every_required_field_must_be_a_non_blank_string(field, bad):
    problem = poc.describe_problem(_synthetic(**{field: bad}))

    assert problem is not None
    assert field in problem


def test_a_client_secret_not_in_googles_format_is_refused():
    """Blank is not the only way to ship a non-identity. Google's client
    secrets have one shape; anything else here is a placeholder, a
    truncation, or a paste from the wrong field."""
    for bad in (CANARY, 'GOCSPX-tooshort', 'not-a-secret-at-all'):
        problem = poc.describe_problem(_synthetic(client_secret=bad))
        assert problem is not None, bad
        assert 'client_secret' in problem
        assert bad not in problem


def test_the_descriptor_must_carry_a_secret_or_the_browser_opens_for_nothing():
    """WHY `client_secret` IS REQUIRED HERE, measured rather than assumed.

    It is NOT in google-auth-oauthlib's `_REQUIRED_CONFIG_KEYS`, so the
    library builds a flow from a descriptor without one quite happily --
    and then `Flow.fetch_token` evaluates
    `self.client_config["client_secret"]` as an ARGUMENT to `setdefault`,
    so the subscript raises `KeyError` before setdefault can decline to
    use it. By then the browser has opened and the user has consented.

    Requiring it in `describe_problem` turns that into an error message
    before anything launches. This test fails if a library bump ever
    makes the descriptor field unnecessary -- at which point the
    secretless route is worth re-probing against Google.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    secretless = _synthetic()
    del secretless['installed']['client_secret']

    flow = InstalledAppFlow.from_client_config(secretless, ['scope'])
    with pytest.raises(KeyError, match='client_secret'):
        flow.fetch_token(code='irrelevant')

    assert 'client_secret' in poc.describe_problem(secretless)


def test_a_placeholder_client_id_is_refused():
    """Blank is not the only way to ship a non-identity. Every Google
    client id ends in the same suffix, so anything that does not is a
    stand-in somebody forgot to replace."""
    for placeholder in ('PASTE_HERE', 'TODO', 'client-id'):
        problem = poc.describe_problem(_synthetic(client_id=placeholder))
        assert problem is not None
        assert 'client_id' in problem


@pytest.mark.parametrize('field', ['auth_uri', 'token_uri'])
def test_a_non_https_endpoint_is_refused(field):
    problem = poc.describe_problem(_synthetic(**{field: 'http://example.test'}))
    assert problem is not None and field in problem


def test_a_web_client_is_named_as_the_wrong_client_type():
    """A web client needs a registered redirect and a callback host --
    the hosted service this journey deliberately does not have. Saying
    'wrong type' beats failing later on a redirect mismatch."""
    problem = poc.describe_problem({'web': _synthetic()['installed']})

    assert problem is not None
    assert 'web' in problem and 'Desktop' in problem


@pytest.mark.parametrize('config', [
    None, [], 'a string', 42, {}, {'installed': None}, {'installed': 'nope'},
])
def test_structurally_wrong_descriptors_are_refused(config):
    assert poc.describe_problem(config) is not None


# ---------------------------------------------------------------------------
# Errors name fields, never values
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('config', [
    _synthetic(client_id=''),
    _synthetic(client_id='PASTE_HERE'),
    _synthetic(auth_uri='http://evil.test'),
    _synthetic(refresh_token=CANARY),
    _synthetic(client_secret=CANARY),
    {'web': _synthetic()['installed']},
])
def test_no_problem_message_ever_quotes_a_value(config):
    """These sentences end up in logs, tracebacks and pasted bug reports.
    Naming the field that is wrong is the job; printing what is in it is
    how a client secret gets published by accident."""
    problem = poc.describe_problem(config) or ''
    assert CANARY not in problem
    assert 'PASTE_HERE' not in problem
    assert 'evil.test' not in problem


def test_the_packaging_error_blames_packaging_and_not_the_user():
    """A stranger who sees this must not go and make a Google Cloud
    project -- removing that requirement is the entire ticket."""
    message = poc.PACKAGING_ERROR.format(problem='...')

    assert 'packaging problem' in message
    assert 'do not need a Google Cloud project' in message
    assert 'GOOGLE_PUBLIC_OAUTH_CLIENT_PATH' in message


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def test_bundled_client_config_hands_back_a_copy(monkeypatch):
    """A caller passes this straight into a Google library. If that
    library (or a caller) mutates it, the next consent in the same
    process must not inherit the damage."""
    monkeypatch.setattr(poc, 'BUNDLED_PUBLIC_CLIENT', _synthetic())

    first = poc.bundled_client_config()
    first['installed']['client_id'] = 'MUTATED'

    assert poc.bundled_client_config()['installed']['client_id'] != 'MUTATED'


def test_an_unusable_bundled_descriptor_raises_the_packaging_error(monkeypatch):
    monkeypatch.setattr(poc, 'BUNDLED_PUBLIC_CLIENT', _synthetic(client_id=''))

    assert not poc.is_bundled_client_usable()
    with pytest.raises(RuntimeError, match='packaging problem'):
        poc.bundled_client_config()


# ---------------------------------------------------------------------------
# The gate -- inverted, and the inversion is the design
# ---------------------------------------------------------------------------

def test_the_TRACKED_descriptor_ships_no_identity():
    """THE ONE THAT KEEPS THE CREDENTIAL OUT OF HISTORY.

    An earlier revision asserted the opposite -- that a clone receives a
    working identity -- because the plan was to commit the credential.
    That plan died on an external constraint: GitHub's partner secret
    scanning reads public history independently of anything this repo can
    configure, reports supported credentials to Google, and leaves Google
    holding the decision about whether the credential stays valid. Git
    history cannot be edited afterwards, so the credential must never
    arrive.

    It is injected into a release bundle instead
    (`tools/build_release_bundle.py`). A clone is the DEVELOPER path and
    is supposed to be credential-free; consumers download the bundle.

    So a populated descriptor in tracked source is now the failure, and
    this is what says so.
    """
    fields = poc.BUNDLED_PUBLIC_CLIENT[poc.INSTALLED_KEY]

    assert fields['client_id'] == ''
    assert fields['client_secret'] == ''
    assert not poc.is_bundled_client_usable()


def test_a_credential_free_clone_fails_with_a_packaging_message():
    """And fails HONESTLY. The error a developer hits must not send them
    to Google Cloud to make a client -- removing that requirement is the
    entire ticket, and the answer is the release bundle or the documented
    env override."""
    with pytest.raises(RuntimeError) as exc:
        poc.bundled_client_config()

    message = str(exc.value)
    assert 'packaging problem' in message
    assert 'do not need a Google Cloud project' in message
    assert 'GOOGLE_PUBLIC_OAUTH_CLIENT_PATH' in message
