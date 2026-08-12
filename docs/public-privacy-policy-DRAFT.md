# Fantasy League Almanac Privacy Policy — DRAFT

**Effective date: [PUBLICATION DATE]**

This draft has not been reviewed by legal counsel and is not published policy.

Fantasy League Almanac is open-source desktop software operated by Kyle
Dawson, an individual (“Kyle,” “we,” or “us”). Questions may be sent to
kpdawson.github@gmail.com.

## What the app processes

The app processes information needed to build a fantasy-league almanac. For
the current public ESPN workflow, that may include league and team names;
member, owner, or display names and platform identifiers; rosters, lineups,
players and statistics; matchups, scores, standings, drafts, transactions,
league settings, records, and derived historical results.

ESPN authentication cookies stay in the user's local `.env` file. Extracted
data is stored locally as Parquet and DuckDB by default. A user may
deliberately choose an advanced path that sends league data to a Snowflake
account the user configures and controls.

## Google Drive access and storage

If the user chooses the Google workbook flow, the app requests only Google's
`drive.file` permission. It uses that permission to create a new spreadsheet
in the Google account the user selects, write the almanac into that
app-created file, set its sharing permission, and verify that permission. The
app does not use this permission to list or read other files in the user's
Drive, and it does not accept an existing workbook as the public-flow target.

The app stores the public Google authorization grant in Windows Credential
Locker, encrypted at rest by Windows. The v1.9 public Google workflow is
supported on Windows only and stops if Credential Locker cannot be used; it
does not fall back to a plaintext token file. If an older plaintext public
token cache exists, the app writes and verifies the secure copy before
removing the plaintext copy. Google tokens and refresh tokens are not bundled
in release files, committed to the repository, logged, sent to Kyle, or
uploaded to a maintainer backend.

The release contains the application's Google OAuth client identity so Google
can identify Fantasy League Almanac during consent. That application identity
is not a user's access token or refresh token.

## Workbook sharing

The app renders the new workbook while it is private. Immediately before
sharing, it explains that the workbook may contain real league/member
information and requires an affirmative continuation. An explicit automation
flag can provide that affirmation without an interactive prompt. If affirmed,
the app sets the new workbook to “Anyone with the link — Viewer.” Anyone who
receives the link can then read the workbook without signing in. The workbook
is not made searchable, and no other Drive file is shared or changed.

## What Kyle receives

Fantasy League Almanac has no hosted application backend, user-account
database, analytics, advertising, payment processing, automatic telemetry, or
automatic log upload. Kyle does not automatically receive ESPN cookies,
Google tokens, league data, member identities, local files, or workbook links.
Kyle receives support material only when a user intentionally sends it, for
example by email or a public issue. Users should remove credentials and data
they do not want to disclose before requesting support.

## Retention and deletion

The app does not promise automatic expiration or deletion. Users control their
local ESPN cookies, Parquet/DuckDB data, workbook ledger, any user-configured
Snowflake data, and Google Drive workbooks. Users can delete local data and
Drive workbooks themselves. They can revoke Fantasy League Almanac's Google
access through their Google Account permissions; a later workbook run will
require authorization again. Removing local authorization does not by itself
delete a Drive workbook, and deleting a workbook does not by itself revoke
Google authorization.

If a user voluntarily sends support material, the user may ask Kyle to delete
Kyle's copy at kpdawson.github@gmail.com. We will address reasonable requests,
but cannot promise deletion from systems Kyle does not control, public GitHub
history, recipients chosen by the user, or backups where immediate removal is
not technically available.

## Security

We use the safeguards described above, including narrow Google permissions and
OS-backed storage for the public Google grant. No software or storage method is
guaranteed secure. Users are responsible for protecting their Windows account,
ESPN cookies, local data, Snowflake credentials if used, and workbook links.

## Children

Fantasy League Almanac is intended for people age 13 and older and is not
directed to children under 13. **Counsel should review this section before
publication.**

## Changes and contact

We may update this policy as the software changes. The effective date above
will identify the current published version. Privacy questions may be sent to
kpdawson.github@gmail.com.
