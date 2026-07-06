# Clone & Run Demo Mode

**Linear:** [Clone & Run Demo Mode](https://linear.app/roguelitedevelopment/project/clone-and-run-demo-mode-f4fe17ce59c4) · Planned · High

Make the project runnable by a stranger in one command: a `dbt-duckdb` target
with parquet extract artifacts and a packaged (anonymized) sample season.

Triple duty: the first concrete step toward the self-serve app (engine
portability), the escape hatch from hosting other people's data, and the
single biggest portfolio unlock — a recruiter can clone and run without
Snowflake credentials. Also upgrades CI from parse-only to real builds
against the sample data.

Known work: a Snowflake-ism audit (MAX_BY, QUALIFY, MODE, LATERAL FLATTEN
over VARIANT, the leaderboard UNPIVOT) with a dispatch/macro strategy per
construct. BigQuery as a later third target.

**Depends on:** nothing — runs parallel to Platform Abstraction; feeds the
Self-Serve Web App.

**Seeded issues:** MLB-9 Snowflake-ism audit · MLB-10 dbt-duckdb target ·
MLB-11 packaged sample league + one-command demo
