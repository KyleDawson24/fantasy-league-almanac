# Public Dashboard

**Linear:** [Public Dashboard](https://linear.app/roguelitedevelopment/project/public-dashboard-2d4af260989f) · Backlog · Medium

A public BI dashboard (Looker Studio vs Tableau Public — decision pending)
over the reporting marts. Per the 2026-07-06 decision: shipping ESPN-only
first and iterating as other platforms land is fine — the gate was
usability-by-others, not a single unveiling.

Early decisions: tool choice (refresh model, embedding, cost) and a
privacy/anonymization pass (owner names and league identifiers are in every
mart and boxscore link) — rules shared with Clone & Run's sample league. The
semantic definitions largely transfer from the almanac work; if MetricFlow
ever earns adoption, this is the initiative that justifies it (MLB-27).

**Depends on:** nothing hard; sequenced after Platform Abstraction by choice.
Unblocks Player Profiles as a navigation surface.

**Seeded issues:** MLB-15 dashboard v1 tool choice + ESPN-first scope cut
