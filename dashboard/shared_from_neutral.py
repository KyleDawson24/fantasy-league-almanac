#!/usr/bin/env python3
"""
shared_from_neutral.py -- the shared almanac tables, regenerated from the
neutral layer (MLB-319).

The shared export (output/export_shared.py) reads ESPN-shaped marts, so it
has nothing to say about a season-points league. This rebuilds the same
tables -- the same files, row shapes and printed values, through the
export's OWN shapers -- from one league's neutral extract: the atom, the
team-grain matchups and the dims. Any league with an extract gets them.

Every format has matchups (Kyle, 2026-09-26). In head-to-head a matchup is
a week against one opponent; in a season-points league it is the whole
season against every other team. Standings W-L, all-play, finishes and
rivalries therefore come out of one code path; only points AGAINST (an
opponent-in-a-pairing quantity) is absent for a points league. Points
round where the warehouse rounds them -- once per team per MATCHUP -- so
the dashboard prints what the Sheets almanac prints.

Not carried yet, emitted empty with the reason in the manifest:
acquisition channels (contract ask W5) and club affinities (the club-name
vocabulary is still ESPN-shaped).

Usage:
    python dashboard/shared_from_neutral.py --extract <out>/<stamp>/<league_slug> \
        --out <fresh dir>
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "output"))
import almanac_data  # noqa: E402
import almanac_logic  # noqa: E402
import almanac_render  # noqa: E402
import export_shared as ex  # noqa: E402

ATOM_NAME = {"b_bb": "bb", "b_so": "so", "b_ibb": "ibb"}  # fact name -> atom name
# Colour-rule prose in the words the published demo already uses. "Blank" is
# an ordinary word that is also an exact entry in a real-name reference a
# public edition's gate holds, so the neutral descriptor avoids it. The
# export's own wording (output/export_shared.py) is left as it is.
GATE_NEUTRAL_RULES = {
    "affinities": ("One scale per block (season, all-time), across all cells; bold each MLB club maximum, "
                   "including ties. Zero and empty cells remain unpainted."),
    "rivalries": "Row team win rate against each opponent. The empty diagonal and 0-0 carry no colour.",
}


def champion_records(finishes, league_format, finals=None):
    """One dict per finished season whose champion is known.

    Args:
        finishes: the list built by main()'s finish computation.
        league_format: "h2h" or "points".
        finals: for h2h only, list of dicts with season_year,
                team_points, opponent_points, opponent_abbrev
                for the champion's championship-round matchup.
    Returns:
        list of dicts, one per season that has exactly one champion.
        Raises ValueError if any finished season has != 1 champion.
    """
    from collections import defaultdict as _dd
    by_season = _dd(list)
    for f in finishes:
        by_season[f["season_year"]].append(f)
    finals_by = {}
    for m in (finals or []):
        finals_by[m["season_year"]] = m
    records = []
    for year in sorted(by_season):
        champs = [e for e in by_season[year] if e["is_champion"]]
        finished = any(e.get("final_rank") is not None for e in by_season[year])
        if not champs:
            if finished:
                raise ValueError(f"Season {year}: finished with 0 champions, expected 1")
            continue
        if len(champs) != 1:
            raise ValueError(f"Season {year}: {len(champs)} champions, expected 1")
        c = champs[0]
        rec = {"season_year": year, "team_id": c["team_id"],
               "team_abbrev": c["team_abbrev"], "owner_display": c["owner_display"]}
        if league_format == "h2h":
            rec["seed"] = c["finish"]
            r_parts = [str(c["wins"]), str(c["losses"])]
            if c["ties"]:
                r_parts.append(str(c["ties"]))
            rec["regular_season_record"] = "-".join(r_parts)
            final = finals_by.get(year)
            if final:
                rec["finals_score"] = f"{final['team_points']}-{final['opponent_points']}"
                rec["finals_opponent_abbrev"] = final["opponent_abbrev"]
        else:
            rec["final_standing"] = c["final_rank"]
            rec["points"] = c.get("calculated_points")
        records.append(rec)
    return records


def rows(con, sql):
    cur = con.execute(sql)
    cols = [d[0].lower() for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def write_table(out_dir, name, data, meta):
    """Always JSON: the pages fetch shared tables as JSON."""
    path = out_dir / f"{name}.json"
    path.write_text(ex._dumps({"table": name, "rows": data}), encoding="utf-8")
    import hashlib
    entry = {"file": path.name, "format": "json", "rows": len(data),
             "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    entry.update(meta)
    return entry


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--extract", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    ext, out_dir = Path(a.extract), Path(a.out)
    if out_dir.exists():
        sys.exit(f"{out_dir} exists; never overwritten")
    out_dir.mkdir(parents=True)
    manifest_in = json.loads((ext / "manifest.json").read_text(encoding="utf-8"))
    con = duckdb.connect()
    con.execute(f"CREATE VIEW atom AS SELECT * FROM read_parquet('{(ext / 'atom' / '*.parquet').as_posix()}')")
    for d in (ext / "dims").glob("*.parquet"):
        con.execute(f"CREATE VIEW dim_{d.stem} AS SELECT * FROM read_parquet('{d.as_posix()}')")
    league = rows(con, "SELECT * FROM dim_league")[0]
    points = league["league_format"] == "points"
    current = int(league["last_season"])

    # -- stat specs: scored counting stats the atom carries, in the export's order
    atom_cols = {r["column_name"] for r in rows(con, "SELECT column_name FROM (DESCRIBE atom)")}
    specs = rows(con, """
        SELECT v.leaderboard_name AS stat_name, v.display_name, v.abbrev, v.stat_category,
               r.points_per_unit
        FROM dim_scoring_rule r JOIN dim_stat_vocabulary v ON v.stat_name = r.stat_name
        WHERE r.is_scored AND v.stat_category IN ('hitting', 'pitching') AND v.is_counting""")
    col_of = {}
    for s in specs:
        fact_col = almanac_data._fact_stat_column_name(s["stat_name"])
        col_of[s["stat_name"]] = ATOM_NAME.get(fact_col, fact_col)
    specs = sorted([s for s in specs if col_of[s["stat_name"]] in atom_cols],
                   key=almanac_data._team_week_stat_sort_key)
    hitting = almanac_render._team_week_specs_for_category(specs, "hitting")
    pitching = almanac_render._team_week_specs_for_category(specs, "pitching")

    # -- the matchup key on atom rows: H2H = the period; points = the season (1)
    mkey = "1" if points else "period_key"
    exact = lambda e: f"ROUND(SUM(CAST({e} AS DECIMAL(18, 6))), 1)"
    con.execute(f"""CREATE TEMP TABLE team_matchup_pts AS
        SELECT season_year, {mkey} AS matchup_period, team_id,
               {exact('batting_pts * batting_credited_share')} AS hit,
               {exact('pitching_pts * pitching_credited_share')} AS pit,
               {exact('batting_pts * batting_credited_share + pitching_pts * pitching_credited_share')} AS tot
        FROM atom WHERE phase = 'regular' AND has_game AND team_id IS NOT NULL GROUP BY ALL""")
    stat_sums = ", ".join(
        f"SUM({col_of[s['stat_name']]} * {'batting' if s['stat_category'] == 'hitting' else 'pitching'}_credited_share) "
        f"AS {almanac_data._fact_stat_column_name(s['stat_name'])}" for s in specs)
    con.execute(f"""CREATE TEMP TABLE team_season_stats AS
        SELECT season_year, team_id {(', ' + stat_sums) if stat_sums else ''}
        FROM atom WHERE phase = 'regular' AND has_game AND team_id IS NOT NULL GROUP BY ALL""")
    con.execute("""CREATE TEMP TABLE league_days AS
        SELECT season_year, COUNT(DISTINCT scoring_day) AS days FROM atom
        WHERE phase = 'regular' AND has_game GROUP BY 1""")
    std_days = {r["season_year"]: r["std"] for r in rows(con, """
        SELECT season_year, mode(date_diff('day', start_date, end_date) + 1) AS std
        FROM dim_periods WHERE NOT is_playoff GROUP BY 1""")}
    has_matchups = "dim_matchups" in {r["name"] for r in rows(con, "SELECT table_name AS name FROM information_schema.tables")}

    # -- standings rows per season, in the shape get_team_standings returns
    season_rows = rows(con, f"""
        WITH rec AS (
            SELECT season_year, team_id,
                   COUNT(*) FILTER (WHERE result = 'W') AS wins,
                   COUNT(*) FILTER (WHERE result = 'L') AS losses,
                   COUNT(*) FILTER (WHERE result = 'T') AS ties,
                   COUNT(DISTINCT matchup_period) AS matchup_periods_played
            FROM dim_matchups WHERE NOT is_playoff GROUP BY ALL
        ),
        pts AS (
            SELECT season_year, team_id, SUM(hit) AS hit, SUM(pit) AS pit, SUM(tot) AS tot
            FROM team_matchup_pts GROUP BY ALL
        ),
        against AS (
            SELECT m.season_year, m.team_id, SUM(o.tot) AS against
            FROM dim_matchups m JOIN team_matchup_pts o
              ON o.season_year = m.season_year AND o.matchup_period = m.matchup_period
             AND o.team_id = m.opponent_id
            WHERE NOT m.is_playoff GROUP BY ALL
        )
        SELECT t.season_year, t.team_id, t.team_abbrev, t.team_name, t.owner_display,
               t.playoff_seed, t.standings_rank,
               r.wins, r.losses, r.ties, r.matchup_periods_played,
               d.days AS scoring_days_played,
               CAST(p.hit AS DOUBLE) AS calculated_hitting_pts,
               CAST(p.pit AS DOUBLE) AS calculated_pitching_pts,
               CAST(p.tot AS DOUBLE) AS calculated_points,
               {'NULL' if points else 'CAST(ag.against AS DOUBLE)'} AS against_calculated_points,
               s.* EXCLUDE (season_year, team_id)
        FROM dim_teams t
        JOIN rec r USING (season_year, team_id)
        JOIN league_days d USING (season_year)
        LEFT JOIN pts p USING (season_year, team_id)
        LEFT JOIN team_season_stats s USING (season_year, team_id)
        {'' if points else 'LEFT JOIN against ag USING (season_year, team_id)'}""") if has_matchups else []
    by_season = defaultdict(list)
    for r in season_rows:
        r["standard_matchup_days"] = r["scoring_days_played"] if points else std_days.get(r["season_year"], 7)
        by_season[int(r["season_year"])].append(r)
    for y, rs in by_season.items():
        # H2H: the platform's seed; points: the official finish, else points.
        rs.sort(key=lambda r: ((r["playoff_seed"] if not points else r["standings_rank"]) or 999,
                               -(r["wins"] or 0), -(r["ties"] or 0), -(r["calculated_points"] or 0), r["team_id"]))
    seasons = sorted(by_season)

    alltime = defaultdict(lambda: defaultdict(float))
    labels = {}
    for y in seasons:
        for r in by_season[y]:
            t = alltime[r["team_id"]]
            for k, v in r.items():
                if isinstance(v, (int, float)) and k not in ("season_year", "team_id", "playoff_seed",
                                                              "standings_rank", "standard_matchup_days"):
                    t[k] += v or 0
            t["standard_matchup_days"] = max(t["standard_matchup_days"], r["standard_matchup_days"] or 0)
            labels[r["team_id"]] = r
    alltime_rows = []
    for tid, t in alltime.items():
        lab = labels[tid]
        row = dict(t, team_id=tid, team_abbrev=lab["team_abbrev"], team_name=lab["team_name"],
                   owner_display=lab["owner_display"])
        for k in ("calculated_hitting_pts", "calculated_pitching_pts", "calculated_points"):
            row[k] = round(row[k], 1)
        if points:
            row["against_calculated_points"] = None
        else:
            row["against_calculated_points"] = round(row["against_calculated_points"], 1)
        alltime_rows.append(row)
    alltime_rows.sort(key=lambda r: (-((r["wins"] + 0.5 * r["ties"]) / (r["wins"] + r["losses"] + r["ties"] or 1)),
                                     -r["calculated_points"]))

    tables = {}
    ds = []
    for y in seasons:
        ds.extend(ex.standings_long_rows(str(y), y, by_season[y], hitting, pitching))
    ds.extend(ex.standings_long_rows(ex.ALL_TIME, None, alltime_rows, hitting, pitching))
    if points:  # no pairing, no points against
        ds = [r for r in ds if r["metric_key"] != "AGAINST"]
    tables["detailed_standings"] = write_table(out_dir, "detailed_standings", ds, {
        "grain": "one row per (scope, team_id, metric_key)", "scopes": [str(y) for y in seasons] + [ex.ALL_TIME],
        "matchup": "season-long, against every other team" if points else "weekly pairing",
        "source": ["neutral atom", "dims/matchups", "dims/teams"]})

    # -- points by lineup slot: season-rounded; all-time sums the seasons
    slot_rows = rows(con, f"""
        SELECT a.season_year, a.team_id, a.platform_lineup_slot AS lineup_slot,
               CAST({exact('batting_pts * batting_credited_share + pitching_pts * pitching_credited_share')} AS DOUBLE) AS slot_pts,
               MIN(s.sort_order) AS sort_order,
               BOOL_OR(a.slot_provenance = 'estimated') AS is_estimate
        FROM atom a JOIN dim_lineup_slot s
          ON s.season_year = a.season_year AND s.lineup_slot = a.platform_lineup_slot
        WHERE a.phase = 'regular' AND a.has_game AND a.deployment_state = 'active' AND s.is_starting_slot
        GROUP BY ALL""")
    slots_out = []
    est = {}
    for y in seasons:
        srows = [r for r in slot_rows if int(r["season_year"]) == y]
        mp = {r["team_id"]: r["matchup_periods_played"] for r in by_season[y]}
        lab = {r["team_id"]: r for r in by_season[y]}
        for r in srows:
            est[(str(y), r["team_id"], r["lineup_slot"])] = r["is_estimate"]
        slots_out.extend(ex.slot_long_rows(str(y), y, srows, mp, lab))
    at = defaultdict(float)
    at_sort = {}
    for r in slot_rows:
        at[(r["team_id"], r["lineup_slot"])] += r["slot_pts"]
        at_sort[r["lineup_slot"]] = r["sort_order"]
        est[(ex.ALL_TIME, r["team_id"], r["lineup_slot"])] = est.get((ex.ALL_TIME, r["team_id"], r["lineup_slot"])) or r["is_estimate"]
    at_rows = [{"team_id": t, "lineup_slot": s, "slot_pts": round(v, 1), "sort_order": at_sort[s]} for (t, s), v in at.items()]
    mp_at = {r["team_id"]: r["matchup_periods_played"] for r in alltime_rows}
    slots_out.extend(ex.slot_long_rows(ex.ALL_TIME, None, at_rows, mp_at, {r["team_id"]: r for r in alltime_rows}))
    for r in slots_out:  # provenance travels with the value (pre-2026 CBS slots are estimates)
        r["is_estimate"] = bool(est.get((r["scope"], r["team_id"], r["lineup_slot"])))
    tables["points_by_lineup_slot"] = write_table(out_dir, "points_by_lineup_slot", slots_out, {
        "grain": "one row per (scope, team_id, lineup_slot); active slots, regular season",
        "note": "is_estimate marks slots whose rows are not observed lineups (label est.)",
        "source": ["neutral atom"]})

    # -- finishes: H2H follows the bracket, points follow the season points
    teams = rows(con, "SELECT * FROM dim_teams")
    team_by = {(int(t["season_year"]), t["team_id"]): t for t in teams}
    complete = {int(r["season_year"]): bool(r["done"]) for r in rows(con, """
        SELECT season_year, BOOL_AND(is_complete) AS done FROM dim_matchups GROUP BY 1""")} if has_matchups else {}
    # A season is closed once the neutral layer says it is over
    # (dims/seasons.is_season_complete: the last scheduled matchup ended, or
    # a points league's last scoring period was delivered) and its matchups
    # are all complete. Without that fact (an older extract), the current
    # season of a head-to-head league is never closed: between bracket rounds
    # every matchup played so far can be complete while the final is not.
    season_cols = {r["column_name"] for r in rows(con, "SELECT column_name FROM (DESCRIBE dim_seasons)")}
    season_done = ({int(r["season_year"]): bool(r["is_season_complete"]) for r in rows(
        con, "SELECT season_year, is_season_complete FROM dim_seasons")}
        if "is_season_complete" in season_cols else None)
    finishes = []
    for y in seasons:
        for r in by_season[y]:
            t = team_by.get((y, r["team_id"]), {})
            if season_done is not None:
                closed = season_done.get(y, False) and complete.get(y, False)
            else:
                closed = complete.get(y, False) and y != current or (complete.get(y, False) and points)
            if points:
                finish = r["standings_rank"] or (1 + sum(1 for o in by_season[y] if (o["calculated_points"] or 0) > (r["calculated_points"] or 0)))
                final_rank = finish if closed else None
                champion = bool(closed and finish == 1)
            else:
                finish = r["playoff_seed"]
                final_rank = t.get("final_rank") if closed else None
                champion = bool(closed and t.get("is_champion"))
            finishes.append({"season_year": y, "team_id": r["team_id"], "team_abbrev": r["team_abbrev"],
                             "owner_display": r["owner_display"], "wins": r["wins"], "losses": r["losses"],
                             "ties": r["ties"], "finish": finish, "final_rank": final_rank,
                             "is_champion": champion,
                             "calculated_points": r.get("calculated_points")})
    tables["season_finishes"] = write_table(out_dir, "season_finishes", ex.finish_rows(finishes, current), {
        "grain": "one row per (season_year, team_id)",
        "note": ("finish = official season finish; champion = first on points, closed seasons only"
                 if points else "finish = platform seed; champion = the platform's bracket result, closed seasons only"),
        "source": ["dims/teams", "dims/matchups"]})

    # -- champion records: one per closed season, format-aware
    finals_rows = []
    if not points and has_matchups:
        finals_rows = rows(con, """
            WITH champ AS (
                SELECT season_year, team_id FROM dim_teams WHERE is_champion
            ),
            last_round AS (
                SELECT season_year, MAX(matchup_period) AS period
                FROM dim_matchups WHERE is_playoff AND is_complete GROUP BY 1
            )
            SELECT m.season_year, m.team_points, m.opponent_points,
                   t.team_abbrev AS opponent_abbrev
            FROM dim_matchups m
            JOIN champ c ON c.season_year = m.season_year AND c.team_id = m.team_id
            JOIN last_round lr ON lr.season_year = m.season_year
                              AND lr.period = m.matchup_period
            JOIN dim_teams t ON t.team_id = m.opponent_id
                            AND t.season_year = m.season_year""")
    fmt = "points" if points else "h2h"
    champ_recs = champion_records(finishes, fmt, finals_rows or None)
    tables["champion"] = write_table(out_dir, "champion", champ_recs, {
        "grain": "one row per closed season with a known champion",
        "note": ("H2H: seed, regular-season record, finals result; "
                 "points: final standing, total points" if not points
                 else "Points league: final standing, total points"),
        "source": ["season_finishes", "dims/teams", "dims/matchups"]})

    if points and "dim_period_standings" in {r["name"] for r in rows(con, "SELECT table_name AS name FROM information_schema.tables")}:
        rank_arc = rows(con, f"""SELECT CAST(team_id AS INTEGER) AS team_id, CAST(period AS INTEGER) AS period,
            CAST(standings_rank AS INTEGER) AS standings_rank FROM dim_period_standings
            WHERE CAST(season_year AS INTEGER) = {current}""")
    elif has_matchups:
        rank_arc = rows(con, f"""
            WITH w AS (SELECT m.team_id, m.matchup_period,
                              CASE m.result WHEN 'W' THEN 1 ELSE 0 END AS w,
                              CASE m.result WHEN 'T' THEN 1 ELSE 0 END AS t,
                              COALESCE(p.tot, 0) AS pts
                       FROM dim_matchups m LEFT JOIN team_matchup_pts p
                         ON p.season_year = m.season_year AND p.matchup_period = m.matchup_period
                        AND p.team_id = m.team_id
                       WHERE m.season_year = {current} AND NOT m.is_playoff),
                 c AS (SELECT team_id, matchup_period,
                              SUM(w) OVER (PARTITION BY team_id ORDER BY matchup_period) AS cw,
                              SUM(t) OVER (PARTITION BY team_id ORDER BY matchup_period) AS ct,
                              SUM(pts) OVER (PARTITION BY team_id ORDER BY matchup_period) AS cp FROM w)
            SELECT team_id, matchup_period AS period,
                   ROW_NUMBER() OVER (PARTITION BY matchup_period ORDER BY cw DESC, ct DESC, cp DESC, team_id) AS standings_rank
            FROM c""")
    else:
        rank_arc = []
    tables["season_finish_summary"] = write_table(
        out_dir, "season_finish_summary",
        ex.finish_summary_rows(finishes, by_season.get(current, []), current, rank_arc),
        {"grain": "one row per team_id (current standings teams)", "source": ["season_finishes"]})

    # -- all-play: each team's points against every other team's, per matchup
    all_play = rows(con, """
        WITH p AS (SELECT DISTINCT season_year, matchup_period, team_id, team_points
                   FROM dim_matchups WHERE NOT is_playoff AND is_complete)
        SELECT a.season_year, a.matchup_period, a.team_id, t.team_name, t.team_abbrev,
               a.team_points AS platform_points,
               COUNT(*) FILTER (WHERE b.team_points < a.team_points) AS all_play_wins,
               COUNT(*) FILTER (WHERE b.team_points > a.team_points) AS all_play_losses,
               COUNT(*) FILTER (WHERE b.team_points = a.team_points) AS all_play_ties,
               COUNT(*) AS opponent_count
        FROM p a JOIN p b ON b.season_year = a.season_year AND b.matchup_period = a.matchup_period
                         AND b.team_id <> a.team_id
        LEFT JOIN dim_teams t ON t.season_year = a.season_year AND t.team_id = a.team_id
        GROUP BY ALL""") if has_matchups else []
    for r in all_play:
        n = r["opponent_count"] or 1
        r.update(expected_win_share=r["all_play_wins"] / n, expected_loss_share=r["all_play_losses"] / n,
                 expected_tie_share=r["all_play_ties"] / n)
    tables["all_play_summary"] = write_table(out_dir, "all_play_summary", ex.all_play_summary_rows(all_play), {
        "grain": "one row per (scope, team_id)",
        "note": "a season-points matchup is the season: all-play equals the season record" if points else None,
        "source": ["dims/matchups"]})

    # -- rivalries: the franchise-identity ledgers, as published
    axes = rows(con, "SELECT * FROM dim_rivalry_axes") if "dim_rivalry_axes" in {r["name"] for r in rows(con, "SELECT table_name AS name FROM information_schema.tables")} else []
    pairs = rows(con, "SELECT * FROM dim_rivalries") if axes else []
    pair_rows = []
    for p in pairs:
        row = {k: ex._num(v) for k, v in p.items()}
        row["matchup_record_printed"] = almanac_logic.format_rivalry_record(
            ex._int(p["matchup_wins"]) or 0, ex._int(p["matchup_losses"]) or 0, ex._int(p["matchup_ties"]) or 0)
        row["season_record_printed"] = almanac_logic.format_rivalry_record(
            ex._int(p["season_wins"]) or 0, ex._int(p["season_losses"]) or 0, ex._int(p["season_ties"]) or 0)
        pair_rows.append(row)
    pair_rows.sort(key=lambda r: (r["row_identity_key"], r["opponent_identity_key"]))
    grid = almanac_logic.rivalry_matrix_grid(axes, pairs) if axes else None
    tables["rivalry_axes"] = write_table(out_dir, "rivalry_axes", [{k: ex._num(v) for k, v in r.items()} for r in axes],
                                         {"grain": "one row per active team identity", "source": ["dims/rivalry_axes"]})
    tables["rivalries"] = write_table(out_dir, "rivalries", pair_rows, {
        "grain": "one row per ordered pair of team identities with a result",
        "ledger": grid["ledger"] if grid else None, "available": bool(grid and grid.get("available")),
        "explainer": grid.get("explainer") if grid else None, "source": ["dims/rivalries"]})

    # -- teams: the identity spine
    ident = {}
    for r in rows(con, f"SELECT * FROM dim_franchise_identity WHERE CAST(season_year AS INTEGER) = {current}") \
            if "dim_franchise_identity" in {r["name"] for r in rows(con, "SELECT table_name AS name FROM information_schema.tables")} else []:
        tail = str(r.get("franchise_id") or "").rsplit(":", 1)[-1].rsplit("-", 1)[-1]
        if tail.isdigit():
            ident.setdefault(int(tail), []).append(r)
    seen = {}
    for y in seasons:
        for t in by_season[y]:
            e = seen.setdefault(t["team_id"], {"team_id": t["team_id"], "seasons": []})
            e["seasons"].append(y)
            e.update(team_abbrev=t["team_abbrev"] or "", team_name=t["team_name"] or "",
                     owner_display=t["owner_display"] or "")
    for tid, e in seen.items():
        rs = ident.get(tid, [])
        e["identity_key"] = rs[0].get("identity_key") if len(rs) == 1 else None
        e["is_current"] = current in e["seasons"]
    tables["teams"] = write_table(out_dir, "teams", sorted(seen.values(), key=lambda t: t["team_id"]),
                                  {"grain": "one row per platform team_id", "source": ["dims/teams", "dims/franchise_identity"]})

    for name, why in (("acquisition_channels", "needs acquisition_channel on the atom (contract ask W5)"),
                      ("affinities", "club-name vocabulary is still ESPN-shaped")):
        tables[name] = write_table(out_dir, name, [], {"not_available": why})

    presentation = ex.presentation_descriptor(hitting, pitching)
    for key, rule in GATE_NEUTRAL_RULES.items():
        if key in presentation.get("tables", {}):
            presentation["tables"][key]["rule"] = rule
    (out_dir / "presentation.json").write_text(ex._dumps(presentation), encoding="utf-8")
    manifest = {
        "schema_version": ex.SCHEMA_VERSION, "ticket": "MLB-319",
        "league_key": league["league_key"], "league_slug": league["league_key"].replace("-", "_"),
        "league_format": league["league_format"],
        "snapshot_ts": manifest_in["generated_at"], "current_season": current, "seasons": seasons,
        "performance_cutoff": str(league["data_horizon"]),
        # Team labels come from the extract's dims, so they are as of its horizon.
        "club_identity_as_of": str(league["data_horizon"]),
        # The extract's identity, never its local path: this manifest ships
        # inside a published site. An edition's label projection is pinned
        # by hash (the map itself stays private).
        "source": {"extract_generated_at": manifest_in["generated_at"],
                   "extract_commit": manifest_in["source"]["commit"],
                   "extract_dirty": manifest_in["source"].get("dirty"),
                   **({"edition_labels": {k: manifest_in["edition_labels"][k]
                                          for k in ("label_map_sha256", "player_names")
                                          if k in manifest_in["edition_labels"]}}
                      if manifest_in.get("edition_labels") else {})},
        "tables": tables, "presentation": {"file": "presentation.json"},
    }
    (out_dir / "manifest.json").write_text(ex._dumps(manifest), encoding="utf-8")
    print(json.dumps({k: v["rows"] for k, v in tables.items()}))


if __name__ == "__main__":
    main()
