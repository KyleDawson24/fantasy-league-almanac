"""Pure tests for the v1.1 almanac Sheets helpers."""

import pytest

import almanac_data
import almanac_sheets


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

        monkeypatch.setattr(almanac_sheets, 'query_snowflake', fake_query)

        result = almanac_sheets.get_slot_capacities(2026, 8)

        assert result == {'C': 1, 'SP': 5}
        assert 'dim_roster_slot_counts' in calls[0][0]
        assert calls[0][1] == (2026,)

    def test_missing_configured_counts_raises_helpful_error(self, monkeypatch):
        monkeypatch.setattr(almanac_sheets, 'query_snowflake', lambda *_: [])

        with pytest.raises(RuntimeError, match='No roster slot counts found'):
            almanac_sheets.get_slot_capacities(2026, 8)


class TestBoxscoreFormula:
    def test_builds_espn_boxscore_hyperlink(self):
        result = almanac_sheets.boxscore_formula(
            league_id=1156117086,
            season_year=2026,
            matchup_period=8,
            team_id=8,
        )

        assert result == (
            '=HYPERLINK("https://fantasy.espn.com/baseball/boxscore?'
            'leagueId=1156117086&matchupPeriodId=8&seasonId=2026&teamId=8'
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
            team_titles=['AAA', 'BP'],
            league_id=1156117086,
        )

        # Banner (spans both bands).
        assert rows[0] == ['Fantasy Beat Reporter Almanac']
        assert 'current-season scoring' in rows[1][0]
        assert rows[2] == []

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
        c_rows = [r for r in rows if len(r) > 14 and r[5] == 'C' and r[7] == 'Catcher A']
        assert len(c_rows) == 2
        week_c, season_c = c_rows
        assert str(week_c[10]).startswith('=HYPERLINK(')
        assert season_c[10] == 12.2
        assert week_c[13] == 'Bench Catcher' and week_c[14] == 30.0
        assert season_c[13] == 'Bench Catcher' and season_c[14] == 30.0

        # Left band (cols A-D): nav labels, per-team grid, glossary, all-time.
        left_first = [r[0] if r else '' for r in rows]
        assert 'Navigate' in left_first
        assert 'Points Glossary' in left_first
        assert 'All-League Team -- All-Time' in left_first
        grid = next(r for r in rows if r and r[0] == 'AAA')
        assert grid[1] == 'BP'
        alltime_c = next(
            r for r in rows
            if len(r) > 3 and r[0] == 'C' and r[1] == 'All-Time Catcher'
        )
        assert alltime_c[2] == 600.0
        assert alltime_c[3] == '3.00'  # 600.0 / 200 games


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
            league_id=1156117086,
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
            'leagueId=1156117086&matchupPeriodId=8&seasonId=2026&teamId=8'
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
        assert almanac_sheets._record_side_is_small_tie('WALK')
        assert almanac_sheets._record_side_is_small_tie('CYCL, FUBB')
        assert almanac_sheets._record_side_is_small_tie('3 teams tied')
        assert not almanac_sheets._record_side_is_small_tie('4 teams tied')
        assert not almanac_sheets._record_side_is_small_tie('22 teams tied')

    def test_current_season_tie_count_is_scoped(self, monkeypatch):
        calls = []

        def fake_query(sql, params=None):
            calls.append((sql, params))
            return [{'n': 14}]

        monkeypatch.setattr(almanac_sheets, 'query_snowflake', fake_query)

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

        monkeypatch.setattr(almanac_sheets, 'query_snowflake', fake_query)

        result = almanac_sheets.get_wasted_points_records('current_season')

        assert result[0]['contributors'] == []
        assert 'performance_status = \'inactive\'' in calls[0][0]
        assert calls[0][1] == ('current_season',)

    def test_record_row_formats_team_record_with_contributors_and_boxscore(self):
        row = _record(stat='HR', value=5)

        result = almanac_sheets.format_record_row(
            row,
            scope_label='All-Time',
            league_id=1156117086,
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

    def test_records_tab_uses_side_by_side_current_then_all_time(self):
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
            league_id=1156117086,
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
        monkeypatch.setattr(almanac_sheets, 'query_snowflake', lambda *_: [
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
            league_id=1156117086,
        )

        assert result[0:5] == ['SP 2', 'BOS', 'Player 1', 'SP', 12.2]
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
        assert [r[2] for r in result[0][1][4:]] == ['Player 10', 'Player 11']

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
        assert result['LF']['player'] == 'Big Bench Bat'
        assert result['LF']['active_points'] == 10
        # Bench: Everyday LF (20+0=20) > whoever's left (Big Bench Bat
        # already picked); IL Stash has 0+0=0 so Everyday LF lands BE.
        assert result['BE']['player'] == 'Everyday LF'
        # IL still uses il_days filter; first IL row goes to IL Stash.
        assert result['IL 1']['player'] == 'IL Stash'
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

        assert result['BE']['player'] == 'Mostly Benched Slugger'  # 75 > 30
        assert result['Other 1']['player'] == 'Modest Everyday'

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
        assert rows[3][15] == 'All-Time'
        assert rows[4] == almanac_sheets.TEAM_ROSTER_HEADER
        assert rows[5][9:14] == ['Avg', 'OBP', 'Slg', 'HR', 'SB']
        # v1.1.1: current_fantasy_team column now holds '*' when the
        # player is still on this tab's team (was the redundant abbrev).
        # The _history_player helper hardcodes the field to the team's
        # full name -- testing only the slot/player/pro columns here.
        assert rows[6][1:4] == ['LF', 'Current LF', 'BOS']
        assert rows[6][16:19] == ['LF', 'All Time LF', 'BOS']

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
        assert rows[5][9:14] == ['Avg', 'OBP', 'Slg', 'HR', 'SB']
        assert rows[7][9:14] == ['W-L (Sv)', 'ERA', 'WHIP', 'K', 'BB']
        assert rows[9][9:14] == ['Avg|W-L-Sv', 'OBP|ERA', 'Slg|WHIP', 'HR|K', 'SB|BB']

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
