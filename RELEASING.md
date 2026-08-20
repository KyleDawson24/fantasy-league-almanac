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
      needs a reviewed cause + `REGENERATE_BASELINES=1`). Record in the
      commit message **the anchor matchup period AND the warehouse state
      behind it** -- how far every platform and source feeding the render
      was extracted or loaded. The harness pins one MP, but the render
      reads whole-season marts, so the MP alone names the slice and says
      nothing about the data underneath it: two cuts can pin the same MP
      and still differ byte-for-byte because one of them had another week
      loaded. At v1.8.0 that state was **ESPN extracted through matchup
      period 18, CBS loaded through period 20** -- record whichever
      platforms and sources feed the render at the time, not those two by
      name, since the set changes as platforms and inputs are added.
      Recording the MP alone is what failed the first re-anchor attempt
      on 2026-08-09: there was no way to tell a real regression from a
      fuller warehouse.
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
- [ ] **Sync `dbt_league/dbt_project.yml`'s `version:` to the release
      number.** It is not derived from anything, so nothing catches it
      drifting: it sat at `1.0.2` through six releases before the v1.7.0
      cut noticed. Every number in this repo is either true or
      explained, and this one is the cheapest to keep true.
- [ ] Write `RELEASE NOTES vX.Y.Z.md` at the repo root, and move the
      previous release's notes file into `docs/releases/`. **The root
      carries exactly one notes file -- the current release.** Build it
      from the commit range, not from `[Unreleased]`: that section only
      holds what each session remembered to add.
- [ ] Commit (first-person message, no AI attribution), then create the
      **local** `vX.Y.Z` tag. Do not push it yet.
- [ ] **Build the release bundle** (MLB-209). The tag alone is
      the developer path; a consumer needs the archive that carries the
      public Google OAuth identity, because that credential is
      deliberately NOT in tracked source:

      ```bash
      .venv\Scripts\python.exe tools\build_release_bundle.py --client-json C:\Users\kyled\.gcp\fantasy-league-almanac-public-v1.9-oauth-client.json --version X.Y.Z --ref vX.Y.Z
      ```

      Build from the TAG, not `HEAD` -- the script reads a git ref and
      never the working tree, so this is what makes the archive match
      what was published. It prints lengths and verdicts, never values,
      and refuses to continue if the exported source already contains a
      credential (which would mean one reached git history, where nothing
      can remove it and GitHub's partner scanning will report it to
      Google).

      A successful build also writes
      `dist\fantasy-league-almanac-X.Y.Z.zip.sha256`. The sidecar carries
      the standard digest-and-filename line so a stranger can verify the
      public download without trusting terminal output copied by the
      maintainer. `dist/` is gitignored; never commit either artifact.
- [ ] Run [the clean-machine rehearsal](docs/v2.0-clean-machine-rehearsal.md)
      from that exact tag-built ZIP. A failure means fix, commit, move the
      local tag to the reviewed replacement commit, rebuild and start the
      rehearsal from a new extraction. Do not push, publish or announce a
      failed candidate.
- [ ] **Ask before pushing** (standing rule). Only after the rehearsal
      passes, push `main` and the exact tag to `origin`.
- [ ] **Publish the GitHub Release and both assets together** --
      `gh release create vX.Y.Z dist\fantasy-league-almanac-X.Y.Z.zip dist\fantasy-league-almanac-X.Y.Z.zip.sha256 --notes-file "RELEASE NOTES vX.Y.Z.md"`.
      This step is not optional and it is easy to lose: v1.6.0 was tagged
      and published on GitHub while no notes file ever landed in the
      repo, because neither this document nor the maintainer's local
      house-rules file carried the step and each assumed the other did.
      The tag, notes file, GitHub Release, ZIP and checksum are one release
      action; omitting any one is a publication bug.

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
- [ ] Public-facing (2.0+): use the tracked
      [v2.0 Reddit launch kit](docs/v2.0-reddit-launch.md) for the first
      stranger-feedback post. LinkedIn remains gated by the resulting
      48–72-hour evidence and the maintainer's local `RELEASE PLAN.md`
      (untracked -- see the note in "The cut" above).
