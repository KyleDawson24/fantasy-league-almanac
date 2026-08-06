# RELEASING -- the release checklist

One pass per numbered release. Written 2026-07-12; commit this file with
the first release it governs (v1.5.0). Keep it short enough to actually
follow.

## When to cut

Cut when a coherent story completes (a surface ships, a foundation
lands), not on a calendar. Hard backstop: if `[Unreleased]` exceeds
~4 weeks or ~30 commits, the next session's first job is a cut.

## Pre-cut -- verify green (from the main checkout, on `main` post-merge)

- [ ] `git symbolic-ref -q HEAD` -- on `main`, not detached.
- [ ] `pytest tests/` -- unit suite green.
- [ ] `pytest tests/ -m warehouse` -- goldens byte-identical (any diff
      needs a reviewed cause + `REGENERATE_BASELINES=1`; record the
      anchor MP in the commit message).
- [ ] `dbt build` -- clean, including tests.
- [ ] Skim `BRAINTHOUGHTS.md`; mark superseded items OBSOLETE.
      **Local and untracked**, like `RELEASE PLAN.md` below: a fresh clone
      will not have it and does not need it.
- [ ] Sheets/live sinks were suppressed during verification runs.

## The cut

- [ ] Refresh what's derived, re-cut what's stale:
      `python tools/refresh_doc_inventory.py` rewrites the inventory
      counts in `SETUP.md` and `dbt_league/README.md` from the parsed
      manifest + pytest collection, and `RELEASE PLAN.md` gets re-cut in
      the same pass so the plan describes the release actually being cut
      (that recurring step is what retires the standing "re-cut owed"
      debt). **`RELEASE PLAN.md` is a local, untracked planning doc -- it
      is deliberately not in the repository, so a fresh clone will not
      have it and does not need it.** Never hand-count either one -- the
      ticket that asked for the script had itself gone stale by two
      models. Ceremony only, not CI:
      these are allowed to lag mid-cycle, and a check that goes red
      between releases is one people learn to ignore.
- [ ] Curate `CHANGELOG.md`: move `[Unreleased]` content under a new
      `## [X.Y.Z] - YYYY-MM-DD` header (plain hyphen, per Keep a
      Changelog); prune stale forward-references
      (nothing in a cut section should promise future version numbers).
- [ ] Version rationale sanity check: MAJOR = breaking/platform-shift
      milestone, MINOR = additive features (ESPN byte-neutral), PATCH =
      fixes only.
- [ ] Write `RELEASE NOTES vX.Y.Z.md` at the repo root, and move the
      previous release's notes file into `docs/releases/`. **The root
      carries exactly one notes file -- the current release.** Build it
      from the commit range, not from `[Unreleased]`: that section only
      holds what each session remembered to add.
- [ ] Commit (first-person message, no AI attribution), then
      `git tag vX.Y.Z`.
- [ ] **Ask before pushing** (standing rule), then push `main` + tags
      to `origin` (the public portfolio repo).
- [ ] **Publish the GitHub Release** --
      `gh release create vX.Y.Z --notes-file "RELEASE NOTES vX.Y.Z.md"`.
      This step is not optional and it is easy to lose: v1.6.0 was tagged
      and published on GitHub while no notes file ever landed in the
      repo, because neither this document nor the maintainer's local
      house-rules file carried the step and each assumed the other did.
      The tag, the notes file and the GitHub Release are one action in
      three places; if you do two of them, the third is a bug someone
      finds months later.

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
      in the maintainer's local `RELEASE PLAN.md` (untracked -- see the
      note in "The cut" above).
