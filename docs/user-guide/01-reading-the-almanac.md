# Reading the almanac

Status: **DRAFT** -- not yet published. Written for a league member first
and a curious outside reader second.

The almanac is a Google Sheets workbook, rebuilt from scratch every time
the pipeline runs. Nothing in it is hand-maintained, so nothing in it
goes stale in the way a manually-updated spreadsheet does. There is one
workbook per league.

Two leagues are published from the same codebase, and they do not have
identical tabs. That is deliberate: one is a head-to-head league where
teams play each other week by week, the other is a season-long points
league with no matchups at all. A tab that cannot mean anything in a
format is not rendered as an empty shell.

## The tabs

### Home

The landing page, laid out in two bands.

The **left band** is navigation and reference: links to every other tab,
a grid of links to each team's own page, and the **Points Glossary** --
the short definitions of every points column used elsewhere in the
workbook. If a column name anywhere confuses you, the glossary is the
first place to look. The points league's Home also carries a **Stat
sources** table, which says where each era's numbers came from; see
[Stat sources and fidelity](03-stat-sources-and-fidelity.md).

The **right band** is the "best lineup" boards -- an all-league team
assembled from everyone's production, not just one roster. The
head-to-head league shows Team of the Week and Season-to-Date; the points
league shows a running Team of the Month (it rolls over on the 8th),
Season-to-Date, and All-Time. These are computed lineups, not awards --
how they are chosen is explained in [The points
lenses](02-points-lenses.md).

### Records

The record book, laid out as two scopes side by side so you can see at a
glance whether something that happened this season is also an all-time
mark. It is sectioned into score records, hitting, pitching, and records
by lineup slot.

Read the subtitle before reading the numbers. It states the minimum
volume a rate stat needs to qualify (a one-inning relief appearance
should not own the ERA record) and explains the italic and highlight
marks used in the table.

The points league's Records tab also carries a **Franchise Hall of Fame**
and a **Wasted Hall of Shame**. The head-to-head league does not show the
futility block.

### Advanced Standings

The analysis tab, and the densest one. It contains, in order: per-stat
weekly-average standings; a rank-by-week chart you can toggle; a Season
Finishes table (the points league's goes back to 2001, with champions
marked and defunct franchises collapsed into a hidden row group); points
by lineup slot for the season and all-time; production by acquisition
channel -- how much of your output came from drafted players versus
waiver pickups versus trades -- and a roster-affinity chart showing which
real MLB clubs you lean on.

### Matchup History (head-to-head league only)

One row for every team-week ever played: the per-stat lines, hitting and
pitching and total points, the margin, the result, the combined total of
both sides, and the league average that week. This is the tab to use when
you want to argue about whether a loss was unlucky.

There is no equivalent in the points league, because there are no
matchups to have a history of.

### Trades (head-to-head league only)

The current trading block -- who has marked what as available, and how
much interest each player has drawn -- above the season's completed trade
record.

This is the one tab that reads the fantasy platform live at render time
rather than the warehouse. If that call fails, the tab keeps whatever it
showed last run rather than blanking. So if the Trades tab looks stale
and everything else looks current, that is the reason.

### Draft Recap

Best values and biggest busts, then the season's draft board. Below that
is an all-time board re-cut to the current team count, so drafts from
years when the league was a different size still line up in a readable
grid. The points league adds a **Draft Classes** digest for its early
years, where the draft was recorded but the pick order was not.

### One tab per team

Each team gets its own page, showing its **own best lineup** -- not the
league's. Current season and all-time sit side by side: the optimal
starters, then bench, then injured list, then everyone else who ever
appeared, then a summary line for the long tail. There is also a Best
Individual Seasons block, and a franchise-futility entry that you may or
may not want to look at.

The head-to-head league titles these tabs with the team abbreviation; the
points league uses the full team name, and adds a per-era **Lineup Data**
note explaining how much of that era's day-to-day detail is reconstructed
rather than recorded. That note matters -- see [Stat sources and
fidelity](03-stat-sources-and-fidelity.md).

## Two things worth knowing before you argue with a number

**Every points column has two versions.** One is what the platform
actually awarded; the other is what the same performance would score
under this season's rules. They disagree, on purpose. [The points
lenses](02-points-lenses.md) explains which is which and when to trust
each.

**The older the season, the more of it is reconstructed.** Recent years
are captured directly. Older years are rebuilt from transaction logs, and
the earliest are estimated. The almanac labels this per era rather than
hiding it.
