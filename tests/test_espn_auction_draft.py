"""Auction draft regression contract: preserve facts, refuse snake meaning."""

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

from dotenv import load_dotenv

from almanac_logic import (
    build_draft_board_color_grid,
    build_draft_tab_rows,
)


_REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv()
os.environ.setdefault("LEAGUE_ID", "0")
_spec = importlib.util.spec_from_file_location(
    "extract_auction_under_test", _REPO_ROOT / "extract" / "extract.py")
extract = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(extract)


def _purchase(*, year=2026, team_id=1, team="AAA", player="Player A",
              player_id=10, price=17, draft_type="AUCTION"):
    return {
        "season_year": year,
        "overall_pick": player_id,
        "round_num": 1,
        "round_pick": player_id,
        "keeper": False,
        "draft_type": draft_type,
        "is_auction": draft_type == "AUCTION",
        "bid_amount": price,
        "team_id": team_id,
        "team_abbrev": team,
        "player_id": player_id,
        "player_name": player,
        "official_player_name": None,
        "season_points": 123.4,
        # An upstream rank must still be ignored on the auction path.
        "points_rank": 1,
        "value_delta": 999,
    }


def _flat(rows):
    return [str(value) for row in rows for value in row]


def test_extract_preserves_wrapper_bid_and_nominating_team(monkeypatch):
    pick = SimpleNamespace(
        team=SimpleNamespace(team_id=7),
        playerId=123,
        playerName="Auction Player",
        round_num=1,
        round_pick=4,
        keeper_status=False,
        bid_amount=42,
        nominatingTeam=SimpleNamespace(team_id=9),
    )
    monkeypatch.setattr(
        extract, "connect_espn", lambda year: SimpleNamespace(draft=[pick]))

    assert extract.fetch_draft(2026) == [{
        "overall_pick": 1,
        "round_num": 1,
        "round_pick": 4,
        "player_id": 123,
        "player_name": "Auction Player",
        "team_id": 7,
        "keeper": False,
        "bid_amount": 42,
        "nominating_team_id": 9,
    }]


def test_auction_renders_prices_without_pick_round_or_grade_surfaces():
    # Alphabetic team order intentionally disagrees with both capture order
    # and price order. Neither is allowed to become an implied draft order.
    purchases = [
        _purchase(team_id=2, team="ZZZ", player="Cheap", player_id=1,
                  price=1),
        _purchase(team_id=1, team="AAA", player="Expensive", player_id=2,
                  price=99),
    ]

    rows = build_draft_tab_rows(purchases, 2026, history_rows=purchases)
    flat = _flat(rows)
    header = next(i for i, row in enumerate(rows)
                  if row[:2] == ["Season", "Team"])

    assert rows[header + 1][1:4] == ["AAA", "Expensive", "$99"]
    assert rows[header + 2][1:4] == ["ZZZ", "Cheap", "$1"]
    assert not any(label in flat for label in (
        "Best Value Picks", "Biggest Busts", "Top Pick", "Rd",
        "Δ = Overall pick minus Total Points rank (+steal)",
    ))
    assert not any(value.startswith("Draft Board -") for value in flat)
    assert build_draft_board_color_grid(purchases) == []


def test_auction_without_bid_amount_says_unavailable_and_never_zero():
    purchase = _purchase(price=None)

    rows = build_draft_tab_rows([purchase], 2026)
    flat = _flat(rows)

    assert "Unavailable" in flat
    assert any(value.startswith("Auction price unavailable") for value in flat)
    assert "$0" not in flat


def test_non_null_bid_is_auction_evidence_even_without_type_setting():
    purchase = _purchase(price=23, draft_type=None)
    purchase["is_auction"] = False

    flat = _flat(build_draft_tab_rows([purchase], 2026))

    assert "$23" in flat
    assert "Best Value Picks" not in flat


def test_snake_without_auction_evidence_keeps_existing_board_and_grades():
    snake = _purchase(draft_type="SNAKE", price=None)
    snake.update({"is_auction": False, "overall_pick": 1,
                  "round_num": 1, "round_pick": 1,
                  "points_rank": 1, "value_delta": 0})

    rows = build_draft_tab_rows(
        [snake], 2026, history_rows=[snake], season_clocks={2026: 142},
    )
    flat = _flat(rows)

    assert "Best Value Picks" in flat
    assert "Biggest Busts" in flat
    assert "Top Pick" in flat
    assert any(value.startswith("Draft Board -") for value in flat)
    assert build_draft_board_color_grid([snake]) == [[123.4]]


def test_mixed_history_excludes_auction_from_snake_slot_analysis():
    snake = _purchase(year=2026, draft_type="SNAKE", price=None)
    snake.update({"is_auction": False, "overall_pick": 1,
                  "round_num": 1, "round_pick": 1,
                  "points_rank": 1, "value_delta": 0})
    auction = _purchase(year=2025, price=35, player="Prior Purchase")

    rows = build_draft_tab_rows(
        [snake], 2026, history_rows=[snake, auction],
        season_clocks={2025: 142, 2026: 142},
    )
    flat = _flat(rows)

    coverage = next(value for value in flat
                    if value.startswith("Team-agnostic, re-cut"))
    assert "Coverage: 2026." in coverage
    assert "2025" not in coverage
    assert "Auction Purchase History" in flat
    assert "Prior Purchase" in " ".join(flat)
    assert "$35" in flat
