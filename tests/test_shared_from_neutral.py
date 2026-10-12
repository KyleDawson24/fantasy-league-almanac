"""Tests for dashboard/shared_from_neutral.py (MLB-329).

Pure-function tests built on small in-memory dicts. No warehouse, no DuckDB
file, no network. Must run under the default pytest.ini selection (no
warehouse marker).
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))
from shared_from_neutral import champion_records


# -- fixtures: small finish lists for H2H and points leagues ----------------

def _h2h_finish(season, team_id, abbrev, owner, wins, losses, ties,
                seed, final_rank=None, champion=False, pts=None):
    return {"season_year": season, "team_id": team_id, "team_abbrev": abbrev,
            "owner_display": owner, "wins": wins, "losses": losses,
            "ties": ties, "finish": seed, "final_rank": final_rank,
            "is_champion": champion, "calculated_points": pts}


def _pts_finish(season, team_id, abbrev, owner, wins, losses, ties,
                rank, final_rank=None, champion=False, pts=None):
    return {"season_year": season, "team_id": team_id, "team_abbrev": abbrev,
            "owner_display": owner, "wins": wins, "losses": losses,
            "ties": ties, "finish": rank, "final_rank": final_rank,
            "is_champion": champion, "calculated_points": pts}


FINISHED_H2H = [
    _h2h_finish(2025, 1, "AA", "Jonas", 14, 8, 0, 3, final_rank=1, champion=True),
    _h2h_finish(2025, 2, "BB", "Seth", 16, 6, 0, 1, final_rank=2),
    _h2h_finish(2025, 3, "CC", "Zeke", 12, 10, 0, 7, final_rank=3),
    _h2h_finish(2026, 4, "DD", "Grant", 12, 10, 0, 7, final_rank=1, champion=True),
    _h2h_finish(2026, 1, "AA", "Jonas", 19, 3, 0, 1, final_rank=4),
    _h2h_finish(2026, 2, "BB", "Seth", 15, 7, 0, 2, final_rank=2),
]

OPEN_H2H = [
    _h2h_finish(2026, 1, "AA", "Jonas", 10, 5, 0, 1),
    _h2h_finish(2026, 2, "BB", "Seth", 8, 7, 0, 2),
]

FINISHED_POINTS = [
    _pts_finish(2025, 10, "XX", "Jasper", 0, 0, 0, 1, final_rank=1, champion=True, pts=5200.5),
    _pts_finish(2025, 11, "YY", "Dorian", 0, 0, 0, 2, final_rank=2, pts=4800.3),
    _pts_finish(2026, 12, "ZZ", "Silas", 0, 0, 0, 1, final_rank=1, champion=True, pts=6100.0),
    _pts_finish(2026, 10, "XX", "Jasper", 0, 0, 0, 2, final_rank=2, pts=5900.0),
]

OPEN_POINTS = [
    _pts_finish(2026, 10, "XX", "Jasper", 0, 0, 0, 1, pts=3200.0),
    _pts_finish(2026, 11, "YY", "Dorian", 0, 0, 0, 2, pts=3100.0),
]

FINALS_H2H = [
    {"season_year": 2025, "team_points": 318.6, "opponent_points": 296.3,
     "opponent_abbrev": "BB"},
    {"season_year": 2026, "team_points": 352.8, "opponent_points": 191.9,
     "opponent_abbrev": "BB"},
]


# -- one champion per finished season --------------------------------------

class TestOneChampionPerFinishedSeason:

    def test_h2h_exactly_one_per_season(self):
        recs = champion_records(FINISHED_H2H, "h2h", FINALS_H2H)
        assert len(recs) == 2
        years = [r["season_year"] for r in recs]
        assert years == [2025, 2026]
        for r in recs:
            assert r["team_abbrev"] in ("AA", "DD")

    def test_points_exactly_one_per_season(self):
        recs = champion_records(FINISHED_POINTS, "points")
        assert len(recs) == 2
        years = [r["season_year"] for r in recs]
        assert years == [2025, 2026]

    def test_single_finished_season(self):
        single = [f for f in FINISHED_H2H if f["season_year"] == 2025]
        recs = champion_records(single, "h2h")
        assert len(recs) == 1
        assert recs[0]["season_year"] == 2025
        assert recs[0]["team_abbrev"] == "AA"


# -- no champion for an open season ----------------------------------------

class TestNoChampionForOpenSeason:

    def test_h2h_open_season_returns_empty(self):
        recs = champion_records(OPEN_H2H, "h2h")
        assert recs == []

    def test_points_open_season_returns_empty(self):
        recs = champion_records(OPEN_POINTS, "points")
        assert recs == []

    def test_mixed_open_and_closed(self):
        mixed = FINISHED_H2H[:3] + OPEN_H2H
        recs = champion_records(mixed, "h2h")
        assert len(recs) == 1
        assert recs[0]["season_year"] == 2025


# -- H2H finals fields present for ESPN, absent for points -----------------

class TestH2HFieldsPresent:

    def test_h2h_has_seed(self):
        recs = champion_records(FINISHED_H2H, "h2h", FINALS_H2H)
        for r in recs:
            assert "seed" in r

    def test_h2h_has_regular_season_record(self):
        recs = champion_records(FINISHED_H2H, "h2h", FINALS_H2H)
        assert recs[0]["regular_season_record"] == "14-8"
        assert recs[1]["regular_season_record"] == "12-10"

    def test_h2h_has_finals_score(self):
        recs = champion_records(FINISHED_H2H, "h2h", FINALS_H2H)
        assert recs[0]["finals_score"] == "318.6-296.3"
        assert recs[0]["finals_opponent_abbrev"] == "BB"

    def test_h2h_seed_is_playoff_seed(self):
        recs = champion_records(FINISHED_H2H, "h2h", FINALS_H2H)
        assert recs[0]["seed"] == 3
        assert recs[1]["seed"] == 7

    def test_h2h_fields_absent_for_points(self):
        recs = champion_records(FINISHED_POINTS, "points")
        for r in recs:
            assert "seed" not in r
            assert "regular_season_record" not in r
            assert "finals_score" not in r
            assert "finals_opponent_abbrev" not in r


# -- points-league fields present for CBS, absent for ESPN ------------------

class TestPointsFieldsPresent:

    def test_points_has_final_standing(self):
        recs = champion_records(FINISHED_POINTS, "points")
        for r in recs:
            assert "final_standing" in r
            assert r["final_standing"] == 1

    def test_points_has_points(self):
        recs = champion_records(FINISHED_POINTS, "points")
        assert recs[0]["points"] == 5200.5
        assert recs[1]["points"] == 6100.0

    def test_points_fields_absent_for_h2h(self):
        recs = champion_records(FINISHED_H2H, "h2h")
        for r in recs:
            assert "final_standing" not in r
            assert "points" not in r


# -- guard: != 1 champion in a finished season must fail --------------------

class TestChampionGuard:

    def test_two_champions_raises(self):
        bad = [
            _h2h_finish(2025, 1, "AA", "Jonas", 14, 8, 0, 3,
                        final_rank=1, champion=True),
            _h2h_finish(2025, 2, "BB", "Seth", 16, 6, 0, 1,
                        final_rank=1, champion=True),
        ]
        with pytest.raises(ValueError, match="2 champions"):
            champion_records(bad, "h2h")

    def test_zero_champions_no_final_rank_means_open(self):
        no_champ = [
            _h2h_finish(2025, 1, "AA", "Jonas", 14, 8, 0, 3),
            _h2h_finish(2025, 2, "BB", "Seth", 16, 6, 0, 1),
        ]
        recs = champion_records(no_champ, "h2h")
        assert recs == []

    def test_zero_champions_with_final_rank_raises(self):
        bad = [
            _h2h_finish(2025, 1, "AA", "Jonas", 14, 8, 0, 3, final_rank=2),
            _h2h_finish(2025, 2, "BB", "Seth", 16, 6, 0, 1, final_rank=1),
        ]
        with pytest.raises(ValueError, match="0 champions"):
            champion_records(bad, "h2h")


# -- ties in the record ----------------------------------------------------

class TestTiesInRecord:

    def test_h2h_record_with_ties(self):
        with_ties = [
            _h2h_finish(2025, 1, "AA", "Jonas", 10, 8, 4, 3,
                        final_rank=1, champion=True),
            _h2h_finish(2025, 2, "BB", "Seth", 12, 10, 0, 1),
        ]
        recs = champion_records(with_ties, "h2h")
        assert recs[0]["regular_season_record"] == "10-8-4"

    def test_h2h_record_without_ties(self):
        recs = champion_records(FINISHED_H2H, "h2h")
        assert recs[0]["regular_season_record"] == "14-8"
