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
different existing league ID, and any attempt to replace a nonempty cookie
with a different value. Cookie rotation needs an explicit product policy; this
rung does not invent one. Failure messages name the repair without displaying
credential values.

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
