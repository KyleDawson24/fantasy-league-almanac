# Transaction Records

**Linear:** [Transaction Records](https://linear.app/roguelitedevelopment/project/transaction-records-9d5cf6bf673c) · Backlog · Medium

Track transaction events and their downstream scoring. Key output: **team
rankings by acquisition channel** — production from keepers vs draft picks vs
trades vs FA pickups, counted while under that acquisition.

Independent of the platform work (ESPN-only is fine), session-sized, and it
enriches the future player cards (acquisition story per player) and the
draft board (keeper economics — flags already exist on `mart_draft_board`).

**LANDED 2026-07-09 (MLB-16 done / MLB-17 in review).** The feasibility
question resolved to a direct EXTRACT: ESPN's full-season add/drop/trade log
lives on the league message board (the `communication` /
`kona_league_communication` `ACTIVITY_TRANSACTIONS` topics, paged to
exhaustion) — NOT `mTransactions2`, a current-scoring-period decoy for `flb`.
Verified by content, 3,028 topics draft-day → today; current-season only for
now (prior seasons aren't reachable via `leagueHistory`'s topics filter). The
build: `RAW.TRANSACTIONS` extract → `stg_transactions` (platform-neutral
directed events) → `fct_roster_stints` (contiguous stints, open-channel +
close-type, roster-state skeleton disambiguated by the log's directed trade
edges) → `mart_team_acquisition_channels` (wide per-team, ACTIVE + ROSTERED
lenses, FA / Trade Net deltas). Surfaced as two ranked blocks on the Advanced
Standings tab; golden re-anchored there only, recap + records byte-identical.
The stint shape is what CBS transaction data (already captured) feeds later,
and the acquisition story feeds Player Profiles.

**Depends on:** nothing — a good parallel-track initiative.

**Seeded issues:** MLB-16 extract feasibility (done) · MLB-17
acquisition-channel team rankings (in review)
