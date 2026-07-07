# Roadmap

Shipped history lives in [CHANGELOG.md](CHANGELOG.md). As of 2026-07-06 the
forward-looking roadmap lives on a Linear team (`fantasy-league-almanac`,
key `MLB`) and **mirrors into this repo**: one brief per initiative under
[docs/roadmap/](docs/roadmap/), refreshed in the same commit as any session
that changes board state. (Automating that sync is itself a tracked issue,
MLB-29.) This file is the index: what the initiatives are, how they depend
on each other, and what was deliberately excluded.

Ordering note: the maintainer's *value* ranking and the *build* order
diverge on purpose — value is cost-blind, and several cheap independent
items ship in the gaps while the platform work runs.

## Initiatives

| Initiative | Brief | One-liner |
|---|---|---|
| Season Milestone Recaps | [brief](docs/roadmap/season-milestone-recaps.md) | All-Star break + end-of-season special editions (ASB is now) |
| Platform Abstraction | [brief](docs/roadmap/platform-abstraction.md) | The adapter contract: stat crosswalk, schedule derivation, settings ingestion |
| Clone & Run Demo Mode | [brief](docs/roadmap/clone-and-run-demo-mode.md) | DuckDB target + sample league; runnable by a recruiter in one command |
| Multi-Platform Support | [brief](docs/roadmap/multi-platform-support.md) | CBS · Yahoo · a recon-chosen third platform |
| Public Dashboard | [brief](docs/roadmap/public-dashboard.md) | Looker/Tableau public surface; ESPN-first, grows with the platforms |
| Transaction Records | [brief](docs/roadmap/transaction-records.md) | Rankings by acquisition channel: keeper / draft / trade / FA |
| Self-Serve Web App | [brief](docs/roadmap/self-serve-web-app.md) | Bring-your-own-credentials app; highest total value, furthest out |
| Player Profiles | [brief](docs/roadmap/player-profiles.md) | Player cards — parked until a front surface exists |
| Newsletter & Distribution | [brief](docs/roadmap/newsletter-and-distribution.md) | Email edition + the multi-sink interface |
| Scheduler & Orchestration | [brief](docs/roadmap/scheduler-and-orchestration.md) | One-command weekly runner → scheduled runs → (stretch) hands-free posting |
| Ops, Debt & Hardening | [brief](docs/roadmap/ops-debt-and-hardening.md) | Rolling lane: known debt, operability practice, performance |
| Docs & Portfolio | [brief](docs/roadmap/docs-and-portfolio.md) | Continuous review + the post-multi-platform deep-clean capstone |

Project-less items in the team backlog: **Points by MLB team** (MLB-22) — a
likely quick win (one small mart + one almanac block) — **wasted-points
provenance display** (MLB-30, filed 2026-07-06), and **playoff contention
identification** (MLB-38, promoted from the parked list 2026-07-07).

## Sequencing and dependencies

```
Platform Abstraction ──→ Multi-Platform (CBS → Yahoo → #3) ──→ Dashboard (multi-source)
        │                                                          │
        └──────────────┐                                           └──→ Player Profiles
Clone & Run Demo Mode ─┴──→ Self-Serve Web App

Independent / gap-fillers: Season Milestone Recaps (now), Scheduler &
Orchestration tiers 1-2 (runner + local schedule), Transaction Records,
Newsletter, Points by MLB team.
Rolling lanes: Ops/Debt/Hardening, Docs & Portfolio.
```

The Public Dashboard may ship an ESPN-only v1 earlier (decided 2026-07-06);
its multi-source form waits on the platforms. The Docs capstone waits on
CBS + Yahoo.

## Parked details (pre-Linear scoping worth keeping)

Small items not yet ticketed; the detailed scoping in git history
(pre-2026-07-06 versions of this file) remains valid:

- **Playoff-contention identification** — distinguish bracket teams from
  consolation teams during playoff weeks so records can filter/annotate.
- **`fct_team_career_stats`** — team-side career aggregates; likely rides
  with Transaction Records' rankings or an all-time standings expansion.
- **Inactive-fact column symmetry decision** — full mirror vs documented
  asymmetry for the inactive facts' stat columns.
- **Dynamic rate-stat thresholds** from roster config (constants today).
- **ESPN `pointsAdjustment` investigation** — split
  `platform_calculated_delta` into commissioner vs derivation components.
- **Stat ID 30 verification** — confirm the cycle stat's lone observed rows.
- **Bucket-specific inactive leaderboard view** — FA-pool-only rankings.

## Decided against (deliberate exclusions)

- **Frequency-table / "Notable Frequencies" tab.** The tie-collapse pattern
  in the records output already handles the underlying need.
- **Player-grain rate stats at the mart layer.** Phase 6.3.3 Path A choice;
  team-grain rates keep meaning, player rates would need per-stat threshold
  tuning for diminishing return.
- **Sheets formatting-preservation for the legacy records sink.** The
  almanac writer superseded the legacy 3-tab layout; future formatting work
  targets the almanac.
- **`output/_setup.py` boilerplate factoring.** Shipped in substance via
  `db.init()`; the remainder wasn't worth the indirection.

---

This roadmap is a snapshot, not a contract — Linear is the working surface,
this file and the briefs are its documentation mirror.
