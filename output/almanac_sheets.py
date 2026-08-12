"""
output/almanac_sheets.py

Google Sheets writer for the v1.1 league almanac surface.

The legacy `sheets_writer.py` module still owns the three records tabs.
This module starts the new browsable almanac surface, beginning with the
Home tab's "All-League Team of the Week" block.
"""

import math
import os
import re
import time
from collections import defaultdict

import gspread

from db import query_snowflake
import records
import stat_catalog
from formatters import fmt_avg, fmt_ip, fmt_record_value, format_top_scorer_stats_line
from sheets_writer import _get_authorized_client

# v1.1.1 Tier 2c.1: data-access surface lives in almanac_data.py now.
# Re-exported here so `import almanac_sheets` continues to resolve
# all data names without churn at every call site.
from almanac_data import (
    RATE_RECORD_SPECS,
    HITTING_RECORD_LABELS,
    HITTING_RECORD_ORDER,
    get_latest_matchup_period,
    get_team_week_stat_specs,
    get_team_weeks,
    get_team_week_record_marks,
    get_all_league_team,
    get_draft_board,
    get_draft_history_boards,
    get_season_scoring_periods,
    get_team_standings,
    get_team_slot_points,
    get_team_slot_points_alltime,
    get_team_acquisition_channels,
    get_team_acquisition_channels_alltime,
    get_team_affinity_weights,
    get_team_rank_arc,
    get_rivalry_axes,
    get_rivalry_matrix,
    get_espn_season_finishes,
    get_team_standings_alltime,
    get_player_season_points,
    get_trades_tab_data,
    get_home_tab_data,
    get_optimal_team,
    get_slot_capacities,
    get_roster_slot_capacities,
    get_team_roster_history_stats,
    get_current_team_roster_stats,
    get_almanac_records,
    _almanac_tie_counter,
    count_value_occurrences_for_scope,
    get_rate_records,
    get_lineup_slot_records,
    _get_rate_record_rows,
    get_wasted_points_records,
    get_franchise_hall_of_fame,
    get_wasted_hall_of_shame,
    get_wasted_career_total,
    get_scored_record_specs,
    get_lineup_slot_record_specs,
    _lineup_slot_stat_name,
    build_scored_record_specs,
    _scored_record_sort_key,
    _team_record_label,
    _fact_stat_column_name,
    slot_label,
    PITCHING_STAT_ORDER,
    _team_week_stat_sort_key,
    _team_week_good_record_direction,
)

# v1.1.1 Tier 2c.2: render surface lives in almanac_render.py now.
from almanac_render import (
    ADVANCED_STANDINGS_TAB,
    DRAFT_TAB,
    DRAFT_VALUE_HEADER,
    HOME_ALLTIME_HEADER,
    HOME_DEVIATION_LABEL,
    HOME_HEADER,
    HOME_TAB,
    RECORDS_HALL_BANNER,
    RECORDS_HALL_DETAIL_HEADER,
    RECORDS_HALL_OF_FAME_CAPTION,
    RECORDS_HALL_OF_SHAME_CAPTION,
    RECORDS_TAB_WIDTH,
    RECORDS_HEADER,
    RECORDS_MATRIX_DETAIL_HEADER,
    RECORDS_MATRIX_WIDTH,
    RECORDS_TAB,
    SLOT_ORDER,
    TEAM_HISTORY_DETAIL_HEADER,
    TEAM_HISTORY_HITTER_HEADER,
    TEAM_HISTORY_HITTER_STATS,
    TEAM_HISTORY_MIXED_HEADER,
    TEAM_HISTORY_MIXED_STATS,
    TEAM_HISTORY_PITCHER_HEADER,
    TEAM_HISTORY_PITCHER_STATS,
    TEAM_ROSTER_HEADER,
    TEAM_ROSTER_MATRIX_WIDTH,
    TEAM_WEEKS_BASE_HEADER,
    TEAM_WEEKS_RARE_STATS,
    TEAM_WEEKS_SCORE_HEADER,
    TEAM_WEEKS_TAB,
    TEAM_WEEKS_WHITE_TO_GREEN_STATS,
    TEAM_WEEKS_WHITE_TO_RED_STATS,
    TRADE_AVAILABILITY_LABELS,
    TRADE_RECORD_HEADER,
    TRADE_RECORD_LABEL,
    TRADES_BLOCK_LABEL,
    TRADES_HEADER,
    TRADES_TAB,
    _boxscore_url,
    _collapsed_holder,
    _collapsed_owner,
    _collapsed_period,
    _collapsed_season,
    _compact_inactive_slot,
    _detail_stat_label,
    _display_slot_tokens,
    _empty_team_history_display_row,
    _format_record_side,
    _format_record_value,
    _format_sheet_date,
    _format_team_week_stat,
    _hitting_rate,
    _inactive_position_display,
    _is_active_display_slot,
    _is_hitter_display_slot,
    _is_pitcher_display_slot,
    _is_rare_team_week_stat,
    _one_decimal,
    _period_boxscore_formula,
    _period_label,
    _pitching_decision_display,
    _pitching_rate,
    _rate_as_whole_number,
    _rate_qualifier_detail,
    _record_details,
    _record_label,
    _records_matrix_scope_header,
    _round_half_up,
    _safe_sheet_title,
    _slot_sort_key,
    _team_history_display_row,
    _team_history_is_pitcher,
    _team_history_scope_header,
    _team_history_section_header_row,
    _team_history_side_cells,
    _team_history_stat_line,
    _team_week_specs_for_category,
    _team_week_stat_header,
    _team_week_stat_headers,
    boxscore_formula,
    format_all_league_team_row,
    acquisition_half_values,
    format_hall_of_fame_cells,
    format_hall_of_shame_cells,
    hall_of_shame_wasted,
    format_record_matrix_row,
    format_record_row,
    format_team_history_matrix_row,
    format_team_roster_row,
    format_team_week_row,
    acquisition_gradient_columns,
    standings_gradient_columns,
    standings_header,
    team_tab_title,
    ACQUISITION_HEADER,
    format_trade_record_row,
    format_trades_row,
    trade_eligibility_display,
)

# v1.1.1 Tier 2c.3: logic surface lives in almanac_logic.py now.
from almanac_logic import (
    SCORE_RECORD_SPECS,
    _attach_almanac_contributors,
    _blank_roster_row,
    _candidate_sort_key,
    _group_record_specs,
    _index_records,
    _insert_before_first,
    _is_hitter_team_history_label,
    _is_mixed_team_history_label,
    _is_pitcher_team_history_label,
    _max_other_index,
    _record_never_occurred,
    _spec_key,
    _team_history_row_labels,
    _team_sort_key,
    build_advanced_standings_tab_rows,
    build_draft_tab_rows,
    build_home_tab_rows,
    build_records_tab_rows,
    build_trades_tab_rows,
    build_team_history_side,
    build_team_history_tabs,
    build_team_roster_tabs,
    build_team_weeks_tab_rows,
    expand_team_roster_rows,
    select_all_league_team,
)

# v1.1.1 Tier 2c.4: write surface lives in almanac_write.py now.
from almanac_write import (
    _a1_col,
    _apply_records_tab_dimensions,
    _apply_team_tab_dimensions,
    _apply_team_weeks_conditional_formats,
    _apply_team_weeks_record_formats,
    _apply_team_weeks_tab_dimensions,
    _auto_resize_columns_request,
    _batch_format,
    _cell_format_request,
    _color_scale_ranges,
    _color_scale_request,
    _column_width_request,
    _delete_prefixed_team_tabs,
    _fresh_record_formats,
    _hidden_columns_request,
    _is_quota_error,
    _is_records_scope_header,
    _is_zeroish,
    _merge_records_scope_headers,
    _numeric_values_equal,
    _record_side_is_small_tie,
    _records_header_formats,
    _records_score_value_formats,
    _replace_home_tab,
    _replace_records_tab,
    _replace_team_tab,
    _replace_team_weeks_tab,
    _replace_trades_tab,
    _sheets_batch_update,
    _sheets_call,
    _sort_almanac_tabs,
    _team_weeks_layout,
    _team_weeks_rare_column_indices,
    _team_weeks_standard_data_ranges,
    _team_weeks_stat_column_pairs,
    write_almanac,
)































































































































































































































































