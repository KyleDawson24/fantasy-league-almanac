#!/usr/bin/env python3
"""
publish_extract.py -- Publish dashboard Parquet extracts from a DuckDB warehouse.

Reads fct_player_daily_performance (the DEMO variant) and supporting dims,
writes per-league Parquet files shaped to the master-table contract (v0.3)
into docs/dashboard/data/<league_slug>/.

Usage:
    python dashboard/publish_extract.py [--db path/to/ESPN_FANTASY.duckdb]
                                        [--out docs/dashboard]

The default --db is data/duckdb/demo/ESPN_FANTASY.duckdb.
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb


GENERATED_AT = datetime.now(timezone.utc).isoformat()

# Starting slots per platform, used to filter eligible_slots.
# Populated at runtime from dim_lineup_slot.
STARTING_SLOTS_BY_PLATFORM = {}

# ---------------------------------------------------------------------------
# Column contract (Appendix A ordering)
# ---------------------------------------------------------------------------

SEASON_ATOM_COLUMNS = """
    -- 3A identity
    f.league_key,
    CASE
        WHEN f.league_key LIKE 'espn%' THEN 'espn'
        WHEN f.league_key LIKE 'cbs%' THEN 'cbs'
        ELSE 'unknown'
    END AS platform,
    CAST(f.season_year AS INTEGER) AS season_year,
    f.game_date AS scoring_day,
    f.player_key,
    CAST(f.player_id AS VARCHAR) AS platform_player_id,
    NULL::INTEGER AS mlbam_id,
    NULL::VARCHAR AS person_key,
    f.display_name,

    -- 3B time/completeness
    NULL::INTEGER AS scoring_day_ordinal,
    CAST(f.matchup_period AS INTEGER) AS matchup_period_ordinal,
    NULL::VARCHAR AS week_bin_key,
    CAST(YEAR(f.game_date) * 100 + MONTH(f.game_date) AS INTEGER) AS month_bin_key,
    NULL::VARCHAR AS phase,
    NULL::VARCHAR AS day_state,
    COALESCE((
        SELECT mp.end_date < CURRENT_DATE
        FROM ANALYTICS.DIM_MATCHUP_PERIOD mp
        WHERE mp.league_key = f.league_key
          AND CAST(mp.season_year AS INTEGER) = CAST(f.season_year AS INTEGER)
          AND f.game_date BETWEEN mp.start_date AND mp.end_date
        LIMIT 1
    ), FALSE) AS is_complete,
    NULL::VARCHAR AS matchup_id,

    -- 3C rostering/deployment
    CAST(f.team_id AS INTEGER) AS team_id,
    CAST(f.team_id AS VARCHAR) AS franchise_id,
    CASE
        WHEN f.team_id IS NULL OR f.wasted_bucket = 'FA' THEN 'free_agent'
        WHEN f.provenance LIKE 'estimated%' THEN 'estimated'
        WHEN f.lineup_slot IN ('IL') THEN 'il'
        WHEN f.lineup_slot IN ('BE', 'RS') THEN 'benched'
        WHEN f.is_active_slot THEN 'active'
        ELSE 'benched'
    END AS deployment_state,
    f.lineup_slot,
    f.lineup_slot AS platform_lineup_slot,
    f.lineup_slot_category AS slot_category,
    f.active_weight AS active_share,
    NULL::FLOAT AS batting_credited_share,
    NULL::FLOAT AS pitching_credited_share,
    CASE
        WHEN f.lineup_slot IN ('BE', 'RS') THEN 'benched'
        WHEN f.lineup_slot = 'IL' THEN 'il'
        ELSE NULL
    END AS wasted_reason,
    NULL::VARCHAR AS acquisition_channel,
    f.provenance,
    TRUE AS has_game,

    -- 3D eligibility/position (filter to starting-slot keys only)
    f.eligible_slots AS eligible_slots,
    f.position AS primary_position,
    NULL::VARCHAR[] AS irl_positions_played,
    f.mlb_team_id,

    -- 3E batting components
    NULL::DOUBLE AS batting_games,
    f.ab,
    f.h,
    f.singles,
    f.doubles,
    f.triples,
    f.hr,
    f.xbh,
    f.tb,
    f.r,
    f.rbi,
    f.b_bb AS bb,
    f.b_ibb AS ibb,
    f.b_so AS so,
    f.hbp,
    f.sf,
    f.sb,
    f.cs,
    f.gdp,
    f.cyc,
    NULL::DOUBLE AS sh,
    NULL::DOUBLE AS pa,

    -- 3F pitching components
    NULL::DOUBLE AS pitching_games,
    NULL::DOUBLE AS pitching_starts,
    f.outs,
    f.w,
    f.l,
    f.k,
    f.er,
    f.qs,
    f.sv,
    f.hld,
    f.blsv,
    f.p_h,
    f.p_bb,
    f.p_hr,
    f.p_r,
    f.cg,
    f.sho,
    f.nh,
    f.pg,
    f.blk,
    f.wp,
    f.hbp_p,
    f.pk,
    NULL::DOUBLE AS bf,
    NULL::DOUBLE AS gf,

    -- 3F.1 fielding (all reserved NULL)
    NULL::DOUBLE AS fielding_games,
    NULL::DOUBLE AS po,
    NULL::DOUBLE AS a,
    NULL::DOUBLE AS e,
    NULL::DOUBLE AS dp,
    NULL::DOUBLE AS pb,
    NULL::DOUBLE AS sb_allowed,
    NULL::DOUBLE AS cs_caught,

    -- 3G league-scored value
    f.total_hitting_stat_pts AS batting_pts,
    f.total_pitching_stat_pts AS pitching_pts,
    f.total_stat_pts AS total_pts,
    f.negative_points AS negative_pts,
    f.platform_points AS platform_pts,
    f.platform_hitting_pts AS platform_batting_pts,
    f.platform_pitching_pts AS platform_pitching_pts,
    '2026' AS scoring_rule_version,

    -- raw_ columns (unmapped source columns)
    CAST(f.scoring_period AS INTEGER) AS raw_scoring_period,
    f.team_name AS raw_team_name,
    f.team_abbrev AS raw_team_abbrev,
    f.owner_name AS raw_owner_name,
    f.player_name AS raw_player_name,
    f.pro_team AS raw_pro_team,
    f.is_active_slot AS raw_is_active_slot,
    f.performance_status AS raw_performance_status,
    f.wasted_bucket AS raw_wasted_bucket,
    CAST(f.games_played AS INTEGER) AS raw_games_played,
    f.owner_display AS raw_owner_display,
    f.h_pts AS raw_h_pts,
    f.ab_pts AS raw_ab_pts,
    f.b_bb_pts AS raw_bb_pts,
    f.b_so_pts AS raw_so_pts,
    f.hbp_pts AS raw_hbp_pts,
    f.sf_pts AS raw_sf_pts,
    f.hr_pts AS raw_hr_pts,
    f.r_pts AS raw_r_pts,
    f.rbi_pts AS raw_rbi_pts,
    f.sb_pts AS raw_sb_pts,
    f.cs_pts AS raw_cs_pts,
    f.tb_pts AS raw_tb_pts,
    f.singles_pts AS raw_singles_pts,
    f.doubles_pts AS raw_doubles_pts,
    f.triples_pts AS raw_triples_pts,
    f.xbh_pts AS raw_xbh_pts,
    f.gdp_pts AS raw_gdp_pts,
    f.b_ibb_pts AS raw_ibb_pts,
    f.cyc_pts AS raw_cyc_pts,
    f.w_pts AS raw_w_pts,
    f.l_pts AS raw_l_pts,
    f.k_pts AS raw_k_pts,
    f.er_pts AS raw_er_pts,
    f.outs_pts AS raw_outs_pts,
    f.qs_pts AS raw_qs_pts,
    f.sv_pts AS raw_sv_pts,
    f.hld_pts AS raw_hld_pts,
    f.p_h_pts AS raw_p_h_pts,
    f.p_bb_pts AS raw_p_bb_pts,
    f.p_hr_pts AS raw_p_hr_pts,
    f.p_r_pts AS raw_p_r_pts,
    f.cg_pts AS raw_cg_pts,
    f.blk_pts AS raw_blk_pts,
    f.wp_pts AS raw_wp_pts,
    f.hbp_p_pts AS raw_hbp_p_pts,
    f.blsv_pts AS raw_blsv_pts,
    f.nh_pts AS raw_nh_pts,
    f.pg_pts AS raw_pg_pts,
    f.pk_pts AS raw_pk_pts,
    f.sho_pts AS raw_sho_pts
"""


def slug_for_league(league_key: str) -> str:
    return league_key.replace("-", "_")


def assert_uniqueness(con, league_key: str, season_year: int):
    """Assert one row per (league_key, scoring_day, player_key)."""
    dupes = con.execute("""
        SELECT game_date, player_key, COUNT(*) AS n
        FROM ANALYTICS.FCT_PLAYER_DAILY_PERFORMANCE
        WHERE league_key = ? AND season_year = ?
        GROUP BY game_date, player_key
        HAVING COUNT(*) > 1
        LIMIT 5
    """, [league_key, season_year]).fetchall()
    if dupes:
        print(f"UNIQUENESS VIOLATION in {league_key}/{season_year}:", file=sys.stderr)
        for d in dupes:
            print(f"  {d[0]} / {d[1]} = {d[2]} rows", file=sys.stderr)
        sys.exit(1)


def write_season_parquet(con, league_key: str, season_year: int, out_path: Path):
    """Write one season atom Parquet file via COPY TO."""
    assert_uniqueness(con, league_key, season_year)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_file = str(out_path).replace("\\", "/")
    con.execute(f"""
        COPY (
            SELECT {SEASON_ATOM_COLUMNS}
            FROM ANALYTICS.FCT_PLAYER_DAILY_PERFORMANCE f
            WHERE f.league_key = ? AND CAST(f.season_year AS INTEGER) = ?
            ORDER BY f.game_date, f.player_key
        ) TO '{out_file}'
        (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 50000)
    """, [league_key, season_year])
    row_count = con.execute("""
        SELECT COUNT(*) FROM ANALYTICS.FCT_PLAYER_DAILY_PERFORMANCE
        WHERE league_key = ? AND CAST(season_year AS INTEGER) = ?
    """, [league_key, season_year]).fetchone()[0]
    return row_count


def write_rollup_parquet(con, league_key: str, out_path: Path):
    """Write the season rollup: one row per player x team x season."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_file = str(out_path).replace("\\", "/")
    con.execute(f"""
        COPY (
            SELECT
                f.league_key,
                CASE WHEN f.league_key LIKE 'espn%' THEN 'espn'
                     WHEN f.league_key LIKE 'cbs%' THEN 'cbs'
                     ELSE 'unknown' END AS platform,
                CAST(f.season_year AS INTEGER) AS season_year,
                f.player_key,
                CAST(f.player_id AS VARCHAR) AS platform_player_id,
                f.display_name,
                CAST(f.team_id AS INTEGER) AS team_id,
                f.position AS primary_position,
                f.mlb_team_id,
                MIN(f.game_date) AS first_scoring_day,
                MAX(f.game_date) AS last_scoring_day,
                COUNT(*) AS day_count,

                -- Total sums (all rows)
                SUM(f.total_hitting_stat_pts) AS total_batting_pts,
                SUM(f.total_pitching_stat_pts) AS total_pitching_pts,
                SUM(f.total_stat_pts) AS total_pts,

                -- Active sums (weighted by active_share)
                SUM(f.total_hitting_stat_pts * f.active_weight) AS active_batting_pts,
                SUM(f.total_pitching_stat_pts * f.active_weight) AS active_pitching_pts,
                SUM(f.total_stat_pts * f.active_weight) AS active_pts,

                -- Wasted = total - active
                SUM(f.total_stat_pts) - SUM(f.total_stat_pts * f.active_weight) AS wasted_pts,

                -- 3E batting component sums
                SUM(f.ab) AS ab,
                SUM(f.h) AS h,
                SUM(f.singles) AS singles,
                SUM(f.doubles) AS doubles,
                SUM(f.triples) AS triples,
                SUM(f.hr) AS hr,
                SUM(f.xbh) AS xbh,
                SUM(f.tb) AS tb,
                SUM(f.r) AS r,
                SUM(f.rbi) AS rbi,
                SUM(f.b_bb) AS bb,
                SUM(f.b_ibb) AS ibb,
                SUM(f.b_so) AS so,
                SUM(f.hbp) AS hbp,
                SUM(f.sf) AS sf,
                SUM(f.sb) AS sb,
                SUM(f.cs) AS cs,
                SUM(f.gdp) AS gdp,
                SUM(f.cyc) AS cyc,

                -- 3F pitching component sums
                SUM(f.outs) AS outs,
                SUM(f.w) AS w,
                SUM(f.l) AS l,
                SUM(f.k) AS k,
                SUM(f.er) AS er,
                SUM(f.qs) AS qs,
                SUM(f.sv) AS sv,
                SUM(f.hld) AS hld,
                SUM(f.blsv) AS blsv,
                SUM(f.p_h) AS p_h,
                SUM(f.p_bb) AS p_bb,
                SUM(f.p_hr) AS p_hr,
                SUM(f.p_r) AS p_r,
                SUM(f.cg) AS cg,
                SUM(f.sho) AS sho,
                SUM(f.nh) AS nh,
                SUM(f.pg) AS pg,
                SUM(f.blk) AS blk,
                SUM(f.wp) AS wp,
                SUM(f.hbp_p) AS hbp_p,
                SUM(f.pk) AS pk,

                SUM(f.negative_points) AS negative_pts,
                SUM(f.platform_points) AS platform_pts,
                SUM(f.platform_hitting_pts) AS platform_batting_pts,
                SUM(f.platform_pitching_pts) AS platform_pitching_pts

            FROM ANALYTICS.FCT_PLAYER_DAILY_PERFORMANCE f
            WHERE f.league_key = ?
            GROUP BY f.league_key, f.season_year, f.player_key,
                     f.player_id, f.display_name, f.team_id,
                     f.position, f.mlb_team_id
            ORDER BY CAST(f.season_year AS INTEGER), f.player_key
        ) TO '{out_file}'
        (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 50000)
    """, [league_key])

    row_count = con.execute("""
        SELECT COUNT(*) FROM (
            SELECT 1 FROM ANALYTICS.FCT_PLAYER_DAILY_PERFORMANCE
            WHERE league_key = ?
            GROUP BY season_year, player_key, player_id, display_name,
                     team_id, position, mlb_team_id
        )
    """, [league_key]).fetchone()[0]
    return row_count


def write_dims(con, league_key: str, dims_dir: Path):
    """Write dimension Parquet files."""
    dims_dir.mkdir(parents=True, exist_ok=True)
    manifest_entries = []

    # league.parquet
    league_file = str(dims_dir / "league.parquet").replace("\\", "/")
    con.execute(f"""
        COPY (
            SELECT DISTINCT
                f.league_key,
                CASE WHEN f.league_key LIKE 'espn%' THEN 'espn'
                     WHEN f.league_key LIKE 'cbs%' THEN 'cbs'
                     ELSE 'unknown' END AS platform,
                MIN(CAST(f.season_year AS INTEGER)) AS first_season,
                MAX(CAST(f.season_year AS INTEGER)) AS last_season,
                COUNT(DISTINCT CAST(f.season_year AS INTEGER)) AS season_count
            FROM ANALYTICS.FCT_PLAYER_DAILY_PERFORMANCE f
            WHERE f.league_key = ?
            GROUP BY f.league_key
        ) TO '{league_file}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
    """, [league_key])
    manifest_entries.append(("dims/league.parquet", 1))

    # franchise.parquet
    franchise_file = str(dims_dir / "franchise.parquet").replace("\\", "/")
    con.execute(f"""
        COPY (
            SELECT * FROM ANALYTICS.DIM_FRANCHISE
            WHERE league_key = ?
            ORDER BY franchise_id
        ) TO '{franchise_file}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
    """, [league_key])
    fc = con.execute("SELECT COUNT(*) FROM ANALYTICS.DIM_FRANCHISE WHERE league_key = ?",
                     [league_key]).fetchone()[0]
    manifest_entries.append(("dims/franchise.parquet", fc))

    # lineup_slot.parquet (no league filter -- carries no names)
    platform = "espn" if "espn" in league_key else "cbs"
    slot_file = str(dims_dir / "lineup_slot.parquet").replace("\\", "/")
    con.execute(f"""
        COPY (
            SELECT * FROM ANALYTICS.DIM_LINEUP_SLOT
            WHERE platform = ?
            ORDER BY sort_order NULLS LAST
        ) TO '{slot_file}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
    """, [platform])
    sc = con.execute("SELECT COUNT(*) FROM ANALYTICS.DIM_LINEUP_SLOT WHERE platform = ?",
                     [platform]).fetchone()[0]
    manifest_entries.append(("dims/lineup_slot.parquet", sc))

    # scoring_rule.parquet (no names)
    rule_file = str(dims_dir / "scoring_rule.parquet").replace("\\", "/")
    con.execute(f"""
        COPY (
            SELECT * FROM ANALYTICS.DIM_LEAGUE_SCORING_RULE
            WHERE league_key = ?
            ORDER BY stat_name
        ) TO '{rule_file}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
    """, [league_key])
    rc = con.execute("SELECT COUNT(*) FROM ANALYTICS.DIM_LEAGUE_SCORING_RULE WHERE league_key = ?",
                     [league_key]).fetchone()[0]
    manifest_entries.append(("dims/scoring_rule.parquet", rc))

    # calendar.parquet — two paths: leagues with matchup-period dates (ESPN)
    # vs leagues without (CBS has NULL start_date/end_date)
    calendar_file = str(dims_dir / "calendar.parquet").replace("\\", "/")
    has_mp_dates = con.execute("""
        SELECT COUNT(*) FROM ANALYTICS.DIM_MATCHUP_PERIOD
        WHERE league_key = ? AND start_date IS NOT NULL
    """, [league_key]).fetchone()[0] > 0

    if has_mp_dates:
        con.execute(f"""
            COPY (
                SELECT
                    mp.league_key,
                    CAST(mp.season_year AS INTEGER) AS season_year,
                    sd.game_date AS scoring_day,
                    mp.matchup_period AS matchup_period_ordinal,
                    mp.start_date AS matchup_start_date,
                    mp.end_date AS matchup_end_date,
                    mp.is_playoff,
                    CASE
                        WHEN mp.end_date < CURRENT_DATE THEN TRUE
                        ELSE FALSE
                    END AS is_complete
                FROM ANALYTICS.DIM_MATCHUP_PERIOD mp
                CROSS JOIN (
                    SELECT DISTINCT game_date
                    FROM ANALYTICS.FCT_PLAYER_DAILY_PERFORMANCE
                    WHERE league_key = ?
                ) sd
                WHERE mp.league_key = ?
                  AND sd.game_date BETWEEN mp.start_date AND mp.end_date
                ORDER BY sd.game_date
            ) TO '{calendar_file}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
        """, [league_key, league_key])
    else:
        con.execute(f"""
            COPY (
                SELECT
                    f.league_key,
                    CAST(f.season_year AS INTEGER) AS season_year,
                    f.game_date AS scoring_day,
                    CAST(f.matchup_period AS INTEGER) AS matchup_period_ordinal,
                    NULL::DATE AS matchup_start_date,
                    NULL::DATE AS matchup_end_date,
                    FALSE AS is_playoff,
                    CASE
                        WHEN f.game_date < CURRENT_DATE - INTERVAL '7' DAY THEN TRUE
                        ELSE FALSE
                    END AS is_complete
                FROM (
                    SELECT DISTINCT league_key, season_year, game_date, matchup_period
                    FROM ANALYTICS.FCT_PLAYER_DAILY_PERFORMANCE
                    WHERE league_key = ?
                ) f
                ORDER BY f.game_date
            ) TO '{calendar_file}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
        """, [league_key])

    cc = con.execute(f"SELECT COUNT(*) FROM read_parquet('{calendar_file}')").fetchone()[0]
    manifest_entries.append(("dims/calendar.parquet", cc))

    return manifest_entries


def write_fallback(con, league_key: str, out_dir: Path):
    """Write pre-baked fallback JSON for the default All-League Team view."""
    fallback_dir = out_dir / "fallback"
    fallback_dir.mkdir(parents=True, exist_ok=True)

    latest_season = con.execute("""
        SELECT MAX(CAST(season_year AS INTEGER))
        FROM ANALYTICS.FCT_PLAYER_DAILY_PERFORMANCE
        WHERE league_key = ?
    """, [league_key]).fetchone()[0]

    rows = con.execute("""
        SELECT
            f.player_key,
            f.display_name,
            f.position AS primary_position,
            f.lineup_slot,
            SUM(f.total_stat_pts * f.active_weight) AS active_pts,
            SUM(f.total_stat_pts) AS total_pts,
            SUM(f.total_stat_pts) - SUM(f.total_stat_pts * f.active_weight) AS wasted_pts,
            COUNT(*) AS day_count
        FROM ANALYTICS.FCT_PLAYER_DAILY_PERFORMANCE f
        WHERE f.league_key = ? AND CAST(f.season_year AS INTEGER) = ?
        GROUP BY f.player_key, f.display_name, f.position, f.lineup_slot
        ORDER BY active_pts DESC
        LIMIT 200
    """, [league_key, latest_season]).fetchall()

    fallback = {
        "league_key": league_key,
        "season_year": latest_season,
        "measure": "active",
        "generated_at": GENERATED_AT,
        "players": [
            {
                "player_key": r[0],
                "display_name": r[1],
                "primary_position": r[2],
                "lineup_slot": r[3],
                "active_pts": round(r[4], 2) if r[4] else 0,
                "total_pts": round(r[5], 2) if r[5] else 0,
                "wasted_pts": round(r[6], 2) if r[6] else 0,
                "day_count": r[7],
            }
            for r in rows
        ],
    }

    out_file = fallback_dir / "all_league_default.json"
    with open(out_file, "w") as fp:
        json.dump(fallback, fp, indent=2)
    return latest_season


def build_manifest(league_key: str, league_dir: Path, season_info: list, rollup_rows: int,
                   dim_entries: list, fallback_season: int):
    """Build and write manifest.json."""
    slug = slug_for_league(league_key)
    platform = "espn" if "espn" in league_key else "cbs"
    entries = []

    for season_year, row_count, parquet_path in season_info:
        fsize = os.path.getsize(parquet_path)
        con_temp = duckdb.connect()
        col_count = len(con_temp.execute(
            f"SELECT * FROM read_parquet('{str(parquet_path).replace(chr(92), '/')}') LIMIT 0"
        ).description)
        min_max = con_temp.execute(f"""
            SELECT MIN(scoring_day), MAX(scoring_day)
            FROM read_parquet('{str(parquet_path).replace(chr(92), '/')}')
        """).fetchone()
        con_temp.close()
        entries.append({
            "path": f"data/{slug}/{season_year}.parquet",
            "rows": row_count,
            "bytes": fsize,
            "season": season_year,
            "min_scoring_day": str(min_max[0]) if min_max[0] else None,
            "max_scoring_day": str(min_max[1]) if min_max[1] else None,
            "column_count": col_count,
        })

    rollup_path = league_dir / "rollup.parquet"
    entries.append({
        "path": f"data/{slug}/rollup.parquet",
        "rows": rollup_rows,
        "bytes": os.path.getsize(rollup_path),
        "season": "all",
        "column_count": None,
    })

    for dim_rel_path, dim_rows in dim_entries:
        dim_full = league_dir / dim_rel_path
        entries.append({
            "path": f"data/{slug}/{dim_rel_path}",
            "rows": dim_rows,
            "bytes": os.path.getsize(dim_full),
        })

    fallback_path = league_dir.parent / "fallback" / "all_league_default.json"
    if fallback_path.exists():
        entries.append({
            "path": "fallback/all_league_default.json",
            "bytes": os.path.getsize(fallback_path),
            "default_season": fallback_season,
        })

    manifest = {
        "league_key": league_key,
        "league_slug": slug,
        "platform": platform,
        "generated_at": GENERATED_AT,
        "files": entries,
    }
    manifest_path = league_dir / "manifest.json"
    with open(manifest_path, "w") as fp:
        json.dump(manifest, fp, indent=2)
    return manifest


def names_gate(con):
    """Print distinct values of name-bearing columns for inspection."""
    def safe_print(s):
        try:
            print(s)
        except UnicodeEncodeError:
            print(s.encode('ascii', errors='replace').decode('ascii'))

    safe_print("\n========== NAMES GATE ==========")
    name_cols = [
        "owner_name", "team_name", "owner_display", "display_name"
    ]
    for col in name_cols:
        vals = con.execute(f"""
            SELECT DISTINCT {col}
            FROM ANALYTICS.FCT_PLAYER_DAILY_PERFORMANCE
            WHERE {col} IS NOT NULL
            ORDER BY {col}
        """).fetchall()
        safe_print(f"\n--- {col} ({len(vals)} distinct) ---")
        for v in vals[:30]:
            safe_print(f"  {v[0]}")
        if len(vals) > 30:
            safe_print(f"  ... and {len(vals) - 30} more")

    franchise_cols = con.execute("""
        SELECT DISTINCT canonical_name, configured_name
        FROM ANALYTICS.DIM_FRANCHISE
        ORDER BY canonical_name
    """).fetchall()
    safe_print(f"\n--- DIM_FRANCHISE names ({len(franchise_cols)} distinct) ---")
    for v in franchise_cols:
        safe_print(f"  canonical={v[0]}, configured={v[1]}")

    safe_print("\n=== Columns checked: owner_name, team_name, owner_display, "
               "display_name, DIM_FRANCHISE.canonical_name, DIM_FRANCHISE.configured_name ===")
    safe_print("========== END NAMES GATE ==========\n")


def main():
    parser = argparse.ArgumentParser(description="Publish dashboard Parquet extracts")
    parser.add_argument("--db", default="data/duckdb/demo/ESPN_FANTASY.duckdb",
                        help="Path to the DuckDB warehouse file")
    parser.add_argument("--out", default="docs/dashboard",
                        help="Output directory root")
    parser.add_argument("--names-gate-only", action="store_true",
                        help="Only run the names gate check, don't write files")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: warehouse not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    out_root = Path(args.out)
    con = duckdb.connect(str(db_path), read_only=True)

    # Names gate first
    names_gate(con)

    if args.names_gate_only:
        con.close()
        return

    # Get leagues
    leagues = [r[0] for r in con.execute(
        "SELECT DISTINCT league_key FROM ANALYTICS.FCT_PLAYER_DAILY_PERFORMANCE ORDER BY league_key"
    ).fetchall()]
    print(f"Leagues: {leagues}")

    for league_key in leagues:
        slug = slug_for_league(league_key)
        league_dir = out_root / "data" / slug
        print(f"\n{'='*60}")
        print(f"Processing league: {league_key} -> {league_dir}")

        # Delete and recreate (re-runnable)
        if league_dir.exists():
            shutil.rmtree(league_dir)
        league_dir.mkdir(parents=True)

        # Get seasons
        seasons = [r[0] for r in con.execute("""
            SELECT DISTINCT CAST(season_year AS INTEGER) AS sy
            FROM ANALYTICS.FCT_PLAYER_DAILY_PERFORMANCE
            WHERE league_key = ?
            ORDER BY sy
        """, [league_key]).fetchall()]
        print(f"  Seasons: {seasons}")

        # Write season atoms
        season_info = []
        for sy in seasons:
            parquet_path = league_dir / f"{sy}.parquet"
            rows = write_season_parquet(con, league_key, sy, parquet_path)
            fsize = os.path.getsize(parquet_path)
            print(f"  {sy}.parquet: {rows:,} rows, {fsize:,} bytes ({fsize/1024/1024:.2f} MB)")
            season_info.append((sy, rows, parquet_path))

        # Write rollup
        rollup_path = league_dir / "rollup.parquet"
        rollup_rows = write_rollup_parquet(con, league_key, rollup_path)
        rollup_size = os.path.getsize(rollup_path)
        print(f"  rollup.parquet: {rollup_rows:,} rows, {rollup_size:,} bytes ({rollup_size/1024/1024:.2f} MB)")

        # Write dims
        dims_dir = league_dir / "dims"
        dim_entries = write_dims(con, league_key, dims_dir)
        for dim_path, dim_rows in dim_entries:
            dim_full = league_dir / dim_path
            print(f"  {dim_path}: {dim_rows:,} rows, {os.path.getsize(dim_full):,} bytes")

        # Write fallback (only for the first/ESPN league for the default page)
        fallback_season = write_fallback(con, league_key, out_root)

        # Build manifest
        manifest = build_manifest(league_key, league_dir, season_info,
                                  rollup_rows, dim_entries, fallback_season)
        print(f"  manifest.json: {len(manifest['files'])} entries")

    # Total size
    total_bytes = sum(
        os.path.getsize(os.path.join(dp, f))
        for dp, _, files in os.walk(out_root / "data")
        for f in files
    )
    print(f"\nTotal data size: {total_bytes:,} bytes ({total_bytes/1024/1024:.2f} MB)")

    con.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
