"""
Harvest the filled continuity mapping sheet into dbt seeds (MLB-64).

Reads the human-edited Google Sheet (the override surface built by
build_continuity_sheet.py) and writes the seeds dbt consumes to make the
warehouse's franchise + owner continuity faithful:

  cbs_franchise_lineage.csv  franchise_id -> canonical_franchise_id (the
                             earliest id in a lineage) + optional name/abbrev
                             overrides. OVERRIDE-ONLY: an unlinked franchise
                             isn't listed and resolves to itself in
                             dim_franchise.
  cbs_owner_alias.csv        owner_id -> canonical_owner_id + preferred_name
                             (collapses Dave/Desmond Foster and friends).
  cbs_owner_by_year.csv      per-season owner OVERRIDES (the historian's
                             Owner 1/2/3 entries). Downstream takes
                             COALESCE(this, stg_cbs__ui_rosters.owner_name).

Re-runnable: re-harvest after each editing pass, then `dbt seed` + rebuild.
Header text is matched loosely (first line, case-insensitive, substring), so
the historian can retitle a column or add their own (e.g. the preferred
abbreviation column) without breaking the harvest. Links are resolved with
union-find, so mutual / chained pointers ('A->B' and 'B->A', or 'A->B->C')
collapse to one canonical anchor rather than looping.

  python harvest_continuity_sheet.py --league cbs-bsb --sheet-id <id|url>
"""

import argparse
import csv
from pathlib import Path

from build_continuity_sheet import (_get_authorized_client, _sheet_id_from,
                                    _split_owners)

_SEEDS = Path(__file__).resolve().parent.parent / 'dbt_league' / 'seeds'


class _UF:
    """Tiny union-find so mutual/chained 'Same As' pointers collapse cleanly."""
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:      # path-compress
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a, b):
        self.parent[self.find(a)] = self.find(b)


def _load(ss, tab):
    """(header_keys, body_rows). Header is the SECOND sheet row (row 1 is the
    purpose banner); keys are lowercased first lines for loose matching."""
    v = ss.worksheet(tab).get_all_values()
    hdr = [h.split('\n')[0].strip().lower() for h in v[1]]
    return hdr, v[2:]


def _col(hdr, *subs):
    for i, h in enumerate(hdr):
        if all(s in h for s in subs):
            return i
    return None


def _cell(row, idx):
    return row[idx].strip() if idx is not None and idx < len(row) else ''


def harvest(league_key, sheet_id):
    ss = _get_authorized_client().open_by_key(sheet_id)

    # ---- franchise lineage (Teams tab) ------------------------------------
    hdr, body = _load(ss, 'Teams')
    c_id, c_sa = _col(hdr, 'team id'), _col(hdr, 'same as')
    c_name, c_ab = _col(hdr, 'canonical', 'name'), _col(hdr, 'abbrev')
    uf, over_name, over_ab, ids = _UF(), {}, {}, []
    for r in body:
        fid = _cell(r, c_id)
        if not fid:
            continue
        ids.append(fid)
        sa = _cell(r, c_sa)
        if sa.isdigit():                       # numeric -> same-league merge
            uf.union(fid, sa)
        if _cell(r, c_name):
            over_name[fid] = _cell(r, c_name)
        if _cell(r, c_ab):
            over_ab[fid] = _cell(r, c_ab)
    clusters = {}
    for fid in ids:
        clusters.setdefault(uf.find(fid), []).append(fid)
    canon = {}
    for members in clusters.values():
        anchor = str(min(int(m) for m in members))   # earliest id = anchor
        canon.update({m: anchor for m in members})
    lineage_rows = []
    for fid in sorted(ids, key=int):
        cid = canon.get(fid, fid)
        nm = over_name.get(fid) or over_name.get(cid) or ''
        ab = over_ab.get(fid) or over_ab.get(cid) or ''
        if cid != fid or nm or ab:             # override-only: skip self+empty
            lineage_rows.append((league_key, fid, cid, nm, ab))

    # ---- owner aliases (Owners tab) ---------------------------------------
    hdr, body = _load(ss, 'Owners')
    c_oid, c_sa, c_pn = (_col(hdr, 'owner id'), _col(hdr, 'same as'),
                         _col(hdr, 'preferred'))
    current = set()
    with open(_SEEDS / 'cbs_team_owners.csv', newline='') as f:
        for row in csv.DictReader(f):
            if row['league_key'] == league_key:
                current.add(row['owner_id'])
    ufo, pref, oids = _UF(), {}, []
    for r in body:
        oid = _cell(r, c_oid)
        if not oid:
            continue
        oids.append(oid)
        if _cell(r, c_sa):
            ufo.union(oid, _cell(r, c_sa))
        if _cell(r, c_pn):
            pref[oid] = _cell(r, c_pn)
    oclusters = {}
    for oid in oids:
        oclusters.setdefault(ufo.find(oid), []).append(oid)
    ocanon = {}
    for members in oclusters.values():
        cur = sorted(m for m in members if m in current)
        anchor = cur[0] if cur else sorted(members)[0]   # a current owner wins
        ocanon.update({m: anchor for m in members})
    alias_rows = []
    for oid in sorted(oids):
        cid = ocanon.get(oid, oid)
        pn = pref.get(oid) or pref.get(cid) or ''
        if cid != oid or pn:
            alias_rows.append((league_key, oid, cid, pn))

    # ---- per-season owner overrides (Team-Owner by Year tab) --------------
    hdr, body = _load(ss, 'Team-Owner by Year')
    c_yr, c_fid = _col(hdr, 'year'), _col(hdr, 'team id')
    c_owner = [_col(hdr, f'owner {i}') for i in (1, 2, 3)]
    oby_rows = []
    for r in body:
        yr, fid = _cell(r, c_yr), _cell(r, c_fid)
        if not yr or not fid:
            continue
        names = []
        for ci in c_owner:
            names += _split_owners(_cell(r, ci))
        for nm in dict.fromkeys(names):        # dedupe, preserve order
            oby_rows.append((league_key, yr, fid, nm))

    return lineage_rows, alias_rows, oby_rows


def _write(path, header, rows):
    with open(path, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"  wrote {path.name}  ({len(rows)} rows)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument('--league', required=True)
    ap.add_argument('--sheet-id', required=True, help='sheet id or full url')
    args = ap.parse_args()

    lineage, alias, oby = harvest(args.league, _sheet_id_from(args.sheet_id))
    _write(_SEEDS / 'cbs_franchise_lineage.csv',
           ['league_key', 'franchise_id', 'canonical_franchise_id',
            'canonical_name', 'canonical_abbrev'], lineage)
    _write(_SEEDS / 'cbs_owner_alias.csv',
           ['league_key', 'owner_id', 'canonical_owner_id', 'preferred_name'],
           alias)
    _write(_SEEDS / 'cbs_owner_by_year.csv',
           ['league_key', 'season_year', 'franchise_id', 'owner_name'], oby)
    print(f"\nHarvested: {len(lineage)} lineage links, {len(alias)} owner "
          f"aliases, {len(oby)} owner-year rows.")


if __name__ == '__main__':
    main()
