"""CBS Draft Recap builder (2026-07-18): pure layout tests over injected
draft history -- no warehouse, no NDJSON. Covers the current-season
leaderboard/board math, the all-time re-cut to 16-team rounds (ordered
years only, current season excluded), and the Draft Classes digest's
sequence labels."""

import cbs_almanac_sheets as cbs


def _pick(year, overall, rnd, team, name, pts, tier='true', pid='1'):
    return {'season_year': year, 'draft_label': 'Test', 'round_num': rnd,
            'round_pick': None, 'overall_pick': overall, 'list_seq': 1,
            'team_name_raw': team, 'player_cbs_id': pid,
            'player_name_raw': name, 'pos_team_raw': None,
            'page_total_fpts': None, 'page_active_fpts': None,
            'order_tier': tier, 'calc_total': float(pts),
            'calc_hitting': float(pts), 'calc_pitching': 0.0,
            'resolution': 'id', 'twoway_sum': False}


def _history():
    # 2026: two teams, two rounds, fully ordered.
    picks = [
        _pick(2026, 1, 1, 'Alpha', 'Stud One', 400),
        _pick(2026, 2, 1, 'Beta', 'Bust One', 10),
        _pick(2026, 3, 2, 'Beta', 'Steal Two', 390),
        _pick(2026, 4, 2, 'Alpha', 'Meh Two', 50),
    ]
    # 2025: ordered history -- overall 1 and 17 land in all-time rounds 1
    # and 2 of the 16-team shape.
    picks.append(_pick(2025, 1, 1, 'Alpha', 'Old Ace', 500))
    picks.append(_pick(2025, 17, 2, 'Beta', 'Round Two Guy', 300))
    # 2018: no order recorded -- Draft Classes only.
    picks.append(_pick(2018, None, None, 'Alpha', 'Listy', 700, tier='none'))
    report = {
        2018: {'picks': 1, 'order': 'none', 'rounds': None, 'note': None,
               'resolution': {'id': 1}},
        2025: {'picks': 2, 'order': 'true', 'rounds': 2, 'note': None,
               'resolution': {'id': 2}},
        2026: {'picks': 4, 'order': 'true', 'rounds': 2, 'note': None,
               'resolution': {'id': 4}},
    }
    return picks, report


def _fmap():
    return {1: {'name': 'Alpha', 'abbrev': 'ALP'},
            2: {'name': 'Beta', 'abbrev': 'BET'}}


def _build():
    return cbs.build_draft_recap_rows(2026, _fmap(), history=_history())


def _cell_text(cell):
    return str(cell)


def test_layout_and_bands():
    rows, formats = _build()
    assert rows[0] == [cbs.DRAFT_TAB]
    flat = ['\t'.join(_cell_text(c) for c in r) for r in rows]
    assert any(l.startswith('Draft Recap: 2026') for l in flat)
    assert any(l.startswith('Draft Board - 2026') for l in flat)
    assert any(l.startswith('All-Time Draft Board') for l in flat)
    assert any(l.startswith('Draft Classes') for l in flat)
    # Title + subtitle formats target A1/A2, and a gradient rides the
    # all-time grid.
    assert formats[0]['range'].startswith('A1:')
    assert any('gradient' in f for f in formats)


def test_current_season_value_math():
    rows, _ = _build()
    # points ranks: Stud One 1, Steal Two 2, Meh Two 3, Bust One 4.
    # value_delta = overall - rank: Stud +0, Steal +1, Meh +1, Bust -2.
    header = next(i for i, r in enumerate(rows) if r and r[0] == 'Best Value Picks')
    first = rows[header + 2]
    assert 'Steal Two' in first[0] or 'Meh Two' in first[0]
    assert first[4] == '+1'
    assert 'Bust One' in first[6]        # biggest bust, delta -2
    assert first[10] == '-2'


def test_board_round_summaries():
    rows, _ = _build()
    hdr = next(i for i, r in enumerate(rows) if r and r[0] == 'Rd' and r[1] == 'Min')
    assert rows[hdr][4:] == ['ALP', 'BET']       # round-1 pick order
    r1 = rows[hdr + 1]
    assert r1[:4] == [1, 10, 205, 400]           # min/median/max of 400,10
    assert 'Stud One' in r1[4] and 'Bust One' in r1[5]


def test_alltime_board_excludes_current_and_recuts():
    rows, _ = _build()
    hdr = next(i for i, r in enumerate(rows) if r and r[0] == 'Rd' and r[1] == 'Med')
    r1, r2 = rows[hdr + 1], rows[hdr + 2]
    # 2026 excluded: only 2025's two picks appear, one per re-cut round.
    assert r1[0] == 1 and r1[4] == 500 and 'Old Ace -2025' in r1[3]
    assert r2[0] == 2 and r2[4] == 300 and 'Round Two Guy -2025' in r2[3]
    # Slots without history stay blank.
    assert r1[5] == ''


def test_draft_classes_sequence_labels():
    rows, _ = _build()
    hdr = next(i for i, r in enumerate(rows) if r and r[0] == 'Year')
    by_year = {r[0]: r for r in rows[hdr + 1:] if r}
    assert by_year[2018][3] == 'not recorded'
    assert by_year[2025][3] == 'recorded'
    assert 'Listy (700)' in by_year[2018][4]
