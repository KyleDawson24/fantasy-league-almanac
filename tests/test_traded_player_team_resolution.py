"""A player traded mid-period resolves to the team he was traded TO.

WHAT THIS GUARDS, and why the obvious version of it would not.

`almanac_data._enrich_optimal_team_with_stats` must resolve a traded
player to ONE fantasy team so the All-League Team of the Week can print
a team and an owner beside him. It does that with MAX_BY over a recency
key, and that key's last term used to be `team_id` -- described in its
own comment as breaking the same-period trade tie. A team id is an
IDENTIFIER, so it broke the tie toward the numerically larger id: right
when a trade ran low-id to high-id, wrong when it ran the other way.

Measured at the point of the fix: 131 of 254 traded player-weeks across
2025-2026 resolved to the wrong team (48.6% / 53.8%). The coin flip IS
the diagnosis -- a tiebreak correlated with anything real cannot land on
50% -- and it is also why the bug survived two seasons unreported: half
of every trade came out right by luck, including, on any given week,
most of the ones anyone happened to look at.

SO A SINGLE-CASE TEST IS NOT ENOUGH, and specifically a test pinned to a
low-id-to-high-id trade PASSES against the unfixed code. That is the
trap this file exists to avoid: the sweep below asserts the invariant
over EVERY qualifying player-week in the league, so the half that used
to be right by accident cannot vouch for the half that was wrong.

SCOPE, and it is a real boundary rather than a convenience. The oracle
is the DAILY PERFORMANCE fact -- the weekly fact's own source -- and the
population is player-weeks that PERFORMED for two or more teams. A
player rostered by two teams who only ever appeared for one produces no
weekly row for the other, so no aggregation over this fact can name it;
that is not a tiebreak defect, it is the performance fact being asked a
roster question. Measured at the time of the fix: 145 player-weeks
performed for 2+ teams (all correct after the fix, a coin flip before)
and 91 were rostered-by-two / performed-for-one, of which 17 resolve to
the team whose points they actually are. Whether THAT is the desired
label is a product question -- "whose points are these" (the old team,
which is what this returns) versus "who owns him now" (the new team) --
and it is deliberately not settled here.

Warehouse-marked: the invariant is about real league history, and a
synthetic fixture would be asserting the arithmetic back to itself.
"""
import pytest

import almanac_data
import db


ESPN_LEAGUE = 'espn-main'


def _traded_player_weeks():
    """Player-weeks that PERFORMED for more than one fantasy team.

    Truth comes from the DAILY grain, which is both where the trade is
    observable and what the weekly fact aggregates -- so the last day's
    team_id is the answer by construction and needs no tiebreak at all.
    Using the daily fact rather than the weekly one keeps the oracle
    independent of the arithmetic under test.
    """
    return almanac_data.query_for_presentation("""
        WITH multi AS (
            SELECT season_year, player_id, matchup_period
            FROM fct_player_daily_performance
            WHERE league_key = %s AND team_id IS NOT NULL
              AND matchup_period IS NOT NULL
            GROUP BY 1, 2, 3
            HAVING COUNT(DISTINCT team_id) > 1
        )
        SELECT
            d.season_year,
            d.player_id,
            d.matchup_period,
            MAX_BY(d.team_id, d.scoring_period) AS chrono_team_id,
            MAX(d.team_id)                      AS largest_team_id
        FROM fct_player_daily_performance d
        JOIN multi m
          ON d.season_year = m.season_year
         AND d.player_id = m.player_id
         AND d.matchup_period = m.matchup_period
        WHERE d.league_key = %s AND d.team_id IS NOT NULL
        GROUP BY 1, 2, 3
    """, [ESPN_LEAGUE, ESPN_LEAGUE])


def _resolved_team_id(season_year, player_id, matchup_period):
    """What the enrichment actually labels this player-week with."""
    rows = [{'player_id': player_id}]
    almanac_data._enrich_optimal_team_with_stats(
        rows, season_year, matchup_period, None, 'all')
    return rows[0].get('team_id')


@pytest.fixture(autouse=True)
def _espn():
    previous = db.league_key()
    db.set_league(ESPN_LEAGUE)
    try:
        yield
    finally:
        db.set_league(previous)


@pytest.mark.warehouse
class TestTradedPlayerResolvesToAcquiringTeam:

    def test_the_population_is_not_empty(self):
        """A silent zero here would make every assertion below vacuous."""
        traded = _traded_player_weeks()
        assert traded, (
            "no intra-period trades found in the ESPN league -- the sweep "
            "below would pass without testing anything")

    def test_every_traded_player_week_resolves_to_the_last_days_team(self):
        """The whole invariant, over the whole population.

        Deliberately NOT sampled. The defect this replaces was a coin
        flip, so any subset has a ~50% chance of looking clean.
        """
        traded = _traded_player_weeks()
        wrong = []
        for row in traded:
            resolved = _resolved_team_id(
                row['season_year'], row['player_id'], row['matchup_period'])
            if resolved != row['chrono_team_id']:
                wrong.append({
                    'season_year': row['season_year'],
                    'matchup_period': row['matchup_period'],
                    'player_id': row['player_id'],
                    'resolved_team_id': resolved,
                    'expected_team_id': row['chrono_team_id'],
                })

        assert not wrong, (
            f"{len(wrong)} of {len(traded)} traded player-weeks resolved to a "
            f"team the player did not finish the period on. Identified by id "
            f"rather than name because team and owner names are user data. "
            f"First five: {wrong[:5]}")

    def test_a_high_id_to_low_id_trade_is_present_in_the_population(self):
        """The direction that fails against the unfixed code.

        Without at least one of these the sweep is satisfiable by the old
        team_id tiebreak, and this file would be green on the bug it was
        written for.
        """
        traded = _traded_player_weeks()
        descending = [r for r in traded
                      if r['chrono_team_id'] < r['largest_team_id']]
        assert descending, (
            "every trade in the league ran low-id to high-id, so the "
            "team_id tiebreak would satisfy the sweep above; this file "
            "cannot currently prove the fix")

