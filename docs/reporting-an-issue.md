# Reporting a problem or requesting coverage

The [GitHub issue chooser](https://github.com/KyleDawson24/fantasy-league-almanac/issues/new/choose) is the default place for public bug reports and feedback. It offers three structured forms:

- **Report a bug** when a supported run stops or produces a materially wrong result.
- **Request league coverage** when your platform, scoring format, draft type, or league shape is not supported yet.
- **Share product feedback** when setup or the workbook is confusing, incomplete, or unexpectedly useful.

The coverage form records voluntary requests for roadmap planning. A future summary may say which platform or format was requested most often, but those submissions are not telemetry or market share, and they are not a representative survey. They do not promise a delivery date.

## Keep the report safe

GitHub issues are public. Before submitting, remove:

- ESPN cookies, `.env` contents, Google OAuth tokens, and Credential Locker contents;
- league IDs, league-member names, team or owner identities, and private screenshots;
- workbook URLs, workbook ledgers, and other capability links;
- raw league data, exports, `.duckdb` files, and `.parquet` files;
- Windows usernames, home-directory paths, and other identifying file paths; and
- full logs.

Replace sensitive values inside a command or error with `[redacted]`. For a failure, the command and the final 10-20 sanitized error lines are normally enough. Do not upload a full log or private data file.

If a report cannot safely be public, email **kpdawson.github@gmail.com** instead. Email is a private fallback, not a reason to send credentials or private artifacts: never send the files or values listed above through either channel.

## What happens to a report

The maintainer will reproduce or classify actionable reports where possible. A public GitHub report may be linked to an internal planning issue so the work is not lost, but only its sanitized technical facts belong there. Private correspondence, credentials, capability URLs, and identifying league data are never copied into the internal board.

Code contributions are not being accepted yet. Reports, measured league-shape evidence, and usability feedback are welcome.
