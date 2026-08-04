"""Pins for the club-of-game flip and the label-collapse rule.

MLB-159 Exit 1 moved `pro_team` from ESPN's person-level `proTeam` stamp to
the game-level `clubOfGame` field, and MLB-168 replaced the string-maximum
collapse that the flip would otherwise have activated.

Both changes are the kind that a well-meaning edit silently reverts -- the
old spellings look tidier and pass every other test -- so the shapes are
pinned here rather than left to review. These are cheap text assertions on
purpose: the semantics are verified against the warehouse in the byte-diff
and dbt test suites, and this file exists to catch a REGRESSION, not to
re-derive correctness.
"""

from pathlib import Path

import pytest

import db

REPO = Path(__file__).resolve().parents[1]
MODELS = REPO / 'dbt_league' / 'models'
OUTPUT = REPO / 'output'


def _read(path):
    return path.read_text(encoding='utf-8')


def _code_only(text, prefix):
    """Source with whole-line comments stripped.

    Needed because the comments here deliberately QUOTE the spellings
    being banned -- explaining why `proTeam` is preserved in RAW, or what
    the retired explainer sentence used to say. A naive substring check
    over the raw file fails on its own documentation.
    """
    return '\n'.join(line for line in text.splitlines()
                     if not line.lstrip().startswith(prefix))


class TestStagingReadsClubOfGame:
    """stg_box_scores must source pro_team from the game-level field."""

    def test_all_three_unions_read_club_of_game(self):
        sql = _read(MODELS / 'staging' / 'stg_box_scores.sql')
        # home lineup, away lineup, free agents -- all three, or the flip
        # is half-applied and the book mixes two attribution rules.
        assert sql.count("'clubOfGame'") == 3

    def test_pro_team_column_is_not_fed_from_the_person_stamp(self):
        """`proTeam` may still be DISCUSSED in comments -- it is the
        preserved observation record -- but must not feed the column."""
        sql = _read(MODELS / 'staging' / 'stg_box_scores.sql')
        code = '\n'.join(line for line in sql.splitlines()
                         if not line.lstrip().startswith('--'))
        assert "'proTeam'" not in code

    def test_the_flip_is_explained_where_it_happens(self):
        sql = _read(MODELS / 'staging' / 'stg_box_scores.sql')
        assert 'clubOfGame' in sql and 'producing' in sql.lower()


class TestLabelCollapseRule:
    """MLB-168: no label column may collapse by string maximum."""

    def test_position_pts_uses_latest_by_not_max(self):
        sql = _read(MODELS / 'marts' / 'core' / 'fct_player_position_pts.sql')
        code = '\n'.join(line for line in sql.splitlines()
                         if not line.lstrip().startswith('--'))
        assert 'max(pro_team)' not in code
        assert "latest_by('pro_team'" in code

    def test_position_pts_orders_by_scoring_period_not_game_date(self):
        """game_date is NULL on every ESPN row by construction, so
        ordering by it returns NULL for every ESPN group."""
        sql = _read(MODELS / 'marts' / 'core' / 'fct_player_position_pts.sql')
        assert "latest_by('pro_team', 'scoring_period')" in sql

    def test_optimal_team_queries_do_not_string_max_the_club(self):
        src = _read(OUTPUT / 'almanac_data.py')
        assert 'MAX(pro_team)' not in src

    def test_row_picking_consumers_take_the_latest_labelled_day(self):
        """These pick one row per player and read the club off it, so an
        unlabelled latest day would blank the club. Both feed BBCode
        goldens."""
        for name in ('generate_summary.py', 'generate_season_report.py'):
            src = _read(OUTPUT / name)
            assert "latest_by('pro_team'" in src, name


class TestLatestByFragment:
    """The guard is the load-bearing part, and it is what makes the two
    engines agree -- verified live on both: the UNGUARDED spelling returns
    NULL on Snowflake where DuckDB skips to the last labelled row."""

    def test_aggregate_form_nulls_the_ordering_expression(self):
        frag = db.latest_by('pro_team', 'scoring_period')
        assert frag == ('MAX_BY(pro_team, CASE WHEN pro_team IS NULL '
                        'THEN NULL ELSE scoring_period END)')

    def test_window_form_appends_the_partition(self):
        frag = db.latest_by('pro_team', 'scoring_period', 'player_id')
        assert frag.endswith('OVER (PARTITION BY player_id)')
        assert 'CASE WHEN pro_team IS NULL THEN NULL' in frag

    def test_the_value_is_never_the_thing_nulled(self):
        """Nulling the VALUE instead of the ordering key would drop the
        row from the ordering's tie-break rather than from consideration,
        which is a different and wrong aggregate."""
        frag = db.latest_by('club', 'day')
        assert frag.startswith('MAX_BY(club, CASE WHEN club IS NULL')


class TestUnattributedBandStaysRenderable:
    """MLB-193 / MLB-188 ruling: zero rows in THIS league's data is a data
    property, not deleted code. For a league backfilled years late the
    band is a live diagnostic, so the path must survive."""

    def test_sentinel_and_label_both_still_exist(self):
        import almanac_data
        import almanac_render
        assert almanac_data.AFFINITY_UNATTRIBUTED
        assert almanac_render.ESPN_UNATTRIBUTED_CLUB == 'Unattributed'

    def test_the_bucketing_case_is_still_in_the_query(self, monkeypatch):
        import almanac_data
        calls = []
        monkeypatch.setattr(almanac_data, 'query_snowflake',
                            lambda sql, params=None: calls.append(sql) or [])
        almanac_data.get_team_affinity_weights(2026)
        sql = calls[0]
        # The 'FA' arm is unreachable post-flip (clubOfGame is one of 30
        # clubs or NULL, never 'FA') and is KEPT as the tripwire against
        # an FA filter being silently restored -- the regression that once
        # deleted 11.7% of 2025 from the chart.
        assert "pro_team IS NULL OR pro_team = 'FA'" in sql
        assert almanac_data.AFFINITY_UNATTRIBUTED in sql


class TestExplainerIsForwardTrue:
    """The old closing sentence became dead text at the flip. Ruled
    REWRITTEN rather than deleted (MLB-188), and rewritten to describe
    what a visible band means for a reader's own league."""

    def test_the_dead_2025_claim_is_gone(self):
        code = _code_only(_read(OUTPUT / 'almanac_logic.py'), '#')
        assert '2025 cannot place anyone' not in code

    def test_the_band_is_still_explained(self):
        src = _read(OUTPUT / 'almanac_logic.py')
        assert 'Unattributed is involvement whose' in src
        assert 'not free-agent time' in src


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
