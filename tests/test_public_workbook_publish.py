"""The app-created workbook lifecycle (MLB-209).

The v1.9/2.0 journey ends on one line -- `Your almanac: <link> --
share-ready.` -- and that line is a promise about three separate things
having happened: a workbook exists, the almanac is in it, and a stranger
holding the link can open it. Any of the three can fail on its own, so
the interesting tests here are the ones where SOME of it worked:

  - Drive refuses the sharing call (a Workspace policy). The workbook is
    the user's and is intact, so it is not deleted and its URL is still
    printed -- but never under a word claiming anyone else can read it.
  - The render dies. Sharing must not have happened, because a public
    link over a half-written book is worse than no link.
  - The user re-runs after either. That must RESUME the workbook the last
    run created, not add a second one to their Drive.

Account-free and fully synthetic: a fake gspread client records calls, no
Google client library reaches the network, no real workbook exists.
"""
from __future__ import annotations

import json

import gspread
import pytest
import requests

import generate_almanac_sheet as gas
import sheets_workbook


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeSpreadsheet:
    def __init__(self, spreadsheet_id, title):
        self.id = spreadsheet_id
        self.title = title

    @property
    def url(self):
        return f'https://docs.google.com/spreadsheets/d/{self.id}/edit'


def _encode(payload):
    """`json` is shadowed by the request kwarg inside the fakes, so
    encoding lives out here."""
    return json.dumps(payload).encode('utf-8')


def _permission_response(permissions):
    """A Drive permissions.list body, as a real requests.Response."""
    response = requests.Response()
    response.status_code = 200
    response._content = _encode({'permissions': permissions})
    return response


# What Drive returns for a correctly shared, app-created workbook: the
# owner, plus one link-only reader.
LINK_VIEWER_PERMISSIONS = [
    {'id': 'owner-1', 'type': 'user', 'role': 'owner'},
    {'id': 'anyone-1', 'type': 'anyone', 'role': 'reader',
     'allowFileDiscovery': False},
]


class _FakeHTTPClient:
    """Stands in for gspread's authorized session.

    Deliberately the ONLY sharing seam these tests offer. An earlier
    version of this file faked `client.insert_permission` instead, and
    that is exactly what let the pinned library's wire-format mismatch
    (a Drive v2 `withLink` posted to a v3 endpoint) sail through a full
    green suite: mocking the helper meant nothing ever looked at the
    bytes. Assert on the request, not on the convenience wrapper.
    """

    def __init__(self, owner):
        self._owner = owner

    def request(self, method, url, params=None, json=None, **kwargs):
        owner = self._owner
        owner.http_calls.append({
            'method': method, 'url': url,
            'params': dict(params or {}), 'body': json,
        })
        if method == 'post':
            owner.calls.append(('share', url))
            if owner.share_raises is not None:
                raise owner.share_raises
            owner.stored_permissions = list(owner.permissions_after_share)
            return _permission_response([])
        return _permission_response(owner.stored_permissions)


class _FakeClient:
    """Records what would have been asked of Drive."""

    def __init__(self, share_raises=None, permissions_after_share=None):
        self.created = []
        self.deleted = []
        self.calls = []
        self.http_calls = []
        self.stored_permissions = []
        self.permissions_after_share = (
            LINK_VIEWER_PERMISSIONS if permissions_after_share is None
            else permissions_after_share)
        self.share_raises = share_raises
        self._n = 0
        self.http_client = _FakeHTTPClient(self)

    def create(self, title, folder_id=None):
        self._n += 1
        spreadsheet_id = f'sheet-{self._n}'
        self.created.append(title)
        self.calls.append(('create', title))
        return _FakeSpreadsheet(spreadsheet_id, title)

    def insert_permission(self, *args, **kwargs):
        raise AssertionError(
            'sharing went through gspread insert_permission, which posts the '
            'Drive v2 field `withLink` to a v3 endpoint'
        )

    def del_spreadsheet(self, file_id):          # must never be reached
        self.deleted.append(file_id)

    # -- conveniences over the recorded traffic -----------------------
    @property
    def share_posts(self):
        return [c for c in self.http_calls if c['method'] == 'post']

    @property
    def permission_reads(self):
        return [(c['method'], c['url'], c['params'])
                for c in self.http_calls if c['method'] == 'get']


def _api_error(code, message, reason=None):
    """A real gspread APIError, built from a synthetic Drive response."""
    response = requests.Response()
    response.status_code = code
    body = {'error': {
        'code': code,
        'message': message,
        'errors': ([{'reason': reason, 'message': message}] if reason else []),
    }}
    response._content = json.dumps(body).encode('utf-8')
    return gspread.exceptions.APIError(response)


_POLICY_ERROR = _api_error(
    403, 'Sharing is not permitted for this account.',
    reason='shareOutNotPermitted')


@pytest.fixture
def ledger(tmp_path):
    return sheets_workbook.WorkbookLedger(tmp_path / 'workbooks.json')


def _recording_render(log):
    def _render(spreadsheet_id):
        log.append(('render', spreadsheet_id))
    return _render


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------

def test_creation_passes_the_title_through_and_returns_id_and_url(ledger):
    client = _FakeClient()

    result = sheets_workbook.publish_workbook(
        client, 'Fantasy League Almanac', lambda sid: None, ledger=ledger)

    assert client.created == ['Fantasy League Almanac']
    assert result.spreadsheet_id == 'sheet-1'
    assert result.url.endswith('/sheet-1/edit')
    assert result.title == 'Fantasy League Almanac'
    assert result.created is True


def test_the_happy_path_is_share_ready(ledger):
    client = _FakeClient()
    result = sheets_workbook.publish_workbook(
        client, 'Almanac', lambda sid: None, ledger=ledger)

    assert (result.created, result.rendered, result.shared) == (True,) * 3
    assert result.is_share_ready is True
    assert result.share_error is None


# ---------------------------------------------------------------------------
# Sharing
# ---------------------------------------------------------------------------

def test_the_public_permission_is_viewer_and_never_writer(ledger):
    client = _FakeClient()
    sheets_workbook.publish_workbook(
        client, 'Almanac', lambda sid: None, ledger=ledger)

    assert len(client.share_posts) == 1
    body = client.share_posts[0]['body']
    assert body['type'] == 'anyone'
    assert body['role'] == 'reader'
    assert body['role'] not in ('writer', 'owner', 'commenter')


def test_the_sharing_request_is_a_drive_v3_permissions_post(ledger):
    """Method, endpoint and params, not just the payload."""
    client = _FakeClient()
    sheets_workbook.publish_workbook(
        client, 'Almanac', lambda sid: None, ledger=ledger)

    post = client.share_posts[0]
    assert post['method'] == 'post'
    assert post['url'] == (
        'https://www.googleapis.com/drive/v3/files/sheet-1/permissions')
    assert post['params'] == {'supportsAllDrives': True}


def test_link_only_is_sent_as_the_drive_v3_field(ledger):
    """`allowFileDiscovery: false` IS anyone-with-the-link. Anything
    else is either discoverable or unstated."""
    client = _FakeClient()
    sheets_workbook.publish_workbook(
        client, 'Almanac', lambda sid: None, ledger=ledger)

    body = client.share_posts[0]['body']
    assert body['allowFileDiscovery'] is False
    assert body == {'type': 'anyone', 'role': 'reader',
                    'allowFileDiscovery': False}


def test_the_obsolete_v2_link_field_is_never_sent(ledger):
    """THE regression. gspread 6.2.1's insert_permission posts
    `withLink` -- a Drive v2 field -- to a v3 endpoint that does not
    define it, so the one field controlling discoverability never got
    sent at all. Faking the helper hid this behind a green suite."""
    client = _FakeClient()
    sheets_workbook.publish_workbook(
        client, 'Almanac', lambda sid: None, ledger=ledger)

    body = client.share_posts[0]['body']
    assert 'withLink' not in body
    assert 'with_link' not in body


# ---------------------------------------------------------------------------
# Sharing is PROVED, not assumed from a call that did not raise
# ---------------------------------------------------------------------------

def test_the_permission_is_read_back_before_success_is_claimed(ledger):
    client = _FakeClient()
    result = sheets_workbook.publish_workbook(
        client, 'Almanac', lambda sid: None, ledger=ledger)

    assert client.permission_reads, 'nothing read the permission back'
    assert result.is_share_ready is True


def test_the_read_back_asks_drive_for_the_fields_it_needs_by_name(ledger):
    """gspread's own list_permissions asks for the default representation,
    which omits allowFileDiscovery -- verifying against that could never
    prove link-only."""
    client = _FakeClient()
    sheets_workbook.publish_workbook(
        client, 'Almanac', lambda sid: None, ledger=ledger)

    _, url, params = client.permission_reads[0]
    assert url.endswith('/sheet-1/permissions')
    assert 'allowFileDiscovery' in params['fields']
    assert 'role' in params['fields']


@pytest.mark.parametrize('permissions,expected_phrase', [
    ([], 'no anyone-with-the-link permission'),
    ([{'id': 'a', 'type': 'anyone', 'role': 'writer',
       'allowFileDiscovery': False}], "rather than 'reader'"),
    ([{'id': 'a', 'type': 'anyone', 'role': 'reader',
       'allowFileDiscovery': True}], 'discoverable'),
    ([{'id': 'a', 'type': 'anyone', 'role': 'reader'}],
     'did not report allowFileDiscovery'),
    ([{'id': 'a', 'type': 'anyone', 'allowFileDiscovery': False}],
     'did not report the role'),
    ([{'id': 'a', 'type': 'domain', 'role': 'reader',
       'allowFileDiscovery': False}], 'domain-wide'),
    ([{'id': 'a', 'type': 'anyone', 'role': 'reader',
       'allowFileDiscovery': False},
      {'id': 'b', 'type': 'anyone', 'role': 'reader',
       'allowFileDiscovery': False}], 'ambiguous'),
])
def test_anything_broader_different_or_unverifiable_fails_closed(
        ledger, permissions, expected_phrase):
    client = _FakeClient(permissions_after_share=permissions)
    result = sheets_workbook.publish_workbook(
        client, 'Almanac', lambda sid: None, ledger=ledger)

    assert result.shared is False
    assert result.is_share_ready is False
    assert expected_phrase in result.share_error
    assert result.recovery == sheets_workbook.VERIFY_RECOVERY_MESSAGE
    assert client.deleted == [], 'a failed verification deleted the workbook'
    assert result.url, 'the user was not told where their workbook is'


def test_a_missing_allow_file_discovery_is_not_treated_as_false():
    """Absent is not false. Treating it as false would make the whole
    verification decorative."""
    problem = sheets_workbook.verify_link_viewer(
        [{'id': 'a', 'type': 'anyone', 'role': 'reader'}])
    assert problem is not None


def test_a_verified_link_viewer_permission_passes():
    assert sheets_workbook.verify_link_viewer(LINK_VIEWER_PERMISSIONS) is None


def test_an_unverified_share_is_not_marked_shared_in_the_ledger(ledger):
    client = _FakeClient(permissions_after_share=[])
    result = sheets_workbook.publish_workbook(
        client, 'Almanac', lambda sid: None, ledger=ledger)

    entry = [e for e in ledger.entries()
             if e['spreadsheet_id'] == result.spreadsheet_id][0]
    assert entry['shared'] is False
    assert entry['rendered'] is True


def test_the_read_back_follows_pagination(ledger, monkeypatch):
    """A file with enough permissions to paginate must not have the
    anyone-permission fall off page one unnoticed."""
    pages = [
        {'permissions': [{'id': 'owner-1', 'type': 'user', 'role': 'owner'}],
         'nextPageToken': 'page-2'},
        {'permissions': [{'id': 'anyone-1', 'type': 'anyone',
                          'role': 'reader', 'allowFileDiscovery': False}]},
    ]
    seen = []

    class _PagingHTTP:
        def request(self, method, url, params=None, json=None, **kwargs):
            if method == 'post':
                return _permission_response([])
            seen.append(dict(params or {}))
            response = requests.Response()
            response.status_code = 200
            response._content = _encode(pages[len(seen) - 1])
            return response

    client = _FakeClient()
    client.http_client = _PagingHTTP()

    result = sheets_workbook.publish_workbook(
        client, 'Almanac', lambda sid: None, ledger=ledger)

    assert len(seen) == 2
    assert seen[1]['pageToken'] == 'page-2'
    assert result.is_share_ready is True


def test_sharing_happens_only_after_the_render(ledger):
    """A public link over a half-written workbook is worse than no link."""
    client = _FakeClient()
    log = []
    sheets_workbook.publish_workbook(
        client, 'Almanac', _recording_render(log), ledger=ledger)

    kinds = [kind for kind, _ in client.calls]
    assert kinds == ['create', 'share']
    assert log == [('render', 'sheet-1')]
    # ...and the render ran between them.
    assert client.calls[0][0] == 'create'
    assert client.calls[-1][0] == 'share'


def test_affirmative_confirmation_runs_after_render_and_before_sharing(ledger):
    client = _FakeClient()
    events = []

    def _render(spreadsheet_id):
        events.append('render')

    def _confirm():
        events.append('confirm')
        assert client.share_posts == []
        return True

    result = sheets_workbook.publish_workbook(
        client, 'Almanac', _render, ledger=ledger, confirm_share=_confirm)

    assert events == ['render', 'confirm']
    assert result.is_share_ready is True
    assert len(client.share_posts) == 1


def test_declined_confirmation_leaves_rendered_workbook_private(ledger):
    client = _FakeClient()
    result = sheets_workbook.publish_workbook(
        client, 'Almanac', lambda sid: None, ledger=ledger,
        confirm_share=lambda: False)

    assert result.created is True
    assert result.rendered is True
    assert result.shared is False
    assert result.is_share_ready is False
    assert client.share_posts == []
    assert result.recovery == sheets_workbook.SHARE_NOT_APPROVED_MESSAGE


def test_a_render_failure_never_shares_anything(ledger):
    client = _FakeClient()

    def _boom(spreadsheet_id):
        raise RuntimeError('warehouse went away')

    with pytest.raises(RuntimeError, match='warehouse went away'):
        sheets_workbook.publish_workbook(
            client, 'Almanac', _boom, ledger=ledger)

    assert client.share_posts == []
    assert client.http_calls == []
    assert [k for k, _ in client.calls] == ['create']


# ---------------------------------------------------------------------------
# Policy refusal: created, but not share-ready
# ---------------------------------------------------------------------------

def test_a_policy_refusal_is_reported_as_created_but_not_share_ready(ledger):
    client = _FakeClient(share_raises=_POLICY_ERROR)

    result = sheets_workbook.publish_workbook(
        client, 'Almanac', lambda sid: None, ledger=ledger)

    assert result.created is True
    assert result.rendered is True
    assert result.shared is False
    assert result.is_share_ready is False
    assert result.share_error
    assert result.recovery


def test_a_policy_refusal_does_not_delete_the_users_workbook(ledger):
    client = _FakeClient(share_raises=_POLICY_ERROR)
    result = sheets_workbook.publish_workbook(
        client, 'Almanac', lambda sid: None, ledger=ledger)

    assert client.deleted == []
    assert result.spreadsheet_id == 'sheet-1'
    assert result.url


def test_the_recovery_message_says_where_the_workbook_is_and_what_to_do(ledger):
    client = _FakeClient(share_raises=_POLICY_ERROR)
    result = sheets_workbook.publish_workbook(
        client, 'Almanac', lambda sid: None, ledger=ledger)

    recovery = result.recovery.lower()
    assert 'drive' in recovery
    assert 'share' in recovery
    assert 'viewer' in recovery


def test_a_non_policy_error_propagates_instead_of_being_reported(ledger):
    """A 500 or a revoked token is a breakage, not a product outcome.
    Swallowing it would report 'not share-ready' for a bug."""
    client = _FakeClient(share_raises=_api_error(500, 'Backend error'))

    with pytest.raises(gspread.exceptions.APIError):
        sheets_workbook.publish_workbook(
            client, 'Almanac', lambda sid: None, ledger=ledger)


@pytest.mark.parametrize('reason', ['shareOutNotPermitted', 'domainPolicy',
                                    'publishOutNotPermitted'])
def test_the_known_sharing_policy_reasons_are_recognised(reason):
    assert sheets_workbook.is_share_policy_error(
        _api_error(403, 'nope', reason=reason))


def test_a_403_that_is_not_about_sharing_is_not_a_policy_error():
    assert not sheets_workbook.is_share_policy_error(
        _api_error(403, 'Request had insufficient authentication scopes.',
                   reason='insufficientPermissions'))
    assert not sheets_workbook.is_share_policy_error(ValueError('nope'))


# ---------------------------------------------------------------------------
# Retry: resume, do not pile up
# ---------------------------------------------------------------------------

def test_a_retry_after_a_render_failure_resumes_the_same_workbook(ledger):
    client = _FakeClient()

    def _boom(spreadsheet_id):
        raise RuntimeError('first run died')

    with pytest.raises(RuntimeError):
        sheets_workbook.publish_workbook(
            client, 'Almanac', _boom, ledger=ledger)

    result = sheets_workbook.publish_workbook(
        client, 'Almanac', lambda sid: None, ledger=ledger)

    assert client.created == ['Almanac'], (
        'the retry created a second workbook -- this is the unbounded pile'
    )
    assert result.resumed is True
    assert result.spreadsheet_id == 'sheet-1'
    assert result.is_share_ready is True


def test_a_retry_after_a_policy_refusal_resumes_the_same_workbook(tmp_path):
    ledger = sheets_workbook.WorkbookLedger(tmp_path / 'wb.json')
    blocked = _FakeClient(share_raises=_POLICY_ERROR)
    sheets_workbook.publish_workbook(
        blocked, 'Almanac', lambda sid: None, ledger=ledger)

    allowed = _FakeClient()
    result = sheets_workbook.publish_workbook(
        allowed, 'Almanac', lambda sid: None, ledger=ledger)

    assert allowed.created == []
    assert result.resumed is True
    assert result.spreadsheet_id == 'sheet-1'
    assert result.is_share_ready is True


def test_resume_can_be_turned_off_explicitly(ledger):
    client = _FakeClient()

    def _boom(spreadsheet_id):
        raise RuntimeError('first run died')

    with pytest.raises(RuntimeError):
        sheets_workbook.publish_workbook(
            client, 'Almanac', _boom, ledger=ledger)

    result = sheets_workbook.publish_workbook(
        client, 'Almanac', lambda sid: None, ledger=ledger, resume=False)

    assert client.created == ['Almanac', 'Almanac']
    assert result.resumed is False
    assert result.spreadsheet_id == 'sheet-2'


def test_a_finished_workbook_is_never_silently_reused(ledger):
    """Asking for the same title again after a clean run means the user
    wants another book, not a second write into the first one."""
    client = _FakeClient()
    first = sheets_workbook.publish_workbook(
        client, 'Almanac', lambda sid: None, ledger=ledger)
    second = sheets_workbook.publish_workbook(
        client, 'Almanac', lambda sid: None, ledger=ledger)

    assert first.spreadsheet_id != second.spreadsheet_id
    assert second.resumed is False


def test_a_different_title_is_never_resumed_onto(ledger):
    client = _FakeClient()

    with pytest.raises(RuntimeError):
        sheets_workbook.publish_workbook(
            client, 'Almanac', lambda sid: (_ for _ in ()).throw(
                RuntimeError('died')), ledger=ledger)

    result = sheets_workbook.publish_workbook(
        client, 'Other Almanac', lambda sid: None, ledger=ledger)

    assert result.resumed is False
    assert client.created == ['Almanac', 'Other Almanac']


def test_the_creation_is_recorded_before_the_render_can_fail(ledger):
    """The ledger entry is what makes a resume possible at all."""
    client = _FakeClient()

    def _boom(spreadsheet_id):
        assert ledger.find_resumable('Almanac')['spreadsheet_id'] == \
            spreadsheet_id
        raise RuntimeError('died after the record existed')

    with pytest.raises(RuntimeError):
        sheets_workbook.publish_workbook(
            client, 'Almanac', _boom, ledger=ledger)

    assert len(ledger.entries()) == 1


def test_a_corrupt_ledger_degrades_to_creating_rather_than_guessing(tmp_path):
    path = tmp_path / 'wb.json'
    path.write_text('{ not json at all', encoding='utf-8')
    ledger = sheets_workbook.WorkbookLedger(path)
    client = _FakeClient()

    result = sheets_workbook.publish_workbook(
        client, 'Almanac', lambda sid: None, ledger=ledger)

    assert result.resumed is False
    assert client.created == ['Almanac']


# ---------------------------------------------------------------------------
# What gets printed
# ---------------------------------------------------------------------------

def _share_ready_line(url='URL'):
    return sheets_workbook.SHARE_READY_LINE.format(url=url)


def test_the_share_ready_line_appears_only_when_all_three_steps_succeeded(
        capsys):
    ready = sheets_workbook.PublishResult(
        spreadsheet_id='x', url='URL', title='t',
        created=True, rendered=True, shared=True)
    gas.report_publish_result(ready)

    out = capsys.readouterr().out
    assert _share_ready_line() in out
    assert 'Your almanac: URL -- share-ready.' in out


@pytest.mark.parametrize('created,rendered,shared', [
    (True, True, False),
    (True, False, False),
    (False, False, False),
])
def test_no_share_ready_line_before_creation_render_and_sharing_all_succeed(
        capsys, created, rendered, shared):
    result = sheets_workbook.PublishResult(
        spreadsheet_id='x' if created else None, url='URL', title='t',
        created=created, rendered=rendered, shared=shared,
        share_error='Drive refused' if rendered else None,
        recovery=sheets_workbook.SHARE_RECOVERY_MESSAGE if rendered else None)

    assert result.is_share_ready is False
    gas.report_publish_result(result)

    out = capsys.readouterr().out
    assert _share_ready_line() not in out
    assert '-- share-ready.' not in out
    assert 'Your almanac:' not in out


def test_a_policy_blocked_run_still_tells_the_user_where_their_workbook_is(
        capsys):
    result = sheets_workbook.PublishResult(
        spreadsheet_id='x', url='THE-URL', title='t',
        created=True, rendered=True, shared=False,
        share_error='Drive refused',
        recovery=sheets_workbook.SHARE_RECOVERY_MESSAGE)
    gas.report_publish_result(result)

    out = capsys.readouterr().out
    assert 'NOT share-ready' in out
    assert 'THE-URL' in out
    assert 'Share' in out or 'share' in out
