# Release Notes — v1.6.0

*The pre-port anchor release.*

_This file mirrors the published [GitHub Release for `v1.6.0`](https://github.com/KyleDawson24/fantasy-league-almanac/releases/tag/v1.6.0)
(tagged 2026-07-30). The release was published without a notes file landing
in the repo; the text below is the published body verbatim, so a clone and
the Releases page tell the same story._

The last stable point before the engine port -- MLB-10's rollback anchor,
and the reason this range gets a tag at all. Two halves: a polish pass
league members actually saw, and a determinism sweep they didn't.

The visible half finished the Total-Points vocabulary on both books and
moved scope text off the grid and into the section banners. The invisible
half found that the ESPN writers had been re-rendering on top of their own
previous output for as long as the layout had been row-stable enough to
hide it -- three separate faces of one gap, all surfaced by this release's
own layout changes and all caught in dev review.

Minor, not patch: the glossary rewrite and the banner conversion change
what the surfaces say and how they read.

### Added

- **An "Updated" stamp on both Home tabs.** A3 carries a render-time
  `Updated MMM d, yyyy kk:mm` (ET, 24-hour) in italic size 10, suppressed
  by `SUPPRESS_UPDATED_STAMP=1` so the byte-diff corpora stay stable.
- **Unrostered Points** joins the Points Glossary on both books, and CBS
  mirrors ESPN at six entries.

### Changed

- **The Points Glossary is rewritten to the settled Total-Points lenses**
  (MLB-141): Total = active + inactive + unrostered, and Wasted is
  restated to the canonical three-way with a footnote naming the current
  under-count until MLB-135 lands. CBS keeps Calculated Points for its
  platform-verification job and swaps Rostered Points for ESPN's Inactive
  Points vocabulary.
- **Advanced Standings converts to banner + italic-scope captions on both
  books** (MLB-142): era and scope text move into the navy section
  banners as italic white captions, and the separate era rows above
  Points by Lineup Slot, Production by Acquisition Channel, and the
  affinity chart fold in with them.
- **Every team-tab row is pinned to 21px on both books** (MLB-143) -- the
  values write had been auto-growing rows under wrapped cells with
  nothing to reset them.
- **The Wasted Points definition is trimmed and its merge widened to
  B:D** (MLB-141); the previous footnote clipped after three of seven
  wrapped lines.
- **The acquisition-channel lens explainers render as captions on both
  books** (MLB-161) -- size 10 italic rather than bold or unstyled.
- **The ESPN Draft Recap tab moves to sit directly before the first team
  tab** (MLB-162), so the league-wide surfaces run together.
- Home tab column A widens to 125px on both books.
- The README contact block gains a Ko-fi link, and the repo gains a
  Contributing note: issues and feedback welcome, code contributions not
  accepted for now while the licensing story settles.

### Fixed

- **The ESPN Trades tab dropped Date Executed cells and side sums on
  re-render.** `worksheet.clear()` drops values but keeps merges, and the
  Sheets API silently discards a value written into a non-anchor cell of
  a merged range -- so each render wrote new rows onto the previous
  render's merge lattice and lost whatever landed off-anchor. Unmerging
  now happens before the values write, across every ESPN writer that
  merges (Trades, Home, team tabs, Advanced Standings, Records, Draft)
  and in the CBS writer. The data and the builder were correct
  throughout; only the order was wrong.
- **ESPN tabs painted over the previous render's formatting.** `clear()`
  drops values but not cell formats, and unlike the CBS writer the ESPN
  writers had no reset -- invisible while the layout was row-stable, and
  exposed the moment this release's era-row deletions shifted blocks up
  one to three rows. Every ESPN tab writer now opens its format phase
  with a whole-sheet `userEnteredFormat` reset, mirroring CBS doctrine.
  One consequence worth naming: hand formatting on ESPN tabs no longer
  survives a re-render, which has always been true on CBS.
- **The banner gate no longer silently stops banding on a reworded
  title** -- it is prefix-matched, in the same commit as the renames.

### Internal

Groundwork for the MLB-10 port, and value-neutral by design: every site
was checked against the warehouse before it was touched, and each commit
in the sweep carries the same standing constraint -- engine-only, values
must not move. (The Home tab re-anchor in this release belongs to the
glossary rewrite above, not to any of this.)

- **The row-selection ordering sweep** (MLB-134): the 10 latest-load-wins
  dedups and every remaining row-selection window are now totally
  ordered, so no engine gets to choose which payload survives a tie.
  Keys are picked per source -- CBS raw leads with `captured_at` (the
  recency signal those models actually meant), ESPN raw falls back to a
  payload hash as a backstop behind real semantic keys, never as the
  semantics itself.
- **NULL placement is stated on 21 DESC row-selection keys across 16
  sites** (MLB-134). Snowflake defaults to NULLS FIRST on DESC and DuckDB
  to NULLS LAST, so an inherited engine default could otherwise pick a
  different row on each. A documented no-op today -- every key came back
  zero NULLs -- which is the point: it stops being luck and starts being
  stated.
- **New singular test `assert_label_payload_constant`**, which carries
  the half a tie-breaker cannot: pinning an order fixes which row is
  chosen, and this is what makes the value trustworthy. Invisible to the
  byte-diff goldens by construction, which is why it earns its place.
- `owner_nicknames` column_types completed against the six-column local
  seed (MLB-134), unblocked by the MLB-95 ruling that identity seeds
  carry no contact columns in git.
- The INITCAP site in `dim_owner` is named for the port, so a golden that
  moves there has a documented cause instead of reading as a data bug.
- The `mart_team_alltime` header now says outright that it is MLB-69's
  pre-built data layer with no readers by design, so the next
  zero-readers sweep gets a self-answering comment instead of proposing
  it for deletion.

### Documentation

- **The setup and dbt docs no longer lie about their own inventory**
  (MLB-153). A fresh clone was promised 112 pure tests, 4 seeds, 173 dbt
  tests and view-based staging; it gets 250, 18, 543 and tables. Every
  count now comes from the parsed manifest at HEAD. Command docs are
  restructured into three honestly-labeled tiers -- offline (any clone,
  touches nothing) / live read-only (needs credentials) / mutation
  (writes; deliberate ceremony) -- and the private golden corpora are
  named, with the note that the tests needing them *skip* rather than
  fail in a public clone.
- **A DAG boundary design draft** (`docs/dag-boundaries-DRAFT.md`,
  MLB-158 Phase A): every model mapped to a target layer, with the
  graph's backward edges catalogued and each one written up as options
  rather than decisions. Draft status is deliberate -- nothing has moved.

