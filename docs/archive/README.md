# Archive — session journals, handoffs, and superseded drafts

**Which "archive" is this?** The repo has three, and they hold different
things: `docs/archive/` (here) is *prose* — the written record of how the
project got here, published with the docs. `archive/` at the repo root is
*code exhaust* — retired scripts and scaffolding, with its own index.
`archives/` is local-only safekeeping and is not in git at all.

These files were written to hand work between sessions. They are kept
because the decision archaeology is worth reading, not because they are
current: **nothing here is canonical.** When one of these disagrees with
`README.md`, `SETUP.md`, `CHANGELOG.md` or the models themselves, the
current docs win — these describe what was true on the day they were
written, and several describe plans that were later abandoned or reversed.
They moved here from the repository root in MLB-154 (2026-08-02); paths
inside them that name "the repo root" mean the root as it was then.

## Phase journals

The architectural record, one per phase. `CHANGELOG.md` links each release
entry to the phase document behind it.

- `Phase 1.0 Documentation.md`
- `Phase 2.0 Documentation.md`
- `Phase 2.1 Documentation.md`
- `Phase 3.0 Documentation.md`
- `Phase 3.1 Documentation.md`
- `Phase 3.2 Documentation.md`
- `Phase 3.3 Documentation.md`
- `Phase 4.0 Documentation.md`
- `Phase 5.0 Documentation.md`
- `Phase 6.3.3 Documentation.md`
- `Phase 7 Documentation.md` — the rearchitect; the canonical Phase 7 story

## Session handoffs

Written at the end of a working session for whoever picked it up next.
Useful for "why was this done this way", unreliable for "what is true now".

- `HANDOFF.md` — the early master project reference
- `HANDOFF_BSB_JULY20_SHARE.md`
- `HANDOFF_CBS_DRAFT_RECAP.md`
- `HANDOFF_DRAFT_RECAP_SESSION.md`
- `HANDOFF_IDENTITY_DIM_AND_TEAM_PAGES.md`
- `CBS Almanac Handoff.md`
- `Two-Way Split Bug Handoff.md`
- `v1.3 Session Handoff.md`
- `v1.x Handoff.md`

## Progress journals

Running notes kept across a multi-session investigation.

- `DRAFT_RECAP_PROGRESS.md`
- `WALKBACK_PROGRESS.md`

## Superseded release-notes drafts

The published notes live in `docs/releases/`. These two are working
material for v1.5.0, kept for a specific reason: `REBUILT` is the
reconstruction of the release notes from the commit range, reconciling the
substantive commits against the draft. It is a better answer to "how do you
know the changelog is complete" than the changelog is, and deleted it would
survive only in git history where nobody would find it.

- `RELEASE NOTES v1.5.0 DRAFT.md`
- `RELEASE NOTES v1.5.0 REBUILT.md`
