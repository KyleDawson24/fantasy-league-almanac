# Multi-Platform Support

**Linear:** [Multi-Platform Support](https://linear.app/roguelitedevelopment/project/multi-platform-support-aaaa5e9aa79f) · Backlog · Medium

Per-platform rollouts once Platform Abstraction defines the contract. One
Linear milestone per platform (CBS · Yahoo · Platform #3 pending recon), each
starting with an access spike — what can this login actually see? — before
any code.

Data assets in hand: a login to a 20-year CBS league (father-in-law's;
archive depth and keeper status unknown — if the history is accessible it is
a spectacular test corpus) and a dormant 4-team Yahoo dummy league (messy is
fine for API-shape recon). Platform-landscape recon runs first: verify
whether Sleeper even offers season-long MLB (it is football-first; baseball
support is the open question) and assess Fantrax (the deep/dynasty baseball
standard) and Ottoneu before committing the #3 slot.

## Recon findings (2026-07-07, MLB-12)

| Platform | Season-long MLB? | Data access | Auth |
|---|---|---|---|
| ESPN | ✓ (current adapter) | unofficial (`espn-api` + raw views) | session cookies |
| Yahoo | ✓ (#2 mainstream) | official, documented Fantasy Sports API | OAuth2 app |
| CBS | ✓ (paid commissioner OG, deep archives) | official Developer API v3.0; community token tooling | league-scoped token |
| Fantrax | ✓ (deep/dynasty standard, ~1M+ claimed) | community access by league ID (`fantraxapi`-class libs) | league ID (+ session) |
| Sleeper | **✗ — DFS picks only; no season-long MLB** | n/a for baseball | — |
| Ottoneu | ✓ (salary-cap niche) | limited/exports | — |

Decided (2026-07-07): Sleeper off the board; integration order is
**CBS → Yahoo → Fantrax** (milestone renamed), mainstream first, with
each go/no-go still made individually at its access spike. Ottoneu only
on user demand.

**CBS spike: DONE, verdict GO** (MLB-13). The reference league ("Box
Score Baseball", 16 teams, pure points, no keepers, fielding scored)
exposes real season-grain player history (sparse 2004–2010, full
2020+), while daily stats / rosters / standings / transactions are
current-season only — so the adapter backfills season grain and
forward-captures daily from 2026-07 on. Auth is a browser-extracted
token (login is reCAPTCHA-walled). The non-H2H format spawned the
league-format abstraction workstream (MLB-43); full capability manifest
on the ticket and in the adapter contract's living notes.

**CBS product plan (Per Offline Chat, 2026-07-07):** the manifest splits
the league's data into a real 20-year *player* archive and a perishable
*owner* layer that exists only for 2026 — the product follows that
split. Approved order: **(1)** MLB-44 fantasy-layer capture (landing:
gitignored raw JSON at `data/cbs_raw/` in the main checkout; Snowflake
can load the files later) → **(2)** MLB-45 gamelog+season backfill →
**(3)** MLB-43 first slice on 2026 data (points standings, weekly
movement, bench efficiency — computable for 2026+ only, since deployed
slots are captured in-season; upgraded 2026-07-07: CBS's own
period-end standings history turned out to be servable via
`period=N` and is captured outright, so movement views can read
official standings instead of recomputing) → **(4)** the "20 Years of Box Score
Baseball" retrospective (MLB-46), an end-of-season league deliverable
built from the backfill (player-division records under the league's own
scoring; owner history only via a manually-seeded league-lore file,
since the API cannot recover historical standings). Ruled out by the
manifest: historical wasted-points/optimal-lineup, owner-attributed
historical records, draft surfaces, transactions beyond the rolling
window. (Softened 2026-07-08: the site UI's league-history pages carry
**year-end rosters, transactions, and drafts back to at least 2021**
— owner names included — that the API denies under every probed param;
MLB-47 chases the page sources and capture. For 2021+ that means exact
ownership reconstruction — year-end roster walked backwards through
the season's transaction log — plus a recent-era draft lens, with
per-year completeness verified by content. Only deployed slots /
started-sat stay unknowable pre-2026.) Decided 2026-07-08: 2026-forward surfaces are ACTIVE-ONLY
(deployed-lineup crediting, as on ESPN) with explainers wherever eras
mix — the started/sat signal is the value that accrues as seasons
stack.

**Depends on:** Platform Abstraction (the contract); access spikes can run
anytime.

**Seeded issues:** MLB-12 platform recon (done — findings above) ·
MLB-13 CBS access spike (done — verdict GO) · MLB-44 2026 fantasy-layer
capture (season-to-date landed + content-verified 2026-07-07 — 105
roster dates, 16 standings periods, full 197-entry transaction window;
cadence decided Per Offline Chat 2026-07-07: rides the ESPN weekly
runbook as its last step — SETUP.md documents it, and the MLB-31
runner inherits it as a non-fatal step) · MLB-45
gamelog backfill (2004–2025 COMPLETE + verified 2026-07-07: 3,809
player-season gamelogs, 237,181 player-games, one evidenced tombstone;
2026 sweeps at rollover) · MLB-46 retrospective (end
of season) · MLB-47 UI league-history capture (inventory complete 2026-07-08: the
league reaches back to 2001 — standings + transactions 2001+, the
transactions including bench/start moves, rosters 2003+, drafts 2017+;
API denies all of it; pages are session-cookie-gated behind the
reCAPTCHA login, so the capture waits on a browser-extracted cookie
header in .env, ESPN-style) · MLB-14 Yahoo access spike ·
MLB-42 Fantrax access spike (needs a dummy league)
