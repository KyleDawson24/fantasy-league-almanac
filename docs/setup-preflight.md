# Setup preflight and local configuration (development rungs)

The v2.0 onboarding core now validates an ESPN league and, only after every
check succeeds, fills the two existing local configuration destinations. It
validates the league ID, both private-league cookies, every requested season,
the served league name and team count, and enough platform evidence to choose
a supported points workbook format before any file changes.

Run it from an activated Python 3.13 environment:

```powershell
python tools/setup_league.py
```

The cookie prompts are hidden. The command does not accept secrets as command
line arguments or print them. A successful preflight writes credentials only
to the gitignored repo-root `.env` and writes only non-secret league metadata
to the existing `espn-main` slot in `config/leagues.yml`. It creates `.env`
from `.env.example` when needed and preserves unrelated environment keys,
comments, registry entries, and sinks.

The writer builds and validates both complete byte streams in memory before it
creates a temporary file. It stages same-directory temporary files and uses
atomic replacement for each destination. If the second destination fails, it
restores the first destination to its prior state. A rerun with the same state
is byte-idempotent.

Setup refuses malformed files, duplicate keys, ambiguous registry metadata, a
different existing league ID, and every ordinary attempt to replace a
nonempty cookie with a different value. Returning users whose ESPN session has
expired have a separate, explicit action:

```powershell
python tools/setup_league.py --rotate-credentials
```

Before accepting replacement values, the CLI explains that ESPN credentials
are shared by every configured ESPN league and rotation can affect all of
them. It validates the new cookies against the exact league and season range,
then requires the user to type `ROTATE` before replacement. A validation
failure or declined confirmation leaves the old credentials byte-for-byte
unchanged. Rotation changes only `ESPN_S2` and `SWID`; it preserves the league
ID, other-platform credentials, unrelated environment structure, and the
entire registry. The same importable core exposes a credential-free warning
and confirmation callback for the planned web shell.

Leave the final-season prompt blank for an ongoing league. The preflight checks
through the current season while the registry writer preserves
`final_season: null`, so the league does not become accidentally frozen at
today's year.

Failure messages use stable categories for bad input, expired authentication,
denied access, unavailable history, network failures, unexpected ESPN
responses, unsupported/undetermined formats, malformed local configuration,
conflicts, and write failures. Validation lives in `config/bootstrap.py`, the
transaction lives in `config/bootstrap_writer.py`, and this CLI is only one UI
shell over them.

This rung does not create a second configuration root, write the separate dbt
league-override CSVs, download league data, open Google, touch Snowflake, or
start `tools/create_public_almanac.py`. The v1.9.1 release Quickstart remains
the supported manual configuration journey until the complete wizard is
packaged into a later release.
