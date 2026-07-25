# RELEASING — the release checklist

One pass per numbered release. Written 2026-07-12; commit this file with
the first release it governs (v1.5.0). Keep it short enough to actually
follow.

## When to cut

Cut when a coherent story completes (a surface ships, a foundation
lands), not on a calendar. Hard backstop: if `[Unreleased]` exceeds
~4 weeks or ~30 commits, the next session's first job is a cut.

## Pre-cut — verify green (from the main checkout, on `main` post-merge)

- [ ] `git symbolic-ref -q HEAD` — on `main`, not detached.
- [ ] `pytest tests/` — unit suite green.
- [ ] `pytest tests/ -m warehouse` — goldens byte-identical (any diff
      needs a reviewed cause + `REGENERATE_BASELINES=1`; record the
      anchor MP in the commit message).
- [ ] `dbt build` — clean, including tests.
- [ ] Skim `BRAINTHOUGHTS.md`; mark superseded items OBSOLETE.
- [ ] Sheets/live sinks were suppressed during verification runs.

## The cut

- [ ] Curate `CHANGELOG.md`: move `[Unreleased]` content under a new
      `## [X.Y.Z] — YYYY-MM-DD` header; prune stale forward-references
      (nothing in a cut section should promise future version numbers).
- [ ] Version rationale sanity check: MAJOR = breaking/platform-shift
      milestone, MINOR = additive features (ESPN byte-neutral), PATCH =
      fixes only.
- [ ] Commit (first-person message, no AI attribution), then
      `git tag vX.Y.Z`.
- [ ] **Ask before pushing** (standing rule), then push `main` + tags
      to `origin` (the public portfolio repo).

## Post-cut

- [ ] Linear: flip shipped issues with paired comments; note the release
      number on each.
- [ ] Add a one-line narrative note for the release to `ROADMAP.md`. The
      per-ticket `docs/roadmap/` mirror is retired (that directory no
      longer exists); Linear is the roadmap's working source of truth.
- [ ] README: refresh version references / screenshots if the release
      changed a surface.
- [ ] New `[Unreleased]` section seeded at the top of the changelog.
- [ ] If the release added/changed an output surface: re-run the weekly
      runbook end-to-end once before announcing anything.

## Announce (only when the release warrants it)

- [ ] League-facing: share/refresh the relevant Sheet or board post.
- [ ] Public-facing (2.0+): LinkedIn / Reddit per the launch checklist
      in `RELEASE PLAN.md`.
