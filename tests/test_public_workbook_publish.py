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


class _FakeClient:
    """Records what would have been asked of Drive."""

    def __init__(self, share_raises=None):
        self.created = []
        self.permissions = []
        self.deleted = []
        self.calls = []
        self._share_raises = share_raises
        self._n = 0

    def create(self, title, folder_id=None):
        self._n += 1
        spreadsheet_id = f'sheet-{self._n}'
        self.created.append(title)
        self.calls.append(('create', title))
        return _FakeSpreadsheet(spreadsheet_id, title)

    def insert_permission(self, file_id, value=None, perm_type=None,
                          role=None, notify=True, email_message=None,
                          with_link=False):
        self.calls.append(('share', file_id))
        if self._share_raises is not None:
            raise self._share_raises
        self.permissions.append({
            'file_id': file_id, 'value': value, 'perm_type': perm_type,
            'role': role, 'notify': notify,
        })

    def del_spreadsheet(self, file_id):          # must never be reached
        self.deleted.append(file_id)


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

    assert len(client.permissions) == 1
    granted = client.permissions[0]
    assert granted['perm_type'] == 'anyone'
    assert granted['role'] == 'reader'
    assert granted['role'] not in ('writer', 'owner', 'commenter')
    assert granted['notify'] is False


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


def test_a_render_failure_never_shares_anything(ledger):
    client = _FakeClient()

    def _boom(spreadsheet_id):
        raise RuntimeError('warehouse went away')

    with pytest.raises(RuntimeError, match='warehouse went away'):
        sheets_workbook.publish_workbook(
            client, 'Almanac', _boom, ledger=ledger)

    assert client.permissions == []
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
