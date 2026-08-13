"""The ESPN Draft Recap must contain Google-JSON-compatible scalars."""

from decimal import Decimal

from almanac_logic import build_draft_tab_rows


def _pick(*, season_year=Decimal("2026")):
    return {
        "season_year": season_year,
        "overall_pick": 1,
        "round_num": 1,
        "round_pick": 1,
        "keeper": False,
        "team_id": 999999,
        "team_abbrev": "EX",
        "player_name": "Example Player",
        "official_player_name": None,
        # The mart's points measure is DOUBLE; only the dbt season key is
        # DECIMAL on DuckDB, matching the real rehearsal payload.
        "season_points": 12.5,
        "points_rank": 1,
        "value_delta": 0,
    }


def test_duckdb_decimal_season_year_is_json_serializable_in_draft_rows():
    pick = _pick()

    rows = build_draft_tab_rows(
        [pick],
        2026,
        history_rows=[pick],
        season_clocks={Decimal("2026"): 142},
    )

    assert not [
        value
        for row in rows
        for value in row
        if isinstance(value, Decimal)
    ]
    assert any(row[:2] == [1, 2026] for row in rows)
