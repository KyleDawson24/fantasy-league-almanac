# Eligibility, lineups, and two-way players

Status: **DRAFT** -- not yet published.

This page explains which positions a player can fill, how lineup slots
work in each league, and what happens to players who both hit and pitch.

## Position eligibility

**In the head-to-head league, eligibility is handed to us.** The platform
publishes the full list of slots each player can fill, so a first
baseman already arrives listed as eligible at the corner-infield and
utility slots too. Nothing is inferred.

**In the points league, eligibility has to be rebuilt**, because the
platform serves only today's answer and the almanac needs every past
season's. The league's own rule is applied literally:

> A player is eligible at their primary position, plus any position
> they played 20 games at last year or 10 games at this year.

Three details make the reconstruction faithful rather than approximate:

- **Eligibility is dated, not seasonal.** The 10-games-this-year clause
  opens on the day the tenth game is played, not retroactively for the
  whole season. Points earned before that day do not count toward the
  new position.
- **Games played at each position** are taken as the larger of two
  independent counts -- a season-level fielding summary and a game-by-game
  positional record -- so neither source silently under-reports.
- **Primary position is estimated, and this is the one soft spot.** The
  platform serves no historical record of what it considered a player's
  primary position, so the pipeline derives it as the position they
  played most the prior season. It is graded against present-day captures
  rather than assumed correct.

The designated-hitter slot is deliberately *not* handled as an earned
eligibility. It is a property of the slot -- anyone who hits can fill it
-- rather than something a player qualifies for, so it is applied when
lineups are built instead of being written into each player's
eligibility windows.

## Lineup slots

The head-to-head league reads its slot configuration from the platform,
including how many of each slot exist. Where a slot repeats, the almanac
labels them in order (OF 1, OF 2, and so on) rather than showing three
identical rows.

The points league's active shape is fixed by its own rules: catcher,
first, second, third, short, three outfield, a designated hitter, one
utility, and nine pitchers -- nineteen active slots, plus eleven reserve
slots.

The two leagues use different names for the utility slot, which is worth
knowing if you compare tabs side by side.

## The slot-validity rule

This is the rule most likely to explain a number you think is wrong.

**Stats only count from a slot of the matching kind.** A hitter's hitting
line counts when he is in a hitting slot; a pitcher's pitching line
counts when he is in a pitching slot. If someone is slotted somewhere
that does not match the kind of production they generated, that
production does not count toward the team.

This matches how the platform itself scores teams, which is the reason it
is the default rather than a choice.

One deliberate exception: **bench, injured-list and unowned rows skip the
filter entirely.** Those lenses exist to measure production a team did
*not* get, so they capture the full stat line rather than a slot-filtered
one.

## Two-way players

A player who both hits and pitches -- an Ohtani-type -- breaks the
assumption that one player is one thing. The points league's own platform
solves this by splitting such a player into **two separate entries**, a
batter card and a pitcher card, both pointing at the same real person.
The pipeline follows that convention rather than fighting it.

What that means in practice:

- **The two halves are independent.** They can be on different rosters at
  the same time, and they are rostered, benched and started separately.
  In the record book and on leaderboards, they appear as two players.
- **Each half sees only its own kind of production.** Hitting game logs
  feed the batter entry, pitching game logs feed the pitcher entry. A
  player who is not split -- an ordinary pitcher who occasionally batted
  in the pre-universal-DH era -- keeps a single entry and takes both.
- **Eligibility follows the same split.** The batter half never shows a
  pitching slot; the pitcher half shows only pitching. This mirrors how
  the platform's own player cards behave.
- **The handling is data-driven, not a hard-coded list of names.** Nothing
  in the pipeline names a specific player; a new two-way player is
  handled by the same mechanism automatically.

### The one thing the optimal lineups cannot see

On the computed best-lineup boards, a two-way player may be selected
**twice -- but only once as a hitter and once as a pitcher.** Being
picked twice in the same category (say, at both first base and DH) would
count the same production twice, so it is blocked.

There is a real limitation underneath this, and it is worth stating
plainly rather than glossing:

> If a player genuinely both pitched and hit on the same day but was
> slotted in only one of those roles, **only the slotted half counts.**

That follows directly from the slot-validity rule above -- the off-slot
production was already zeroed before the lineup board ever sees it, and
the board does not recover it. So a real two-way day is under-credited on
those boards by design. This is a known, accepted trade-off rather than an
oversight; changing it would mean changing what "counted for your team"
means everywhere else.

On a team's own page, the two halves are shown against their own
categories -- hitting points at the hitting slot, pitching points at the
pitching slot -- rather than repeating one combined total in both rows.
