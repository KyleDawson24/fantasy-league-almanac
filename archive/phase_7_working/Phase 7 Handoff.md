# Phase 7 Continuation Brief — for fresh chat

**Status**: Phase 6.3.3 shipped end-to-end (commits `302c109`, `8fa40bf`, `b3fc3bf`). Two NOW-list items shipped (`360fa8f`: `--no-sheets` CLI flag + long-tail tier collapse). All four commits pushed to `origin/main`. Working tree clean. Worktree `.claude/worktrees/phase-7-v1.0/` already created on branch `claude/phase-7-v1.0`. The user paused before kicking off Phase 7 prep so we could line up a fresh chat.

---

## TL;DR

Phase 7 is **v1.0 portfolio prep** — taking a working project and making it presentable. ~7 chunks, mostly documentation + repo hygiene + one tag/release. Substantive code refactors are explicitly available if they surface during the work (the user has flagged appetite for them — connection-management consolidation, splitting `records.py`, etc.). The user will work through this over multiple sessions; don't try to ship 7.1-7.6 in one go.

The first major user-facing artifact this phase produces is `README.md` — that's the recruiter-facing front door. Second-most-important: `CHANGELOG.md` (semver-pinned phase mapping). Everything else is supporting cast.

---

## Setup decisions already made (don't relitigate)

1. **Worktree**: `.claude/worktrees/phase-7-v1.0/` on branch `claude/phase-7-v1.0`. Already created; just `cd` in. The user expects "some risky refactoring" during this phase, hence the dedicated worktree.

2. **License**: MIT (recommended default unless user overrides). User explicitly said they don't have strong feelings — I gave them a primer covering MIT / Apache 2.0 / copyleft, recommended MIT for portfolio purposes, and they're fine with that being the default. Confirm with them before writing the LICENSE file in Phase 7.5.

3. **Project name**: TBD. Current GitHub repo name is `fantasy-league-front-page` but the user wants to change it. They don't know to what yet. Surface naming decision when README rewrite (Phase 7.3) needs the title locked. Some directions floated:
   - Descriptive: `espn-fantasy-baseball-pipeline`, `fantasy-baseball-elt`
   - Punchy: `Diamond Cuts`, `Box Score`, `Bullpen`
   - League-flavored: a nod at "Baseball Buns in the Sun"
   - Tech-forward: `dbt-fantasy-frontpage`

4. **The pre-Phase-1 overview doc** (in user's Downloads, NOT in repo): historical only. Phase docs supersede per `feedback_documentation_source_of_truth.md`. The README rewrite should call out direct contradictions where they materially changed (most likely: "Path A only" vs the original overview's consideration of Paths B + C).

---

## What's already done (do not redo)

- All Phase 6.3.3 work (mart expansion, Sheets writer, playoff naming, league_notes registry, etc.) — see `Phase 6.3.3 Documentation.md` in repo root.
- `--no-sheets` CLI flag + long-tail tier collapse for value=0 records — committed `360fa8f`.
- `HANDOFF.md` (comprehensive project handoff written from the prior fresh chat) — repo root. **Read this before starting; it's the master reference.**

---

## Phase 7 chunks (suggested order: 7.1 → 7.6)

Per the user's roadmap and HANDOFF.md §10. Order is mechanical first (low creativity required), then user-facing/creative, then release.

### 7.1 — `CHANGELOG.md` (~30-45 min)

[keepachangelog.com](https://keepachangelog.com) format. Map phases retroactively to semver:

| Version | Phase | Highlights |
|---|---|---|
| `0.1.0` | Phase 1 | Initial pipeline + dbt scaffold |
| `0.2.0` | Phase 2 | Player contributions, records section |
| `0.2.1` | Phase 2.1 | Consolidated team scores mart, owner names |
| `0.3.0` | Phase 3.0 | Incremental stat facts, leaderboard, rate macros |
| `0.3.1` | Phase 3.1 | Wide convergence facts |
| `0.3.2` | Phase 3.2 | calculated_points, scoring settings |
| `0.3.3` | Phase 3.3 | Doubleheader bug fix |
| `0.3.4` | Phase 3.3.1 | Raw-always extraction simplification |
| `0.4.0` | Phase 4 | Wasted points, kona migration, slot validity |
| `0.5.0` | Phase 5 | Records on calculated_*, new-record callouts, eligible slots, negative-active-as-waste, recap restructure |
| `0.6.0` | Phase 6.2 + 6.3 | Records module extraction + Google Sheets integration + tracked-stats expansion + tie-collapse |
| `1.0.0` | Phase 7 | Portfolio polish + first stable release |

Each entry: 2-3 bullets summarizing the user-visible change. Link to the corresponding `Phase X.Y Documentation.md` for the full architectural detail. Don't try to compress phase docs into the changelog — keep it scannable.

Conventional sections per release: `Added`, `Changed`, `Fixed`, `Removed`, `Deprecated`, `Security`. Most entries will be `Added` and `Changed`.

### 7.2 — dbt project polish (~1-2 hours)

Three things, all mechanical:

1. **Fill in description fields** in every `schema.yml` for models, columns, sources, seeds. Focus on *why*, not *what*. Dbt's `description: ''` defaults are the most common gap. Look for missing descriptions and write them.

2. **Add `exposures`** for the three output scripts: `generate_summary.py`, `generate_records_report.py`, `sheets_writer.py`. Each gets:
   - `name`, `type: application`, `owner`, `description`
   - `depends_on:` (the marts/models they consume)
   - `url:` linking to the file in GitHub

3. **Run `dbt docs generate` locally; verify** exposures appear in the lineage graph. Then **set up GitHub Pages hosting**:
   - One-liner script (or manual): push `dbt_league/target/` contents to a `gh-pages` branch
   - Enable Pages on that branch in GitHub repo settings (Settings → Pages → branch=gh-pages, folder=/(root))
   - Verify hosted docs render at `https://kpdawson24.github.io/<repo-name>/`

GitHub Pages docs URL goes into the README's "Architecture" section as a link.

### 7.3 — `README.md` rewrite (~2-3 hours; biggest creative chunk)

The single biggest portfolio-impact item. Current `README.md` is whatever exists today (probably stale) — full rewrite as the entry point.

Suggested structure (per HANDOFF.md §10):

- **Header**: project name (locked here per setup decision #3) + 1-sentence pitch + tech-stack badges (Python, dbt, Snowflake, Google Sheets API)
- **The 30-second pitch** (1 paragraph): user story first (14-team H2H league commissioner generating weekly recaps), technical layer second (ELT pipeline demonstrating production dbt patterns).
- **Sample output**: code block of an actual generated weekly summary. **Sells the product before explaining the engineering.** Use a real recent week's BBCode output (re-run `generate_summary.py` and paste).
- **Architecture diagram (Mermaid)**: ESPN API → extract → Snowflake raw → dbt staging/int/marts → output scripts → BBCode/Sheets/Console. End-to-end in one image. Mermaid renders natively on GitHub.
- **Link to hosted dbt docs** (from Phase 7.2) for the detailed model lineage.
- **"Notable engineering decisions"** section: 4-6 bullets, each linking to the phase doc with full detail. Suggested:
  - Wide convergence facts at the consumer surface (Phase 3.1)
  - The doubleheader silent-overwrite bug (Phase 3.3)
  - Kona migration and slot-validity filter (Phase 4.0)
  - Cross-platform staging contract (designed for Yahoo portability)
  - Calculate-once-present-many architecture (records module + multiple consumers)
- **What this demonstrates** section (recruiter-facing): incremental dbt models, semantic-modeling discipline, ELT separation, real-data debugging, willingness to refactor.
- **Quick start for the user's setup** (the simple path).
- **Setup guide for new users** (the bring-your-own-credentials path) — link out to a separate `SETUP.md` so README stays scannable. `SETUP.md` covers ESPN cookies, Snowflake free-tier provisioning, GCP project + OAuth, dbt profile config. Use the user's own setup screenshots from Phase 6.3.1 if available.
- **Project status**: link to `CHANGELOG.md` and `ROADMAP.md`.

### 7.4 — `ROADMAP.md` (~30-45 min)

Now / Next / Later / Won't Do structure. Pull HANDOFF.md §10 as the source — it's already structured this way. Just curate, format for public consumption (drop internal-snark), and split into the four buckets.

- **Now**: anything you're considering for v1.x (Sheets formatting preservation, conditional 3rd Top Scorer line, etc.)
- **Next (v2.0)**: cross-platform support (Yahoo, Sleeper), tracked-stats config seed/YAML for portability, dynamic rate-stat thresholds, dbt-bigquery target
- **Later**: MetricFlow Semantic Layer, slot productivity mart, Discord webhook sink
- **Won't Do (and why)**: Path C (hosted multi-tenant SaaS) until demand validates. Anything else consciously rejected.

### 7.5 — Repo hygiene (~30 min)

Mechanical cleanup:

1. **Replace stale top-level overview**. The user has a pre-Phase-1 overview doc in their Downloads (NOT in repo). It's not a delete-from-repo issue; just be aware README is now the entry point and we don't surface the old overview anywhere.
2. **Verify `.gitignore` is comprehensive**: `.env`, `output/logs/`, `target/`, `dbt_packages/`, `LeagueNote.txt`, OAuth token cache, worktree directories. The `.gitignore` was last touched in Phase 6.3.3; double-check nothing's missing.
3. **Clean up worktrees**. Several stale worktrees from prior phases still exist:
   - `.claude/worktrees/distracted-swirles-7aee21/` (stale)
   - `.claude/worktrees/happy-elion-8a788e/` (stale)
   - `.claude/worktrees/wizardly-wozniak-683b30/` (stale)
   - `.claude/worktrees/phase-3.2/` (long-lived; ignore unless user says otherwise)
   - `.claude/worktrees/phase-7-v1.0/` (current)
   
   Run `git worktree list` to see; `git worktree remove <path>` to clean. If files locked (dbt logs), `--force`.
4. **Verify `requirements.txt` is pinned and minimal**. Currently uses approximate pins. Tighten to exact versions where possible.
5. **Add `LICENSE`** (MIT per setup decision #2, unless user overrides). Standard MIT text + your copyright year + name.

### 7.6 — Tag and release (~15 min)

When everything else is shipped:

1. Final commit: bump any version references (in code or docs) to `1.0.0`.
2. `git tag -a v1.0.0 -m "<release notes>"`. Annotated tag with the v1.0.0 changelog entry as the message.
3. `git push origin v1.0.0`.
4. **Create a GitHub Release** off the tag (Releases tab on the repo page → "Draft a new release" → pick the tag → paste changelog entry → publish).

This is the moment the project is "1.0." Recruiters can link to `v1.0.0` and see a stable point-in-time snapshot.

### 7.7 — Socialize (post-release, optional, not gating)

If the user is up for it: post to r/dbt, r/dataengineering, r/fantasybaseball. LinkedIn. Mention in job applications. Each subreddit has its own posting norms — read them before posting.

This is a Phase 7 "stretch" — won't gate v1.0 from a code-completeness standpoint, but compounds portfolio impact.

---

## Refactoring candidates (user has flagged appetite)

The user said "there could well end up being some risky refactoring in this stage." From HANDOFF.md §10:

- **Connection-management consolidation** (medium, ~2 hours). Single Snowflake connection per script run. Saves 10-20 handshakes per script. Bundle with extract perf optimizations if going hard.
- **Split `output/records.py`** (medium, ~3 hours). 1030 lines; natural splits exist (data, polarity, orchestrator, collapse, schedule). Backward-compat with thin re-export shim during transition.
- **Factor shared output-script boilerplate** (small, ~30 min). UTF-8 stdout reconfig + dotenv loading + schedule_lookup loading appear in both `generate_summary.py` and `generate_records_report.py`.
- **Conditional 3rd "Top Scorer" line** (small, ~30 min). Long-standing backlog item. Show only when the overall winner had BOTH non-zero hitting AND non-zero pitching contributions.

Treat these as **opportunistic** — if you're already in `records.py` for an exposure addition and notice the split makes sense, do it. Don't go hunting for refactor-work-for-its-own-sake.

---

## Don't-touch list (per HANDOFF.md §11)

The user runs this every week. Don't break:

- Section ordering in `generate_summary.py` (locked Phase 5)
- Header conventions: `[u][b]Section[/b][/u]` and `[b]Label[/b]: value`
- Player-card shape: `Player (TeamAbbr), X.X pts -- {stats}`
- "Records show owner names; recap doesn't" convention
- "Week N" / playoff-round-name display
- Sheet schema (17 cols × 3 tabs)
- `SHEETS_OUTPUT_ID` env-var opt-in
- `output/LeagueNote.txt` verbatim append under "Additional Notes"

If a Phase 7 change touches user-facing behavior, surface it before merging.

---

## Memory + reference index

Recommended reading order for fresh chat (~1 hour):

1. **`HANDOFF.md`** in repo root — master reference. Sections 1-3 (project orientation), §6 (code map), §10 (roadmap = Phase 7 backlog).
2. **`Phase 6.3.3 Documentation.md`** in repo root — most recent shipped phase, gives architecture snapshot.
3. **`Phase 5.0 Documentation.md`** — best-shaped phase doc for understanding the project conventions.
4. **Memory files** at `~/.claude/projects/C--Users-kyled-projects-espn-league-manager/memory/`:
   - `MEMORY.md` (index)
   - `user_role.md` (portfolio context)
   - `project_phase_plan.md` (cadence + shipped/upcoming)
   - `project_conventions.md` (patterns; lead with these rather than re-debating)
   - `feedback_documentation_source_of_truth.md` (when conflicts arise, phase docs win)
   - `feedback_test_running_side_effects.md` (Sheets-suppression idiom; now superseded by `--no-sheets`)

---

## Verification approach

- **For docs**: render-check on GitHub. CHANGELOG, README, ROADMAP, SETUP need to look good in GitHub's markdown renderer. Mermaid diagrams render natively. Push to a branch first to preview before merging if uncertain.
- **For code refactors**: standard pattern from HANDOFF.md §12 — `dbt build` clean + smoke test + spot-check BBCode + diff review.
- **For dbt docs**: `dbt docs generate` locally, then preview by running `dbt docs serve` (or push to `gh-pages` and view). Verify exposures appear, descriptions populate, lineage graph renders.

---

## Suggested kickoff message for fresh chat

```
Phase 7 — v1.0 portfolio prep. Read "Phase 7 Handoff.md" in the repo
root for the full brief. Worktree is at .claude/worktrees/phase-7-v1.0/
on branch claude/phase-7-v1.0; cd in to start.

Setup decisions already made:
- License = MIT (default; ask before writing LICENSE)
- Project name = TBD (surface during README rewrite)
- Worktree configured

Suggested order: 7.1 (CHANGELOG) → 7.2 (dbt docs polish + GitHub Pages
hosting) → 7.3 (README rewrite + SETUP.md) → 7.4 (ROADMAP) →
7.5 (repo hygiene + LICENSE) → 7.6 (tag v1.0.0 + GitHub Release).

Master reference: HANDOFF.md in repo root. Memory files at
~/.claude/projects/.../memory/ have project context. Phase docs in
repo root are source of truth for architectural decisions.

Start with 7.1 (CHANGELOG) -- mechanical, gets the version map locked
in early. Or propose your own first chunk + approach if you have a
strong reason to deviate.
```

---

## What's NOT in scope for Phase 7

For clarity, so fresh chat doesn't get scope creep:

- **No new product features** — Phase 7 is polish + presentation. New features go to v1.x (after v1.0.0 ships).
- **No mart-layer changes** unless directly motivated by an exposure or refactor opportunity.
- **No public socializing** until the user explicitly green-lights post-release (7.7 is optional).
- **No Sheets formatting preservation work** — that's still on the NOW list as a separate chip; user said it'll happen separately.
- **No cross-platform / BigQuery target work** — explicitly v2.0.
