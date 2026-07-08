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
slots are captured daily) → **(4)** the "20 Years of Box Score
Baseball" retrospective (MLB-46), an end-of-season league deliverable
built from the backfill (player-division records under the league's own
scoring; owner history only via a manually-seeded league-lore file,
since the API cannot recover historical standings). Ruled out by the
manifest: historical wasted-points/optimal-lineup, owner-attributed
historical records, draft surfaces, transactions beyond the rolling
window.

**Depends on:** Platform Abstraction (the contract); access spikes can run
anytime.

**Seeded issues:** MLB-12 platform recon (done — findings above) ·
MLB-13 CBS access spike (done — verdict GO) · MLB-44 2026 fantasy-layer
capture (in progress; hard deadline = rollover ~2026-09-27) · MLB-45
gamelog backfill (unhurried) · MLB-46 retrospective (end of season) ·
MLB-14 Yahoo access spike · MLB-42 Fantrax access spike (needs a dummy
league)
