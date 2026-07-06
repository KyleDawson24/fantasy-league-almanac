# Ops, Debt & Hardening

**Linear:** [Ops, Debt & Hardening](https://linear.app/roguelitedevelopment/project/ops-debt-and-hardening-a390c4c991b4) · Planned · High (rolling lane)

The rolling lane for correctness debt and operability — the "semi-soft
skill" track.

Known debt promoted from BRAINTHOUGHTS: the `platform_*` two-way
misbucketing fix (slot-aware split, mirroring the calculated lens), the
unfinished cross-session float-determinism items (season rollups +
`mart_draft_board.season_points` — the 0.1-boundary residual observed at the
2026-07-02 golden re-anchor is this class), and extract/backfill performance
(~30 min today; onboarding-critical once a second league needs a full
historical backfill).

Operability, concretely: GitHub provides Actions logs, not incident
management — so the project rolls lightweight versions: a
`docs/postmortems/` convention (first entry: the 2026-07-06 quota-retry
payload corruption, fixed in 1371dee), a structured run manifest per weekly
run, and a retry-idempotency audit across the Sheets writers. `dbt source
freshness` already covers data staleness.

**Depends on:** nothing — items slot into gaps between feature sessions.

**Seeded issues:** MLB-23 platform_* two-way fix · MLB-24 float determinism
· MLB-25 backfill performance · MLB-26 operability baseline
