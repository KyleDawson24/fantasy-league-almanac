# Decisions

**Which "archive" is this next to?** `docs/archive/` holds the session
journals and handoffs -- the record of how things happened. This directory
holds the few documents that are still *load-bearing*: a reader needs them
to understand why the code does what it does today.

The list is deliberately short. A decisions index is only useful if you can
trust that everything in it is still in force; a directory that collects
every document anyone once called a decision is just another archive.

- **`CBS_EARLY_ANCHORS_BACKFILL.md`** -- the method behind the pre-2013
  eligibility anchors that ship today. The walk-back laws, the 2004-20
  estimator and the 2001-02 sentinel are all still in the models, so this
  is the document that explains numbers a reader can currently see.
- **`STANDINGS_ORDER_AND_THE_RANK_CHART.md`** -- why the standings tables
  order by ESPN's own playoff seed while the Rank by Week chart stays
  reconstructed, and why those two are allowed to disagree. Also records
  which quantity the podium marks read, and the weekly-extract flag the
  seed's freshness now depends on. Read this before "fixing" either order.
- **`v2.0 Groundwork.md`** -- the scope call that 2.0 is still executing
  against: what "run it yourself" does and does not include, and what was
  deferred to 2.x. **Scope re-drawn 2026-08-05** -- v2.0 is the engine
  port, the blank-templates/demo-fixture separation, and the truth pass;
  the frictionless-bootstrap acceptance moved to v2.1 under MLB-11. The
  original acceptance text stays visible beneath the note in that
  document.
- **`../matchup-period-lifecycle-DRAFT.md`** -- the closed-period restriction
  is at the wrong LAYER: it protects an *aggregation* invariant but is
  enforced at *ingestion*, which is why it reads as a gate to be defeated.
  Kyle's ruling, 2026-08-24: ingest incomplete matchups freely, and scope the
  derivations that presume completeness (outcomes, records/extrema, standard
  period length, standings, rates). Carries the inventory of those
  derivations and the per-grain completeness rule. Read this before designing
  another force flag or positional-period override -- the stopgap has been
  re-litigated in several consecutive weekly sessions and the document exists
  to stop that. **Proposal, not in force**, so it stays in `docs/` for the
  same reason the draft below does.
- **`../dag-boundaries-DRAFT.md`** -- the layer-boundary design (MLB-158
  Phase A): every model mapped to a target layer, with the graph's backward
  edges catalogued as options rather than decisions. **Draft status is
  deliberate -- nothing has moved yet.** It stays in `docs/` as the
  architecture page rather than moving here, because it records a proposal,
  not a decision in force.
