# Eligibility, lineups, and two-way players

This page documents how eligibility, roster slots, and two-way players are handled in the two almanacs this project currently publishes.

These behaviors are not consequences of head-to-head versus season-points scoring. They come from each league's rules, the way ESPN and CBS represent those rules, and the historical data each platform makes available.

For a new league, the platform's eligibility and roster configuration are the starting point. Historical eligibility is reconstructed only when the platform does not preserve it and the league rule can be applied from dated evidence.

## Position eligibility

**For our published ESPN league, ESPN supplies eligibility directly.**
Its feed includes the full list of slots each player can fill, so a first baseman already arrives listed as eligible at corner infield and utility too. The almanac does not infer additional positions. This eligibility is stamped on a daily level; a player may show as eligible at SS for the first 20 games of the season, and then show SS/2B from the 21st onwards.

ESPN, as of writing, declares each player's primary position and the almanac simply reads that stated value.

**For the published CBS league, only current eligibility is available directly.**
The almanac therefore rebuilds historical eligibility using that league's rule:

> A player is eligible at their primary position, plus any position at which they played 20 games last year or 10 games this year.

Three details matter to that reconstruction:

- **Eligibility is dated, not seasonal.**
  The 10-games-this-year clause opens on the day the tenth game is played, not retroactively for the whole season. Earlier production cannot be assigned to the new position on date-aware lineup views.
- **Games played at each position are taken as the larger of two independent counts**

  1. A season-level fielding summary.
  2. A game-by-game positional record.

  This reduces the chance that an omission in either source suppresses a position the player earned.
- **Primary position is estimated in CBS.**
  The CBS history available to the project does not preserve what the platform considered a player's primary position, so the pipeline derives it as the position they played most in the prior season. It is graded against present-day captures rather than assumed correct.

In this CBS league, the designated-hitter slot is deliberately *not* handled as earned eligibility. Anyone who hits can fill it, so that rule is applied when lineups are built rather than written into each player's eligibility history. I believe that is a platform-wide rule, but have not yet tested on additional CBS leagues.

## Lineup slots

The published ESPN league reads its slot configuration from ESPN, including how many of each slot exist. Where a slot repeats, the almanac labels the instances in order (OF 1, OF 2, and so on) rather than showing three identical rows. (Most often, those are ordered by descending Active Points within whatever scope is being viewed.)

The published CBS league has a different configured shape: catcher, first, second, third, short, three outfield, a designated hitter, one utility, and nine pitchers -- nineteen active slots, plus eleven reserve slots.

These slots and shapes are read from the platforms' respective APIs. New-league configurations are designed to flow through automatically, but that behavior has not yet been exercised against additional leagues.

## The slot-validity rule

This is the rule most likely to explain a number you think is wrong.

**Hitting and pitching stats count only from a slot of the matching kind.**
Hitting production counts from a hitting slot, and pitching production counts from a pitching slot. If a player generates production of the other kind, it does not count toward the team's calculated score. Fielding stats are handled separately rather than classified as hitting or pitching.

For the published ESPN league, this rule was verified by reconciling the calculated totals to ESPN's team scoring. In the CBS history it is applied to the reconstructed lineup state under that league's slot and eligibility rules.

One deliberate exception: **bench, injured-list and unowned rows skip the filter entirely.** Those lenses exist to measure production a team did *not* get, so they capture the full stat line rather than a slot-filtered one.

## How the All-League Teams use these rules

When constructing the "All-League Teams," the picker optimizes according to positional eligibility AND league-specific roster restrictions. Most obviously, this means those teams take the shape of a roster in the league's current season. More subtly:

1. **Roster limits are observed.**
   - E.g., our sample ESPN league has a maximum of 1 "Primary" DH per team. You should not see a DH/1B in the Utility slot and then another at 1B, although you may see a 1B/DH and a C/DH on the same lineup.
2. **Only points earned AFTER earning eligibility at a position are deemed eligible for a given slot.**
   - A pure DH who scores 1000 points and earns 1B eligibility after the last game of the season will count as having 0 1B-eligible points.
   - Where that player's stats are detailed, though, the default is to display all active stats.

## Two-way players

This division becomes especially impactful regarding...

A player who both hits and pitches breaks the usual assumption that one fantasy player produces only one kind of stat. ESPN and CBS represent that case differently in the two published leagues.

Theoretically a player could produce both from both types of slot -- an obscure fella named Shohei Ohtani being the most likely culprit. In our sample leagues, however, such players are divided differently -- in ESPN Shohei is treated as one player, but can only be slotted as a hitter or pitcher in a given day. In CBS, he is treated as two separate players -- a hitter Ohtani and a pitcher Ohtani can be on different rosters. In either case, his production is tied to the slot he occupies.

**CBS uses two fantasy assets.** Its batter card and pitcher card point to the same real person but remain independently rosterable entries. The almanac preserves that platform convention:

- The two assets can belong to different fantasy teams and can be rostered, benched, and started separately.
- Hitting game logs feed the batter asset; pitching game logs feed the pitcher asset. Their eligibility is separated the same way.
- In CBS records and leaderboards, they appear as two players, matching the platform.
- The split is identified from the data rather than from a hard-coded list of names.

**ESPN uses one fantasy asset** but only allows that asset to occupy a single lineup slot per calendar day, and adheres to the slot-validity rules in the prior section: a hitting slot only counts hitting production, a pitching slot only counts pitching production.
A two-way player's deployed lineup slot determines which kind of production counts toward the team's calculated score on that day. The almanac retains hitting and pitching components separately so it can report each without pretending that the player occupied two slots at once.

On a team's own page, the two disciplines are shown against their own categories -- hitting points at a hitting slot and pitching points at a pitching slot -- rather than repeating one combined total in both rows. (In hybrid roles like bench slots, this can get wonky, but has not yet reared its head in either of our sample leagues.)

### A relevant known ESPN issue

ESPN's own API often briefly bungles this rule. On days that a player both hits and pitches, his stats will often count as if he occupied both types of slots for roughly 24 hours. I am yet to observe it not self-correct relatively quickly but, at times, if you run the extract right after a game has finished you will see inaccurate stats for the owners of a 2-way player that did actually hit and pitch in the same game that day.

### The one thing the optimal lineups cannot see

On the computed best-lineup boards, ESPN's unified two-way asset may be selected **twice -- but only once as a hitter and once as a pitcher.** Those selections use separate pools of production. A second selection in the same category, such as first base and designated hitter, would count the same production twice and is blocked. CBS's two assets already enter the selection independently, each in its own category.

Sometimes, this means a player -- again most likely known rascal Shohei Ohtani -- will appear on an All-League lineup as both a pitcher and hitter because he produced points in both slot types over the course of the observed timespan, even though the platform does not allow him to do so on any given day.

There is a real limitation underneath this:

> In the published ESPN league, if a player both pitched and hit on the
> same day but occupied only one of those roles, **only the matching
> discipline counts toward the calculated team score.**

That follows directly from the slot-validity rule above and is "correct" -- the off-slot production is zeroed out before the lineup board ever sees it, assuming that our extract ran after ESPN's feed had settled (see [the known ESPN timing issue](#a-relevant-known-espn-issue)). The board does not recover that production. It therefore reflects what could count for that fantasy team under the league's slot rules, not every MLB stat the player produced that day. This is an ESPN-league behavior, not a general limitation imposed on every two-way-player model.

The handling is designed but not yet proven against additional platform representations. I expect to encounter tripwires as we ingest a variety of leagues, and feedback from early adopters will be essential in making our coverage comprehensive.
