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
    assert formats[0]['range'].startswith('A1:')
    # Both boards paint via cell_colors; three merges = a 'Top Pick' (B:D)
    # on each board + the all-time cells label (G:V).
    assert sum('cell_colors' in f for f in formats) == 2
    assert sum(bool(f.get('merge')) for f in formats) == 3
    assert any(cbs._ALLTIME_CELLS_LABEL in r for r in rows if r)
    # Data-driven coverage line names the ordered-draft years.
    assert any('Coverage: 2025' in str(r[0]) for r in rows if r)


def test_current_season_value_math():
    rows, _ = _build()
    # points ranks: Stud One 1, Steal Two 2, Meh Two 3, Bust One 4.
    # value_delta = overall - rank: Stud +0, Steal +1, Meh +1, Bust -2.
    # Row now: buffer A, value Pts/Tm/Player/(Rd)#Pick/ΔRank in B-F, busts G-K.
    header = next(i for i, r in enumerate(rows) if len(r) > 1 and r[1] == 'Best Value Picks')
    first = rows[header + 2]
    assert 'Steal Two' in first[3] or 'Meh Two' in first[3]   # value Player col (D)
    assert first[5] == '+1'                                   # value Δ Rank (F)
    assert 'Bust One' in first[8]                             # bust Player col (I)
    assert first[10] == '-2'                                  # bust Δ Rank (K)


def test_board_top_pick_and_summary():
    rows, _ = _build()
    # Current board header: Rd | Pick | Team | Player | Max | Med | teams.
    hdr = next(i for i, r in enumerate(rows) if r and r[0] == 'Rd' and r[1] == 'Pick')
    assert rows[hdr][2] == 'Team' and rows[hdr][4] == 'Max' and rows[hdr][5] == 'Med'
    assert rows[hdr][6:] == ['ALP', 'BET']       # round-1 pick order
    r1 = rows[hdr + 1]
    assert r1[0] == 1
    assert 'Stud One' in r1[3]                    # round-1 top pick (Player)
    assert r1[2] == 'ALP'                         # top pick's team
    assert r1[4] == 400 and r1[5] == 205          # Max, Med of 400 & 10 (int, no Min)
    # Team cells are first-initial links.
    assert 'S One' in r1[6] and 'B One' in r1[7]


def test_alltime_board_includes_current_and_recuts():
    rows, _ = _build()
    # All-time header: Rd | Year | Team | Player | Max | Med | 1..16.
    hdr = next(i for i, r in enumerate(rows) if r and r[0] == 'Rd' and r[1] == 'Year')
    r1, r2 = rows[hdr + 1], rows[hdr + 2]
    # No season_clocks -> uniform pacing. 2026 is INCLUDED now: round-1 slot 1
    # median of {2025 Old Ace 500, 2026 Stud One 400} = 450; the straight
    # (raw) Top Pick is 2025's Old Ace at 500.
    assert r1[0] == 1 and r1[1] == 2025 and 'Old Ace' in r1[3] and r1[4] == 500
    assert r1[6] == 450                           # slot-1 median across seasons
    assert r2[0] == 2 and r2[1] == 2025 and 'Round Two Guy' in r2[3] and r2[4] == 300


def test_draft_classes_sequence_labels():
    rows, _ = _build()
    hdr = next(i for i, r in enumerate(rows) if r and r[0] == 'Year')
    by_year = {r[0]: r for r in rows[hdr + 1:] if r}
    assert by_year[2018][3] == 'not recorded'
    assert by_year[2025][3] == 'recorded'
    assert 'Listy (700)' in by_year[2018][4]
