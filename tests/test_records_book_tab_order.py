"""The MLB-212 Records book rides directly after the standings tab in both
standing renders' tab order (Kyle 2026-09-14): the standing sort pass must
slot whichever of Lifetime / Season / Matchup the workbook carries there,
not shove them to the end of the strip behind the appendix tab."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'output'))

from almanac_write import RECORDS_BOOK_TABS, with_records_book_tabs  # noqa: E402

LIFETIME, SEASON, MATCHUP = RECORDS_BOOK_TABS
ESPN = ['Home', 'Records', 'Advanced Standings', 'Trades', 'Draft Recap',
        'AAA', 'BP', 'Matchup History']
CBS = ['Home', 'Records', 'Advanced Standings', 'Draft Recap',
       'Betty White Sox', 'Season History']


def test_titles_are_the_records_book_titles():
    assert RECORDS_BOOK_TABS == ('Lifetime Records', 'Season Records', 'Matchup Records')


def test_espn_book_slots_all_three_after_advanced_standings():
    present = set(ESPN) | set(RECORDS_BOOK_TABS)
    out = with_records_book_tabs(ESPN, present, 'Advanced Standings')
    assert out == ['Home', 'Records', 'Advanced Standings',
                   LIFETIME, SEASON, MATCHUP,
                   'Trades', 'Draft Recap', 'AAA', 'BP', 'Matchup History']


def test_points_league_slots_only_the_tabs_it_has():
    present = set(CBS) | {LIFETIME, SEASON}
    out = with_records_book_tabs(CBS, present, 'Advanced Standings')
    assert out == ['Home', 'Records', 'Advanced Standings', LIFETIME, SEASON,
                   'Draft Recap', 'Betty White Sox', 'Season History']


def test_fresh_book_without_a_records_book_is_untouched():
    assert with_records_book_tabs(ESPN, set(ESPN), 'Advanced Standings') == ESPN


def test_missing_anchor_leaves_order_alone():
    present = set(ESPN) | set(RECORDS_BOOK_TABS)
    assert with_records_book_tabs(ESPN, present, 'Nope') == ESPN


def test_titles_already_in_order_are_not_duplicated():
    order = ['Home', 'Advanced Standings', SEASON, 'Draft Recap']
    present = set(order) | {LIFETIME, MATCHUP}
    out = with_records_book_tabs(order, present, 'Advanced Standings')
    assert out == ['Home', 'Advanced Standings', LIFETIME, MATCHUP, SEASON, 'Draft Recap']
    assert out.count(SEASON) == 1


def test_input_list_is_not_mutated():
    order = list(ESPN)
    with_records_book_tabs(order, set(ESPN) | set(RECORDS_BOOK_TABS), 'Advanced Standings')
    assert order == ESPN


# --------------------------------------------------------------------------
# The Records book's own placement agrees with the standing renders, so the
# two writers stop taking turns moving the strip.
# --------------------------------------------------------------------------

from records_book_write import placement_order  # noqa: E402

NEW = [LIFETIME, SEASON, MATCHUP]


def test_records_book_places_itself_after_advanced_standings():
    # Freshly written tabs land at the end of the strip.
    current = ESPN + NEW
    assert placement_order(current, NEW, 'Records') == [
        'Home', 'Records', 'Advanced Standings', LIFETIME, SEASON, MATCHUP,
        'Trades', 'Draft Recap', 'AAA', 'BP', 'Matchup History']


def test_records_book_placement_is_idempotent_after_a_standing_render():
    placed = ['Home', 'Records', 'Advanced Standings', LIFETIME, SEASON, MATCHUP,
              'Trades', 'Draft Recap', 'AAA', 'BP', 'Matchup History']
    assert placement_order(placed, NEW, 'Records') == placed


def test_records_book_placement_moves_tabs_back_from_the_front():
    # The 09-09 rule had parked them in the Records slot, ahead of standings.
    current = ['Home', LIFETIME, SEASON, MATCHUP, 'Records', 'Advanced Standings',
               'Trades', 'Draft Recap', 'AAA', 'BP', 'Matchup History']
    assert placement_order(current, NEW, 'Records') == [
        'Home', 'Records', 'Advanced Standings', LIFETIME, SEASON, MATCHUP,
        'Trades', 'Draft Recap', 'AAA', 'BP', 'Matchup History']


def test_points_league_places_its_two_tabs():
    current = CBS + [LIFETIME, SEASON]
    assert placement_order(current, [LIFETIME, SEASON], 'Records') == [
        'Home', 'Records', 'Advanced Standings', LIFETIME, SEASON,
        'Draft Recap', 'Betty White Sox', 'Season History']


def test_book_without_advanced_standings_uses_the_records_slot():
    current = ['Home', 'Records', 'Draft Recap'] + NEW
    assert placement_order(current, NEW, 'Records') == [
        'Home', LIFETIME, SEASON, MATCHUP, 'Records', 'Draft Recap']


def test_book_with_neither_anchor_is_left_alone():
    current = ['Home', 'Draft Recap'] + NEW
    assert placement_order(current, NEW, 'Records') == current
