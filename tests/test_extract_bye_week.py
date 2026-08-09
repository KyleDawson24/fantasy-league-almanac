"""
Pure tests for the two league-shape crash paths through
`extract.serialize_box_scores` -- MLB-222 C-1 (a bye week in an
odd-numbered H2H league) and C-6 (a lineup slot id espn-api does not map).

Kyle's league has no byes: ESPN pairs everyone, consolation included,
across all 17 periods of 2026 and all 26 of 2025. So the condition cannot
be reproduced against real data and is built synthetically here, from the
dependency's own behaviour.

The shape being reproduced is `espn_api.baseball.box_score`'s, verbatim:
`H2HPointsBoxScore._get_team_data` returns `(0, 0, -1, [])` when its side
is absent from the payload, and `League.box_scores()` then only swaps an
int for a `Team` when some team's `team_id` equals it -- no team has id 0,
so the bye side stays the int `0`. Every fake below preserves that.

Nothing here opens a connection: extract.py is loaded by path (same
convention as test_extract_club_of_game.py) and the one network call
inside serialize_box_scores is substituted.
"""

import importlib.util
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[1]

# See the long note in test_extract_club_of_game.py: load_dotenv() first,
# then setdefault, so a fresh clone imports and Kyle's real id never leaks
# into the session.
load_dotenv()
os.environ.setdefault("LEAGUE_ID", "0")

_spec = importlib.util.spec_from_file_location(
    "extract_bye_under_test", _REPO_ROOT / "extract" / "extract.py")
extract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(extract)


# ---------------------------------------------------------------------------
# Fakes -- shaped after the wrapper, not after what would be convenient
# ---------------------------------------------------------------------------
class _FakePlayer:
    def __init__(self, player_id, name, slot):
        self.playerId = player_id
        self.name = name
        self.position = "1B"
        self.lineupSlot = slot
        self.proTeam = "Bos"
        self.eligibleSlots = ["1B"]
        self.stats = {}


class _FakeTeam:
    def __init__(self, team_id, name, abbrev):
        self.team_id = team_id
        self.team_name = name
        self.team_abbrev = abbrev
        self.owners = [{"firstName": "test", "lastName": "owner"}]


class _FakeMatchup:
    """A box score after League.box_scores() has run its swap loop."""

    def __init__(self, home_team, away_team, home_lineup, away_lineup,
                 home_score=0, away_score=0):
        self.home_team = home_team
        self.away_team = away_team
        self.home_lineup = home_lineup
        self.away_lineup = away_lineup
        self.home_score = home_score
        self.away_score = away_score


class _FakeLeague:
    year = 2026

    def __init__(self, matchups):
        self._matchups = matchups

    def box_scores(self, matchup_period=None, scoring_period=None):
        return self._matchups


@pytest.fixture(autouse=True)
def _no_kona(monkeypatch):
    """serialize_box_scores' one network call. Empty is the honest answer
    for a synthetic league -- it drives the wrapper-fallback branch, which
    is the branch a bye-week row would take anyway."""
    monkeypatch.setattr(extract, "fetch_all_player_stats", lambda year, sp: {})


def _bye_league():
    """One real team, one bye. The int 0 on the away side is the whole
    point -- see the module docstring."""
    home = _FakeTeam(4, "Test Team Home", "TTH")
    home_lineup = [_FakePlayer(101, "Real Hitter", "1B")]
    return _FakeLeague([
        _FakeMatchup(home, 0, home_lineup, [], home_score=88.5, away_score=0),
    ])


# ---------------------------------------------------------------------------
# C-1 -- the crash
# ---------------------------------------------------------------------------
def test_bye_week_does_not_crash_the_extract():
    """The regression itself. Before the fix this raises
    AttributeError: 'int' object has no attribute 'owners' and takes the
    WHOLE extract down -- not one row, the run."""
    result = extract.serialize_box_scores(
        _bye_league(), scoring_period=100, matchup_period=7)
    assert len(result["matchups"]) == 1


def test_bye_side_is_labelled_not_invented():
    """Kyle: 'a ghost team or a BYE setting'. The ghost is explicit and
    identifiable -- and carries no team_id, so nothing downstream can
    mistake it for a real franchise or collide with a league that really
    does number a team 0."""
    m = extract.serialize_box_scores(
        _bye_league(), scoring_period=100, matchup_period=7)["matchups"][0]

    assert m["is_bye"] is True
    assert m["away_team_id"] is None
    assert m["away_team"] == "BYE"
    assert m["away_team_abbrev"] == "BYE"
    assert m["away_lineup"] == []


def test_bye_week_team_keeps_its_production():
    """The load-bearing assertion. A bye must cost the team its OPPONENT,
    never its own stat line -- that is the difference between a bye and a
    silent data hole."""
    m = extract.serialize_box_scores(
        _bye_league(), scoring_period=100, matchup_period=7)["matchups"][0]

    assert m["home_team_id"] == 4
    assert m["home_team"] == "Test Team Home"
    assert m["home_score"] == 88.5
    assert [p["playerId"] for p in m["home_lineup"]] == [101]


def test_normal_matchup_is_untouched_by_the_guard():
    """The fix must be invisible to a paired league -- which is every
    period of both pioneer leagues."""
    home = _FakeTeam(4, "Test Team Home", "TTH")
    away = _FakeTeam(9, "Test Team Away", "TTA")
    league = _FakeLeague([_FakeMatchup(
        home, away,
        [_FakePlayer(101, "Home Bat", "1B")],
        [_FakePlayer(202, "Away Bat", "2B")],
        home_score=88.5, away_score=91.25)])

    m = extract.serialize_box_scores(
        league, scoring_period=100, matchup_period=7)["matchups"][0]

    assert m["is_bye"] is False
    assert (m["home_team_id"], m["away_team_id"]) == (4, 9)
    assert m["away_team"] == "Test Team Away"
    assert m["away_owner"] == "Test Owner"
    assert [p["playerId"] for p in m["away_lineup"]] == [202]


def test_unmapped_lineup_slot_fails_with_the_id_named():
    """C-6. espn_api's BoxPlayer does POSITION_MAP[data['lineupSlotId']]
    with no default, so a league using an unmapped slot id dies with a
    bare KeyError(18) and no indication of what broke. The guard turns it
    into something a stranger can act on."""
    class _SlotBomb(_FakeLeague):
        def box_scores(self, matchup_period=None, scoring_period=None):
            raise KeyError(18)

    with pytest.raises(RuntimeError) as excinfo:
        extract.serialize_box_scores(
            _SlotBomb([]), scoring_period=100, matchup_period=7)

    msg = str(excinfo.value)
    assert '18' in msg
    assert 'lineup slot id' in msg
    # The period is named too -- "it broke" is not actionable, "it broke
    # on period 7" is.
    assert 'matchup period 7' in msg


def test_unmapped_slot_failure_keeps_the_original_keyerror():
    """Chained, not swallowed: the traceback still reaches the wrapper
    line that raised, which is where a maintainer needs to land."""
    class _SlotBomb(_FakeLeague):
        def box_scores(self, matchup_period=None, scoring_period=None):
            raise KeyError(18)

    with pytest.raises(RuntimeError) as excinfo:
        extract.serialize_box_scores(
            _SlotBomb([]), scoring_period=100, matchup_period=7)

    assert isinstance(excinfo.value.__cause__, KeyError)


def test_unmapped_slot_is_not_silently_coerced():
    """The load-bearing half of C-6. An unrecognised slot must NOT be
    guessed: classified as hitting it counts a benched player as active
    starter production, classified as pitching it deletes the stat line.
    A stopped run is the correct outcome, so this asserts the extract
    produces NOTHING rather than a plausible-looking payload."""
    class _SlotBomb(_FakeLeague):
        def box_scores(self, matchup_period=None, scoring_period=None):
            raise KeyError(18)

    with pytest.raises(RuntimeError):
        extract.serialize_box_scores(
            _SlotBomb([]), scoring_period=100, matchup_period=7)


def test_known_slot_ids_are_reported_so_the_gap_is_visible():
    """The message names what espn-api DOES map, so the unmapped id can be
    reported upstream without reading the dependency."""
    msg = str(extract.unmapped_lineup_slot_error(KeyError(18), 7, 100))
    # 16 = BE and 19 = IF are real entries in the installed POSITION_MAP.
    assert '16' in msg and '19' in msg


def test_bye_on_the_home_side_is_handled_too():
    """The wrapper guards `if team not in data` for BOTH sides, so the
    guard here is symmetric rather than assuming ESPN only ever drops the
    away key."""
    away = _FakeTeam(9, "Test Team Away", "TTA")
    league = _FakeLeague([_FakeMatchup(
        0, away, [], [_FakePlayer(202, "Away Bat", "2B")],
        home_score=0, away_score=77.0)])

    m = extract.serialize_box_scores(
        league, scoring_period=100, matchup_period=7)["matchups"][0]

    assert m["is_bye"] is True
    assert m["home_team_id"] is None
    assert m["home_team"] == "BYE"
    assert m["away_team_id"] == 9
    assert [p["playerId"] for p in m["away_lineup"]] == [202]
