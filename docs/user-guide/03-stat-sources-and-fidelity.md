# Stat sources and walk-back fidelity
This page documents the sources and reconstruction quality of the two almanacs this project currently publishes: one for an ESPN head-to-head league and one for a CBS season-points league. 

The differences below are not inherent to those scoring formats. They reflect the data each platform exposes, the records retained by these particular leagues, and when collection began.

The published ESPN league's lineup and scoring history is short and recorded directly. The published CBS league's history runs back to 2001, and most of it was never recorded in a form anyone intended to be read again. This page is about how much of each era is *known* versus
*reconstructed*, because the CBS almanac shows both and labels which is which.

If you only read one thing: **the further back you go, the more the numbers are a careful reconstruction rather than a transcript.** The workbook says so per era, on the team pages and on its Home tab. As of 8/6/2026 publication, we've had the opportunity to carefully develop that 

Nothing here promises the same coverage for an unseen league, although w feedback we hope to create similar results. A new league has to be assessed against the history its platform exposes and the records that survive: full historical lineups can provide direct history; transactions and roster anchors can support reconstruction; season totals alone support only coarser estimates. Scoring format by itself does not determine fidelity.
## Where the numbers come from

**The published ESPN league** reads ESPN's own feeds: per-week box scores with full lineups and stat breakdowns, the scoring settings, roster settings, ownership, and the draft. For the seasons covered, those feeds provide direct lineup and scoring history rather than a reconstruction.
**One important exception is transaction history, which ESPN exposes only for the current season.**

**A second exception is the MLB club attached to each player,** and it is worth telling as what it was: a defect this project found in its own work, measured, and then closed. **ESPN's *player record* carries only his current club**, not the one he played for on a given day. For a season loaded after the fact that leaves a single label for the whole year, so no mid-season move was represented at all. For a live season the label follows moves only to within a scoring week. Lineups, stats and scoring were never affected -- those come from the box scores directly -- but the club label on them was the one the player held at collection time.

The roster-affinity chart on Advanced Standings is the only surface that depends on that label, so it is where the cost landed. Measured against 2025: **22.25%** of that chart's weight was filed under the wrong club, and a further **11.7%** could not be placed at all (basically games played by players who were free-agents at collection time) and rendered in a labelled *Unattributed* row.

**This is fixed.** The club now comes from the game rather than from the person -- each per-scoring-period split already carried the club the player was actually with, so reading it needed no identity crosswalk. The Unattributed band now measures **0.0 across 2025, 2026 and all-time**, all 30 MLB clubs render in every scope, and the 2025 half of that chart no longer has to be read as directional. The CBS chart resolves club the same way, from the game itself.

The historical measurement is kept in [Known Data Issues](../known-data-issues.md) rather than deleted, and the person-level club stamp is still written and preserved on every extract: it is the observation record of what ESPN believed and when. It is simply no longer what the chart attributes from.

**The published CBS league's 2001-2026 history** draws on three different sources, because no single source available during implementation covers the full history:

1. **CBS's API**, for current-season rosters, standings, config, and season-level stat totals. In the endpoints verified for this league, CBS publishes no per-game fantasy points, and its historical player-stat endpoint covered only free agents -- meaning the players actually on rosters were the ones missing. That discovery is why the third source exists.
2. **The league's own web pages, scraped**, for the history the API does not serve: standings back to 2001, year-end roster snapshots back to 2003, 55,980 player-actions across roughly 25,700 transactions back to 2001, and drafts back to 2009.
	1. The year-end snapshots not existing 2001-2002 means that we are missing ownership evidence players that never moved; a trait that disproportionately affects stars. We have an ownership override CSV available to manually input ownership in such cases, but the data is simply inaccessible in CBS's logs at certain points. 
3. **The public MLB Stats API**, for the actual baseball: full career game logs for every player involved, independent of any fantasy platform. This is the platform-independent layer -- real games, real stat lines -- which the fantasy layer (rosters/lineups/etc) is then mapped onto.

The MLB feed supplies the underlying *baseball* statistics. The primary reconstruction uncertainty is in the *fantasy* state around them: who owned whom, and who was in the lineup on a given day. That uncertainty is measured and stated on affected surfaces.

## What "walk-back" means

The surviving records for the CBS league's early years do not include drafts, so a season's opening rosters exist in no transaction record available to the project -- the log tells you that someone was traded in May, but not who started the year on the roster.

The walk-back reconstructs roster membership day by day, working from two kinds of evidence: 
1. The transaction log's move history, with year-end roster snapshots as anchors. 
	1. If a player shows up in the year-end snapshot with no acquisition earlier that season, the reconstruction infers they were there from opening day.

2. Lineup Activity 
	1. Starting or benching a player is not an acquisition, but you cannot bench a player you do not own -- so those events count as proof of membership even though they say nothing about how the player arrived.

Each of these, it would appear, are limited to 9999 rows of transaction data per season. 
Even in our highly-active, 25+ year old CBS league, that limitation never posed an issue, but it is not inconceivable that a league would exist where that truncates some of the year's earliest transactions.

Crucially, though, the reconstruction is **graded, not asserted**. 
The pipeline carries a report card that compares its reconstructed season totals against the league's official published final standings, for every season it covers. The error figures below come from that comparison.

## Fidelity of the CBS history by era

| Era         | How it is known                                                                                                                                                                                                                                          | Typical error vs official |
| ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------- |
| 2026 onward | Captured live, daily                                                                                                                                                                                                                                     | --                        |
| 2020-2025   | Reconstructed day by day from the transaction log                                                                                                                                                                                                        | about 2-4%                |
| 2004-2019   | Estimated from year-end start-share rates. <br>Membership is reconstructed near perfectly, but start/bench transactions do not exist. We used CBS' %Owned and %Start figures to estimate.                                                                | about 5-13%               |
| 2001-2003   | Reconstructed from the transaction log, without roster anchors. <br>This Era, strangely, DOES include start/bench transactions, but superstars like Barry Bonds were often put into a starting slot and never moved -- we can't see what team he was on. | 2001-02 about 12-15%      |

A few things this table implies that are worth stating outright:

**The 2004-2020 estimates are consciously conservative, for our sample league in particular.** For those years the log records who was on a roster but not who was in the lineup on a given day. The reconstruction uses each player's season-long rate of being started as a weight. Where even that is unavailable, the contribution is counted as **zero** rather than guessed -- so those seasons under-count rather than over-count. 

The error is not flat across the era, either: it is roughly unbiased 2005-2010, runs about 8-13% low from 2011. This seems to mostly be due to GS limits, that are no longer recorded, changing over the years

Plus, **That start-share rate is a platform-wide player statistic, not a team one.** 
It describes the start-rate across all CBS fantasy leagues for a player https://help.baseball.cbssports.com/s/article/What-are-roster-trends. So, to estimate what a player actually did for his team, we take MLB-Stats-Produced-During-Stint, and multiply by (Start%/Rostered%). We use that multiplier because we know he was rostered during the stint, and Start% on its own is universal. 
(Practically, for our sample league, this leads to a consistent undercounting -- the 25+ year old super active league is unsurprisngly making reliably better start/sit decisions than the random public CBS league)

**2001 and 2002 are the weakest years in the book, but in an unusual way.** 
There are no year-end roster pages for them, which removes the anchors the reconstruction leans on everywhere else. We believe this is a CBS-level limitation, but COULD be specific to our sample league's running their draft elsewhere. Such mysteries are lost to the sands of time. 

The main cost is *coverage*, not accuracy: a player who was drafted and held all season -- never added, dropped, traded, or benched -- never touches the transaction log, so the walk-back has no way to know whose roster he was on. Those are disproportionately the stars, since nobody drops their best players. 
Production that can be tied to the league but not to any team is parked in a clearly-labelled placeholder franchise (Named ####) rather than being silently assigned to somebody, and the uncertainty is flagged on the surfaces it touches.
Importantly, the PlayerMembershipHistory csv allows for manual input/overrides in cases where memory or offline documents can answer questions that CBS's data cannot.

## Known gaps and open questions in the CBS history

**Pre-season trades are structurally invisible.** 
The transaction report the history is scraped from does not include them. In the one season
where a second independent source was available for comparison, 747 of 748 moves matched -- and the one miss was a pre-season trade. These are fairly unusual in practice across the board, but if your league drafts early and moves a lot of pieces before opening day, it could be a blind spot.

**At least one scoring category disagrees with itself.** 
CBS's season-level figure for one pitching category, IRStr (Inherited Runners Stranded) is off by up to three either way from its own per-game data. One documented case is Hoby Milner 2025, where CBS's own per-game rates imply 41 against its season key's 38. That is a case where our derived number is more accurate than CBS's published total, and there are no documented cases where the opposite is true.

**Unresolved:** 
Official standings for 2021-2023 show team pitching running roughly 8-11% below the reconstruction, while hitting tracks within 3-5%, and both line up again from 2024. That pattern suggests a team-level pitching cap in those seasons that has not been confirmed. It is an open question, not a finding -- do not read those three seasons' pitching totals as settled.

**Residual loose ends:** 
A negligible number of roster stints whose end was never logged, and a few players whose last logged event was a departure despite appearing in the year-end snapshot.

## Why we default to 'calculated' or 're-priced' points

See [The points lenses](02-points-lenses.md). In short, leagues change their scoring system over time, often to the degree that the scale changes radically -- in our ESPN league, the average 2025 score was nearly 500 points, and the highest score in all of 2026 (as of this 8/6/26 writing) was 413. I made a judgment call that leagues generally move in the direction of their "ideal" scoring system, and are therefore  interested in viewing things in the context of current conditions. "Platform Points" are, however, tracked in both platforms and reflect points as awarded at the time. If there is significant interest in seeing points represented that way, we will consider design changes

(Further, when we eventually deploy a more interactive dashboard-type presentation, we plan to make switching between calculated/platform points a simple toggle on any view that surfaces either. That is a ways down the road, however.)

Two population caveats remain and are worth knowing: the earliest era misses players who passed through without ever appearing in an anchor snapshot, and a player's production counts toward league-wide leaderboards even in seasons when nobody rostered him.
