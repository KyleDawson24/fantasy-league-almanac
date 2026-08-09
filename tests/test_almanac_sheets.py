"""Pure tests for the v1.1 almanac Sheets helpers."""

import re

import pytest

import almanac_data
import almanac_render
import almanac_sheets
import stat_catalog


def _player_text(cell):
    """Visible text of a cell, unwrapping a bref =HYPERLINK(url, "text") link
    to its display text. Non-link cells (and non-strings) pass through, so the
    helper is safe to map over a whole row of mixed cells."""
    if isinstance(cell, str):
        match = re.match(r'^=HYPERLINK\("[^"]*", "(.*)"\)$', cell)
        if match:
            return match.group(1).replace('""', '"')
    return cell


def _texts(cells):
    """_player_text mapped over a list of cells."""
    return [_player_text(c) for c in cells]


def _candidate(player_id, slot, points, name=None, team_id=1):
    return {
        'season_year': 2026,
        'matchup_period': 8,
        'lineup_slot': slot,
        'team_id': team_id,
        'team_name': f'Team {team_id}',
        'owner_name': f'Owner {team_id}',
        'player_id': player_id,
        'player_name': name or f'Player {player_id}',
        'display_name': name or f'Player {player_id}',
        'pro_team': 'BOS',
        'platform_points': points,
    }


def _roster_row(team_id, slot, player_id=1, slot_rank=1, slots_to_fill=1):
    return {
        'season_year': 2026,
        'latest_matchup_period': 8,
        'latest_scoring_period': 54,
        'team_id': team_id,
        'team_name': f'Team {team_id}',
        'team_abbrev': f'T{team_id}',
        'owner_name': f'Owner {team_id}',
        'player_id': player_id,
        'player_name': f'Player {player_id}',
        'display_name': f'Player {player_id}',
        'position': 'SP',
        'pro_team': 'BOS',
        'lineup_slot': slot,
        'slot_rank': slot_rank,
        'slots_to_fill': slots_to_fill,
        'active_points': 12.24,
        'active_weeks': 2,
        'active_days': 4,
        'rostered_days': 9,
        'inactive_points': 3.25,
        'hr': 0,
        'rbi': 0,
        'r': 0,
        'sb': 0,
        'w': 1,
        'sv': 0,
        'hld': 1,
        'k': 8,
        'outs': 14,
    }


def _history_player(player_id, name, scope='current_season', team_id=1,
                    active_points=12.2, rostered_days=20, active_days=5,
                    il_days=0, active_slots='', bench_il_points=0):
    return {
        'scope': scope,
        'team_id': team_id,
        'team_name': f'Team {team_id}',
        'team_abbrev': f'T{team_id}',
        'owner_name': f'Owner {team_id}',
        'latest_scoring_period': 54,
        'player_id': player_id,
        'player_name': name,
        'display_name': name,
        'position': 'LF',
        'pro_team': 'BOS',
        'current_fantasy_team': f'Team {team_id}',
        'active_slots_played': active_slots,
        'rostered_days': rostered_days,
        'active_days': active_days,
        'active_games': active_days,
        'bench_days': 0,
        'il_days': il_days,
        'bench_il_points': bench_il_points,
        'active_points': active_points,
        'h': 5,
        'ab': 20,
        'b_bb': 2,
        'hbp': 0,
        'sf': 0,
        'tb': 8,
        'hr': 1,
        'sb': 2,
        'w': 0,
        'l': 0,
        'sv': 0,
        'er': 0,
        'outs': 0,
        'k': 0,
        'p_bb': 0,
        'p_h': 0,
    }


def _active_slot(player_id, slot, scope='current_season', team_id=1,
                 active_days=5, active_points=12.2):
    return {
        'scope': scope,
        'team_id': team_id,
        'player_id': player_id,
        'lineup_slot': slot,
        'active_days_in_slot': active_days,
        'active_points_in_slot': active_points,
    }


def _optimal_pick(player_id, slot, slot_rank=1, slots_to_fill=1, points=12.2):
    """Stand-in for one row of almanac_data.get_optimal_team output.

    v1.1.1: per-team-tab Starters fill now comes from get_optimal_team
    rather than days-active-at-slot. Tests stub the dispatcher with a
    list of these rows so structural assertions (side-by-side, spacer,
    pitcher headers) don't need a live Snowflake connection.
    """
    label = slot if slots_to_fill == 1 else f'{slot} {slot_rank}'
    return {
        'player_id': player_id,
        'lineup_slot': slot,
        'slot_rank': slot_rank,
        'slots_to_fill': slots_to_fill,
        'slot_label': label,
        'platform_points': points,
    }


def _stub_get_optimal_team(monkeypatch, rows_by_team=None):
    """Patch almanac_data.get_optimal_team to return controlled selections.

    rows_by_team: dict {team_id: list of _optimal_pick rows}, or None to
    return an empty list for every call. The build_team_history_side
    caller passes team_id; the stub picks the matching list.
    """
    rows_by_team = rows_by_team or {}

    def fake_optimal_team(season_year=None, matchup_period=None,
                         team_id=None, points_type='active'):
        return list(rows_by_team.get(team_id, []))

    monkeypatch.setattr(almanac_data, 'get_optimal_team', fake_optimal_team)


def _record(grain='team', stat='CALCULATED_POINTS', direction='most', value=123.4):
    return {
        'entity_grain': grain,
        'stat_name': stat,
        'record_direction': direction,
        'rank': 1,
        'season_year': 2026,
        'matchup_period': 8,
        'team_id': 8,
        'team_name': 'Team 8',
        'team_abbrev': 'T8',
        'owner_name': 'Owner 8',
        'player_id': 99,
        'player_name': 'Player 99',
        'display_name': 'Player 99',
        'stat_value': value,
        'contributors': [
            {'display_name': 'Contributor A', 'stat_value': 20},
            {'display_name': 'Contributor B', 'stat_value': 10},
        ],
    }


def _team_week_row():
    return {
        'season_year': 2026,
        'matchup_period': 8,
        'sort_key': 202608,
        'team_id': 8,
        'team_name': 'Team 8',
        'opponent_name': 'Team 9',
        'result': 'W',
        'h': 44,
        'hr': 9,
        'outs': 64,
        'k': 71,
        'nh': 0,
        'platform_hitting_pts': 123.45,
        'platform_pitching_pts': 98.76,
        'platform_points': 222.2,
        'calculated_hitting_pts': 123.45,
        'calculated_pitching_pts': 98.76,
        'calculated_points': 222.2,
        'calculated_margin': 12.3,
        'matchup_calculated_hitting_pts': 250.1,
        'matchup_calculated_pitching_pts': 180.2,
        'matchup_calculated_points': 430.3,
        'league_avg_hitting_points': 100.2,
        'league_avg_pitching_points': 70.4,
        'league_avg_total_points': 170.6,
    }


class TestSlotLabel:
    def test_single_slot_uses_plain_label(self):
        assert almanac_sheets.slot_label('C', 1, 1) == 'C'

    def test_repeated_slot_gets_ordinal_label(self):
        assert almanac_sheets.slot_label('OF', 2, 3) == 'OF 2'


class TestSelectAllLeagueTeam:
    def test_selects_top_players_by_slot_capacity(self):
        candidates = [
            _candidate(1, 'C', 12, 'Catcher A'),
            _candidate(2, 'C', 9, 'Catcher B'),
            _candidate(3, 'OF', 20, 'Outfielder A'),
            _candidate(4, 'OF', 18, 'Outfielder B'),
            _candidate(5, 'OF', 6, 'Outfielder C'),
        ]

        result = almanac_sheets.select_all_league_team(
            candidates,
            {'C': 1, 'OF': 2},
        )

        assert [r['display_name'] for r in result] == [
            'Catcher A',
            'Outfielder A',
            'Outfielder B',
        ]
        assert [r['slot_label'] for r in result] == ['C', 'OF 1', 'OF 2']

    def test_player_moved_slots_selected_once_at_highest_scoring_slot(self):
        candidates = [
            _candidate(1, 'UTIL', 24, 'Same Player'),
            _candidate(1, '1B', 7, 'Same Player'),
            _candidate(2, '1B', 15, 'Other First Baseman'),
        ]

        result = almanac_sheets.select_all_league_team(
            candidates,
            {'1B': 1, 'UTIL': 1},
        )

        assert [r['display_name'] for r in result] == [
            'Other First Baseman',
            'Same Player',
        ]
        assert [r['slot_label'] for r in result] == ['1B', 'UTIL']

    def test_unknown_slots_sort_after_known_slots(self):
        candidates = [
            _candidate(1, 'P', 20, 'Pitcher'),
            _candidate(2, 'MI', 30, 'Middle Infielder'),
        ]

        result = almanac_sheets.select_all_league_team(
            candidates,
            {'P': 1, 'MI': 1},
        )

        assert [r['slot_label'] for r in result] == ['P', 'MI']

    def test_fills_slot_with_best_negative_score_when_needed(self):
        candidates = [
            _candidate(1, 'RP', -3, 'Reliever A'),
            _candidate(2, 'RP', -1, 'Reliever B'),
        ]

        result = almanac_sheets.select_all_league_team(candidates, {'RP': 1})

        assert [r['display_name'] for r in result] == ['Reliever B']
        assert result[0]['platform_points'] == -1


class TestSlotCapacities:
    def test_reads_configured_counts_from_roster_dim(self, monkeypatch):
        calls = []

        def fake_query(sql, params=None):
            calls.append((sql, params))
            return [
                {'lineup_slot': 'C', 'slots_to_fill': 1},
                {'lineup_slot': 'SP', 'slots_to_fill': 5},
            ]

        monkeypatch.setattr(almanac_data, 'query_snowflake', fake_query)

        result = almanac_sheets.get_slot_capacities(2026, 8)

        assert result == {'C': 1, 'SP': 5}
        assert 'dim_roster_slot_counts' in calls[0][0]
        assert calls[0][1] == (2026,)

    def test_missing_configured_counts_raises_helpful_error(self, monkeypatch):
        monkeypatch.setattr(almanac_data, 'query_snowflake', lambda *_: [])

        with pytest.raises(RuntimeError, match='No roster slot counts found'):
            almanac_sheets.get_slot_capacities(2026, 8)


class TestBoxscoreFormula:
    def test_builds_espn_boxscore_hyperlink(self):
        result = almanac_sheets.boxscore_formula(
            league_id=1234567890,
            season_year=2026,
            matchup_period=8,
            team_id=8,
        )

        assert result == (
            '=HYPERLINK("https://fantasy.espn.com/baseball/boxscore?'
            'leagueId=1234567890&matchupPeriodId=8&seasonId=2026&teamId=8'
            '&view=matchup", '
            '"boxscore")'
        )

    def test_missing_parts_returns_blank(self):
        assert almanac_sheets.boxscore_formula(None, 2026, 8, 8) == ''


class TestHomeRows:
    def test_home_rows_two_band_layout_and_deviation(self):
        # Active picks (week + season-to-date).
        weekly = almanac_sheets.select_all_league_team(
            [_candidate(1, 'C', 12.2400000001, 'Catcher A', team_id=8)],
            {'C': 1},
        )
        season = [dict(row, period_label='Season') for row in weekly]
        # points_type='all' lineups: a DIFFERENT player at C, so the
        # Total-Pts deviation columns populate.
        weekly_all = almanac_sheets.select_all_league_team(
            [_candidate(2, 'C', 30.0, 'Bench Catcher', team_id=9)],
            {'C': 1},
        )
        season_all = [dict(row, period_label='Season') for row in weekly_all]
        # All-time team (left band, thin Slot|Player|Pts|ppg).
        all_time = almanac_sheets.select_all_league_team(
            [_candidate(3, 'C', 600.0, 'All-Time Catcher', team_id=8)],
            {'C': 1},
        )
        for row in all_time:
            row['games_played'] = 200

        rows = almanac_sheets.build_home_tab_rows(
            weekly_rows=weekly,
            season_rows=season,
            weekly_all_rows=weekly_all,
            season_all_rows=season_all,
            all_time_rows=all_time,
            season_year=2026,
            matchup_period=8,
            team_titles=['TTA', 'TTC'],
            league_id=1234567890,
        )

        # Banner (spans both bands). Row 3 carries the render-time
        # 'Updated ...' stamp (MLB-141); the byte-diff harnesses blank it
        # via SUPPRESS_UPDATED_STAMP=1.
        assert rows[0] == ['Fantasy League Almanac']
        assert 'current-season scoring' in rows[1][0]
        assert rows[2][0].startswith('Updated ')

        # Right band (cols F+): section labels + header (HOME_HEADER + the
        # deviation group label).
        right_first = [r[5] if len(r) > 5 else '' for r in rows]
        assert 'All-League Team of the Week: 2026 Week 8' in right_first
        assert 'All-League Team Season-to-Date: 2026' in right_first
        header = next(r for r in rows if len(r) > 5 and r[5] == 'Slot')
        assert header[5:13] == almanac_sheets.HOME_HEADER
        assert header[13] == almanac_sheets.HOME_DEVIATION_LABEL

        # Right-band data rows for slot C: week (boxscore embedded in Points,
        # col K / idx 10) and season (plain Points). Both carry the deviation
        # player + total pts (idx 13/14) because the all-lens pick differs.
        c_rows = [r for r in rows if len(r) > 14 and r[5] == 'C' and _player_text(r[7]) == 'Catcher A']
        assert len(c_rows) == 2
        week_c, season_c = c_rows
        assert str(week_c[10]).startswith('=HYPERLINK(')
        assert season_c[10] == 12.2
        assert _player_text(week_c[13]) == 'Bench Catcher' and week_c[14] == 30.0
        assert _player_text(season_c[13]) == 'Bench Catcher' and season_c[14] == 30.0

        # Left band (cols A-D): nav labels, per-team grid, glossary, all-time.
        left_first = [r[0] if r else '' for r in rows]
        assert 'Navigate' in left_first
        assert 'Points Glossary' in left_first
        assert 'All-League Team: All-Time' in left_first
        # Per-team grid is indented (col A blank, teams in B-C).
        grid = next(r for r in rows if len(r) > 2 and r[1] == 'TTA')
        assert grid[2] == 'TTC'
        alltime_c = next(
            r for r in rows
            if len(r) > 3 and r[0] == 'C' and _player_text(r[1]) == 'All-Time Catcher'
        )
        assert alltime_c[2] == 600  # whole number (no decimal at the all-time scale)
        assert alltime_c[3] == '3.00'  # 600 / 200 games


class TestTeamWeeksRows:
    def test_team_weeks_tab_builds_hidden_key_and_stat_sections(self):
        stat_specs = [
            {
                'stat_name': 'H',
                'abbrev': 'H',
                'display_name': 'Hits',
                'stat_category': 'hitting',
                'points_per_unit': 1,
            },
            {
                'stat_name': 'HR',
                'abbrev': 'HR',
                'display_name': 'Home Runs',
                'stat_category': 'hitting',
                'points_per_unit': 4,
            },
            {
                'stat_name': 'OUTS',
                'abbrev': 'IP',
                'display_name': 'Innings Pitched',
                'stat_category': 'pitching',
                'points_per_unit': 1,
            },
            {
                'stat_name': 'K',
                'abbrev': 'K',
                'display_name': 'Strikeouts',
                'stat_category': 'pitching',
                'points_per_unit': 1,
            },
        ]

        rows = almanac_sheets.build_team_weeks_tab_rows(
            [_team_week_row()],
            stat_specs,
            league_id=1234567890,
        )

        assert rows[0] == [
            'Sort Key', 'Season', 'Matchup', 'Team',
            'H', 'HR', '', 'IP', 'K', '',
            'Hitting Points', 'Pitching Points', 'Total Points', 'Margin', 'W', 'L',
            '', 'Matchup Hit', 'Matchup Pitch', 'Matchup Total',
            '', 'Lg Avg Hit', 'Lg Avg Pitch', 'Lg Avg Total',
        ]
        assert rows[1][0:4] == [
            202608, 2026,
            '=HYPERLINK("https://fantasy.espn.com/baseball/boxscore?'
            'leagueId=1234567890&matchupPeriodId=8&seasonId=2026&teamId=8'
            '&view=matchup", "Week 8")',
            'Team 8',
        ]
        assert rows[1][4:9] == [44, 9, '', '21.1', 71]
        assert rows[1][10:24] == [
            123.5, 98.8, 222.2, 12.3, 1, '',
            '', 250.1, 180.2, 430.3, '', 100.2, 70.4, 170.6,
        ]

    def test_team_week_stat_sort_and_rare_helpers(self):
        assert almanac_sheets._team_week_stat_sort_key({
            'stat_category': 'hitting',
            'stat_name': 'H',
            'display_name': 'Hits',
        }) < almanac_sheets._team_week_stat_sort_key({
            'stat_category': 'hitting',
            'stat_name': 'HR',
            'display_name': 'Home Runs',
        })
        assert almanac_sheets._is_rare_team_week_stat('NH')
        assert not almanac_sheets._is_rare_team_week_stat('HR')


class TestRecordsRows:
    def test_scored_record_specs_are_best_only_and_sectioned(self):
        rows = [
            {
                'stat_name': 'HR',
                'display_name': 'Home Runs',
                'stat_category': 'hitting',
                'polarity': 'positive',
            },
            {
                'stat_name': 'B_SO',
                'display_name': 'Strikeouts (Batter)',
                'stat_category': 'hitting',
                'polarity': 'negative',
            },
            {
                'stat_name': 'K',
                'display_name': 'Strikeouts (Pitcher)',
                'stat_category': 'pitching',
                'polarity': 'positive',
            },
        ]

        result = almanac_sheets.build_scored_record_specs(rows)

        assert result == [
            {
                'section': 'Team Hitting Records',
                'label': 'HR',
                'grain': 'team',
                'stat_name': 'HR',
                'direction': 'most',
            },
            {
                'section': 'Team Hitting Records',
                'label': 'Strikeouts (Batter)',
                'grain': 'team',
                'stat_name': 'B_SO',
                'direction': 'fewest',
            },
            {
                'section': 'Team Pitching Records',
                'label': 'Strikeouts (Pitcher)',
                'grain': 'team',
                'stat_name': 'K',
                'direction': 'most',
            },
        ]

    def test_hitting_record_specs_use_curated_hit_type_order(self):
        rows = [
            {
                'stat_name': 'HR',
                'display_name': 'Home Runs',
                'stat_category': 'hitting',
                'polarity': 'positive',
            },
            {
                'stat_name': 'H',
                'display_name': 'Hits',
                'stat_category': 'hitting',
                'polarity': 'positive',
            },
            {
                'stat_name': 'DOUBLES',
                'display_name': 'Doubles',
                'stat_category': 'hitting',
                'polarity': 'positive',
            },
            {
                'stat_name': 'SINGLES',
                'display_name': 'Singles',
                'stat_category': 'hitting',
                'polarity': 'positive',
            },
            {
                'stat_name': 'TRIPLES',
                'display_name': 'Triples',
                'stat_category': 'hitting',
                'polarity': 'positive',
            },
            {
                'stat_name': 'GDP',
                'display_name': 'GIDP (Batter)',
                'stat_category': 'hitting',
                'polarity': 'negative',
            },
            {
                'stat_name': 'B_IBB',
                'display_name': 'Intentional Walks (Batter)',
                'stat_category': 'hitting',
                'polarity': 'positive',
            },
        ]

        result = almanac_sheets.build_scored_record_specs(rows)

        assert [row['label'] for row in result] == [
            'GIDP (Batter)', 'Hits', '1B', '2B', '3B', 'HR', 'Intentional Walks (Batter)',
        ]

    def test_record_side_is_small_tie_helper_matches_list_threshold(self):
        assert almanac_sheets._record_side_is_small_tie('TTD')
        assert almanac_sheets._record_side_is_small_tie('TTE, TTF')
        assert almanac_sheets._record_side_is_small_tie('3 teams tied')
        assert not almanac_sheets._record_side_is_small_tie('4 teams tied')
        assert not almanac_sheets._record_side_is_small_tie('22 teams tied')

    def test_current_season_tie_count_is_scoped(self, monkeypatch):
        calls = []

        def fake_query(sql, params=None):
            calls.append((sql, params))
            return [{'n': 14}]

        monkeypatch.setattr(almanac_data, 'query_snowflake', fake_query)

        result = almanac_sheets.count_value_occurrences_for_scope(
            'current_season',
            'team',
            'CS',
            0,
        )

        assert result == 14
        assert 'season_year = (' in calls[0][0]
        assert calls[0][1] == (0,)

    def test_positive_zero_record_is_suppressed_as_never_occurred(self):
        row = _record(stat='CYC', direction='most', value=0)

        assert almanac_sheets._record_never_occurred(row)

    def test_negative_zero_record_is_not_suppressed(self):
        row = _record(stat='CS', direction='fewest', value=0)

        assert not almanac_sheets._record_never_occurred(row)

    def test_wasted_points_records_read_inactive_rows(self, monkeypatch):
        calls = []

        def fake_query(sql, params=None):
            calls.append((sql, params))
            return [_record(stat='WASTED_POINTS', value=88.6)]

        monkeypatch.setattr(almanac_data, 'query_snowflake', fake_query)

        result = almanac_sheets.get_wasted_points_records('current_season')

        assert result[0]['contributors'] == []
        assert 'performance_status = \'inactive\'' in calls[0][0]
        assert calls[0][1] == ('current_season',)

    def test_record_row_formats_team_record_with_contributors_and_boxscore(self):
        row = _record(stat='HR', value=5)

        result = almanac_sheets.format_record_row(
            row,
            scope_label='All-Time',
            league_id=1234567890,
            display_map={'HR': 'Home Runs'},
            polarity_map={'HR': 'positive'},
            schedule_lookup={(2026, 8): {'is_playoff': False}},
        )

        assert result[0:6] == [
            'All-Time', 'Best Team Home Runs', 'Team 8', 'T8', 'Owner 8', '5',
        ]
        assert result[7] == 'Week 8'
        assert result[8] == 'Contributor A: 20, Contributor B: 10'
        assert result[9].startswith('=HYPERLINK(')

    def test_records_tab_uses_side_by_side_current_then_all_time(self, monkeypatch):
        # The caption thresholds are the builder's only non-injectable input;
        # stub them so the test never reaches the warehouse.
        monkeypatch.setattr(stat_catalog, 'get_rate_qualifiers',
                            lambda: {'AVG': ('ab', 175), 'ERA': ('outs', 450)})
        all_time = [_record(stat='HR', value=5)]
        current = [_record(stat='HR', value=4)]
        specs = [{
            'section': 'Scored Hitting Records',
            'label': 'Team Home Runs',
            'grain': 'team',
            'stat_name': 'HR',
            'direction': 'most',
        }]

        result = almanac_sheets.build_records_tab_rows(
            all_time,
            current,
            league_id=1234567890,
            display_map={'HR': 'Home Runs'},
            schedule_lookup={(2026, 8): {'is_playoff': False}},
            record_specs=specs,
        )

        assert result[0] == ['League Records']
        header_index = next(
            i for i, row in enumerate(result)
            if row[0:2] == ['Scored Hitting Records', 'Current Season']
        )
        assert result[header_index + 1] == almanac_sheets.RECORDS_MATRIX_DETAIL_HEADER
        data_row = result[header_index + 2]
        assert data_row[0:4] == ['Team Home Runs', 'T8', 'Owner 8', '4']
        assert data_row[4].startswith('=HYPERLINK(')
        assert data_row[7:10] == ['T8', 'Owner 8', '5']
        assert 'Week 8: 2026' in data_row[10]

    def test_lineup_slot_specs_expand_repeated_roster_slots(self, monkeypatch):
        monkeypatch.setattr(almanac_data, 'query_snowflake', lambda *_: [
            {'lineup_slot': 'C', 'slots_to_fill': 1},
            {'lineup_slot': 'SP', 'slots_to_fill': 3},
        ])

        result = almanac_sheets.get_lineup_slot_record_specs()

        assert result == [
            {
                'section': 'Lineup Slot Records',
                'label': 'C',
                'grain': 'player',
                'stat_name': 'LINEUP_SLOT_POINTS__C__1',
                'direction': 'most',
            },
            {
                'section': 'Lineup Slot Records',
                'label': 'SP 1',
                'grain': 'player',
                'stat_name': 'LINEUP_SLOT_POINTS__SP__1',
                'direction': 'most',
            },
            {
                'section': 'Lineup Slot Records',
                'label': 'SP 2',
                'grain': 'player',
                'stat_name': 'LINEUP_SLOT_POINTS__SP__2',
                'direction': 'most',
            },
            {
                'section': 'Lineup Slot Records',
                'label': 'SP 3',
                'grain': 'player',
                'stat_name': 'LINEUP_SLOT_POINTS__SP__3',
                'direction': 'most',
            },
        ]


class TestTeamRosterRows:
    def test_team_tab_title_is_short_stable_and_safe(self):
        row = {
            'team_id': 8,
            'team_abbrev': 'A/B:C*D?',
            'team_name': 'Longer Team Name',
        }

        assert almanac_sheets.team_tab_title(row) == 'A-B-C-D-'

    def test_team_roster_row_formats_repeated_slot_and_counts(self):
        row = _roster_row(8, 'SP', slot_rank=2, slots_to_fill=5)

        result = almanac_sheets.format_team_roster_row(
            row,
            league_id=1234567890,
        )

        assert _texts(result[0:5]) == ['SP 2', 'BOS', 'Player 1', 'SP', 12.2]
        assert result[5:9] == [2, 4, 9, 3.2]
        assert result[13:18] == [1, 0, 1, 8, '4.2']

    def test_build_team_roster_tabs_groups_by_team(self):
        rows = [
            _roster_row(2, 'C', player_id=20),
            _roster_row(1, 'C', player_id=10),
            _roster_row(1, 'BE', player_id=11, slot_rank=1, slots_to_fill=5),
        ]

        result = almanac_sheets.build_team_roster_tabs(rows, season_year=2026)

        assert [title for title, _ in result] == ['T1', 'T2']
        assert result[0][1][0] == ['Team 1']
        assert result[0][1][3] == almanac_sheets.TEAM_ROSTER_HEADER
        assert [_player_text(r[2]) for r in result[0][1][4:]] == ['Player 10', 'Player 11']

    def test_build_team_roster_tabs_adds_blank_configured_slots(self):
        rows = [
            _roster_row(1, 'C', player_id=10),
            _roster_row(1, 'BE', player_id=11, slot_rank=1, slots_to_fill=2),
        ]

        result = almanac_sheets.build_team_roster_tabs(
            rows,
            season_year=2026,
            slot_caps={'C': 1, 'LF': 1, 'BE': 2},
        )

        data_rows = result[0][1][4:]
        assert [r[0] for r in data_rows] == ['C', 'LF', 'BE 1', 'BE 2']
        assert data_rows[1] == ['LF', *([''] * (len(almanac_sheets.TEAM_ROSTER_HEADER) - 1))]
        assert data_rows[3] == ['BE 2', *([''] * (len(almanac_sheets.TEAM_ROSTER_HEADER) - 1))]


class TestTeamHistoryRows:
    def test_history_side_uses_optimal_team_for_starters_and_total_points_for_bench(self, monkeypatch):
        """v1.1.1: Starters come from get_optimal_team; Bench sorts by
        active + bench/IL points (Approach 1) so a higher-producing
        bench-mostly player outranks an everyday-but-low-output starter."""
        players = [
            _history_player(
                1, 'Everyday LF', team_id=1,
                active_points=20, rostered_days=54, active_days=5,
                bench_il_points=0,
            ),
            _history_player(
                2, 'Big Bench Bat', team_id=1,
                active_points=10, rostered_days=10, active_days=2,
                bench_il_points=40, active_slots='1B, LF',
            ),
            _history_player(
                3, 'IL Stash', team_id=1,
                active_points=0, rostered_days=20, il_days=20,
            ),
        ]
        # Stub the optimal-team dispatcher so the LF Starter comes from
        # a known pick (Big Bench Bat) instead of needing live data.
        _stub_get_optimal_team(monkeypatch, {1: [_optimal_pick(2, 'LF')]})

        result = almanac_sheets.build_team_history_side(
            players,
            {'LF': 1, 'BE': 1, 'IL': 2},
            season_year=2026,
            team_id=1,
        )

        # Starter row pulls roster-context fields (active_points, etc.)
        # from the player_rows, not the position-pts in the stub.
        assert _player_text(result['LF']['player']) == 'Big Bench Bat'
        assert result['LF']['active_points'] == 10
        # Bench: Everyday LF (20+0=20) > whoever's left (Big Bench Bat
        # already picked); IL Stash has 0+0=0 so Everyday LF lands BE.
        assert _player_text(result['BE']['player']) == 'Everyday LF'
        # IL still uses il_days filter; first IL row goes to IL Stash.
        assert _player_text(result['IL 1']['player']) == 'IL Stash'
        assert result['IL 2']['player'] == ''

    def test_history_side_bench_sort_uses_total_rostered_production(self, monkeypatch):
        """Approach 1: 'blocked by a better player' bench framing -- a
        player with high inactive (bench/IL) points outranks one with
        only mediocre active points."""
        players = [
            _history_player(
                1, 'Mostly Benched Slugger', team_id=2,
                active_points=15, bench_il_points=60, rostered_days=40,
            ),
            _history_player(
                2, 'Modest Everyday', team_id=2,
                active_points=30, bench_il_points=0, rostered_days=40,
            ),
        ]
        _stub_get_optimal_team(monkeypatch, {2: []})  # no Starters; both go to Bench/Other

        result = almanac_sheets.build_team_history_side(
            players, {'BE': 1}, season_year=2026, team_id=2,
        )

        assert _player_text(result['BE']['player']) == 'Mostly Benched Slugger'  # 75 > 30
        assert _player_text(result['Other 1']['player']) == 'Modest Everyday'
        # No futility chair on a CURRENT-season side unless its 100-cap
        # cut fires (Kyle 2026-07-17); the chair is an all-time feature.
        assert almanac_render.TEAM_HISTORY_OTHER_WORST not in result

    def test_history_side_all_time_pins_futility_chair(self, monkeypatch):
        """All-time sides always seat the chair: the worst player by
        rostered_days - total_points is pulled from the Other ranking
        and pinned under the sentinel label with a Worst slot."""
        players = [
            _history_player(
                1, 'Fine Player', team_id=2,
                active_points=500, bench_il_points=0, rostered_days=100,
            ),
            _history_player(
                2, 'Roster Barnacle', team_id=2,
                active_points=5, bench_il_points=0, rostered_days=300,
            ),
        ]
        _stub_get_optimal_team(monkeypatch, {2: []})

        result = almanac_sheets.build_team_history_side(
            players, {}, season_year=None, team_id=2,
        )

        worst = result[almanac_render.TEAM_HISTORY_OTHER_WORST]
        assert _player_text(worst['player']) == 'Roster Barnacle'
        assert worst['display_slot'].startswith('Worst')
        assert _player_text(result['Other 1']['player']) == 'Fine Player'
        assert 'Other 2' not in result

    def test_hitting_rates_keep_three_digits_for_low_rates(self):
        row = _history_player(1, 'Slumping Bat')
        row.update({'h': 1, 'ab': 25, 'b_bb': 0, 'hbp': 0, 'sf': 0, 'tb': 2})

        result = almanac_sheets._team_history_stat_line(row, 'LF')

        assert result['stat_1'] == '040'
        assert result['stat_2'] == '040'
        assert result['stat_3'] == '080'

    def test_team_history_matrix_keeps_current_and_all_time_side_by_side(self, monkeypatch):
        history_data = {
            'players': [
                _history_player(1, 'Current LF', scope='current_season', team_id=16),
                _history_player(2, 'All Time LF', scope='all_time', team_id=16),
            ],
        }
        # Both sides call get_optimal_team(team_id=16) -- once with
        # season_year=2026, once with None. The stub doesn't distinguish:
        # it returns the LF pick for team 16 in both cases. The two
        # sides differ because each is fed a different scope's players.
        _stub_get_optimal_team(monkeypatch, {16: [_optimal_pick(1, 'LF')]})
        # Re-stub for the all-time side: player_id=2 is the all-time
        # match. Build a side-dispatching stub so the right player_id
        # lands on each side.
        def fake_optimal(season_year=None, matchup_period=None,
                         team_id=None, points_type='active'):
            return [_optimal_pick(1 if season_year else 2, 'LF')]
        monkeypatch.setattr(almanac_data, 'get_optimal_team', fake_optimal)

        tabs = almanac_sheets.build_team_history_tabs(
            history_data,
            season_year=2026,
            slot_caps={'LF': 1},
        )

        rows = tabs[0][1]
        assert rows[3][0] == 'Current Season'
        assert rows[3][16] == 'All-Time'
        # Row 4 also carries the merged-header text (Kyle 2026-07-17):
        # 'Roster Days' over the E/U pairs, 'Points' over the trio.
        assert rows[3][4] == 'Roster Days'
        assert rows[3][6] == 'Points'
        assert rows[4] == almanac_sheets.TEAM_ROSTER_HEADER
        assert rows[5][10:15] == ['Avg', 'OBP', 'Slg', 'HR', 'SB']
        # v1.1.1: current_fantasy_team column now holds '*' when the
        # player is still on this tab's team (was the redundant abbrev).
        # The _history_player helper hardcodes the field to the team's
        # full name -- testing only the slot/player/pro columns here.
        assert _texts(rows[6][1:4]) == ['LF', 'Current LF', 'BOS']
        assert _texts(rows[6][17:20]) == ['LF', 'All Time LF', 'BOS']

    def test_team_history_matrix_inserts_spacer_before_other_rows(self, monkeypatch):
        history_data = {
            'players': [
                _history_player(1, 'Current LF', scope='current_season', team_id=16),
                _history_player(
                    2, 'Other Bat', scope='current_season', team_id=16,
                    active_points=1, rostered_days=1,
                ),
            ],
        }
        _stub_get_optimal_team(monkeypatch, {16: [_optimal_pick(1, 'LF')]})

        tabs = almanac_sheets.build_team_history_tabs(
            history_data,
            season_year=2026,
            slot_caps={'LF': 1},
        )

        rows = tabs[0][1]
        assert rows[7] == [''] * len(almanac_sheets.TEAM_ROSTER_HEADER)
        assert rows[8][1] == 'Other - LF'

    def test_team_history_matrix_adds_pitcher_and_mixed_stat_headers(self, monkeypatch):
        history_data = {
            'players': [
                _history_player(1, 'Current LF', scope='current_season', team_id=16),
                _history_player(2, 'Starter', scope='current_season', team_id=16),
                _history_player(3, 'Bench Bat', scope='current_season', team_id=16),
            ],
        }
        _stub_get_optimal_team(
            monkeypatch,
            {16: [_optimal_pick(1, 'LF'), _optimal_pick(2, 'SP')]},
        )

        tabs = almanac_sheets.build_team_history_tabs(
            history_data,
            season_year=2026,
            slot_caps={'LF': 1, 'SP': 1, 'BE': 1},
        )

        rows = tabs[0][1]
        assert rows[5][10:15] == ['Avg', 'OBP', 'Slg', 'HR', 'SB']
        assert rows[7][10:15] == ['W-L (Sv)', 'ERA', 'WHIP', 'K', 'BB']
        assert rows[9][10:15] == ['Avg|W-L-Sv', 'OBP|ERA', 'Slg|WHIP', 'HR|K', 'SB|BB']

    def test_pitcher_decision_display_drops_saves_when_none(self):
        # v1.1.2: saves == 0 -> plain W-L.
        row = _history_player(2, 'Starter')
        row.update({'w': 6, 'l': 4, 'sv': 0})

        assert almanac_sheets._pitching_decision_display(row, 'SP') == '6-4'

    def test_pitcher_decision_display_appends_saves_when_present(self):
        # v1.1.2: saves > 0 -> W-L-Sv (closers read 2-1-15, not just 15).
        row = _history_player(2, 'Closer')
        row.update({'w': 2, 'l': 1, 'sv': 15})

        assert almanac_sheets._pitching_decision_display(row, 'RP') == '2-1-15'

    def test_display_slot_alignment_helpers_split_pitchers_and_hitters(self):
        assert almanac_sheets._is_pitcher_display_slot('BE - RP')
        assert almanac_sheets._is_pitcher_display_slot('SP 4')
        assert not almanac_sheets._is_pitcher_display_slot('BE - 1B,LF')
        assert almanac_sheets._is_hitter_display_slot('BE - 1B,LF')
        assert almanac_sheets._is_hitter_display_slot('C')
        assert not almanac_sheets._is_hitter_display_slot('Slot')


def _standings_spec(stat_name, category, abbrev=None, points=1):
    return {
        'stat_name': stat_name,
        'display_name': stat_name,
        'abbrev': abbrev or stat_name,
        'stat_category': category,
        'points_per_unit': points,
    }


# Two stats per category, one negative-weighted in each, is enough to
# exercise the layout, the polarity handling, and the OUTS special case.
_STANDINGS_SPECS = [
    _standings_spec('HR', 'hitting', points=2),
    _standings_spec('B_SO', 'hitting', abbrev='K', points=-1),
    _standings_spec('OUTS', 'pitching', abbrev='IP', points=1),
    _standings_spec('L', 'pitching', points=-5),
]


def _standings_team(team_id=1, abbrev='TTA', wins=2, losses=1, ties=0,
                    **overrides):
    row = {
        'team_id': team_id,
        'team_abbrev': abbrev,
        'team_name': f'Team {team_id}',
        'owner_display': f'Owner {team_id}',
        'wins': wins,
        'losses': losses,
        'ties': ties,
        'matchup_periods_played': 2,
        'scoring_days_played': 16,
        'standard_matchup_days': 8,
        'calculated_hitting_pts': 100.0,
        'calculated_pitching_pts': 50.0,
        'calculated_points': 150.0,
        'against_calculated_points': 120.0,
        'hr': 10,
        'b_so': 20,
        'outs': 60,
        'l': 4,
    }
    row.update(overrides)
    return row


def _acq_team(team_id=1, abbrev='TTA', **overrides):
    """A mart_team_acquisition_channels-shaped row for the acquisition blocks."""
    row = {
        'team_id': team_id,
        'team_abbrev': abbrev,
        'owner_display': f'Owner {team_id}',
    }
    for lens in ('active', 'rostered'):
        row.update({
            f'keeper_{lens}_pts': 100.0,
            f'draft_{lens}_pts': 200.0,
            f'trade_{lens}_pts': 50.0,
            f'fa_add_{lens}_pts': 25.0,
            f'acquired_{lens}_pts': 375.0,
            f'dropped_{lens}_pts': 10.0,
            f'traded_away_{lens}_pts': 5.0,
            f'lost_{lens}_pts': 15.0,
            f'fa_delta_{lens}_pts': 15.0,
            f'trade_delta_{lens}_pts': 45.0,
        })
    row.update(overrides)
    return row


class TestAdvancedStandingsRows:
    def test_standings_header_layout(self):
        header = almanac_sheets.standings_header(
            _STANDINGS_SPECS[:2], _STANDINGS_SPECS[2:],
        )

        assert header == [
            'Rank', 'Team', 'Owner', 'W-L',
            'HR', 'K', 'Offense', '',
            'IP', 'L', 'Defense', '',
            'Total', 'Against',
        ]

    def test_format_standings_row_normalizes_per_standard_matchup(self):
        # 16 gameplay days at a standard 8-day matchup halves every total.
        row = almanac_render.format_standings_row(
            3, _standings_team(), _STANDINGS_SPECS[:2], _STANDINGS_SPECS[2:],
        )

        assert row == [
            3, 'TTA', 'Owner 1', '2-1',
            5.0, 10.0, 50.0, '',
            10.0, 2.0, 25.0, '',
            75.0, 60.0,
        ]

    def test_format_standings_row_outs_render_as_decimal_ip(self):
        # 61 outs = 20.33 IP over two standard matchups -> 10.2 IP/week as
        # a base-10 decimal, not baseball .1/.2 thirds notation.
        row = almanac_render.format_standings_row(
            1, _standings_team(outs=61),
            _STANDINGS_SPECS[:2], _STANDINGS_SPECS[2:],
        )

        assert row[8] == pytest.approx(10.2)

    def test_format_standings_row_shows_ties_when_present(self):
        row = almanac_render.format_standings_row(
            1, _standings_team(ties=1),
            _STANDINGS_SPECS[:2], _STANDINGS_SPECS[2:],
        )

        assert row[3] == '2-1-1'

    def test_per_week_value_blank_without_denominator(self):
        assert almanac_render._per_week_value(
            {'scoring_days_played': None}, 42,
        ) == ''

    def test_gradient_columns_positions_and_polarity(self):
        columns = almanac_sheets.standings_gradient_columns(
            _STANDINGS_SPECS[:2], _STANDINGS_SPECS[2:],
        )

        assert columns == [
            (4, 'most'), (5, 'fewest'),    # HR, K (batter strikeouts)
            (6, 'most'),                   # Offense
            (8, 'most'), (9, 'fewest'),    # IP, L
            (10, 'most'),                  # Defense
            (12, 'most'), (13, 'fewest'),  # Total, Against
        ]

    def test_gradient_columns_skip_zero_weighted_stats(self):
        columns = almanac_sheets.standings_gradient_columns(
            [_standings_spec('AB', 'hitting', points=0)], [],
        )

        assert columns[0] == (4, None)

    def test_build_advanced_standings_tab_rows_layout(self):
        standings = [
            _standings_team(team_id=1, abbrev='TTA', wins=2, losses=1),
            _standings_team(team_id=2, abbrev='TTB', wins=1, losses=2),
        ]
        slot_rows = [
            {'team_id': 1, 'lineup_slot': 'SP', 'slot_pts': 9.9, 'sort_order': 140},
            {'team_id': 1, 'lineup_slot': 'C', 'slot_pts': 5.5, 'sort_order': 10},
            {'team_id': 2, 'lineup_slot': 'C', 'slot_pts': 4.4, 'sort_order': 10},
        ]

        rows = almanac_sheets.build_advanced_standings_tab_rows(
            standings, slot_rows, _STANDINGS_SPECS, 2026,
        )

        assert rows[0] == ['Advanced Standings: 2026']
        # Subtitle reads the DERIVED standard matchup length (8 here), not
        # a hardcoded 7.
        assert 'averages per 8 days of gameplay' in rows[1][0]
        # The standings-order caveat sits flush above the table it
        # describes. With no rank arc there is no chart to disagree with,
        # so the chart half of the sentence stays out.
        assert rows[3] == ["Standings order is pulled from league's official "
                           'standings, which may put division winners first.']
        # (β) MLB-142: scope rides the banner row as an italic caption.
        assert rows[4] == ['Detailed Standings', '', '',
                           'Weekly Averages, Current Season']
        assert rows[5][:4] == ['Rank', 'Team', 'Owner', 'W-L']
        assert rows[6][:2] == [1, 'TTA']
        assert rows[7][:2] == [2, 'TTB']

        # Slot grid: indented one cell with Owner added so Team / Owner sit
        # under Table A's columns; slot columns in sort_order (C before SP
        # despite input order); a team missing a slot renders blank. BE/IL
        # never arrive here -- the data layer filters to active slots.
        # (Single blank between sections since the round-11 parity pass.)
        assert rows[9] == ['Points by Lineup Slot', '', '', 'Season Totals']
        assert rows[10] == ['', 'Team', 'Owner', 'C', 'SP']
        assert rows[11] == ['', 'TTA', 'Owner 1', 5.5, 9.9]
        assert rows[12] == ['', 'TTB', 'Owner 2', 4.4, '']

    def test_acquisition_header_layout(self):
        # Kyle rounds 8+12: one table per lens, season half left /
        # all-time half right, and every ESPN L/R split shares the U
        # divider -- the left half pads out so the right starts at V (21).
        half = ['Keeper', 'Draft', 'Pickup', 'Trade', 'Total', '',
                'Release', 'Trade', 'Total', '', 'FA', 'Trade']
        assert almanac_sheets.ACQUISITION_HEADER == [
            '', 'Team', 'Owner', *half, *[''] * 6, *half,
        ]
        assert almanac_sheets.ACQUISITION_HEADER[21] == 'Keeper'
        band = almanac_render.ACQUISITION_BAND_ROW
        for base in (3, 21):
            assert band[base] == 'Points Acquired Via'
            assert band[base + 6] == 'Points Lost Via'
            assert band[base + 10] == 'Net Points via'
        assert len(band) == len(almanac_sheets.ACQUISITION_HEADER)

    def test_acquisition_half_values_active_lens(self):
        half = almanac_render.acquisition_half_values(_acq_team(), 'active')
        assert half == [
            100.0, 200.0, 25.0, 50.0, 375.0, '',
            10.0, 5.0, 15.0, '',
            15.0, 45.0,
        ]

    def test_acquisition_half_reads_the_selected_lens(self):
        # The rostered lens pulls the *_rostered_pts family, not *_active_pts.
        team = _acq_team(keeper_active_pts=1.0, keeper_rostered_pts=999.0)
        assert almanac_render.acquisition_half_values(team, 'active')[0] == 1.0
        assert almanac_render.acquisition_half_values(team, 'rostered')[0] == 999.0

    def test_acquisition_half_zero_and_negative(self):
        # Zeros render as 0.0 (not blank); deltas can go negative.
        team = _acq_team(trade_active_pts=0.0, fa_delta_active_pts=-42.0)
        half = almanac_render.acquisition_half_values(team, 'active')
        assert half[3] == 0.0       # Trade (after Pickup since round 7)
        assert half[10] == -42.0    # Net FA

    def test_acquisition_gradient_columns_positions_and_polarity(self):
        # Acquired channels green-high, Lost buckets green-low, Net deltas
        # zero-centered diverging -- BOTH halves; buffer columns skipped.
        per_half = [(0, 'most'), (1, 'most'), (2, 'most'), (3, 'most'),
                    (4, 'most'),
                    (6, 'fewest'), (7, 'fewest'), (8, 'fewest'),
                    (10, 'diverging'), (11, 'diverging')]
        assert almanac_sheets.acquisition_gradient_columns() == [
            (base + off, d) for base in (3, 21) for off, d in per_half
        ]

    def test_build_advanced_standings_appends_ranked_acquisition_blocks(self):
        standings = [_standings_team(team_id=1, abbrev='TTA')]
        acq = [
            _acq_team(team_id=1, abbrev='TTA', acquired_active_pts=100.0),
            _acq_team(team_id=2, abbrev='TTB', acquired_active_pts=300.0),
        ]
        rows = almanac_sheets.build_advanced_standings_tab_rows(
            standings, [], _STANDINGS_SPECS, 2026, acquisition_rows=acq,
        )

        # MLB-142 round 2: era scopes ride the banner once for both lenses.
        acq_banner = next(r for r in rows
                          if r and r[0] == 'Production by Acquisition Channel')
        assert acq_banner[3] == 'Current Season'
        assert acq_banner[21] == 'All-Time (2026-)'
        lens_labels = [r[0] for r in rows if r and str(r[0]).startswith(
            ('Active Lens', 'Rostered Lens'))]
        assert len(lens_labels) == 2
        # Both block headers carry 'Keeper' (the write layer keys off it).
        acq_headers = [i for i, r in enumerate(rows)
                       if len(r) > 3 and r[1] == 'Team' and 'Keeper' in r]
        assert len(acq_headers) == 2
        # Ranked by the lens's Acquired total desc: BBB (300) before AAA (100).
        first_block = rows[acq_headers[0] + 1: acq_headers[0] + 3]
        assert [r[1] for r in first_block] == ['TTB', 'TTA']

    def test_build_advanced_standings_omits_blocks_without_data(self):
        rows = almanac_sheets.build_advanced_standings_tab_rows(
            [_standings_team()], [], _STANDINGS_SPECS, 2026,
        )
        assert not any(r and r[0] == 'Production by Acquisition Channel'
                       for r in rows)
        titles = [r for r in rows
                  if r and str(r[0]).startswith('Points by Lineup Slot')]
        assert titles == [['Points by Lineup Slot', '', '', 'Season Totals']]
        assert not any(r and r[0] == 'Roster Affinity by MLB Team'
                       for r in rows)

    def test_build_advanced_standings_alltime_slot_grid(self):
        standings = [_standings_team(team_id=1, abbrev='TTA')]
        season_slots = [
            {'team_id': 1, 'lineup_slot': 'C', 'slot_pts': 5.5, 'sort_order': 10},
        ]
        alltime_slots = [
            {'team_id': 1, 'lineup_slot': 'C', 'slot_pts': 1.2, 'sort_order': 10},
            {'team_id': 1, 'lineup_slot': 'SP', 'slot_pts': 9.1, 'sort_order': 140},
        ]

        rows = almanac_sheets.build_advanced_standings_tab_rows(
            standings, season_slots, _STANDINGS_SPECS, 2026,
            slot_rows_alltime=alltime_slots,
        )

        # Rounds 8+12: ONE grid, BOTH halves per-matchup averages, the
        # left half padded so the right starts past the U divider; slot
        # union across halves (SP exists all-time only here, so its
        # season cell is blank).
        titles = [r[0] for r in rows
                  if r and str(r[0]).startswith('Points by Lineup Slot')]
        assert titles == ['Points by Lineup Slot']
        pad = [''] * 16                     # 3 id cols + 2 slots -> V
        hdr = rows.index(['', 'Team', 'Owner', 'C', 'SP', *pad, 'C', 'SP'])
        # (β) MLB-142: the era scopes ride the banner row itself; the
        # separate era-header row is gone and the grid sits one row up.
        banner = rows[hdr - 1]
        assert banner[0] == 'Points by Lineup Slot'
        assert banner[3] == 'Weekly Averages, Current Season'
        assert banner[21] == 'Weekly Averages, All-Time'
        # Season totals now divide by the team's matchups played (2 in
        # the fixture): 5.5 -> 2.8.
        assert rows[hdr + 1] == ['', 'TTA', 'Owner 1',
                                 2.8, '', *pad, 1.2, 9.1]

    def test_build_advanced_standings_affinity_shares(self):
        standings = [_standings_team(team_id=1, abbrev='TTA'),
                     _standings_team(team_id=2, abbrev='TTB')]
        affinity = [
            {'team_id': 1, 'pro_team': 'Atl',
             'season_wt': 30.0, 'alltime_wt': 60.0},
            {'team_id': 1, 'pro_team': 'NYY',
             'season_wt': 10.0, 'alltime_wt': 40.0},
            {'team_id': 2, 'pro_team': 'Atl',
             'season_wt': 5.0, 'alltime_wt': 25.0},
            # A team that left the league: no column, no distortion.
            {'team_id': 99, 'pro_team': 'Atl',
             'season_wt': 7.0, 'alltime_wt': 7.0},
        ]

        rows = almanac_sheets.build_advanced_standings_tab_rows(
            standings, [], _STANDINGS_SPECS, 2026, affinity_rows=affinity,
        )

        assert any(r and r[0] == 'Roster Affinity by MLB Team' for r in rows)
        # Round 14: the season BLOCK indents -- spine at C (riding the
        # Owner column's width), season columns from E, all-time still
        # past the U divider; columns alphabetical by abbrev.
        aff_pad = [''] * 15                 # E + 2 teams -> pad to V
        hdr = rows.index(['', '', 'MLB Team', '', 'TTA', 'TTB', *aff_pad,
                          'TTA', 'TTB'])
        # MLB-142 round 2: the era scopes ride the banner row (two above
        # the header, past the explainer); the separate era row is gone.
        banner = rows[hdr - 2]
        assert banner[0] == 'Roster Affinity by MLB Team'
        assert banner[4] == 'Current Season'
        assert banner[21] == 'All-Time'
        # The spine shows full club names (static abbrev map).
        atl = next(r for r in rows[hdr + 1:]
                   if len(r) > 2 and r[2] == 'Atlanta Braves')
        nyy = next(r for r in rows[hdr + 1:]
                   if len(r) > 2 and r[2] == 'New York Yankees')
        # Shares are per COLUMN, as FRACTIONS (the write layer formats the
        # blocks as PERCENT): AAA season = 30 + 10 involvement, BBB = 5.
        assert atl == ['', '', 'Atlanta Braves', '', 0.75, 1.0,
                       *aff_pad, 0.6, 1.0]
        assert nyy == ['', '', 'New York Yankees', '', 0.25, '',
                       *aff_pad, 0.4, '']

    def test_affinity_unattributed_bucket_sorts_last_and_counts(self):
        """MLB-159: the sentinel bucket renders as a named row, is PINNED
        below every real club, and sits INSIDE the denominator.

        The fixture is built so a regression on any of the three is
        visible. 'Unattributed' sorts BEFORE 'Washington Nationals'
        alphabetically, so a plain name sort would put the band second
        rather than last -- the pin is what this catches. And AAA's
        Atlanta share is 0.75 if the bucket is excluded from the column
        total (the pre-MLB-159 behaviour) versus 0.6 if it is counted, so
        restoring the old filter cannot leave this test green.
        """
        standings = [_standings_team(team_id=1, abbrev='TTA'),
                     _standings_team(team_id=2, abbrev='TTB')]
        affinity = [
            {'team_id': 1, 'pro_team': 'Atl',
             'season_wt': 30.0, 'alltime_wt': 60.0},
            {'team_id': 1, 'pro_team': 'Wsh',
             'season_wt': 10.0, 'alltime_wt': 20.0},
            # What the query emits for rows ESPN stamped 'FA' or NULL --
            # free agent ON EXTRACT DAY, club-when-played unknown.
            {'team_id': 1, 'pro_team': almanac_data.AFFINITY_UNATTRIBUTED,
             'season_wt': 10.0, 'alltime_wt': 20.0},
            # BBB has none, so its column must be untouched: shares are
            # per column, and one team's unknowns cannot move another's.
            {'team_id': 2, 'pro_team': 'Atl',
             'season_wt': 5.0, 'alltime_wt': 25.0},
        ]

        rows = almanac_sheets.build_advanced_standings_tab_rows(
            standings, [], _STANDINGS_SPECS, 2026, affinity_rows=affinity,
        )

        aff_pad = [''] * 15
        hdr = rows.index(['', '', 'MLB Team', '', 'TTA', 'TTB', *aff_pad,
                          'TTA', 'TTB'])
        spine = [r[2] for r in rows[hdr + 1:] if len(r) > 2 and r[2]]

        # Named, not left as the raw sentinel and not rendered as 'FA'.
        assert 'Unattributed' in spine
        assert almanac_data.AFFINITY_UNATTRIBUTED not in spine
        assert 'FA' not in spine
        # Pinned last, though it sorts before Washington by name.
        assert spine == ['Atlanta Braves', 'Washington Nationals',
                         'Unattributed']

        band = rows[hdr + 3]
        atl = rows[hdr + 1]
        # In the denominator: AAA all-time is 60 + 20 + 20, so Atlanta is
        # 0.6 rather than the 0.75 it would be with the bucket dropped.
        assert atl == ['', '', 'Atlanta Braves', '', 0.6, 1.0,
                       *aff_pad, 0.6, 1.0]
        assert band == ['', '', 'Unattributed', '', 0.2, '',
                        *aff_pad, 0.2, '']

    def test_affinity_query_buckets_extract_day_free_agents(self, monkeypatch):
        """MLB-159: the club filter is gone and both stamps route to the
        sentinel -- while the LINEUP-SLOT 'FA' exclusion stays.

        Those two FAs mean different things: pro_team 'FA' is ESPN's
        extract-day stamp (the bug), lineup_slot 'FA' means nobody had the
        player rostered that day (a real exclusion the chart depends on).
        Dropping the wrong one lets unrostered production into the chart,
        so both halves are pinned here.
        """
        calls = []

        def fake_query(sql, params=None):
            calls.append((sql, params))
            return []

        monkeypatch.setattr(almanac_data, 'query_snowflake', fake_query)

        almanac_sheets.get_team_affinity_weights(2026)

        sql = calls[0][0]
        assert "pro_team <> 'FA'" not in sql
        assert "pro_team IS NOT NULL" not in sql
        assert "pro_team IS NULL OR pro_team = 'FA'" in sql
        assert almanac_data.AFFINITY_UNATTRIBUTED in sql
        # The other FA -- unrostered days -- stays excluded.
        #
        # Asserted as a SET rather than as literal text since MLB-222 F-1:
        # the slot list is now rendered from the slot_classification seed
        # and sorted for stable generated SQL, so ('BE', 'FA', 'IL') and
        # ('BE', 'IL', 'FA') are the same exclusion. This is stricter than
        # the substring it replaced -- it fails if a slot is added OR
        # dropped, where the substring only noticed one exact spelling.
        exclusion = re.search(r"lineup_slot NOT IN \(([^)]*)\)", sql)
        assert exclusion, 'the lineup-slot exclusion disappeared entirely'
        excluded = {s.strip().strip("'") for s in exclusion.group(1).split(',')}
        assert excluded == {'BE', 'IL', 'FA'}
        assert calls[0][1] == (2026,)

    def test_rank_chart_block_leads_the_tab(self):
        import almanac_write

        arc = []
        for p in (1, 2):
            arc += [
                {'team_id': 1, 'team_abbrev': 'TTA', 'period': p,
                 'standings_rank': 1},
                {'team_id': 2, 'team_abbrev': 'TTB', 'period': p,
                 'standings_rank': 2},
            ]
        rows = almanac_sheets.build_advanced_standings_tab_rows(
            [_standings_team(team_id=1, abbrev='TTA')], [],
            _STANDINGS_SPECS, 2026, rank_arc_rows=arc)

        # The chart section leads (title, subtitle, blank, then chart).
        assert rows[3] == ['Rank by Week', '', '', 'Current Season']
        chk_idx = next(i for i, r in enumerate(rows)
                       if r and r[0] == '(check to plot)')
        # Kyle's toggle scheme: individuals OFF, one ALL master ON.
        assert rows[chk_idx][1:] == [False, False, True]
        assert rows[chk_idx - 1][:4] == ['Chart teams:', 'TTA', 'TTB', 'ALL']

        b = almanac_write._rank_chart_bounds(rows)
        assert b and b['n_teams'] == 2
        # The helper parks past the widest table (floor col 45 -- Table A
        # runs ~40 wide in production; hiding helper columns inside its
        # width was the Defense/Total/Against truncation Kyle caught).
        assert b['helper_col0'] == 45
        assert b['series_cols'] == [46, 47]
        assert b['raw_end_col0'] == 50
        assert b['last_row'] - b['first_row'] - 1 == 2   # two weeks
        assert rows[b['first_row']][45:] == ['Week', 'TTA', 'TTB',
                                             'TTA', 'TTB']
        data = rows[b['first_row'] + 1]
        # Formulas gate on OR(ALL, own) and read same-row hidden raw ranks.
        assert data[46].startswith('=IF(AND(OR($D$')
        assert '3-' in data[46] and data[46].endswith('NA())')
        assert data[48:50] == [1, 2]

        reqs = almanac_write._rank_chart_requests(9, rows, 2026)
        assert [next(iter(r)) for r in reqs] == [
            'setDataValidation', 'updateDimensionProperties', 'addChart']
        spec = reqs[2]['addChart']['chart']['spec']
        assert spec['basicChart']['chartType'] == 'LINE'
        assert len(spec['basicChart']['series']) == 2
        assert spec['hiddenDimensionStrategy'] == 'SHOW_ALL'

        # The chart half of the standings caveat is present here (there IS
        # a chart) and absent from the chart-less layout below.
        note = next(r[0] for r in rows
                    if r and str(r[0]).startswith('Standings order'))
        assert 'Rank by Week Time Series is reconstructed' in note

        # Without rank rows the tab keeps its classic layout.
        plain = almanac_sheets.build_advanced_standings_tab_rows(
            [_standings_team()], [], _STANDINGS_SPECS, 2026)
        assert plain[3][0].startswith('Standings order is pulled from')
        assert 'Rank by Week' not in plain[3][0]
        assert plain[4] == ['Detailed Standings', '', '',
                            'Weekly Averages, Current Season']
        assert almanac_write._rank_chart_bounds(plain) is None

    def test_finishes_table_beside_the_chart(self):
        import almanac_write

        arc = []
        for p in (1, 2):
            arc += [
                {'team_id': 1, 'team_abbrev': 'TTA', 'period': p,
                 'standings_rank': 1},
                {'team_id': 2, 'team_abbrev': 'TTB', 'period': p,
                 'standings_rank': 2},
            ]
        finishes = [
            {'season_year': 2025, 'team_id': 1, 'team_abbrev': 'TTA',
             'owner_display': 'Owner 1', 'wins': 10, 'losses': 4,
             'ties': 0, 'finish': 1, 'is_champion': False},
            # The playoff upset: BBB finished 2nd but swept the bracket.
            {'season_year': 2025, 'team_id': 2, 'team_abbrev': 'TTB',
             'owner_display': 'Owner 2', 'wins': 8, 'losses': 6,
             'ties': 0, 'finish': 2, 'is_champion': True},
            {'season_year': 2026, 'team_id': 1, 'team_abbrev': 'TTA',
             'owner_display': 'Owner 1', 'wins': 2, 'losses': 0,
             'ties': 0, 'finish': 1, 'is_champion': False},
            {'season_year': 2026, 'team_id': 2, 'team_abbrev': 'TTB',
             'owner_display': 'Owner 2', 'wins': 0, 'losses': 2,
             'ties': 0, 'finish': 2, 'is_champion': False},
        ]
        standings = [_standings_team(team_id=1, abbrev='TTA'),
                     _standings_team(team_id=2, abbrev='TTB')]
        rows = almanac_sheets.build_advanced_standings_tab_rows(
            standings, [], _STANDINGS_SPECS, 2026,
            rank_arc_rows=arc, finishes_rows=finishes)

        fin = almanac_write._espn_finishes_bounds(rows)
        assert fin and fin['col0'] == 21
        assert rows[fin['hdr']][21:] == ['Team', '', '', '', 'Titles',
                                         'W%', 'Avg', '2025', '2026']
        first, second = rows[fin['hdr'] + 1], rows[fin['hdr'] + 2]
        # Sorted by Titles then W%: the 2025 crown outranks the better
        # record; champion cell = trophy; the in-flight column carries
        # the CURRENT reconstructed rank and counts toward nothing.
        assert first[21] == 'Owner 2'
        assert first[25] == 1                       # titles
        assert first[26] == pytest.approx(0.5)      # 8-8 all-time
        assert first[27] == 2.0                     # avg closed finish
        # Trophy AND finish (Kyle 2026-07-18): the champion's regular-
        # season finish is real information in an H2H league.
        assert first[28] == '🏆 2' and first[29] == 2
        assert second[21] == 'Owner 1'
        assert second[25] == ''                     # titles blank at 0
        assert second[26] == pytest.approx(0.75)    # 12-4 all-time
        assert second[28] == 1 and second[29] == 1

    def test_medals_read_the_playoff_finish_not_the_seed(self):
        """MLB-230. In an H2H league the podium is settled in the bracket,
        so silver and bronze key on the platform's post-playoff rank. The
        seed stays printed beside the medal because the two genuinely
        disagree -- here the 1 seed lost the final and the 3 seed won the
        third-place game."""
        import almanac_write

        arc = [{'team_id': t, 'team_abbrev': f'TT{t}', 'period': 1,
                'standings_rank': t} for t in (1, 2, 3)]

        def fin(team, seed, final_rank, champion=False):
            return {'season_year': 2025, 'team_id': team,
                    'team_abbrev': f'TT{team}', 'owner_display': f'Owner {team}',
                    'wins': 10, 'losses': 4, 'ties': 0, 'finish': seed,
                    'final_rank': final_rank, 'is_champion': champion}

        finishes = [fin(1, 1, 2), fin(2, 2, 1, champion=True), fin(3, 3, 3)]
        rows = almanac_sheets.build_advanced_standings_tab_rows(
            [_standings_team(team_id=t, abbrev=f'TT{t}') for t in (1, 2, 3)],
            [], _STANDINGS_SPECS, 2026,
            rank_arc_rows=arc, finishes_rows=finishes)

        fin_b = almanac_write._espn_finishes_bounds(rows)
        cells = {r[21]: r[28] for r in rows[fin_b['hdr'] + 1:fin_b['end']]}
        assert cells['Owner 2'] == '🏆 2'     # champion, from the 2 seed
        assert cells['Owner 1'] == '🥈 1'     # lost the final, from the 1 seed
        assert cells['Owner 3'] == '🥉 3'

    def test_in_flight_column_is_the_seed_so_avg_reconciles(self):
        """The in-flight column asserts a STANDING, so it reads the seed
        like the rest of the table -- not the rank arc's endpoint. Avg
        averages `finish` across every season including the one in flight,
        so while the two sources disagreed a row could print 3 and 1 and an
        Avg of 1.5. A mean has to match the numbers printed beside it."""
        import almanac_write

        # The arc ranks this team 3rd; the platform seeds it 1st. The old
        # code showed the 3 and averaged the 1.
        arc = [{'team_id': 1, 'team_abbrev': 'TT1', 'period': 1,
                'standings_rank': 3},
               {'team_id': 2, 'team_abbrev': 'TT2', 'period': 1,
                'standings_rank': 1}]
        finishes = [
            {'season_year': 2025, 'team_id': 1, 'team_abbrev': 'TT1',
             'owner_display': 'Owner 1', 'wins': 9, 'losses': 5, 'ties': 0,
             'finish': 2, 'final_rank': 2, 'is_champion': False},
            {'season_year': 2026, 'team_id': 1, 'team_abbrev': 'TT1',
             'owner_display': 'Owner 1', 'wins': 4, 'losses': 0, 'ties': 0,
             'finish': 1, 'final_rank': None, 'is_champion': False},
        ]
        rows = almanac_sheets.build_advanced_standings_tab_rows(
            [_standings_team(team_id=t, abbrev=f'TT{t}') for t in (1, 2)],
            [], _STANDINGS_SPECS, 2026,
            rank_arc_rows=arc, finishes_rows=finishes)

        fin = almanac_write._espn_finishes_bounds(rows)
        row = next(r for r in rows[fin['hdr'] + 1:fin['end']]
                   if r[21] == 'Owner 1')
        avg, closed_2025, in_flight = row[27], row[28], row[29]
        assert in_flight == 1                    # the seed, not the arc's 3
        assert closed_2025 == '🥈 2'
        # And now the row adds up on its face: the mean of the two finishes
        # a reader can see. Against the arc's 3 it would have read 1.5
        # beside a 2 and a 3.
        assert avg == pytest.approx(1.5)
        assert avg == pytest.approx((2 + in_flight) / 2)

    def test_champion_derivation_outranks_a_disagreeing_final_rank(self):
        """is_champion is the older definition and the one Titles counts.
        A final_rank of 1 must not mint a second trophy the column beside
        it would not count."""
        import almanac_write

        arc = [{'team_id': t, 'team_abbrev': f'TT{t}', 'period': 1,
                'standings_rank': t} for t in (1, 2)]
        finishes = [
            {'season_year': 2025, 'team_id': 1, 'team_abbrev': 'TT1',
             'owner_display': 'Owner 1', 'wins': 9, 'losses': 5, 'ties': 0,
             'finish': 1, 'final_rank': 1, 'is_champion': False},
            {'season_year': 2025, 'team_id': 2, 'team_abbrev': 'TT2',
             'owner_display': 'Owner 2', 'wins': 8, 'losses': 6, 'ties': 0,
             'finish': 2, 'final_rank': 2, 'is_champion': False},
        ]
        rows = almanac_sheets.build_advanced_standings_tab_rows(
            [_standings_team(team_id=t, abbrev=f'TT{t}') for t in (1, 2)],
            [], _STANDINGS_SPECS, 2026,
            rank_arc_rows=arc, finishes_rows=finishes)

        fin_b = almanac_write._espn_finishes_bounds(rows)
        cells = {r[21]: r[28] for r in rows[fin_b['hdr'] + 1:fin_b['end']]}
        assert cells['Owner 1'] == 1          # plain rank, no trophy
        assert cells['Owner 2'] == '🥈 2'     # silver is unaffected

    def test_finishes_legend_names_every_podium_glyph(self):
        """A new symbol with no legend entry is a worse surface than no
        symbol -- and an italic glyph 'looks quite bad' (Kyle round 12), so
        the writer's runs pass has to reach all three where they actually
        sit in the REAL note, not just a leading trophy."""
        import almanac_render
        import almanac_write

        arc = [{'team_id': 1, 'team_abbrev': 'TT1', 'period': 1,
                'standings_rank': 1}]
        finishes = [{'season_year': 2025, 'team_id': 1, 'team_abbrev': 'TT1',
                     'owner_display': 'Owner 1', 'wins': 9, 'losses': 5,
                     'ties': 0, 'finish': 1, 'final_rank': 1,
                     'is_champion': True}]
        rows = almanac_sheets.build_advanced_standings_tab_rows(
            [_standings_team(team_id=1, abbrev='TT1')], [],
            _STANDINGS_SPECS, 2026, rank_arc_rows=arc, finishes_rows=finishes)

        fin = almanac_write._espn_finishes_bounds(rows)
        note = rows[fin['note']][fin['col0']]
        for glyph in ('🏆', '🥈', '🥉'):
            assert glyph in note
        # The exact expression the writer feeds to the updateCells request.
        runs = almanac_render.upright_emoji_runs(note)
        u16 = note.encode('utf-16-le')
        upright = [r['startIndex'] for r in runs
                   if r['format'].get('italic') is False]
        assert len(upright) == 3
        for start in upright:
            assert u16[start * 2:start * 2 + 4].decode('utf-16-le') in (
                '🏆', '🥈', '🥉')


class TestPodiumMarks:
    """almanac_render's shared medal vocabulary (MLB-230). Both books and
    both writers read it, so a change here moves two surfaces."""

    def test_only_the_top_three_get_a_glyph(self):
        import almanac_render

        assert [almanac_render.finish_medal(r) for r in (1, 2, 3, 4)] == [
            '🏆', '🥈', '🥉', None]
        # An in-flight season has no finish yet; neither form is an error.
        assert almanac_render.finish_medal(None) is None
        assert almanac_render.finish_medal('') is None

    def test_fill_matches_the_leading_glyph_only(self):
        """Nothing but a medal cell takes a fill: team names are user data
        and an emoji team name is a real team name, so a fill must never
        follow from a cell merely CONTAINING a medal."""
        import almanac_render

        assert almanac_render.medal_fill_for_cell(2, [1, 2, 3]) is None
        assert almanac_render.medal_fill_for_cell('Runner-Up 🥈',
                                                  [1, 2, 3]) is None
        # The champion's fill is the value it has always had, so no
        # existing render moves.
        assert almanac_render.medal_fill_for_cell('🏆 7', [1, 7, 16]) == {
            'red': 0.341, 'green': 0.733, 'blue': 0.541}

    def test_medals_take_the_scale_colour_for_their_own_rank(self):
        """Kyle 2026-08-09: the medals "shouldn't override" the colour
        grading. A medal cell is text and the conditional gradient paints
        numeric cells only, so it needs a static fill -- but that fill has
        to be the one the gradient WOULD have given it, or the best finish
        in the grid ends up a grey cell surrounded by greens."""
        import almanac_render

        column = [1, 2, 3, 4, 5, 6, 7]
        # Rank 1 is the column minimum, so it lands exactly on the scale's
        # green end -- the same colour a plain 1 would have been painted.
        assert (almanac_render.medal_fill_for_cell('🥈 1', column)
                == pytest.approx(almanac_render.FINISH_GREEN))
        # Rank 4 is the median, so it lands exactly on yellow.
        assert (almanac_render.medal_fill_for_cell('🥉 4', column)
                == pytest.approx(almanac_render.FINISH_YELLOW))
        # And a mark between the stops interpolates rather than snapping.
        mid = almanac_render.medal_fill_for_cell('🥈 2', column)
        assert (almanac_render.FINISH_GREEN['red'] < mid['red']
                < almanac_render.FINISH_YELLOW['red'])

    def test_each_year_scales_to_its_own_spread(self):
        """The gradient is per-year auto-scaled, so the same rank is not
        the same colour in a 16-team season and a 4-team one."""
        import almanac_render

        wide = almanac_render.medal_fill_for_cell('🥉 3', list(range(1, 17)))
        narrow = almanac_render.medal_fill_for_cell('🥉 3', [1, 2, 3])
        assert wide != narrow
        # 3rd of 3 is last, so it sits at the red end.
        assert narrow == pytest.approx(almanac_render.FINISH_RED)

    def test_finish_cell_rank_reads_every_cell_shape(self):
        """The scale is built from the column's own ranks, so the reader
        has to see through both cell shapes -- and stop at a name."""
        import almanac_render

        assert almanac_render.finish_cell_rank(12) == 12
        assert almanac_render.finish_cell_rank('12') == 12
        assert almanac_render.finish_cell_rank('🥈 1') == 1
        assert almanac_render.finish_cell_rank('🥈') == 2      # CBS's bare
        assert almanac_render.finish_cell_rank('🥉') == 3
        assert almanac_render.finish_cell_rank('') is None
        assert almanac_render.finish_cell_rank('Some Team') is None
        # A glyph mid-string is never a rank...
        assert almanac_render.finish_cell_rank('Runner-Up 🥈') is None
        # ...but a name STARTING with one reads as that medal, which is why
        # both callers scope this to year columns. Pinned so the constraint
        # is visible rather than discovered.
        assert almanac_render.finish_cell_rank('🥈 Silver Sluggers') == 2

    def test_upright_runs_match_the_hardcoded_pair_they_replace(self):
        """The old pass de-italicized at 0 and resumed at 2. That is still
        exactly right for a note whose only emoji leads it, and this pins
        it so the generalization cannot quietly move an old render."""
        import almanac_render

        assert almanac_render.upright_emoji_runs('🏆 = Season Champion.') == [
            {'startIndex': 0, 'format': {'italic': False}},
            {'startIndex': 2, 'format': {'italic': True}},
        ]

    def test_upright_runs_reach_emoji_mid_sentence(self):
        """The reason the pass had to be generalized: MLB-230 put two
        glyphs in the middle of both legends, where a fixed leading pair
        left them italic."""
        import almanac_render

        note = '🏆 = Champ. 🥈 = 2nd. 🥉 = 3rd.'
        runs = almanac_render.upright_emoji_runs(note)
        u16 = note.encode('utf-16-le')
        upright = [r['startIndex'] for r in runs
                   if r['format'].get('italic') is False]
        assert len(upright) == 3
        for start in upright:
            assert u16[start * 2:start * 2 + 4].decode('utf-16-le') in (
                '🏆', '🥈', '🥉')
        # Italics resume immediately after each 2-unit glyph.
        assert [r['startIndex'] for r in runs
                if r['format'].get('italic') is True] == [
                    s + 2 for s in upright]

    def test_upright_runs_state_the_base_run_when_text_leads(self):
        """Sheets rejects a run list whose first entry does not start at 0."""
        import almanac_render

        runs = almanac_render.upright_emoji_runs('Champion: 🏆')
        assert runs[0] == {'startIndex': 0, 'format': {'italic': True}}
        assert runs[1]['format'] == {'italic': False}

    def test_a_note_with_no_emoji_needs_no_runs(self):
        import almanac_render

        assert almanac_render.upright_emoji_runs('No glyphs here.') == []

    def test_writer_bounds_locate_every_standings_table(self):
        import almanac_write

        rows = almanac_sheets.build_advanced_standings_tab_rows(
            [_standings_team(team_id=1, abbrev='TTA')],
            [{'team_id': 1, 'lineup_slot': 'C', 'slot_pts': 5.5,
              'sort_order': 10}],
            _STANDINGS_SPECS, 2026,
            acquisition_rows=[_acq_team()],
            slot_rows_alltime=[
                {'team_id': 1, 'lineup_slot': 'C', 'slot_pts': 1.2,
                 'sort_order': 10}],
            affinity_rows=[
                {'team_id': 1, 'pro_team': 'Atl',
                 'season_wt': 1.0, 'alltime_wt': 2.0}],
        )

        (a_hdr, a_end), = almanac_write._standings_table_bounds(rows)
        assert rows[a_hdr][0] == 'Rank' and a_end - a_hdr - 1 == 1
        # Round 8: ONE combined L/R slot grid; the 'Keeper'-carrying
        # acquisition headers stay out of the slot list and keep their
        # own locator.
        assert len(almanac_write._slot_grid_bounds(rows)) == 1
        assert len(almanac_write._acquisition_table_bounds(rows)) == 2
        (aff,) = almanac_write._affinity_bounds(rows)
        assert rows[aff['hdr']][aff['spine0']] == 'MLB Team'
        assert aff['end'] - aff['hdr'] - 1 == 1   # one MLB club row
        # Round-14 geometry: spine at C, season columns from E, all-time
        # past the divider.
        assert aff['spine0'] == 2
        assert aff['left0'] == 4 and aff['n_t'] == 1
        assert aff['right0'] == 21


class TestReapplyFormulaCellsRetry:
    def test_quota_retry_rebuilds_payload_instead_of_reusing_mutated_dicts(
            self, monkeypatch):
        """gspread's Worksheet.batch_update rewrites each entry's 'range' in
        place to "'<title>'!<range>" before posting. A quota retry that
        resends the same list therefore double-prefixes the title
        ("'HH'!'HH'!C7" -> 400 Unable to parse range) -- the live failure
        from the 2026-07-06 weekly run. The fix hands gspread fresh dicts
        on every attempt; this fake mimics the in-place mutation and the
        first-call 429."""
        import almanac_write

        monkeypatch.setattr(almanac_write.time, 'sleep', lambda seconds: None)

        class FakeQuotaError(almanac_write.gspread.exceptions.APIError):
            def __init__(self):
                Exception.__init__(self, '[429]: Quota exceeded')

            def __str__(self):
                return '[429]: Quota exceeded'

        class FakeWorksheet:
            title = 'HH'

            def __init__(self):
                self.calls = []

            def batch_update(self, data, value_input_option=None):
                for entry in data:
                    entry['range'] = f"'{self.title}'!{entry['range']}"
                self.calls.append([entry['range'] for entry in data])
                if len(self.calls) == 1:
                    raise FakeQuotaError()

        worksheet = FakeWorksheet()
        rows = [
            ['plain', '=HYPERLINK("https://x", "link")'],
            ['also plain'],
        ]

        almanac_write._reapply_formula_cells(worksheet, rows)

        assert worksheet.calls[0] == ["'HH'!B1"]
        # The retry must send the SAME single-prefixed range, not a
        # double-prefixed one built from the mutated first payload.
        assert worksheet.calls[1] == ["'HH'!B1"]
