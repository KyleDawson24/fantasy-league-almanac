#!/usr/bin/env python3
"""Measure the Google consent experience without publishing anything real.

MLB-209 needs one number: what does an ordinary consumer see when a
`drive.file`-only desktop app asks for permission, and does the resulting
grant actually produce an anyone-with-the-link VIEWER workbook? That is a
question about Google, not about anyone's fantasy league.

Running the real almanac generator to find out would answer it by
PUBLISHING A LEAGUE -- building the configured league's almanac from the
warehouse and then making it readable to anyone holding the link. That is
a real-data publication wearing a probe's clothes, and it would also fail
to exercise the local/stranger journey it was supposed to de-risk.

So this probe is deliberately sealed off. It imports the production auth
and workbook services and NOTHING else from the project: no `db`, no
`almanac_*`, no `sheets_target`, no extract, no DuckDB, no league config.
It cannot read a configured Sheet id because it never imports the module
that resolves one. The only content it writes is a fixed line of its own
text and a UTC timestamp. `tests/test_consent_probe.py` enforces all of
that by running this module in a subprocess and inspecting what it pulled
in, so the seal is checked rather than promised.

It exercises the SAME production seam the real path uses --
`sheets_auth.PUBLIC` for authorization, `sheets_workbook.publish_workbook`
for create/render/share, and the same read-back verification -- so a
green probe is evidence about the shipped code, not about a mock.

Nothing happens without --run-live. Importing this module, or running it
with --help or with no arguments, cannot create a Drive file.

Usage (from the repo root):
    .\\.venv\\Scripts\\python.exe tools\\check_google_consent.py --run-live

Leaves behind: one scratch spreadsheet in the signed-in account's Drive,
for you to inspect and delete; a `drive.file`-only grant in Windows
Credential Locker; and one gitignored ledger row at
output/.sheets_public_workbooks.json. It never creates a plaintext token
cache.
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_OUTPUT_DIR = _REPO_ROOT / 'output'
if str(_OUTPUT_DIR) not in sys.path:
    sys.path.insert(0, str(_OUTPUT_DIR))

import sheets_auth          # noqa: E402  (path set up above)
import sheets_workbook      # noqa: E402


# Generic on purpose. This title is visible in the account's Drive
# listing and in any share dialog, and it should say what the file is
# without naming a league, an owner, or a season.
CONSENT_CHECK_TITLE = 'Fantasy League Almanac consent check'

_BODY_LINES = (
    'This is a scratch file created by a consent check.',
    'It contains no fantasy league data of any kind.',
    'Safe to delete.',
)


def consent_check_rows(created_utc):
    """The entire contents of the probe workbook.

    A fixed shape plus one timestamp -- deliberately the least
    interesting spreadsheet in the world. Kept pure so a test can assert
    the exact rows without any of this reaching Google.
    """
    return [
        [CONSENT_CHECK_TITLE],
        ['Created (UTC)', created_utc],
        *[[line] for line in _BODY_LINES],
    ]


def _utc_stamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def render_consent_check(client, spreadsheet_id, created_utc):
    """Write the fixed rows into the first tab of the new workbook."""
    spreadsheet = client.open_by_key(spreadsheet_id)
    worksheet = spreadsheet.get_worksheet(0)
    worksheet.update(consent_check_rows(created_utc), 'A1')


def run_probe(title=CONSENT_CHECK_TITLE, resume=True, client=None,
              publish=None, created_utc=None):
    """Authorize, create, write, share, verify, report.

    `client` / `publish` / `created_utc` are injection points for tests.
    A default run reaches Google.
    """
    created_utc = created_utc or _utc_stamp()
    if client is None:
        client = sheets_auth.authorized_client(sheets_auth.PUBLIC)
    if publish is None:
        publish = sheets_workbook.publish_workbook

    def _render(spreadsheet_id):
        render_consent_check(client, spreadsheet_id, created_utc)

    print(f'[consent-check] creating a scratch workbook titled {title!r}')
    result = publish(client, title, _render, resume=resume)
    sheets_workbook.report_result(result)
    return result


def _describe_dry_run(title):
    print('[consent-check] nothing was done -- this run touches Google only '
          'with --run-live.')
    print(f'[consent-check] with it, this would: consent under the '
          f"'{sheets_auth.PUBLIC.name}' profile for "
          f'{list(sheets_auth.PUBLIC.scopes)}, create a workbook titled '
          f'{title!r}, write {len(consent_check_rows("<utc>"))} rows of '
          f'fixed synthetic text into it, set anyone-with-the-link viewer, '
          f'read the permission back to confirm it, and print the URL.')
    print('[consent-check] it reads no league data and no configured sheet '
          'id, because it does not import anything that has either.')


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Measure the Google consent flow with a synthetic '
                    'scratch workbook. Touches no league data.',
    )
    parser.add_argument(
        '--run-live', action='store_true',
        help='REQUIRED to do anything. Without it this prints what it would '
             'do and exits, so importing or --help can never create a file '
             'in anyone\'s Drive.',
    )
    parser.add_argument(
        '--title', default=CONSENT_CHECK_TITLE,
        help='Override the scratch workbook title.',
    )
    parser.add_argument(
        '--force-new', action='store_true',
        help='Create a fresh scratch workbook even if a previous run left an '
             'unfinished one with the same title.',
    )
    args = parser.parse_args(argv)

    if not args.run_live:
        _describe_dry_run(args.title)
        return 0

    result = run_probe(title=args.title, resume=not args.force_new)
    return 0 if result.is_share_ready else 1


if __name__ == '__main__':
    sys.exit(main())
