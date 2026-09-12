"""Render the redesigned Records surfaces (MLB-212) for one league.

    python output/generate_records_book.py --league espn-main
    python output/generate_records_book.py --league cbs-bsb --no-sheets --preview-dir out/

Default target is the league's DEV sheet (sheets_target's safe default).
--prod writes the same tabs to the standing book -- offered from Kyle's
09-11 "write these to prod" ruling on; it never ran to prod before that.
The three tabs take the standing Records tab's place on the dev book
(Lifetime · Season · Matchup) and that tab is hidden, not deleted;
nothing else in the workbook is written.
"""

import argparse
import csv
from pathlib import Path

import db
db.init()

import sheets_target
from records_book_data import load_book
from records_book_logic import build_lifetime_tab, build_period_tab


def build_tabs(book):
    """[(title, Sheet)] in tab order."""
    out = []
    for tab in book['tabs']:
        if tab['key'] == 'lifetime':
            sheet = build_lifetime_tab(tab, book['lifetime'], book['ctx'],
                                       book['catalog'], book['slots'])
        else:
            extra = ''
            caption = None
            if book['format'] == 'points' and tab['key'] == 'season':
                extra = ('This is a season-long league: the season IS the matchup, so '
                         'there is no separate Matchup Records tab; the third band '
                         'drills down to Monday-Sunday scoring weeks.')
            elif tab['key'] == 'season':
                # Spec 2.17 (09-09): team season figures per standard matchup.
                caption = ('Team records here are per standard matchup (week): Value is '
                           'the per-week figure over the regular-season matchups played, '
                           'Details lead with the raw season total and the matchup count. '
                           'Player records are raw season figures.')
            sheet = build_period_tab(tab, book['pools'], book['ctx'], book['catalog'],
                                     book['slots'], legend_extra=extra, team_caption=caption)
        out.append((tab['title'], sheet))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--league', default=None, metavar='LEAGUE_KEY')
    parser.add_argument('--no-sheets', action='store_true',
                        help='Build the tabs and print a summary without writing.')
    parser.add_argument('--prod', action='store_true',
                        help="Write to the league's production book instead of dev.")
    parser.add_argument('--preview-dir', default=None,
                        help='Write one TSV per tab to this directory.')
    parser.add_argument('--duckdb', nargs='?', const=True, default=None, metavar='PATH')
    args = parser.parse_args()
    if args.duckdb:
        db.use_duckdb(None if args.duckdb is True else args.duckdb)
    db.set_league(args.league)

    book = load_book()
    tabs = build_tabs(book)
    print(f"[records-book] league={db.league_key()} platform={book['platform']} "
          f"format={book['format']} seasons={book['seasons'][0]}-{book['seasons'][-1]} "
          f"current={book['current_season']} latest_unit={book['latest_unit']} "
          f"teams={book['ctx'].team_count} source_rows={book['counts']}")
    for title, sheet in tabs:
        print(f"[records-book] {title}: {len(sheet.rows)} rows, {len(sheet.groups)} groups")

    if args.preview_dir:
        out = Path(args.preview_dir)
        out.mkdir(parents=True, exist_ok=True)
        for title, sheet in tabs:
            path = out / (title.replace(' ', '-') + '.tsv')
            with path.open('w', newline='', encoding='utf-8') as f:
                csv.writer(f, delimiter='\t', lineterminator='\n').writerows(sheet.rows)
        print(f"[records-book] wrote {len(tabs)} preview TSVs to {out}")

    if args.no_sheets:
        return
    sheet_id, label = sheets_target.resolve_sheets_target(args.prod, db.league())
    if not sheet_id:
        print('[records-book] no dev sheet configured for this league; preview only')
        return
    from records_book_write import tab_url, write_records_book
    print(f"[records-book] writing to {label} sheet: {sheet_id}")
    gids = write_records_book(sheet_id, tabs)
    for title, gid in gids.items():
        print(f"[records-book] {title}: {tab_url(sheet_id, gid)}")


if __name__ == '__main__':
    main()
