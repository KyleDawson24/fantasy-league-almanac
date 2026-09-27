# One consented real-name exception: the maintainer's own league

**Status: in force from 2026-09-22 (approved by Kyle). Amended 2026-09-27:
a second league, `cbs-bsb`, is added under Amendment 1 below.**

## The rule this bends

The project's standing posture is that real names never appear in anything
public. The public repository carries blank `league_config/` templates and the
name-anonymized demo twins (`demo/league_config/`); the real league data stays
on the maintainer's disk (MLB-95, MLB-176). That posture is unchanged for
every league except the one below.

## The exception: `espn-main`

One league (`league_key = espn-main`), the maintainer's own
ESPN league, may show its members' real names on the public dashboard.

**Why it is allowed:** the league's members are fine with their names being
out there. The maintainer's own name, in any form (Kyle Dawson, kpdawson), is
fine anywhere, the repository included.

**Contact details are the line.** Email addresses and phone numbers stay off
everything public. The members have not objected to sharing them; this is the
maintainer's own rule, so that publishing the league never gets its members
targeted by spam.

## What may be published, and where

| Field class | Public dashboard | Repository |
|---|---|---|
| Kyle's own name | Yes | Yes |
| Member and franchise display names | Yes | Avoid adding them; existing appearances are not worth a scrub |
| Stats, standings, rosters, records | Yes | As today |
| Member GUIDs (identity-resolution keys) | Allowed, only where a page needs them | As today |
| Email, phone, any other contact field | **Never** | **Never** |

The publication step works from an **allowlist**: it copies only the files and
fields on an approved list, and everything else stays behind automatically. A
new QA file or a stray column cannot leak just because nobody remembered to
exclude it.

## How it is served

- The published data is **served, not committed.** A GitHub Actions artifact
  deploy hands the built files to GitHub Pages directly, so the published data
  does not live on any branch. The real `league_config/` stays gitignored on
  the maintainer's machine.
- Visitors browse it like any website. Nothing runs on their machine beyond
  the page itself.
- **Not linked from any public page.** The README, the portfolio pages and the
  release notes link the anonymized dashboard, never the real-name one.

## Amendment 1 (2026-09-27): the CBS league, `cbs-bsb`

**In force from 2026-09-27 (approved by Kyle).** The league's commissioner
confirmed on the morning of 2026-09-27, on behalf of the members, that their
names may be shown.

A second league, `league_key = cbs-bsb` (the CBS "Box Score Baseball" league,
2001 to present), may show its members' real names on its own public
dashboard, on exactly the terms above: the same field table, the same
allowlist publication step, served through the artifact deploy and never
committed, reachable only at an unlinked address that the maintainer hands to
the league. Email, phone and every other contact field stay off it.

Two things are specific to this league:

- **A favor to the league first, a stress test second.** Twenty-six seasons
  of daily data make this the largest edition the site serves, so historic
  pages may be slow to load, and the site says so in a caveat line. The
  league's contact should never need to install anything: the deliverable is
  a link that works in a browser.
- **Temporary as a real-name site.** The intent is to anonymize this league
  later so it can serve as the project's display piece. When that happens
  the real-name site comes down and this amendment is marked superseded. The
  members' consent permits the site; it does not oblige the project to keep
  it up.

If a member withdraws consent, the rule below applies unchanged: struck from
future versions, nothing further promised or built.

## What this does NOT change

- Email or phone data anywhere public is still a leak.
- Every league other than the two named in this document publishes only as
  an anonymized twin. A further league needs its own consent and its own
  amendment here.
- The `league_config/` skip-worktree mechanics and the false-alarm rules in
  CLAUDE.md are unchanged.
- Per-league privacy modes, for users who want less exposure than this league
  accepted, are 2.3 or later.

## For agents

Showing either consented league's member names on its dashboard through the
sanctioned pipeline is **not a leak. Do not scrub it.** Do not deliberately add member
names to tracked files, and do not start a history rewrite over names already
there. Kyle's own name is never a finding.

## If a member withdraws consent

Their name is struck from future versions. Nothing further is promised or
built.

## References

MLB-285 (2.2 definition ruling, 2026-09-22) · MLB-299 (the prototype and its
publication acceptance) · MLB-176 (name guard) · MLB-95 (public scrub) ·
MLB-308 (BSB dashboard; commissioner consent 2026-09-27) · MLB-319 (the
platform-neutral data layer that builds the BSB edition)
