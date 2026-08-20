# Release Notes -- v2.0.0

**Fantasy League Almanac now has a genuinely guided ESPN-to-Google-Workbook journey.**

Starting from the release ZIP on Windows, a user double-clicks `START_ALMANAC.cmd`, follows an illustrated ESPN setup guide, enters the minimum league facts and hidden session-cookie values, validates the entire requested history, and receives a shareable workbook in their own Google Drive. They do not open a terminal, edit `.env`, edit YAML, create a Google Cloud project, configure an OAuth client, or create a warehouse account.

Major rather than minor because this is the promised stranger-facing platform shift: v1.9 ended in a workbook only after manual configuration; v2.0 owns the supported setup journey from the downloaded ZIP through that workbook. Existing developer and advanced Snowflake paths remain available.

---

## The guided journey

`START_ALMANAC.cmd` is the Windows front door. It locates Python 3.13, creates or repairs the release-local `.venv`, installs the pinned requirements, detects whether that exact requirements set is already healthy, and starts guided setup. Paths are passed as argument vectors from the extracted root, including folders with spaces and OneDrive-style locations. A failed or interrupted dependency install never receives a completion stamp; rerunning the launcher safely finishes it.

At the credential step, the wizard offers to open a bundled offline HTML guide with illustrated Edge/Chrome instructions. The user signs in to ESPN themselves. The application never asks for, observes, or automates the ESPN username, password, 2FA entry, CAPTCHA, or identity-provider controls. It accepts only the league ID and the `espn_s2`/`SWID` values through hidden prompts.

Before writing anything, the importable preflight core validates Python, inputs, ESPN access, every requested season, served league identity and team count, and enough platform evidence to choose a supported workbook format. A successful profile is sealed to the exact request so a UI cannot substitute values after validation.

The local writer then treats `.env` and `config/leagues.yml` as one logical transaction. It builds and validates both destinations before the first mutation, uses same-directory temporary files and atomic replacement, rolls the first file back if the second fails, preserves unrelated environment structure and registry leagues, and is byte-idempotent when the requested state already matches. Credentials land only in the gitignored `.env`; registry YAML receives only non-secret metadata. An ongoing league keeps `final_season: null` after the current season is validated.

After setup, Enter means Yes at `Create the almanac now? [Y/n]`. The shell starts the existing `tools/create_public_almanac.py` orchestration rather than duplicating extraction, Parquet/DuckDB/dbt, Google authorization, workbook creation, rendering, or link-sharing logic. Setup failure never invokes that runner, and runner failure leaves saved setup intact for an actionable retry.

## Returning users and recovery

Ordinary setup never overwrites a nonempty credential. `ROTATE_ESPN_CREDENTIALS.cmd` is a separate double-click action for an expired ESPN session. It explains that the two ESPN cookies are shared by every configured ESPN league, validates replacements against the exact league and season range, then requires the user to type `ROTATE`. Only `ESPN_S2` and `SWID` change; a declined confirmation, malformed input, failed validation or failed write preserves the prior credentials byte-for-byte.

The launcher also makes dependency recovery explicit. A connection failure during installation is handled by rerunning the same file. A second run with a healthy matching environment skips installation after `pip check`. The workbook publisher retains its existing local-ledger resume contract so a failed render does not create an uncontrolled pile of replacement workbooks.

## Credential and Google custody

ESPN cookies remain in the release-local gitignored `.env`. Google user tokens remain in Windows Credential Locker. Extracted Parquet data, DuckDB, the workbook ledger and any workbook capability URL remain local. The application has no hosted API, account database, telemetry, analytics, payments or maintainer backend, and it does not automatically transmit credentials, logs or league data to Kyle.

The release ZIP carries the app-owned Desktop OAuth identity that source-code clones deliberately omit. The application requests exactly `drive.file`, creates the workbook private, and makes it anyone-with-the-link viewer only after explicit confirmation. Users do not create Google Cloud projects or personal OAuth clients. The tag-built bundle census still requires zero credential-shaped literals in source and exactly one client credential in the one injected descriptor inside the finished ZIP.

Google branding review may still be pending when this release is published. That fact is disclosed rather than treated as a technical failure. Pending branding alone is not the safety boundary: wrong scopes, misleading permission language, missing policy/support links, unsafe credential handling or an undisclosed warning would stop publication.

## Supported boundary

The guided v2.0 claim is ESPN-first and Windows-first. The measured workbook paths include ESPN head-to-head points and ESPN season-long points, including an ongoing first season. ESPN auction drafts retain v1.9.1's completed-draft gate and render priced purchase ledgers without invented snake rounds, pick order or grades. Historical depth is still limited by what ESPN serves for that league, and unavailable historical auction prices are labelled rather than manufactured.

CBS guided onboarding is an urgent follow and is not part of the v2.0 stranger claim. Packaged sample mode is explicitly deferred. Automated cookie acquisition through an application-owned browser window remains a bounded future feasibility question; v2.0 uses the illustrated manual fallback and never reads a normal browser profile. Rotisserie remains unproven and fails closed. Optional live head-to-head output, special low-volume designs, and late-start attribution corrections remain follow-on work rather than silently expanded release scope.

The Google publishing journey is Windows-only because user tokens use Windows Credential Locker. That does not redefine wider local, non-Google functionality as Windows-only.

## Release proof and feedback

The launcher is tested through the real Windows batch entrypoint in a disposable OneDrive-style path with spaces. Tests cover Python and pip recovery, interrupted installs, requirements changes, optional rotation, hidden/local guidance, no credential-shaped launcher literals, preflight failure gates, atomic rollback, idempotence, privacy/PII contracts, public OAuth behavior, workbook resumption, and ref-built release bundles.

Publication is additionally gated by Kyle running the actual OAuth-bearing candidate ZIP from a clean Windows environment with no project state or manual configuration edits, completing Google consent and explicit sharing, opening the workbook signed out, and checking the material workbook surfaces. A reproducible setup blocker, credential/destructive-data risk or materially misleading output stops the release.

Reddit is intentionally the first broad stranger-validation event, not a venue reached only after a stranger has already completed the journey. Public GitHub issue forms and a private-support route are live for sanitized reports. The following 48–72 hour triage window seeks a credible non-Kyle completion and dispositions any safety, correctness, unsupported-shape, documentation or UX evidence before the later LinkedIn rollout.
