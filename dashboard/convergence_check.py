#!/usr/bin/env python3
"""
convergence_check.py -- does the neutral layer reproduce what the live
export already publishes? (MLB-319's convergence guard.)

When a dashboard page swaps its source from a pre-baked export table to the
neutral layer (atom + dims), its numbers must not move. This recomputes
three export tables from the neutral layer ALONE and compares them cell by
cell at the export's own precision:

  1. Detailed Standings -- per (scope, team, metric) raw totals, regular
     season, for every season and all-time; plus the printed per-matchup
     value through the export's own formula and the scoring-day denominator.
  2. Points by Lineup Slot -- per (scope, team, slot) raw totals and the
     printed per-matchup value; plus the matchups-played denominator.
  3. Season Finishes -- per (season, team) seed, final rank, champion flag
     and W-L-T.

Every mismatch is listed with the values on both sides. Nothing is
tolerance-fudged: a cell matches when both sides print the same number.

Usage:
    python dashboard/convergence_check.py --export-dir <shared/> \
        --extract-dir <out>/<stamp>/espn_main [--report out.json]
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "output"))
import almanac_render  # noqa: E402  (the export's own per-week formula)

# Export metric key -> atom column. Hitting stats count while the row's
# hitting discipline is credited, pitching stats while pitching is.
STAT_RENAMES = {"B_BB": "bb", "B_SO": "so", "B_IBB": "ibb"}
POINT_METRICS = {
    "OFFENSE": "batting_pts * batting_credited_share",
    "DEFENSE": "pitching_pts * pitching_credited_share",
    "TOTAL": "batting_pts * batting_credited_share + pitching_pts * pitching_credited_share",
}
NOT_ATOM = {"AGAINST": "points conceded is an opponent (matchup-grain) quantity; "
                       "contract section 5.3 keeps team-grain outcomes out of the atom"}


def exact(expr):
    """The warehouse's own arithmetic (MLB-128 stable_sum): sum in exact
    DECIMAL(18, 6), round half away from zero to 1 dp. Summing in DOUBLE
    instead turns a true 1606.35 into 1606.3499999999988 and prints 1606.3
    where the export printed 1606.4 -- a difference of method, not data."""
    return f"CAST(ROUND(SUM(CAST({expr} AS DECIMAL(18, 6))), 1) AS DOUBLE)"


def r1(x):
    return None if x is None else round(float(x) + 0.0, 1)


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))["rows"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--export-dir", required=True)
    ap.add_argument("--extract-dir", required=True)
    ap.add_argument("--report")
    args = ap.parse_args(argv)
    exp, ext = Path(args.export_dir), Path(args.extract_dir)
    con = duckdb.connect()
    atom = f"read_parquet('{(ext / 'atom' / '*.parquet').as_posix()}')"
    con.execute(f"CREATE VIEW atom AS SELECT * FROM {atom}")
    con.execute(f"CREATE VIEW periods AS SELECT * FROM read_parquet('{(ext / 'dims' / 'periods.parquet').as_posix()}')")
    con.execute(f"CREATE VIEW teams AS SELECT * FROM read_parquet('{(ext / 'dims' / 'teams.parquet').as_posix()}')")
    report = {}

    # -- 1. Detailed Standings ------------------------------------------------
    ds = load(exp / "detailed_standings.json")
    metrics = sorted({r["metric_key"] for r in ds})
    exprs = {}
    for m in metrics:
        if m in NOT_ATOM:
            continue
        if m in POINT_METRICS:
            exprs[m] = POINT_METRICS[m]
        else:
            cat = next(r["metric_category"] for r in ds if r["metric_key"] == m)
            share = "batting_credited_share" if cat == "hitting" else "pitching_credited_share"
            exprs[m] = f"{STAT_RENAMES.get(m, m.lower())} * {share}"
    sel = ", ".join(f"{exact(e)} AS \"{m}\"" for m, e in exprs.items())
    # The export's denominator is the LEAGUE's regular-season scoring days,
    # summed over the seasons the team played (not the days a team has rows).
    con.execute("""CREATE VIEW league_days AS
        SELECT season_year, COUNT(DISTINCT scoring_day) AS days FROM atom
        WHERE phase = 'regular' GROUP BY 1""")
    grain = {}
    for scope_sql in ("CAST(season_year AS VARCHAR)", "'all_time'"):
        for row in con.execute(f"""
                WITH t AS (SELECT {scope_sql} AS scope, team_id, {sel}
                           FROM atom WHERE phase = 'regular' AND team_id IS NOT NULL GROUP BY ALL),
                     d AS (SELECT {scope_sql} AS scope, team_id, SUM(l.days) AS days
                           FROM (SELECT DISTINCT season_year, team_id FROM atom
                                 WHERE phase = 'regular' AND team_id IS NOT NULL)
                           JOIN league_days l USING (season_year) GROUP BY ALL)
                SELECT t.scope, t.team_id, d.days, t.* EXCLUDE (scope, team_id)
                FROM t JOIN d USING (scope, team_id)""").fetchall():
            scope, team, days, *vals = row
            grain[(scope, team)] = (days, dict(zip(exprs, vals)))
    # The upstream marts round every TEAM-WEEK to 1 dp (stable_sum's default
    # scale) and then sum the rounded weeks. The atom sums unrounded days.
    # For points metrics the same numerator is also computed in the
    # export's order so a mismatch can be attributed, not just counted.
    week_rounded = {}
    for scope_sql in ("CAST(season_year AS VARCHAR)", "'all_time'"):
        cols = ", ".join(f"ROUND(SUM(CAST({e} AS DECIMAL(18, 6))), 1) AS \"{m}\""
                         for m, e in POINT_METRICS.items())
        sums = ", ".join(f"SUM(\"{m}\")" for m in POINT_METRICS)
        for scope, team, *vals in con.execute(f"""
                WITH w AS (SELECT season_year, period_key, team_id, {cols}
                           FROM atom WHERE phase = 'regular' AND team_id IS NOT NULL GROUP BY ALL)
                SELECT {scope_sql}, team_id, {sums} FROM w GROUP BY ALL""").fetchall():
            week_rounded[(scope, team)] = dict(zip(POINT_METRICS, (float(v) for v in vals)))
    # The printed value runs the export's own formula over the ATOM's raw
    # total with the EXPORT's denominators, so a printed mismatch isolates
    # the numerator; the denominator is compared separately.
    ds_cells = Counter()
    ds_mismatch = []
    for r in ds:
        key = (r["scope"], r["team_id"])
        m = r["metric_key"]
        if m in NOT_ATOM:
            ds_cells["not_atom"] += 1
            continue
        if key not in grain:
            ds_cells["missing_in_atom"] += 1
            ds_mismatch.append({"scope": r["scope"], "team_id": r["team_id"], "metric": m,
                                "cause_hint": "team-scope absent from the atom"})
            continue
        days, vals = grain[key]
        atom_raw = vals[m]  # OUTS stays in outs on both sides; the formula divides by 3
        ok_raw = r1(atom_raw) == r1(r["raw_total"])
        ok_days = days == r["denominator_value"]
        spec = {"stat_name": m}
        row_like = {"scoring_days_played": r["denominator_value"],
                    "standard_matchup_days": r["standard_matchup_days"]}
        if m in POINT_METRICS:
            printed = almanac_render._per_week_value(row_like, atom_raw)
        else:
            row_like[m.lower()] = atom_raw  # the formula looks the stat up by its own name
            printed = almanac_render._per_week_stat(row_like, spec)
        printed = None if printed == "" else printed
        ok_printed = printed == r["per_matchup_value"]
        ds_cells["raw_match" if ok_raw else "raw_mismatch"] += 1
        ds_cells["printed_match" if ok_printed else "printed_mismatch"] += 1
        ds_cells["denominator_match" if ok_days else "denominator_mismatch"] += 1
        if not (ok_raw and ok_printed and ok_days):
            cause = None
            if m in POINT_METRICS and r1(week_rounded[key][m]) == r1(r["raw_total"]):
                cause = "team_week_rounding: upstream rounds each team-week to 1 dp, then sums"
                ds_cells["mismatch_rows_explained_by_team_week_rounding"] += 1
            ds_mismatch.append({"scope": r["scope"], "team_id": r["team_id"], "metric": m,
                                "export_raw": r["raw_total"], "atom_raw": r1(atom_raw),
                                "export_printed": r["per_matchup_value"], "atom_printed": printed,
                                "export_days": r["denominator_value"], "atom_days": days,
                                "cause": cause})
    report["detailed_standings"] = {"cells": dict(ds_cells), "mismatches": ds_mismatch,
                                    "excluded_metrics": NOT_ATOM}

    # -- 2. Points by Lineup Slot ---------------------------------------------
    ps = load(exp / "points_by_lineup_slot.json")
    slot_pts = {}
    for scope_sql in ("CAST(season_year AS VARCHAR)", "'all_time'"):
        for scope, team, slot, pts in con.execute(f"""
                SELECT {scope_sql}, team_id, platform_lineup_slot,
                       {exact("batting_pts * batting_credited_share + pitching_pts * pitching_credited_share")}
                FROM atom WHERE phase = 'regular' AND deployment_state = 'active'
                GROUP BY ALL""").fetchall():
            slot_pts[(scope, team, slot)] = pts
    # The export's all-time slot figure sums SEASON values that were each
    # already rounded to 1 dp (get_team_slot_points_alltime over the mart);
    # the same numerator in that order, for attribution.
    season_rounded_alltime = {}
    for team, slot, pts in con.execute(f"""
            WITH s AS (SELECT season_year, team_id, platform_lineup_slot,
                       {exact("batting_pts * batting_credited_share + pitching_pts * pitching_credited_share")} AS pts
                       FROM atom WHERE phase = 'regular' AND deployment_state = 'active' GROUP BY ALL)
            SELECT team_id, platform_lineup_slot, SUM(pts) FROM s GROUP BY ALL""").fetchall():
        season_rounded_alltime[("all_time", team, slot)] = pts
    # Matchups played: completed regular-season periods in which the team
    # has any atom row, counted from the periods dim.
    mp = {}
    for scope_sql in ("CAST(a.season_year AS VARCHAR)", "'all_time'"):
        for scope, team, n in con.execute(f"""
                SELECT {scope_sql}, a.team_id, COUNT(DISTINCT (a.season_year, a.period_key))
                FROM atom a JOIN periods p USING (season_year, period_key)
                WHERE a.phase = 'regular' AND a.team_id IS NOT NULL
                GROUP BY ALL""").fetchall():
            mp[(scope, team)] = n
    ps_cells, ps_mismatch = Counter(), []
    for r in ps:
        key = (r["scope"], r["team_id"], r["lineup_slot"])
        atom_raw = slot_pts.get(key)
        n = mp.get((r["scope"], r["team_id"]))
        printed = None if atom_raw is None or not n else round(atom_raw / n, 1)
        ok_raw = r1(atom_raw) == r1(r["raw_total"])
        ok_printed = printed == r["per_matchup_value"]
        ok_den = n == r["denominator_value"]
        ps_cells["raw_match" if ok_raw else "raw_mismatch"] += 1
        ps_cells["printed_match" if ok_printed else "printed_mismatch"] += 1
        ps_cells["denominator_match" if ok_den else "denominator_mismatch"] += 1
        if not (ok_raw and ok_printed and ok_den):
            cause = None
            if key in season_rounded_alltime and \
                    r1(season_rounded_alltime[key]) == r1(r["raw_total"]):
                cause = "season_rounding: the all-time figure sums seasons already rounded to 1 dp"
                ps_cells["mismatch_rows_explained_by_season_rounding"] += 1
            ps_mismatch.append({"scope": r["scope"], "team_id": r["team_id"], "slot": r["lineup_slot"],
                                "export_raw": r["raw_total"], "atom_raw": r1(atom_raw),
                                "export_printed": r["per_matchup_value"], "atom_printed": printed,
                                "export_matchups": r["denominator_value"], "atom_matchups": n,
                                "cause": cause})
    extra = [k for k in slot_pts if (k[0], k[1], k[2]) not in {(r["scope"], r["team_id"], r["lineup_slot"]) for r in ps}
             and abs(slot_pts[k] or 0) >= 0.05]
    report["points_by_lineup_slot"] = {"cells": dict(ps_cells), "mismatches": ps_mismatch,
                                       "atom_cells_absent_from_export": [list(k) for k in extra]}

    # -- 3. Season Finishes -----------------------------------------------------
    sf = load(exp / "season_finishes.json")
    teams = {(s, t): dict(zip(["seed", "final_rank", "is_champion", "wins", "losses", "ties"], v))
             for s, t, *v in con.execute("""SELECT season_year, team_id, playoff_seed, final_rank,
                 is_champion, wins, losses, ties FROM teams""").fetchall()}
    sf_cells, sf_mismatch = Counter(), []
    fields = [("finish", "seed"), ("final_rank", "final_rank"), ("is_champion", "is_champion"),
              ("wins", "wins"), ("losses", "losses"), ("ties", "ties")]
    for r in sf:
        t = teams.get((r["season_year"], r["team_id"]))
        for ef, nf in fields:
            nv = None if t is None else t[nf]
            if nf == "is_champion":
                nv = bool(nv)
            ok = nv == r[ef]
            sf_cells[f"{ef}_{'match' if ok else 'mismatch'}"] += 1
            if not ok:
                sf_mismatch.append({"season": r["season_year"], "team_id": r["team_id"], "field": ef,
                                    "export": r[ef], "neutral": nv})
    report["season_finishes"] = {"cells": dict(sf_cells), "mismatches": sf_mismatch}

    text = json.dumps(report, indent=2, default=str)
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    for k, v in report.items():
        print(k, v["cells"], f"{len(v['mismatches'])} mismatch rows")
    return report


if __name__ == "__main__":
    main()
