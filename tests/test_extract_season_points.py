"""ESPN season-long points acquisition without invented matchups.

The pinned wrapper has concrete H2H box-score classes but no concrete class
for ESPN league type 5. These tests pin the replacement seam: day-specific
mRoster data supplies fantasy-team and lineup attribution, kona supplies the
player-day statistics, and RAW keeps ``matchups`` empty.
"""

import importlib.util
import os
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("LEAGUE_ID", "0")

_spec = importlib.util.spec_from_file_location(
    "extract_season_points_under_test", _REPO_ROOT / "extract" / "extract.py")
extract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(extract)


def _roster_document(slot_id=0):
    return {
        "members": [
            {"id": "member-a", "firstName": "Sample", "lastName": "User"}
        ],
        "teams": [{
            "id": 7,
            "location": "Example",
            "nickname": "Club",
            "abbrev": "EX",
            "owners": ["member-a"],
            "roster": {"entries": [{
                "playerId": 101,
                "lineupSlotId": slot_id,
                "playerPoolEntry": {"player": {
                    "id": 101,
                    "fullName": "Rostered Player",
                    "defaultPositionId": 1,
                    "proTeamId": 10,
                    "eligibleSlots": [0, 12],
                    "stats": [],
                }},
            }]},
        }],
    }


def _player_stats():
    return {
        101: {
            "name": "Rostered Player",
            "breakdown": {"AB": 4},
            "points": 3.5,
            "games_played": 1,
            "pro_team": "NYY",
            "club_of_game": "NYY",
            "default_position_id": 1,
            "eligible_slots": ["C", "UTIL"],
        },
        202: {
            "name": "Available Player",
            "breakdown": {"AB": 3},
            "points": 1.0,
            "games_played": 1,
            "pro_team": "BOS",
            "club_of_game": "BOS",
            "default_position_id": 2,
            "eligible_slots": ["1B"],
        },
    }


def test_team_rosters_reach_raw_without_an_opponent(monkeypatch):
    monkeypatch.setattr(
        extract, "fetch_season_points_rosters",
        lambda year, scoring_period: _roster_document())
    monkeypatch.setattr(
        extract, "fetch_all_player_stats",
        lambda year, scoring_period: _player_stats())

    result = extract.serialize_season_points_rosters(2026, 12)

    assert result["matchups"] == []
    assert len(result["team_rosters"]) == 1
    team = result["team_rosters"][0]
    assert team["team_id"] == 7
    assert team["team_name"] == "Example Club"
    assert team["owner"] == "Sample User"
    assert team["lineup"][0]["playerId"] == 101
    assert team["lineup"][0]["points"] == 3.5
    assert [player["playerId"] for player in result["free_agents"]] == [202]


def test_unknown_lineup_slot_refuses_instead_of_guessing(monkeypatch):
    monkeypatch.setattr(
        extract, "fetch_season_points_rosters",
        lambda year, scoring_period: _roster_document(slot_id=999))
    monkeypatch.setattr(
        extract, "fetch_all_player_stats",
        lambda year, scoring_period: _player_stats())

    with pytest.raises(RuntimeError, match="lineup slot id 999"):
        extract.serialize_season_points_rosters(2026, 12)


def test_mroster_request_is_bounded_and_authenticated(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"teams": []}

    seen = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        seen.update(kwargs)
        return Response()

    monkeypatch.setattr(extract.requests, "get", fake_get)

    assert extract.fetch_season_points_rosters(2026, 12) == {"teams": []}
    assert seen["params"] == {"view": "mRoster", "scoringPeriodId": 12}
    assert seen["timeout"] == 30
    assert seen["cookies"] == {
        "swid": extract.SWID, "espn_s2": extract.ESPN_S2}


def test_mroster_retries_a_transient_timeout(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"teams": []}

    calls = []
    sleeps = []

    def fake_get(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            raise extract.requests.Timeout("ESPN paused")
        return Response()

    monkeypatch.setattr(extract.requests, "get", fake_get)
    monkeypatch.setattr(extract.time, "sleep", sleeps.append)

    assert extract.fetch_season_points_rosters(2026, 12) == {"teams": []}
    assert len(calls) == 2
    assert sleeps == [1]


def test_owner_capture_preserves_public_display_name_when_names_are_private(
        monkeypatch):
    class Team:
        team_id = 123456
        owners = [{
            "id": "member-a", "firstName": None, "lastName": None,
            "displayName": "Public Manager",
        }]

    class League:
        teams = [Team()]

    monkeypatch.setattr(extract, "connect_espn", lambda year: League())

    assert extract.fetch_team_owners(2026) == [{
        "team_id": 123456,
        "owner_id": "member-a",
        "first_name": None,
        "last_name": None,
        "display_name": "Public Manager",
    }]
