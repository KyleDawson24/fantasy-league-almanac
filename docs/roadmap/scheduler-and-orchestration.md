# Scheduler & Orchestration

**Linear:** [Scheduler & Orchestration](https://linear.app/roguelitedevelopment/project/scheduler-and-orchestration-8f4c80f4531a) · Planned · High

Automate the weekly cadence in tiers, each independently valuable:

1. **One-command runner** — extract → dbt build → recap → records report →
   almanac behind a single entry point with failure gates (a failed step
   halts the chain, so the "week in raw but missing from marts" half-land
   class dies by construction), per-step logs, and a structured run
   manifest. Failures produce incident stubs for the `docs/postmortems/`
   convention; the run-manifest bullet of MLB-26 graduates here.
2. **Scheduled execution** — Task Scheduler on the maintainer's machine
   first. The timing rule is the design: stats settle overnight (ESPN's
   post-hoc corrections are documented league history), so Monday morning
   beats Sunday night; the latest-MP incremental re-merge makes an early
   run self-heal on the next one. A GitHub Actions scheduled workflow is
   the laptop-off option, gated on secrets handling (Snowflake key-pair,
   ESPN cookies and their expiry, Sheets OAuth refresh token).
3. **Hands-free posting (stretch)** — ESPN has no posting API for the
   front page. Cheap 90%: the runner copies recap BBCode to the clipboard
   and opens the league page. Browser automation is a spike with honest
   fragility expectations; newsletter send (Newsletter & Distribution) is
   the channel that automates cleanly.

Portability (the "works for future users" part) rides Clone & Run: a
per-OS `--install-schedule` helper rather than bespoke infrastructure.

**Depends on:** nothing for tiers 1–2; the posting stretch benefits from
Newsletter & Distribution; portability rides Clone & Run.

**Seeded issues:** MLB-31 one-command runner · MLB-32 local schedule with
settle-aware cadence · MLB-33 GitHub Actions spike · MLB-34 hands-free
posting rungs · MLB-35 portable schedule installer
