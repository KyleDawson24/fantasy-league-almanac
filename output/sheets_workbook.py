"""
output/sheets_workbook.py

The app-created workbook lifecycle: create a spreadsheet, render into it,
make it link-viewable, and report the URL.

This is the shape the v1.9/2.0 stranger journey ends in -- an app-created
workbook already set to link-viewable, announced once. Everything here
runs under the `public` OAuth profile (`drive.file`), which can create a
file and set that file's permission, and can touch nothing else.

Three properties this module exists to guarantee:

1. The steps are reported SEPARATELY. "Created" is not "shared". A
   Workspace policy that forbids public link sharing must never produce
   the phrase "share-ready" over a workbook nobody else can open. The
   caller gets a result object with a flag per step and asks
   `is_share_ready` for the single question that gates the final line.

2. A policy refusal is not a failure to recover from by deleting things.
   The workbook was created in the USER'S OWN Drive and is theirs. The
   recovery is a sentence telling them where it is and how to share it by
   hand -- never a cleanup that throws their render away.

3. A retry cannot pile up workbooks. Creation is recorded to a local
   ledger the instant it succeeds, before anything downstream can fail.
   A later run for the same title finds the unfinished entry and RESUMES
   it. Without this, every "render failed, try again" leaves another
   orphan spreadsheet in a stranger's Drive, and the tool that was
   supposed to hand them one link has quietly littered.

The ledger is local bookkeeping, not a source of truth about Drive. If
the user deletes a workbook out from under it, the resume attempt fails
loudly on open and the caller can re-run with resume disabled.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import gspread
from gspread.urls import DRIVE_FILES_API_V3_URL


_OUTPUT_DIR = Path(__file__).resolve().parent

# Local bookkeeping for app-created workbooks. Gitignored (both by name
# and by the repo-wide *.json rule) -- it holds workbook ids, which are
# the capability URL for a link-viewable book.
LEDGER_PATH = _OUTPUT_DIR / '.sheets_public_workbooks.json'

# Drive's 403 reasons for "this account may not share files publicly".
# Workspace tenants can forbid link sharing outright, and consumer
# accounts can hit the abuse-prevention variant. Matching on the reason
# string keeps the recovery message specific without pretending we can
# enumerate every policy Google might add.
_SHARING_POLICY_REASONS = frozenset({
    'sharingRateLimitExceeded',
    'shareOutNotPermitted',
    'shareOutNotPermittedFileRestricted',
    'shareOutWarning',
    'publishOutNotPermitted',
    'domainPolicy',
    'insufficientFilePermissions',
    'cannotShareOutsideDomain',
    'abusiveContentRestriction',
})

# The one line the whole journey ends on, kept here as a constant so the
# success claim has exactly one source. Failure messages necessarily talk
# ABOUT share-readiness, so "does the output contain the phrase" is not a
# safe test; "does it contain this line" is.
SHARE_READY_LINE = 'Your almanac: {url} -- share-ready.'

# Said BEFORE consent, not after. `--new-public-workbook` is already an
# explicit request to create and link-share a file, so this is not a
# second confirmation prompt -- adding one would break every automated
# invocation. It is the plain statement of what the flag means, printed
# while the user can still hit Ctrl-C, because "anyone with the link can
# read this" is the kind of thing people should meet before the browser
# opens rather than in the URL they have just been handed.
LINK_SHARING_DISCLOSURE = (
    "The workbook this creates will be set to anyone-with-the-link VIEWER, "
    "so whoever you send the link to can read it (it stays unlisted -- "
    "nobody can find it by searching). Only this new workbook is affected; "
    "nothing already in your Drive changes."
)

SHARE_RECOVERY_MESSAGE = (
    "The workbook was created in your Google Drive and is intact -- only the "
    "link-sharing step was refused, which is usually a Workspace policy on "
    "the signed-in account. Open the workbook, use Share > General access, "
    "and set it to 'Anyone with the link -- Viewer' by hand. If your "
    "organization forbids that, re-run signed in to a personal Google "
    "account."
)

VERIFY_RECOVERY_MESSAGE = (
    "The workbook was created and written, and Drive accepted the sharing "
    "call without an error -- but reading the permission back did not prove "
    "it is anyone-with-the-link VIEWER. The workbook is intact and is yours. "
    "Open it, check Share > General access, and set it to 'Anyone with the "
    "link -- Viewer' by hand before passing the link on."
)

# The Drive v3 permission fields the verification needs BY NAME.
#
# gspread's own `list_permissions` asks for `fields=permissions`, which
# returns Drive's default permission representation -- and
# `allowFileDiscovery` is not in it. Verifying link-only against that
# response could never succeed, so the fields are requested explicitly
# and an absent one is treated as unproven rather than as fine.
_PERMISSION_FIELDS = 'permissions(id,type,role,allowFileDiscovery)'


@dataclass
class PublishResult:
    """What actually happened, step by step.

    Deliberately not a bool. Each step that can fail independently has
    its own flag, so a caller cannot accidentally announce success by
    testing the one thing that happened to work.
    """

    spreadsheet_id: str = None
    url: str = None
    title: str = None
    created: bool = False
    resumed: bool = False
    rendered: bool = False
    shared: bool = False
    share_error: str = None
    recovery: str = None

    @property
    def is_share_ready(self):
        """The single gate for the final success line: the workbook
        exists, it has the almanac in it, and a stranger with the link
        can open it."""
        return bool(self.spreadsheet_id) and self.created and self.rendered \
            and self.shared


class WorkbookLedger:
    """A tiny append/update record of workbooks this app created.

    Entries are keyed by spreadsheet id and carry the title plus one flag
    per completed step. `find_resumable` looks for an entry that was
    created but never finished, which is exactly the state a partial
    failure leaves behind.
    """

    def __init__(self, path=None):
        # Resolved at call time, not at def time, so the module constant
        # stays the single source of the location -- and so a test can
        # redirect it without any run reaching the real file.
        self.path = Path(path or LEDGER_PATH)

    def _read(self):
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            # A corrupt ledger must not block a run. It degrades to "no
            # resumable entry", which is the safe direction: the caller
            # creates a new workbook rather than writing into an id it
            # cannot vouch for.
            return []
        return payload if isinstance(payload, list) else []

    def _write(self, entries):
        self.path.write_text(
            json.dumps(entries, indent=2, sort_keys=True) + '\n',
            encoding='utf-8',
        )

    def entries(self):
        return self._read()

    def remember(self, spreadsheet_id, title, url, now=None):
        """Record a creation. Called BEFORE the render, so a crash in the
        render still leaves a resumable trace."""
        entries = [e for e in self._read()
                   if e.get('spreadsheet_id') != spreadsheet_id]
        entries.append({
            'spreadsheet_id': spreadsheet_id,
            'title': title,
            'url': url,
            'created_at': (now or _utc_now_iso()),
            'rendered': False,
            'shared': False,
        })
        self._write(entries)

    def _mark(self, spreadsheet_id, key):
        entries = self._read()
        for entry in entries:
            if entry.get('spreadsheet_id') == spreadsheet_id:
                entry[key] = True
        self._write(entries)

    def mark_rendered(self, spreadsheet_id):
        self._mark(spreadsheet_id, 'rendered')

    def mark_shared(self, spreadsheet_id):
        self._mark(spreadsheet_id, 'shared')

    def find_resumable(self, title):
        """The most recent unfinished workbook with this title, if any.

        "Unfinished" means created but not both rendered and shared. A
        fully finished workbook is never reused: asking for the same
        title again after a clean run means the user wants another book.
        """
        candidates = [
            e for e in self._read()
            if e.get('title') == title
            and e.get('spreadsheet_id')
            and not (e.get('rendered') and e.get('shared'))
        ]
        if not candidates:
            return None
        return candidates[-1]


def _utc_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def is_share_policy_error(exc):
    """Is this Drive refusing to share, as opposed to a real breakage?

    A 403 whose reason names a sharing policy is a product outcome we
    report and recover from. Anything else -- a 500, a network error, a
    revoked token -- is a bug or an outage and must propagate.
    """
    if not isinstance(exc, gspread.exceptions.APIError):
        return False
    if getattr(exc, 'code', None) != 403:
        return False
    error = getattr(exc, 'error', None) or {}
    reasons = {
        str(detail.get('reason', ''))
        for detail in (error.get('errors') or [])
        if isinstance(detail, dict)
    }
    if reasons & _SHARING_POLICY_REASONS:
        return True
    # Drive is not consistent about populating `errors[].reason`; some
    # tenants return only a message. Fall back to the phrase every
    # policy-block message has carried.
    message = str(error.get('message', '')).lower()
    return 'sharing' in message or 'share' in message


def report_result(result):
    """Print the outcome of a publish.

    Lives here rather than in a caller so there is exactly ONE place that
    decides what counts as success. The consent probe and the almanac
    generator are very different programs; they must not be able to
    disagree about when the share-ready line is earned.

    A workbook that exists but could not be shared is still the user's,
    so its URL is printed too -- just never under a word that claims
    anyone else can open it.
    """
    if result.is_share_ready:
        print(SHARE_READY_LINE.format(url=result.url))
        return

    if result.created and result.rendered:
        print(
            '[workbook] the content was written, but this workbook is NOT '
            'share-ready.'
        )
        if result.share_error:
            print(f'[workbook] {result.share_error}')
        if result.recovery:
            print(f'[workbook] {result.recovery}')
        print(f'[workbook] your workbook: {result.url}')
        return

    print(
        '[workbook] the workbook was not completed, so there is nothing to '
        'hand out yet. Re-run to resume the unfinished workbook.'
    )


def create_workbook(client, title, ledger=None):
    """Create a spreadsheet and record it before returning.

    The ledger write happens between creation and the return, on purpose:
    the caller cannot forget to record it, and no code path exists where
    a workbook exists in Drive but not in the ledger.
    """
    spreadsheet = client.create(title)
    if ledger is not None:
        ledger.remember(spreadsheet.id, title, spreadsheet.url)
    return spreadsheet


def share_link_viewer(client, spreadsheet_id):
    """Grant 'anyone with the link' VIEWER on an app-created workbook.

    Viewer, never writer: the almanac is a thing to read, and a link that
    grants edit to anyone who finds it is a different product. `notify`
    is off because there is no recipient to email -- the grantee is the
    link itself.

    This posts to Drive v3 directly rather than going through gspread's
    `insert_permission`, which is not a stylistic choice. That helper
    sends `{"type", "role", "withLink"}`, and `withLink` is the Drive
    **v2** field name; the v3 Permission resource defines
    `allowFileDiscovery` instead. So the helper's link-only argument
    lands on a field this endpoint does not define, and the one field
    that actually controls discoverability never gets sent. Rather than
    let the first live consent run discover what v3 does with an
    obsolete key, the correct field is sent explicitly.

    `allowFileDiscovery: false` IS the meaning of "anyone with the link":
    anyone holding it can open the file, and nobody can find it by
    searching. `true` would be "anyone on the internet can find and
    read", which is a different and broader product.

    No `notify` is sent because there is no recipient to email -- the
    grantee is the link itself, and Drive only accepts notification
    parameters for `user`/`group` grants.

    Sending the right field is still not proof that Drive applied it.
    That is what the read-back in `verify_link_viewer` is for.
    """
    url = f'{DRIVE_FILES_API_V3_URL}/{spreadsheet_id}/permissions'
    payload = {
        'type': 'anyone',
        'role': 'reader',
        'allowFileDiscovery': False,
    }
    return client.http_client.request(
        'post', url,
        json=payload,
        params={'supportsAllDrives': True},
    )


def list_permissions(client, spreadsheet_id):
    """Read a file's permissions back, asking for the fields by name.

    Goes through gspread's authorized HTTP client rather than its
    `list_permissions` helper, purely to control the `fields` selection
    (see `_PERMISSION_FIELDS`). Same session, same credentials, same
    scope.
    """
    url = f'{DRIVE_FILES_API_V3_URL}/{spreadsheet_id}/permissions'
    params = {
        'supportsAllDrives': True,
        'fields': f'nextPageToken,{_PERMISSION_FIELDS}',
    }

    permissions = []
    token = ''
    while token is not None:
        if token:
            params['pageToken'] = token
        payload = client.http_client.request('get', url, params=params).json()
        permissions.extend(payload.get('permissions') or [])
        token = payload.get('nextPageToken')
    return permissions


def verify_link_viewer(permissions):
    """Is this file actually anyone-with-the-link, viewer, and nothing
    broader? Returns `None` when it is, or a problem sentence when it is
    not.

    Fails closed in three directions, because "share-ready" is a promise
    and each of these breaks it differently:

      - NOT SHARED: no `anyone` permission came back at all, so the
        earlier call did not do what its lack of an exception implied.
      - BROADER: role above reader, or `allowFileDiscovery` true, which
        is not "anyone with the link" but "anyone can find this".
      - UNVERIFIABLE: the field we need is missing from the response.
        Absent is not the same as false, and treating it as false would
        turn this whole function into decoration.
    """
    domain_grants = [p for p in permissions if p.get('type') == 'domain']
    if domain_grants:
        return ('the file carries a domain-wide permission this tool did not '
                'create')

    anyone = [p for p in permissions if p.get('type') == 'anyone']
    if not anyone:
        return ('Drive accepted the sharing call but no anyone-with-the-link '
                'permission came back')
    if len(anyone) > 1:
        return (f'the file carries {len(anyone)} separate anyone-permissions, '
                f'so what a stranger gets is ambiguous')

    permission = anyone[0]

    if 'role' not in permission:
        return 'Drive did not report the role of the link permission'
    if permission['role'] != 'reader':
        return (f"the link permission is {permission['role']!r} rather than "
                f"'reader'")

    if 'allowFileDiscovery' not in permission:
        return ('Drive did not report allowFileDiscovery, so link-only access '
                'could not be confirmed')
    if permission['allowFileDiscovery'] is not False:
        return ('the link permission is discoverable (allowFileDiscovery is '
                'not false), which is broader than anyone-with-the-link')

    return None


def publish_workbook(client, title, render, ledger=None, resume=True):
    """Create (or resume) a workbook, render into it, then share it.

    `render` is called with the spreadsheet id and does the caller's
    actual work. The order is load-bearing: sharing happens LAST, so a
    render that dies never leaves a publicly linked half-written book,
    and the final URL is only ever handed out for something complete.

    Returns a `PublishResult`. Only `is_share_ready` should gate a
    success line.
    """
    if ledger is None:
        ledger = WorkbookLedger()

    result = PublishResult(title=title)

    existing = ledger.find_resumable(title) if resume else None
    if existing:
        result.spreadsheet_id = existing['spreadsheet_id']
        result.url = existing.get('url')
        result.created = True
        result.resumed = True
        print(
            f"[workbook] resuming the unfinished workbook a previous run "
            f"created for {title!r} instead of creating another"
        )
    else:
        spreadsheet = create_workbook(client, title, ledger=ledger)
        result.spreadsheet_id = spreadsheet.id
        result.url = spreadsheet.url
        result.created = True

    render(result.spreadsheet_id)
    result.rendered = True
    ledger.mark_rendered(result.spreadsheet_id)

    try:
        share_link_viewer(client, result.spreadsheet_id)
    except Exception as exc:
        if not is_share_policy_error(exc):
            raise
        result.shared = False
        result.share_error = str(exc)
        result.recovery = SHARE_RECOVERY_MESSAGE
        return result

    # A permission call that did not raise is not evidence that the file
    # is link-viewable. Read it back and check, and treat "could not
    # check" exactly like "wrong" -- this is the only thing standing
    # behind the word share-ready.
    problem = verify_link_viewer(list_permissions(client,
                                                  result.spreadsheet_id))
    if problem:
        result.shared = False
        result.share_error = f'sharing could not be verified: {problem}'
        result.recovery = VERIFY_RECOVERY_MESSAGE
        return result

    result.shared = True
    ledger.mark_shared(result.spreadsheet_id)
    return result
