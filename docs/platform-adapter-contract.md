# Platform adapter contract — v1 (ACCEPTED)

Status: **v1 accepted as written** (MLB-3, maintainer review 2026-07-07
per offline chat). Next on the ticket: the ESPN extract refactored to be
adapter #1 of this contract with goldens held byte-identical, plus
per-adapter contract tests; every later platform (CBS, Yahoo, Fantrax)
implements the same surface.

## The idea

Everything downstream of `RAW` — staging reshapes, the slot-validity
filter, the convergence facts, the record book, the almanac — is
platform-agnostic *if and only if* extracts land data in a common shape.
This document is that shape: the feeds an adapter must deliver, their
grains, and the identity rules that make cross-platform features
(records, stitching, the crosswalk) possible. It is written from what
the ESPN adapter already supplies today, minus ESPN-isms, plus the two
axes the 2026-07-07 CBS findings made explicit: **platform** and
**league format** are independent dimensions (MLB-43) — a feed can be
required, optional, or format-conditional.

## Feeds

Every feed is append-only with an `extracted_at` timestamp (re-extracts
supersede by recency; staging takes latest snapshots), keyed by
`(platform, league_id, season)` plus the feed's grain below. Adapters
deliver raw platform vocabulary — stat ids, slot ids, format labels stay
native; translation to the canonical catalog happens in dbt via mapping
seeds (MLB-4), never inside the adapter.

### F1. Player-day performance (required)

One record per `(scoring_period/date, fantasy_team, player, deployed_slot)`:
the box-score grain. Must carry per-stat values in the platform's native
stat vocabulary, the **deployed** lineup slot (not eligibility), the
platform's own applied-points total where one exists (the `platform_*`
lens), and games-played that day (doubleheader-aware). This feed is the
format-agnostic core — required for every league format.

### F2. Player identity & eligibility (required)

Per player per extract: platform player id, full name, MLB team,
primary position, eligible slots. Names and MLB team are the crosswalk
keys for cross-platform identity (league stitching, MLB-8); platform ids
are never assumed stable across platforms.

### F3. Roster/ownership state (required)

Daily fantasy-team rosters including zero-stat days (the roster-snapshot
grain), plus free-agent determinability — either an explicit FA feed or
a full player universe that supports the anti-join pattern.

### F4. Scoring configuration (required)

The league's scoring rules in native vocabulary: for points formats,
points-per-unit per stat; for category formats (roto/H2H-category), the
category list and direction. Feeds the crosswalk (MLB-4) and the
`league_format` dimension (MLB-43).

### F5. Roster configuration (required)

Lineup slot counts and position limits in native slot vocabulary — the
`dim_roster_slot_counts` source.

### F6. Schedule / periods (format-conditional)

For H2H formats: matchup periods with date ranges, who-plays-whom
pairs, and the platform's authoritative per-matchup team scores (the
`stg_matchup_scores` / `stg_matchup_pairs` equivalents). For non-H2H
formats this feed degrades to the scoring-period calendar only —
matchup-shaped models are skipped, not faked (MLB-43 first slice).

### F7. Standings (format-conditional, non-H2H required)

H2H leagues derive standings from F1/F6. Non-H2H leagues must deliver
the platform's own standings (points totals or category tallies +
ranks), because there is nothing to derive them from.

### F8. Team & owner identity (required)

Fantasy teams per season: platform team id, name, abbreviation, owner
identity (stable owner key where the platform has one; display name
otherwise). Owner keys power franchise continuity.

### F9. Draft results (optional)

Picks with round/overall/keeper flags where the platform exposes them —
enables the draft-value surfaces; their absence disables those surfaces,
nothing else.

### F10. Transactions (optional)

Adds/drops/trades where exposed (MLB-16 scopes the ESPN version);
inferable from F3 transitions when absent.

## Conformance

An adapter ships with: (a) a capability manifest declaring which
optional/conditional feeds it supplies and the league formats it
supports; (b) contract tests asserting each supplied feed's grain and
required fields (the dbt singular-test pattern, per platform); (c) a
documented history statement — how many seasons the platform actually
serves, verified, not assumed (the CBS archive-depth question).

## Known per-platform notes (living)

- **ESPN** (adapter #1): everything above exists today; F1 arrives via
  the kona raw path with wrapper fallback; F6 is authoritative
  (commissioner adjustments included); stat vocabulary is numeric ids
  with documented quirks (HBP collision, stat 64, stat 30).
- **CBS** (spike complete, MLB-13, 2026-07-07): v3 API alive; auth is a
  browser-extracted token (reCAPTCHA-walled login; no scripted
  credential flow; TTL unobserved). Verified for the reference league
  ("Box Score Baseball": 16 teams, pure points, no keepers, fielding
  scored): **season-grain player stats have real history** (sparse
  2004–2010, meaningful ~2013+, full 2020+ — the COVID-2020 points dip
  authenticates it), and **per-game player history is real via
  `players/gamelog?player_id&timeframe`** (Votto's 2015 verified
  game-by-game — backfillable in ≈4,200 polite calls). Still
  current-season only: **the fantasy layer** — rosters (which do carry
  the deployed slot), started/sat, standings, owners, transactions.
  League-wide daily/period/fantasy-points endpoints cosmetically accept
  `timeframe` but return current-window data — verify history claims by
  content, never by HTTP 200. Capture mechanics (MLB-44 first run,
  2026-07-07 — every endpoint has its own history key and its own
  decoy): rosters serve per-date history via `point=YYYYMMDD` (maps the
  date to its scoring period; period-grain content; `roster_status`
  A/RS carries started/sat), while the obvious `date` param answers
  with the current roster dressed in date-varying news headlines —
  judge by membership fingerprints, not bytes; standings serve
  period-end history via the period NUMBER `period=N` (responses echo
  the period served — assert it), while `point` there echoes a period
  label over current totals. `league/rosters` needs `team_id=all` (else
  it silently scopes to the token's own team), transactions live at
  `league/transaction-list/log` (plain `league/transactions` 404s), and
  cross-season `point` 400s or clamps to current — rollover still
  destroys the layer, so the capture cadence still matters. The site UI
  is a separate, richer surface (2026-07-08): cookie-authenticated
  league-history pages serve standings and transaction logs back to
  2001 (activate/reserve moves included — the active set for a points
  league), year-end roster reports 2003+, drafts 2017+, and
  per-franchise overview pages with rename-stable team ids; all
  captured as raw HTML, parse layer with the format work. Adapter
  shape: gamelog + season backfill, forward-capture the fantasy layer
  before rollover; F7 required and satisfiable; F6 calendar-only
  (periods with empty matchup arrays); F9 absent; F10 a rolling window
  at the API, full history on the UI surface.
- **Yahoo** (MLB-14): official OAuth2; expected to satisfy F1–F8;
  archive depth TBD.
- **Fantrax** (MLB-42): league-ID access; deployed-slot availability at
  daily grain is the spike's key question.
