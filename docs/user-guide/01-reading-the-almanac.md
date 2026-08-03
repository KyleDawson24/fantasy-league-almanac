# Reading the almanac

## The tabs

### Home
The landing page, laid out in two bands.

![The Home tab: navigation, team-link grid and points glossary on the left, the All-League Team boards on the right](../img/espn-home-tab.png)

The **left band** is navigation and reference: links to every other tab, a grid of links to each team's own page, and the

**Points Glossary** -- the short definitions of every points column used elsewhere in the workbook. If a column name anywhere confuses you, the glossary is the first place to look. 
Where relevant, this section also carries a **Stat sources** table, which says where each era's numbers came from; see [Stat sources and fidelity](03-stat-sources-and-fidelity.md).

The **right band** is the "best lineup" boards -- an all-league team assembled from everyone's production, not just one roster, foregrounding by "Active Points" -- those produced while part of a team's active lineup. 

Head-to-head leagues show "Team of the Week" -- biggest producers of the most recently completed matchup.
Season-points leagues show "Team of the Month" -- biggest producers of the month, rollover on the 8th

Both league types show "All-League Team Season-to-Date" and All-Time. 

"Stat Line" shows each player's active production in descending order of what produced the most points. The Column immediately to the right shows who "would have" made the given team if all points counted instead of just active. To ease readability, that column only populates when there is a divergence in the player selected -- if the only difference is the point totals (eg top active player achieved the title despite sitting 1 game), the column remains blank.

These are computed lineups, not awards -- how they are chosen is explained in [The points
lenses](02-points-lenses.md).

### Records
The record book, laid out as two scopes side by side so you can see at a glance whether something that happened this season is also an all-time mark. It is sectioned into score records, hitting, pitching, and records by lineup slot.

Read the subtitle before reading the numbers. It states the minimum
volume a rate stat needs to qualify (a one-inning relief appearance
should not own the ERA record) and explains the italic and highlight
marks used in the table.

The points league's Records tab also carries a **Franchise Hall of Fame**
and a **Wasted Hall of Shame**. The head-to-head league does not show the
futility block yet.

### Advanced Standings
Myriad views of historic performances and points-by-source breakdowns. 


It contains, in order: 
A togglable rank-by-week chart for the current season. 
Historic standings.

![Historic standings: a rank-by-period chart above a season-finish matrix by team and year](../img/cbs-advanced-standings.png)

Points by lineup slot, current season & all-time 
Per-stat weekly-average standings, current season & all-time
![Points by lineup slot, current season and all-time](../img/cbs-points-by-slot.png)

Points by Acquisition Channel, current season & all-time -- how much of your output came from drafted players versus waiver pickups versus trades

A roster-affinity chart showing which real MLB clubs you lean on.
### Matchup History (head-to-head league only)

One row for every team-week ever played: 
![Matchup History: one row per team-week, with per-stat hitting and pitching lines](../img/matchup-history.png)

The per-stat lines, hitting and pitching and total points, the margin, the result, the combined total of both sides, and the league average that week. 

The points league equivalent will eventually show equivalent detail, but at the season level.
### Trades (currently ESPN league only)
The current trading block and a scoresheet of the season's completed trade record.

![The season's completed trade record, scored by what each side gained](../img/espn-trade-record.png)

This is the one tab that reads the fantasy platform live at render time rather than the warehouse. If that call fails, the tab keeps whatever it showed last run rather than blanking. 
So if the Trades tab looks stale and everything else looks current, that is the reason.

### Draft Recap
Best values and biggest busts, then the season's draft board. 

![Draft Recap upper section: best value picks and biggest busts, above the season's draft board](../img/draft-recap-upper-section.png)

Below that is an all-time board re-cut to the current team count, so drafts from years when the league was a different size still line up in a readable grid.

![The all-time draft board, re-cut to the current team count](../img/draft-recap.png)

### Team Tabs
Each team gets its own page, showing its **own best lineup** according to who produced while active for their team. 

![A team page: current-season and all-time optimal lineups side by side](../img/cbs-team-tab-flv.png)

Current season and all-time sit side by side: the optimal starters, then bench, then injured list, then everyone else who ever appeared, then a summary line for the long tail. 

There is also a Best Individual Seasons block, and a franchise-futility entry that you may or may not want to look at.

The head-to-head league titles these tabs with the team abbreviation; the points league uses the full team name, and adds a per-era **Lineup Data** note explaining how much of that era's day-to-day detail is reconstructed rather than recorded. That note matters -- see [Stat sources and
fidelity](03-stat-sources-and-fidelity.md).

## Two things worth knowing before arguing with a number
**Every points column has two versions.** One is what the platform actually awarded; the other is what the same performance would score under this season's rules. They disagree on purpose. Anything that compares across seasons leans on the second: pricing every year with one rulebook is the closest to equal footing the numbers can get. [The points lenses](02-points-lenses.md) explains which is which and when to trust each.

**The older the season, the more of it is reconstructed.** Years post "implementation" are captured directly, and more recent years maintain record fidelity that allow for very accurate reconstructions. Older years are rebuilt from transaction logs, and the earliest are estimated using REAL team-specific roster data, but aggregate guesses at start/sit decisions. The almanac labels this per era rather than hiding it -- see [Stat sources and fidelity](03-stat-sources-and-fidelity.md).
