"""Pure-function tests for output/formatters.py.

Covers the value/format helpers, eligible-slots filter, the contributor-
list tie-collapse algorithm, and the three player-stat-line renderers.
No Snowflake dependency — pure inputs to pure outputs.
"""

from formatters import (
    fmt_value,
    fmt_avg,
    fmt_ip,
    fmt_record_value,
    filter_eligible_slots,
    format_contributors,
    format_hitter_stats_line,
    format_pitcher_stats_line,
    format_top_scorer_stats_line,
)


# ---------- fmt_value ----------

class TestFmtValue:
    def test_none(self):
        assert fmt_value(None) == "0"

    def test_zero_int(self):
        assert fmt_value(0) == "0"

    def test_whole_number_int(self):
        assert fmt_value(5) == "5"

    def test_whole_number_float(self):
        assert fmt_value(5.0) == "5"

    def test_fractional_float(self):
        assert fmt_value(5.7) == "5.7"

    def test_negative_whole(self):
        assert fmt_value(-3) == "-3"

    def test_negative_fractional(self):
        assert fmt_value(-2.4) == "-2.4"


# ---------- fmt_avg ----------

class TestFmtAvg:
    def test_none(self):
        assert fmt_avg(None) == ".000"

    def test_zero(self):
        assert fmt_avg(0.0) == ".000"

    def test_typical_avg(self):
        assert fmt_avg(0.391) == ".391"

    def test_one_thousand(self):
        # 1.000 should not strip the leading 1.
        assert fmt_avg(1.0) == "1.000"

    def test_high_slg(self):
        # OPS or SLG can exceed 1.000.
        assert fmt_avg(1.174) == "1.174"


# ---------- fmt_ip ----------

class TestFmtIp:
    def test_none(self):
        assert fmt_ip(None) == "0.0"

    def test_zero(self):
        assert fmt_ip(0) == "0.0"

    def test_one_out(self):
        assert fmt_ip(1) == "0.1"

    def test_full_inning(self):
        assert fmt_ip(3) == "1.0"

    def test_two_thirds_inning(self):
        # 7 outs = 2 IP + 1/3 = "2.1" in baseball notation
        assert fmt_ip(7) == "2.1"

    def test_long_outing(self):
        # 27 outs = 9 IP exact = "9.0"
        assert fmt_ip(27) == "9.0"

    def test_very_long(self):
        # 265 outs = 88 IP + 1/3
        assert fmt_ip(265) == "88.1"


# ---------- fmt_record_value ----------

class TestFmtRecordValue:
    def test_none(self):
        assert fmt_record_value('HR', None) == ""

    def test_outs_renders_as_ip(self):
        assert fmt_record_value('OUTS', 51) == "17.0"

    def test_outs_with_partial(self):
        assert fmt_record_value('OUTS', 88) == "29.1"

    def test_score_stat_keeps_decimal(self):
        # Score stats force 1-decimal precision even for whole numbers.
        assert fmt_record_value('CALCULATED_POINTS', 415) == "415.0"

    def test_score_stat_fractional(self):
        assert fmt_record_value('CALCULATED_HITTING_PTS', 219.7) == "219.7"

    def test_platform_score_stat(self):
        assert fmt_record_value('PLATFORM_POINTS', 380) == "380.0"

    def test_counting_stat_whole(self):
        # Non-score, whole number → no decimal.
        assert fmt_record_value('HR', 5) == "5"

    def test_counting_stat_fractional(self):
        # Non-score, fractional → 1 decimal.
        assert fmt_record_value('ERA', 2.45) == "2.5"


# ---------- filter_eligible_slots ----------

class TestFilterEligibleSlots:
    def test_none(self):
        assert filter_eligible_slots(None) == []

    def test_empty(self):
        assert filter_eligible_slots([]) == []

    def test_drops_bench(self):
        assert filter_eligible_slots(['C', 'BE']) == ['C']

    def test_drops_il(self):
        assert filter_eligible_slots(['1B', 'IL', 'UTIL']) == ['1B']

    def test_drops_flex_slots(self):
        assert filter_eligible_slots(['SS', 'IF', 'UTIL']) == ['SS']

    def test_collapses_generic_of(self):
        # OF dropped when LF/CF/RF present.
        assert filter_eligible_slots(['LF', 'OF']) == ['LF']

    def test_keeps_of_when_no_specific(self):
        assert filter_eligible_slots(['OF']) == ['OF']

    def test_collapses_generic_p(self):
        # P dropped when SP/RP present.
        assert filter_eligible_slots(['SP', 'P']) == ['SP']

    def test_keeps_p_when_no_specific(self):
        assert filter_eligible_slots(['P']) == ['P']

    def test_multi_position(self):
        # 2B/RP — both kept, no collapse.
        assert filter_eligible_slots(['2B', 'RP']) == ['2B', 'RP']

    def test_preserves_source_order(self):
        assert filter_eligible_slots(['CF', 'LF', 'RF']) == ['CF', 'LF', 'RF']


# ---------- format_contributors ----------

class TestFormatContributors:
    def test_empty_returns_none(self):
        assert format_contributors([]) is None

    def test_all_zero_returns_none(self):
        contribs = [{'display_name': 'A', 'stat_value': 0}]
        assert format_contributors(contribs) is None

    def test_single_contributor(self):
        contribs = [{'display_name': 'Player A', 'stat_value': 5}]
        assert format_contributors(contribs) == "Player A: 5"

    def test_three_distinct(self):
        contribs = [
            {'display_name': 'A', 'stat_value': 5},
            {'display_name': 'B', 'stat_value': 3},
            {'display_name': 'C', 'stat_value': 1},
        ]
        assert format_contributors(contribs) == "A: 5, B: 3, C: 1"

    def test_sorts_descending(self):
        contribs = [
            {'display_name': 'A', 'stat_value': 1},
            {'display_name': 'B', 'stat_value': 5},
        ]
        assert format_contributors(contribs) == "B: 5, A: 1"

    def test_tie_collapse_overflows_max_n(self):
        # 4 tied at value 3 with max_n=3 → switches to count format
        contribs = [
            {'display_name': 'A', 'stat_value': 3},
            {'display_name': 'B', 'stat_value': 3},
            {'display_name': 'C', 'stat_value': 3},
            {'display_name': 'D', 'stat_value': 3},
        ]
        assert format_contributors(contribs, max_n=3) == "4 others with 3"

    def test_partial_then_tie_collapse(self):
        # 1 unique at 5, then 3 tied at 3. With max_n=3, the tie group
        # would push count to 4 — switches to count format.
        contribs = [
            {'display_name': 'A', 'stat_value': 5},
            {'display_name': 'B', 'stat_value': 3},
            {'display_name': 'C', 'stat_value': 3},
            {'display_name': 'D', 'stat_value': 3},
        ]
        assert format_contributors(contribs, max_n=3) == "A: 5, 3 others with 3"

    def test_zero_tail_appended(self):
        # 1 non-zero, 2 zero teammates → "A: 5, 2 others with 0"
        contribs = [
            {'display_name': 'A', 'stat_value': 5},
            {'display_name': 'B', 'stat_value': 0},
            {'display_name': 'C', 'stat_value': 0},
        ]
        assert format_contributors(contribs) == "A: 5, 2 others with 0"

    def test_zero_tail_only_when_room(self):
        # max_n=3, 3 non-zero contributors fill the cap → no zero-tail
        contribs = [
            {'display_name': 'A', 'stat_value': 5},
            {'display_name': 'B', 'stat_value': 4},
            {'display_name': 'C', 'stat_value': 3},
            {'display_name': 'D', 'stat_value': 0},
        ]
        assert format_contributors(contribs, max_n=3) == "A: 5, B: 4, C: 3"

    def test_value_fmt_override_for_outs(self):
        # Pass fmt_ip so OUTS counts render as IP notation.
        contribs = [{'display_name': 'A', 'stat_value': 7}]
        assert format_contributors(contribs, value_fmt=fmt_ip) == "A: 2.1"

    def test_none_stat_value_treated_as_zero(self):
        contribs = [
            {'display_name': 'A', 'stat_value': 5},
            {'display_name': 'B', 'stat_value': None},
        ]
        assert format_contributors(contribs) == "A: 5, 1 others with 0"


# ---------- format_hitter_stats_line ----------

def _hitter_row(**overrides):
    """Sample wide hitter row from fct_weekly_player_performance shape."""
    base = {
        'avg':  0.391,  'obp': 0.462,  'slg':  1.174,
        'ab':   23,
        'h':    9,    'h_pts':  9.0,
        'b_bb': 2,    'b_bb_pts': 2.0,
        'b_so': 5,    'b_so_pts': -2.5,
        'hbp':  0,    'hbp_pts': 0,
        'sf':   0,    'sf_pts': 0,
        'hr':   5,    'hr_pts': 20.0,
        'r':    6,    'r_pts':  6.0,
        'rbi':  6,    'rbi_pts': 6.0,
        'sb':   1,    'sb_pts': 2.0,
        'cs':   0,    'cs_pts': 0,
        'tb':   24,   'tb_pts': 12.0,
        'singles': 1,    'singles_pts': 1.0,
        'doubles': 1,    'doubles_pts': 2.0,
        'triples': 0,    'triples_pts': 0,
        'xbh':     6,    'xbh_pts':     6.0,
    }
    base.update(overrides)
    return base


class TestFormatHitterStatsLine:
    def test_basic(self):
        row = _hitter_row()
        result = format_hitter_stats_line(row, top_n=2)
        # Rate slash + AB context first
        assert ".391/.462/1.174" in result
        assert "23 AB" in result
        # Top-2 by pts: HR (20.0 pts), TB (12.0 pts). Display shows COUNT
        # not pts — so HR=5 (count, not its 20.0 pts) and TB=24 (count,
        # not its 12.0 pts).
        assert "5 HR" in result
        assert "24 TB" in result

    def test_skips_zero_count_stats(self):
        # CS=0 should never appear even though it has a (zero) pts column.
        row = _hitter_row(cs=0, hr=0, hr_pts=0, tb=0, tb_pts=0,
                          rbi=3, rbi_pts=3.0)
        result = format_hitter_stats_line(row, top_n=5)
        assert "CS" not in result
        assert "HR" not in result

    def test_positives_only_skips_negative_pts(self):
        # B_SO has pts=-2.5 with positives_only=True → excluded
        row = _hitter_row()
        result = format_hitter_stats_line(row, top_n=5, positives_only=True)
        assert "K" not in result.split("--")[1]  # not in stat suffix

    def test_positives_only_false_includes_negative_pts(self):
        # With positives_only=False, B_SO can appear (sorted by |pts|).
        row = _hitter_row()
        result = format_hitter_stats_line(row, top_n=10, positives_only=False)
        assert "5 K" in result  # b_so=5


# ---------- format_pitcher_stats_line ----------

def _pitcher_row(**overrides):
    base = {
        'w': 2,  'l': 0,  'sv': 0,
        'era': 0.69, 'whip': 1.23,
        'outs': 39,  # 13.0 IP
        'k':   9,    'k_pts':   9.0,
        'er':  1,    'er_pts':  -3.0,
        'qs':  2,    'qs_pts':  10.0,
        'hld': 0,    'hld_pts': 0,
        'p_h': 5,    'p_h_pts': -2.5,
        'p_bb': 2,   'p_bb_pts': -2.0,
        'p_hr': 0,   'p_hr_pts': 0,
        'p_r':  1,   'p_r_pts': -1.0,
        'cg':  0,    'cg_pts':  0,
        'blk': 0,    'blk_pts': 0,
        'wp':  0,    'wp_pts':  0,
    }
    base.update(overrides)
    return base


class TestFormatPitcherStatsLine:
    def test_basic_with_record(self):
        row = _pitcher_row()
        result = format_pitcher_stats_line(row, top_n=2)
        assert "2-0" in result          # W-L prefix
        assert "0.69 ERA" in result
        assert "1.23 WHIP" in result
        assert "13.0 IP" in result

    def test_no_record_no_prefix(self):
        row = _pitcher_row(w=0, l=0, sv=0)
        result = format_pitcher_stats_line(row)
        # No "0-0" — the W-L prefix is suppressed when both are zero
        assert "0-0" not in result
        assert "0.69 ERA" in result

    def test_save_in_prefix(self):
        row = _pitcher_row(w=0, l=0, sv=1)
        result = format_pitcher_stats_line(row)
        assert "1 SV" in result

    def test_null_era_renders_dashes(self):
        row = _pitcher_row(era=None, whip=None)
        result = format_pitcher_stats_line(row)
        assert "-- ERA" in result
        assert "-- WHIP" in result


# ---------- format_top_scorer_stats_line ----------

class TestFormatTopScorerStatsLine:
    def test_two_way_player(self):
        # Mix hitter + pitcher columns; AB and OUTS aren't excluded here.
        row = {
            'h': 3,   'h_pts': 3.0,
            'ab': 5,  'ab_pts': 0,
            'b_bb': 1,'b_bb_pts': 1.0,
            'b_so': 0,'b_so_pts': 0,
            'hbp': 0, 'hbp_pts': 0,
            'sf': 0,  'sf_pts': 0,
            'hr': 1,  'hr_pts': 4.0,
            'r': 2,   'r_pts': 2.0,
            'rbi': 2, 'rbi_pts': 2.0,
            'sb': 0,  'sb_pts': 0,
            'cs': 0,  'cs_pts': 0,
            'tb': 6,  'tb_pts': 3.0,
            'singles': 2, 'singles_pts': 2.0,
            'doubles': 0, 'doubles_pts': 0,
            'triples': 0, 'triples_pts': 0,
            'xbh': 1, 'xbh_pts': 1.0,
            'w': 1,   'w_pts': 5.0,
            'l': 0,   'l_pts': 0,
            'k': 8,   'k_pts': 8.0,
            'er': 0,  'er_pts': 0,
            'outs': 21, 'outs_pts': 7.0,  # 7.0 IP
            'qs': 1,  'qs_pts': 5.0,
            'sv': 0,  'sv_pts': 0,
            'hld': 0, 'hld_pts': 0,
            'p_h': 2, 'p_h_pts': -1.0,
            'p_bb': 1,'p_bb_pts': -1.0,
            'p_hr': 0,'p_hr_pts': 0,
            'p_r': 0, 'p_r_pts': 0,
            'cg': 0,  'cg_pts': 0,
            'blk': 0, 'blk_pts': 0,
            'wp': 0,  'wp_pts': 0,
        }
        result = format_top_scorer_stats_line(row, top_n=5)
        # IP renders via fmt_ip (special-cased for OUTS in TOP_SCORER_STAT_DISPLAY)
        assert "7.0 IP" in result
        # 8 K with 8.0 pts is a top contributor
        assert "8 K" in result
