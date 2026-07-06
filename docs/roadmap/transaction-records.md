# Transaction Records

**Linear:** [Transaction Records](https://linear.app/roguelitedevelopment/project/transaction-records-9d5cf6bf673c) · Backlog · Medium

Track transaction events and their downstream scoring. Key output: **team
rankings by acquisition channel** — production from keepers vs draft picks vs
trades vs FA pickups, counted while under that acquisition.

Independent of the platform work (ESPN-only is fine), session-sized, and it
enriches the future player cards (acquisition story per player) and the
draft board (keeper economics — flags already exist on `mart_draft_board`).
Open feasibility question first: ESPN's transaction history depth (the
recent-activity endpoints may be shallow; fallback is inference from
roster-state transitions, which the kona anti-join pattern already brushes
against).

**Depends on:** nothing — a good parallel-track initiative.

**Seeded issues:** MLB-16 extract feasibility · MLB-17 acquisition-channel
team rankings
