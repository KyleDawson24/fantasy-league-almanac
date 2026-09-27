#!/usr/bin/env python3
"""
publish_extract.py -- the platform-neutral dashboard data layer (MLB-319).

One extract for any league: the master-table contract's atom (v0.3,
Appendix A of the MLB-297 kickoff) plus the converged dims, written per
league as Parquet. Every dashboard page is then a filter over the atom and
the dims; nothing downstream needs to know which platform a league came
from. BSB, its twin and a future Fantrax league are data-source swaps.

WHAT DECIDES BEHAVIOUR, AND WHAT NEVER DOES. The league's FORMAT
(dim_league_format, settings-first) decides period semantics: a head-to-head
league's periods are its matchups (dim_matchup_period, dated); a
season-points league has ONE matchup, the season, which is why its
dim_matchup_period rows carry NULL bounds by design, and its weekly scoring
periods are BINS derived from a rule (period 1 runs from opening day to the
first Sunday, then Monday-Sunday weeks). The platform name is used for
exactly one thing: choosing which rows of dim_lineup_slot translate this
league's slot tokens, because that dim is keyed by platform. It is read from
the league registry, never spelled as a literal here.

Output (a fresh stamped directory per run; nothing is ever deleted or
overwritten -- rerunning writes a new directory beside the old one):

    <out>/<stamp>/<league_slug>/
        atom/<season>.parquet   the day atom, contract columns in Appendix A
                                order, then the extension and raw_ groups
        rollup.parquet          season x phase x team x player sums
        dims/league.parquet     format, period vocabulary, horizon
        dims/seasons.parquet    opener, last day, provenance mix per season
        dims/periods.parquet    period key -> dates, label, completeness
        dims/calendar.parquet   day -> period key, bins, phase, day_state
        dims/teams.parquet      team-season names and standings, no contacts
        dims/lineup_slot.parquet  slot vocabulary + capacity per season
        dims/scoring_rule.parquet
        manifest.json           files (rows, bytes, sha256), source commit,
                                data horizon, every validation result

Usage:
    python dashboard/publish_extract.py --db data/duckdb/ESPN_FANTASY.duckdb
    python dashboard/publish_extract.py --db ... --league espn-main --horizon 2026-09-20

The warehouse file is attached READ_ONLY into an in-memory session, so the
writer can build its temp tables without ever writing to the warehouse. It
reads models only, so it runs over a DuckDB file built by the dbt DuckDB
lane or loaded from a Parquet dump of either engine's models.
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from config.league_registry import get_league  # noqa: E402

PARQUET_OPTS = "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 50000)"
PERIOD_DAYS = 7
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
FORBIDDEN_COLUMN_RE = re.compile(r"email|phone|contact", re.I)


class ExtractError(RuntimeError):
    """A contract invariant failed; the writer stops rather than publish."""


def q(con, sql, params=None):
    return con.execute(sql, params or []).fetchall()


def lit(value):
    return "'" + str(value).replace("'", "''") + "'"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def source_commit():
    def git(*args):
        return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True,
                              text=True).stdout.strip()
    return {"commit": git("rev-parse", "HEAD") or None,
            "branch": git("rev-parse", "--abbrev-ref", "HEAD") or None,
            "dirty": bool(git("status", "--porcelain", "--untracked-files=no"))}


# ---------------------------------------------------------------------------
# Per-league context: format, slot vocabulary, calendar
# ---------------------------------------------------------------------------

class League:
    def __init__(self, con, src, league_key, horizon):
        self.con, self.src, self.key = con, src, league_key
        self.slug = league_key.replace("-", "_")
        self.platform = get_league(league_key).platform
        fmt = q(con, f"SELECT league_format FROM {src}.dim_league_format WHERE league_key = ?",
                [league_key])
        if not fmt:
            raise ExtractError(f"{league_key}: no dim_league_format row")
        self.format = fmt[0][0]
        if self.format not in ("h2h", "points"):
            raise ExtractError(f"{league_key}: league_format {self.format!r} has no period rule")
        self.is_points = self.format == "points"
        self.period_kind = "scoring_period" if self.is_points else "matchup"
        self.period_label = "Period" if self.is_points else "Matchup"
        max_day = q(con, f"SELECT MAX(game_date) FROM {src}.fct_player_daily_performance "
                         f"WHERE league_key = ?", [league_key])[0][0]
        self.horizon = min(horizon, max_day) if horizon else max_day
        self.rule_version = q(con, f"SELECT CAST(MAX(settings_season) AS VARCHAR) FROM "
                                   f"{src}.dim_league_scoring_rule WHERE league_key = ?",
                              [league_key])[0][0]
        self.fact = (f"(SELECT * FROM {src}.fct_player_daily_performance "
                     f"WHERE league_key = {lit(league_key)} "
                     f"AND game_date <= DATE {lit(self.horizon)})")
        # The full stat line (W1/W6) and the dense roster ledger (W10) are
        # read when the warehouse has them; an older warehouse still
        # produces a valid atom (typed NULLs, game rows only), and the
        # manifest says which it was.
        schema = src.split(".", 1)[1]
        rels = {r[0].lower() for r in q(con, """
            SELECT table_name FROM information_schema.tables
            WHERE table_catalog = 'wh' AND lower(table_schema) = lower(?)""", [schema])}
        self.rels = rels
        self.production = (f"(SELECT * FROM {src}.fct_player_daily_production "
                           f"WHERE league_key = {lit(league_key)})"
                           if "fct_player_daily_production" in rels else None)
        self.roster = (f"(SELECT * FROM {src}.fct_player_roster_day "
                       f"WHERE league_key = {lit(league_key)} "
                       f"AND roster_date <= DATE {lit(self.horizon)})"
                       if "fct_player_roster_day" in rels else None)
        self.validations = {"inputs": {
            "full_stat_line": "fct_player_daily_production" if self.production else "absent (credited components only)",
            "dense_roster_days": "fct_player_roster_day" if self.roster else "absent (game days only)"}}

    # -- slot vocabulary ----------------------------------------------------
    def build_slot_map(self):
        con, src = self.con, self.src
        rows = q(con, f"""
            SELECT lineup_slot, canonical_slot_key, is_starting_slot
            FROM {src}.dim_lineup_slot WHERE platform = ?
            ORDER BY sort_order NULLS LAST, lineup_slot""", [self.platform])
        # An open flex slot -- a starting HITTING slot that is not a fielding
        # position (ESPN's UTIL, CBS's U) -- takes any hitter. ESPN prints it
        # in every hitter's eligibility; CBS never prints it on a card but
        # grants it in play. Adding it wherever a row has any hitting
        # eligibility makes both read the same way.
        # A league can state its own open slots (config/leagues.yml
        # settings_overrides.open_hitting_slots, e.g. BSB's U and DH); the
        # slot dim's non-position hitting slots are the default.
        declared = get_league(self.key).settings_overrides.get("open_hitting_slots")
        if declared is not None:
            flex = q(con, f"""
                SELECT DISTINCT canonical_slot_key FROM {src}.dim_lineup_slot
                WHERE platform = ? AND lineup_slot IN (SELECT unnest(?::VARCHAR[]))""",
                     [self.platform, declared])
            if len(flex) != len(set(declared)):
                raise ExtractError(f"{self.key}: open_hitting_slots {declared} names a slot "
                                   f"outside this league's vocabulary")
        else:
            flex = q(con, f"""
                SELECT DISTINCT canonical_slot_key FROM {src}.dim_lineup_slot
                WHERE platform = ? AND is_starting_slot AND slot_category = 'hitting'
                  AND NOT is_position_slot""", [self.platform])
        self.validations["open_hitting_slots"] = {
            "source": "config/leagues.yml settings_overrides" if declared is not None else "dim_lineup_slot (non-position hitting slots)",
            "slots": sorted(r[0] for r in flex)}
        hitting = q(con, f"""
            SELECT DISTINCT canonical_slot_key FROM {src}.dim_lineup_slot
            WHERE platform = ? AND is_starting_slot AND slot_category = 'hitting'""", [self.platform])
        self.open_flex_sql = "[" + ", ".join(lit(r[0]) for r in flex) + "]"
        self.hitting_keys_sql = "[" + ", ".join(lit(r[0]) for r in hitting) + "]"
        self.slot_rows = rows
        starting = {tok: key for tok, key, starting in rows if starting}
        pairs = ", ".join(f"{lit(t)}: {lit(k)}" for t, k in sorted(starting.items()))
        self.starting_map_sql = f"MAP {{{pairs}}}" if pairs else "MAP {}"
        # Canonical starting keys in the slot dim's order, so eligibility
        # lists read the same way for every league (C before DH before UTIL).
        ordered = list(dict.fromkeys(k for _t, k, st in rows if st))
        self.starting_order_sql = "[" + ", ".join(lit(k) for k in ordered) + "]"
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE slot_map AS
            SELECT lineup_slot, canonical_slot_key, slot_category AS dim_slot_category,
                   is_starting_slot, display_slot, sort_order
            FROM {src}.dim_lineup_slot WHERE platform = {lit(self.platform)}""")
        sources = [self.fact] + ([self.roster] if self.roster else [])
        unmapped = q(con, " UNION ".join(f"""
            SELECT DISTINCT f.lineup_slot FROM {rel} f
            LEFT JOIN slot_map s USING (lineup_slot)
            WHERE f.lineup_slot IS NOT NULL AND s.lineup_slot IS NULL""" for rel in sources))
        if unmapped:
            raise ExtractError(f"{self.key}: slot tokens missing from dim_lineup_slot: "
                               f"{[u[0] for u in unmapped]}")

    def eligible_slots_sql(self, alias="f", relation="fct_player_daily_performance"):
        """eligible_slots arrives as JSON text from a dump and may arrive as a
        list from a native DuckDB build; either way it leaves as a list of
        canonical STARTING-slot keys (bench/IL/FA stripped, invariant 7)."""
        dtype = q(self.con, f"""
            SELECT data_type FROM information_schema.columns
            WHERE table_catalog = 'wh' AND lower(table_name) = '{relation}'
              AND lower(column_name) = 'eligible_slots'""")[0][0].upper()
        raw = (f"{alias}.eligible_slots" if dtype.endswith("[]")
               else f"from_json(CAST({alias}.eligible_slots AS VARCHAR), '[\"VARCHAR\"]')")
        # The row's tokens are mapped ONCE (bound as m through a one-element
        # list); the ordered filter then tests membership, plus the open flex
        # slots when m holds any hitting slot.
        mapped = f"list_transform({raw}, x -> {self.starting_map_sql}[x])"
        return (f"list_transform([{mapped}], m -> list_filter({self.starting_order_sql}, "
                f"k -> list_contains(m, k) OR (list_contains({self.open_flex_sql}::VARCHAR[], k) "
                f"AND list_has_any(m, {self.hitting_keys_sql}::VARCHAR[]))))[1]")

    # -- calendar: seasons, periods, days -------------------------------------
    def build_calendar(self):
        con, src = self.con, self.src
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE season_cal AS
            SELECT CAST(season_year AS INTEGER) AS season_year,
                   MIN(game_date) AS opener, MAX(game_date) AS last_day,
                   COUNT(DISTINCT game_date) AS game_days
            FROM {self.fact} GROUP BY 1""")
        max_season = q(con, "SELECT MAX(season_year) FROM season_cal")[0][0]
        # A season is FINAL once a later season exists in the data. The
        # current season is never final here; its periods close one by one.
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE season_state AS
            SELECT season_year, season_year < {max_season} AS is_final FROM season_cal""")

        if self.is_points:
            # The bin rule (Kyle, 2026-09-26, replacing "opener + 7k"):
            # period 1 runs from opening day to the first Sunday; every later
            # period is a Monday-Sunday week. The ruled first version matched
            # CBS's 2026 period COUNT but not its content (1 of 416
            # team-periods); this one lines up the short periods CBS delivered
            # (period 1, and the All-Star week). Only 2026 has delivered
            # periods to check against; historic seasons are derived and
            # known not to be a perfect match.
            con.execute(f"""
                CREATE OR REPLACE TEMP TABLE periods AS
                SELECT c.season_year, k AS period_key,
                       CASE WHEN k = 1 THEN c.opener
                            ELSE CAST(date_trunc('week', c.opener)
                                      + INTERVAL ({PERIOD_DAYS} * (k - 1)) DAY AS DATE) END AS start_date,
                       CAST(date_trunc('week', c.opener)
                            + INTERVAL ({PERIOD_DAYS} * k - 1) DAY AS DATE) AS end_date,
                       FALSE AS is_playoff, 'derived_rule' AS period_source
                FROM season_cal c,
                     range(1, CAST(date_diff('week', date_trunc('week', c.opener),
                                             date_trunc('week', c.last_day)) + 2 AS BIGINT)) r(k)""")
        else:
            con.execute(f"""
                CREATE OR REPLACE TEMP TABLE periods AS
                SELECT CAST(season_year AS INTEGER) AS season_year,
                       CAST(matchup_period AS INTEGER) AS period_key,
                       start_date, end_date, COALESCE(is_playoff, FALSE) AS is_playoff,
                       'platform_calendar' AS period_source
                FROM {src}.dim_matchup_period WHERE league_key = {lit(self.key)}""")

        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE periods_out AS
            SELECT p.*,
                   {lit(self.period_kind)} AS period_kind,
                   {lit(self.period_label)} || ' ' || p.period_key AS period_label,
                   CASE WHEN p.is_playoff THEN 'postseason' ELSE 'regular' END AS phase,
                   (s.is_final OR (p.end_date IS NOT NULL AND p.end_date <= DATE {lit(self.horizon)}))
                       AS is_complete
            FROM periods p JOIN season_state s USING (season_year)""")

        # One row per day that carries data: game days, plus no-game roster
        # days when the dense ledger is present. Days before opening day are
        # preseason, days past the last period offseason.
        roster_days = (f"UNION SELECT DISTINCT CAST(season_year AS INTEGER), roster_date "
                       f"FROM {self.roster}") if self.roster else ""
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE day_map AS
            WITH days AS (
                SELECT DISTINCT CAST(season_year AS INTEGER) AS season_year, game_date
                FROM {self.fact}
                {roster_days}
            )
            SELECT d.season_year, d.game_date AS scoring_day,
                   CAST(date_diff('day', c.opener, d.game_date) + 1 AS INTEGER) AS scoring_day_ordinal,
                   p.period_key,
                   CASE WHEN {str(self.is_points).upper()}
                        THEN CAST(d.season_year AS VARCHAR) || '-P' || lpad(CAST(p.period_key AS VARCHAR), 2, '0')
                        ELSE strftime(d.game_date, '%G-W%V') END AS week_bin_key,
                   CAST(year(d.game_date) * 100 + month(d.game_date) AS INTEGER) AS month_bin_key,
                   COALESCE(p.phase, CASE WHEN d.game_date < c.opener THEN 'preseason'
                                          ELSE 'offseason' END) AS phase,
                   CASE WHEN s.is_final THEN 'final'
                        WHEN p.is_complete THEN 'complete'
                        ELSE 'in_flight' END AS day_state,
                   (s.is_final OR COALESCE(p.is_complete, FALSE)) AS is_complete
            FROM days d
            JOIN season_cal c USING (season_year)
            JOIN season_state s USING (season_year)
            LEFT JOIN periods_out p
              ON p.season_year = d.season_year
             AND d.game_date BETWEEN p.start_date AND p.end_date""")

        dup = q(con, "SELECT COUNT(*) FROM (SELECT scoring_day FROM day_map GROUP BY 1 HAVING COUNT(*) > 1)")[0][0]
        if dup:
            raise ExtractError(f"{self.key}: {dup} days fall in more than one period")
        self.validate_periods()

    def validate_periods(self):
        con, src = self.con, self.src
        if self.is_points:
            # Derived bins per season vs the delivered period count. A season
            # with no delivered standings has no reference: say so, never
            # infer one.
            ref = dict(q(con, f"""
                SELECT CAST(season_year AS INTEGER), COUNT(DISTINCT period)
                FROM {src}.mart_period_standings WHERE league_key = ? GROUP BY 1""", [self.key]))
            derived = q(con, """
                SELECT p.season_year, COUNT(*) AS bins, MIN(c.opener), MAX(c.last_day)
                FROM periods p JOIN season_cal c USING (season_year)
                GROUP BY 1 ORDER BY 1""")
            per_season = []
            for season, bins, opener, last_day in derived:
                reference = ref.get(season)
                per_season.append({
                    "season": season, "opener": str(opener), "last_day": str(last_day),
                    "derived_periods": bins, "reference_periods": reference,
                    "status": ("no_reference" if reference is None
                               else "match" if reference == bins else "MISMATCH"),
                })
            self.validations["period_bins"] = {
                "rule": "period 1 runs from opening day (first game date in the league's data) "
                        "to the first Sunday; every later period is a Monday-Sunday week",
                "historic_note": "only seasons with delivered period standings can be checked; "
                                 "derived periods for other seasons are known not to be a perfect match",
                "reference": "COUNT(DISTINCT period) in mart_period_standings",
                "seasons": per_season,
            }
            # The daily key: a YYYYMMDD game date, never days since the opener.
            bad = q(con, f"""
                SELECT COUNT(*) FROM {self.fact}
                WHERE CAST(strftime(game_date, '%Y%m%d') AS BIGINT) <> CAST(scoring_period AS BIGINT)""")[0][0]
            self.validations["daily_key_is_yyyymmdd"] = {"rows_disagreeing": bad}
        else:
            # A matchup league's rows carry the platform's own matchup key;
            # the date-derived period must agree with it on every row.
            bad = q(con, f"""
                SELECT COUNT(*) FROM {self.fact} f
                JOIN day_map d ON d.scoring_day = f.game_date
                WHERE d.period_key IS DISTINCT FROM CAST(f.matchup_period AS INTEGER)""")[0][0]
            self.validations["period_key_matches_fact_matchup_period"] = {"rows_disagreeing": bad}
            if bad:
                raise ExtractError(f"{self.key}: {bad} rows disagree with their matchup period")
            ordinal_bad = q(con, f"""
                SELECT COUNT(*) FROM {self.fact} f
                JOIN day_map d ON d.scoring_day = f.game_date
                WHERE d.scoring_day_ordinal <> CAST(f.scoring_period AS INTEGER)""")[0][0]
            self.validations["scoring_day_ordinal_matches_platform_ordinal"] = {
                "rows_disagreeing": ordinal_bad}
        offseason = q(con, "SELECT COUNT(*) FROM day_map WHERE period_key IS NULL")[0][0]
        self.validations["days_outside_any_period"] = offseason

    # -- the atom -------------------------------------------------------------
    # (name, game-row expression, dense-row expression). Game rows are the
    # daily fact's rows (alias f) with the full stat line (alias p) and the
    # day map (d); dense rows are rostered days with no game (alias r), whose
    # components and points are NULL, never zero (ruling R5).
    BATTING = ["ab", "h", "singles", "doubles", "triples", "hr", "xbh", "tb", "r", "rbi",
               "bb", "ibb", "so", "hbp", "sf", "sb", "cs", "gdp", "cyc"]
    PITCHING = ["outs", "w", "l", "k", "er", "qs", "sv", "hld", "blsv", "p_h", "p_bb",
                "p_hr", "p_r", "cg", "sho", "nh", "pg", "blk", "wp", "hbp_p", "pk"]
    FACT_NAME = {"bb": "b_bb", "ibb": "b_ibb", "so": "b_so"}

    def component(self, name):
        """The full line where the warehouse has it, else the credited value
        the fact carries (the published dashboard's own coalesce order)."""
        fact = f"f.{self.FACT_NAME.get(name, name)}"
        if self.production:
            return f"CAST(COALESCE(p.{name}, {fact}) AS DOUBLE)"
        return f"CAST({fact} AS DOUBLE)"

    def line_only(self, name):
        """A component only the full line carries (typed NULL without it)."""
        return f"CAST(p.{name} AS DOUBLE)" if self.production else "NULL::DOUBLE"

    def atom_columns(self):
        slot_state = """CASE WHEN {team} IS NULL OR s.canonical_slot_key = 'free_agent' THEN 'free_agent'
             WHEN s.canonical_slot_key = 'estimated' THEN 'estimated'
             WHEN s.canonical_slot_key = 'injured' THEN 'il'
             WHEN s.canonical_slot_key = 'bench' THEN 'benched'
             WHEN s.is_starting_slot THEN 'active'
             WHEN {slot} IS NULL THEN 'unobserved'
             ELSE 'unclassified' END"""
        null = lambda t: f"NULL::{t}"
        cols = [
            # 3A identity
            ("league_key", "f.league_key", "r.league_key"),
            ("platform", lit(self.platform), lit(self.platform)),
            ("season_year", "CAST(f.season_year AS INTEGER)", "CAST(r.season_year AS INTEGER)"),
            ("scoring_day", "f.game_date", "r.roster_date"),
            ("player_key", "f.player_key", "r.player_key"),
            ("platform_player_id", "CAST(CAST(f.player_id AS BIGINT) AS VARCHAR)",
             "CAST(TRY_CAST(r.player_key AS BIGINT) AS VARCHAR)"),
            ("mlbam_id", null("INTEGER"), null("INTEGER")),
            ("person_key", null("VARCHAR"), null("VARCHAR")),
            ("display_name", "f.display_name", "COALESCE(r.display_name, n.display_name)"),
            # 3B time / completeness
            ("scoring_day_ordinal", "d.scoring_day_ordinal", "d.scoring_day_ordinal"),
            ("matchup_period_ordinal",
             null("INTEGER") if self.is_points else "d.period_key",
             null("INTEGER") if self.is_points else "d.period_key"),
            ("week_bin_key", "d.week_bin_key", "d.week_bin_key"),
            ("month_bin_key", "d.month_bin_key", "d.month_bin_key"),
            ("phase", "d.phase", "d.phase"),
            ("day_state", "d.day_state", "d.day_state"),
            ("is_complete", "d.is_complete", "d.is_complete"),
            ("matchup_id", null("VARCHAR"), null("VARCHAR")),
            # 3C rostering / deployment
            ("team_id", "CAST(f.team_id AS INTEGER)", "CAST(r.team_id AS INTEGER)"),
            ("franchise_id",
             "COALESCE(fs.canonical_franchise_id, CAST(CAST(f.team_id AS INTEGER) AS VARCHAR))",
             "COALESCE(fs.canonical_franchise_id, CAST(CAST(r.team_id AS INTEGER) AS VARCHAR))"),
            ("deployment_state", slot_state.format(team="f.team_id", slot="f.lineup_slot"),
             slot_state.format(team="r.team_id", slot="r.lineup_slot")),
            ("lineup_slot", "s.canonical_slot_key", "s.canonical_slot_key"),
            ("platform_lineup_slot", "f.lineup_slot", "r.lineup_slot"),
            ("slot_category", "f.lineup_slot_category", "s.dim_slot_category"),
            ("active_share", "CAST(f.active_weight AS DOUBLE)", null("DOUBLE")),
            ("batting_credited_share", """CAST(CASE f.lineup_slot_category
                 WHEN 'hitting' THEN f.active_weight WHEN 'pitching' THEN 0
                 WHEN 'estimated' THEN f.active_weight ELSE 0 END AS DOUBLE)""", null("DOUBLE")),
            ("pitching_credited_share", """CAST(CASE f.lineup_slot_category
                 WHEN 'pitching' THEN f.active_weight WHEN 'hitting' THEN 0
                 WHEN 'estimated' THEN f.active_weight ELSE 0 END AS DOUBLE)""", null("DOUBLE")),
            ("wasted_reason", "CASE s.canonical_slot_key WHEN 'bench' THEN 'benched' WHEN 'injured' THEN 'il' END",
             "CASE s.canonical_slot_key WHEN 'bench' THEN 'benched' WHEN 'injured' THEN 'il' END"),
            ("acquisition_channel", null("VARCHAR"), null("VARCHAR")),
            ("provenance", "f.provenance", "r.roster_source"),
            ("has_game", "TRUE", "FALSE"),
            # 3D eligibility / position
            ("eligible_slots", self.eligible_slots_sql("f", "fct_player_daily_performance"),
             self.eligible_slots_sql("r", "fct_player_roster_day") if self.roster else null("VARCHAR[]")),
            ("primary_position", "f.position", null("VARCHAR")),
            ("irl_positions_played", null("VARCHAR[]"), null("VARCHAR[]")),
            ("mlb_team_id", "CAST(f.mlb_team_id AS INTEGER)", null("INTEGER")),
            # 3E batting
            ("batting_games", self.line_only("batting_games"), null("DOUBLE")),
        ]
        cols += [(c, self.component(c), null("DOUBLE")) for c in self.BATTING]
        cols += [("sh", self.line_only("sh"), null("DOUBLE")),
                 ("pa", self.line_only("pa"), null("DOUBLE")),
                 ("pitching_games", self.line_only("pitching_games"), null("DOUBLE")),
                 ("pitching_starts", self.line_only("pitching_starts"), null("DOUBLE"))]
        cols += [(c, self.component(c), null("DOUBLE")) for c in self.PITCHING]
        cols += [("bf", self.line_only("bf"), null("DOUBLE")),
                 ("gf", self.line_only("gf"), null("DOUBLE"))]
        # 3F.1 fielding, reserved until MLB-289
        cols += [(c, null("DOUBLE"), null("DOUBLE")) for c in
                 ("fielding_games", "po", "a", "e", "dp", "pb", "sb_allowed", "cs_caught")]
        # 3G league-scored value: the credited calculated lens and the platform lens
        cols += [
            ("batting_pts", "CAST(f.total_hitting_stat_pts AS DOUBLE)", null("DOUBLE")),
            ("pitching_pts", "CAST(f.total_pitching_stat_pts AS DOUBLE)", null("DOUBLE")),
            ("total_pts", "CAST(f.total_stat_pts AS DOUBLE)", null("DOUBLE")),
            ("negative_pts", "CAST(f.negative_points AS DOUBLE)", null("DOUBLE")),
            ("platform_pts", "CAST(f.platform_points AS DOUBLE)", null("DOUBLE")),
            ("platform_batting_pts", "CAST(f.platform_hitting_pts AS DOUBLE)", null("DOUBLE")),
            ("platform_pitching_pts", "CAST(f.platform_pitching_pts AS DOUBLE)", null("DOUBLE")),
            ("scoring_rule_version", lit(self.rule_version), lit(self.rule_version)),
            # extension (nullable additions; the contract columns stay an exact prefix)
            ("period_key", "d.period_key", "d.period_key"),
            ("slot_provenance", "CASE WHEN f.provenance = 'captured' THEN 'captured' ELSE 'estimated' END",
             "CASE WHEN r.roster_source = 'platform_roster_snapshot' THEN 'captured' ELSE 'estimated' END"),
            ("p_ibb", self.line_only("p_ibb"), null("DOUBLE")),
            ("production_source", "p.production_source" if self.production else null("VARCHAR"),
             null("VARCHAR")),
            # raw_ (platform-shaped, for provenance and reconciliation)
            ("raw_scoring_period", "CAST(f.scoring_period AS BIGINT)", null("BIGINT")),
            ("raw_matchup_period", "CAST(f.matchup_period AS INTEGER)", null("INTEGER")),
            ("raw_games_played", "CAST(f.games_played AS INTEGER)", null("INTEGER")),
            ("raw_is_active_slot", "f.is_active_slot", null("BOOLEAN")),
            ("raw_performance_status", "f.performance_status", null("VARCHAR")),
            ("raw_wasted_bucket", "f.wasted_bucket", null("VARCHAR")),
            ("raw_pro_team", "f.pro_team", "r.pro_team"),
            ("raw_roster_status", "rs.roster_status" if self.roster else null("VARCHAR"),
             "r.roster_status"),
        ]
        return cols

    def atom_select(self, season):
        src = self.src
        cols = self.atom_columns()
        franchise = lambda a: f"""LEFT JOIN {src}.dim_franchise_season fs
      ON fs.league_key = {a}.league_key
     AND CAST(fs.season_year AS INTEGER) = CAST({a}.season_year AS INTEGER)
     AND fs.franchise_id = CAST(CAST({a}.team_id AS INTEGER) AS VARCHAR)"""
        game = f"""
    SELECT {", ".join(f"{g} AS {n}" for n, g, _ in cols)}
    FROM {self.fact} f
    JOIN day_map d ON d.season_year = CAST(f.season_year AS INTEGER) AND d.scoring_day = f.game_date
    LEFT JOIN slot_map s ON s.lineup_slot = f.lineup_slot
    {"LEFT JOIN " + self.production + " p ON p.game_date = f.game_date AND p.player_key = f.player_key" if self.production else ""}
    {"LEFT JOIN " + self.roster + " rs ON rs.roster_date = f.game_date AND rs.player_key = f.player_key" if self.roster else ""}
    {franchise("f")}
    WHERE CAST(f.season_year AS INTEGER) = {season}"""
        if not self.roster:
            return game
        dense = f"""
    SELECT {", ".join(f"{e} AS {n}" for n, _, e in cols)}
    FROM {self.roster} r
    JOIN day_map d ON d.season_year = CAST(r.season_year AS INTEGER) AND d.scoring_day = r.roster_date
    LEFT JOIN slot_map s ON s.lineup_slot = r.lineup_slot
    LEFT JOIN names n ON n.player_key = r.player_key
    {franchise("r")}
    WHERE CAST(r.season_year AS INTEGER) = {season}
      AND NOT EXISTS (SELECT 1 FROM {self.fact} f
                      WHERE f.game_date = r.roster_date AND f.player_key = r.player_key)"""
        return f"{game}\n    UNION ALL{dense}"

    def write_atoms(self, league_dir):
        con = self.con
        atom_dir = league_dir / "atom"
        atom_dir.mkdir(parents=True)
        seasons = [r[0] for r in q(con, "SELECT season_year FROM season_cal ORDER BY 1")]
        con.execute(f"""CREATE OR REPLACE TEMP TABLE names AS
            SELECT player_key, arg_max(display_name, game_date) AS display_name
            FROM {self.fact} GROUP BY 1""")
        out, dense_total = [], 0
        for season in seasons:
            path = atom_dir / f"{season}.parquet"
            con.execute(f"""
                COPY (SELECT * FROM ({self.atom_select(season)}) ORDER BY scoring_day, player_key)
                TO {lit(path.as_posix())} {PARQUET_OPTS}""")
            written = f"read_parquet({lit(path.as_posix())})"
            rows, game_rows = q(con, f"SELECT COUNT(*), COUNT(*) FILTER (WHERE has_game) FROM {written}")[0]
            source_rows = q(con, f"SELECT COUNT(*) FROM {self.fact} f "
                                 f"WHERE CAST(f.season_year AS INTEGER) = {season}")[0][0]
            if game_rows != source_rows:
                raise ExtractError(f"{self.key}/{season}: wrote {game_rows} game rows, fact has {source_rows}")
            dense_total += rows - game_rows
            dupes = q(con, f"""SELECT COUNT(*) FROM (SELECT 1 FROM {written}
                GROUP BY league_key, scoring_day, player_key HAVING COUNT(*) > 1)""")[0][0]
            if dupes:
                raise ExtractError(f"{self.key}/{season}: {dupes} duplicate "
                                   f"(league_key, scoring_day, player_key) keys")
            unclassified = q(con, f"SELECT COUNT(*) FROM {written} "
                                  f"WHERE deployment_state = 'unclassified'")[0][0]
            if unclassified:
                raise ExtractError(f"{self.key}/{season}: {unclassified} rows with no deployment state")
            nonnull_dense = q(con, f"SELECT COUNT(*) FROM {written} WHERE NOT has_game "
                                   f"AND (ab IS NOT NULL OR outs IS NOT NULL OR total_pts IS NOT NULL)")[0][0]
            if nonnull_dense:
                raise ExtractError(f"{self.key}/{season}: {nonnull_dense} no-game rows carry production")
            out.append((season, path, rows))
        self.validations["dense_no_game_rows"] = dense_total
        self.validations["uniqueness_league_day_player"] = "pass (every season file checked)"
        self.validations["row_parity_with_fact"] = "pass: game rows = fact rows, every season file"
        # Share-vs-slot disagreements are carried as the warehouse states
        # them (the slot is the roster snapshot, the share is the scoring
        # attribution); counted here so they are visible, never repaired.
        glob = lit((atom_dir / "*.parquet").as_posix())
        self.validations["starting_slot_with_zero_share"] = q(con, f"""
            SELECT season_year, COUNT(*) FROM read_parquet({glob})
            WHERE deployment_state = 'active' AND active_share = 0
            GROUP BY 1 ORDER BY 1""")
        if self.is_points:
            self.validate_period_content(glob)
        return out

    def validate_period_content(self, glob):
        """A matching period COUNT is weak evidence; matching CONTENT is the
        real test. Where the platform delivered per-period points, compare
        each team's credited points inside each derived period with them.
        Reported, never used to move a boundary."""
        rows = q(self.con, f"""
            WITH atom AS (
                SELECT season_year, period_key, team_id,
                       SUM(batting_pts * batting_credited_share
                           + pitching_pts * pitching_credited_share) AS pts
                FROM read_parquet({glob}) WHERE team_id IS NOT NULL GROUP BY ALL
            ),
            delivered AS (
                SELECT CAST(season_year AS INTEGER) AS season_year, CAST(period AS INTEGER) AS period_key,
                       CAST(team_id AS INTEGER) AS team_id, period_points
                FROM {self.src}.mart_period_standings WHERE league_key = {lit(self.key)}
            )
            SELECT d.season_year, COUNT(*) AS team_periods,
                   COUNT(*) FILTER (WHERE ABS(a.pts - d.period_points) < 0.005) AS exact,
                   COUNT(*) FILTER (WHERE ABS(a.pts - d.period_points) <= 1) AS within_1,
                   COUNT(*) FILTER (WHERE a.pts IS NULL) AS no_atom_rows,
                   ROUND(SUM(a.pts), 2) AS atom_points, ROUND(SUM(d.period_points), 2) AS delivered_points
            FROM delivered d LEFT JOIN atom a USING (season_year, period_key, team_id)
            GROUP BY 1 ORDER BY 1""")
        keys = ["season", "team_periods", "exact", "within_1", "no_atom_rows",
                "atom_points", "delivered_points"]
        results = [dict(zip(keys, r)) for r in rows]
        for r in results:
            r["status"] = "match" if r["exact"] == r["team_periods"] else "MISMATCH"
        self.validations["period_bin_content"] = {
            "measure": "per team per derived period: SUM(batting_pts * batting_credited_share "
                       "+ pitching_pts * pitching_credited_share) vs mart_period_standings.period_points",
            "seasons": results,
        }

    # -- rollup ---------------------------------------------------------------
    def write_rollup(self, league_dir):
        atom = lit((league_dir / "atom" / "*.parquet").as_posix())
        bat = ["ab", "h", "singles", "doubles", "triples", "hr", "xbh", "tb", "r", "rbi", "bb",
               "ibb", "so", "hbp", "sf", "sb", "cs", "gdp", "cyc"]
        pit = ["outs", "w", "l", "k", "er", "qs", "sv", "hld", "blsv", "p_h", "p_bb", "p_hr",
               "p_r", "cg", "sho", "nh", "pg", "blk", "wp", "hbp_p", "pk"]
        sums = ",\n".join(
            [f"SUM({c}) AS {c}, SUM({c} * batting_credited_share) AS active_{c}" for c in bat]
            + [f"SUM({c}) AS {c}, SUM({c} * pitching_credited_share) AS active_{c}" for c in pit])
        path = league_dir / "rollup.parquet"
        self.con.execute(f"""
            COPY (
                SELECT league_key, platform, season_year, phase, team_id, franchise_id, player_key,
                       MAX(platform_player_id) AS platform_player_id,
                       arg_max(display_name, scoring_day) AS display_name,
                       MIN(scoring_day) AS first_scoring_day,
                       MAX(scoring_day) AS last_scoring_day,
                       COUNT(*) AS day_count,
                       COUNT(*) FILTER (WHERE has_game) AS game_day_count,
                       BOOL_AND(is_complete) AS all_days_complete,
                       SUM(active_share) AS active_share_days,
                       SUM(batting_pts) AS batting_pts,
                       SUM(pitching_pts) AS pitching_pts,
                       SUM(total_pts) AS total_pts,
                       SUM(batting_pts * batting_credited_share) AS active_batting_pts,
                       SUM(pitching_pts * pitching_credited_share) AS active_pitching_pts,
                       SUM(batting_pts * batting_credited_share
                           + pitching_pts * pitching_credited_share) AS active_pts,
                       SUM(platform_pts) AS platform_pts,
                       SUM(platform_batting_pts) AS platform_batting_pts,
                       SUM(platform_pitching_pts) AS platform_pitching_pts,
                       SUM(platform_batting_pts * batting_credited_share) AS active_platform_batting_pts,
                       SUM(platform_pitching_pts * pitching_credited_share) AS active_platform_pitching_pts,
                       SUM(negative_pts) AS negative_pts,
                       {sums}
                FROM read_parquet({atom})
                GROUP BY ALL
                ORDER BY season_year, phase, team_id NULLS LAST, player_key
            ) TO {lit(path.as_posix())} {PARQUET_OPTS}""")
        return path

    # -- dims -----------------------------------------------------------------
    def write_dims(self, league_dir):
        con, src = self.con, self.src
        dims = league_dir / "dims"
        dims.mkdir()
        atom = lit((league_dir / "atom" / "*.parquet").as_posix())
        written = []

        def copy(name, sql):
            path = dims / f"{name}.parquet"
            con.execute(f"COPY ({sql}) TO {lit(path.as_posix())} {PARQUET_OPTS}")
            written.append(path)

        seasons = q(con, "SELECT MIN(season_year), MAX(season_year), COUNT(*) FROM season_cal")[0]
        copy("league", f"""
            SELECT {lit(self.key)} AS league_key, {lit(self.platform)} AS platform,
                   {lit(self.format)} AS league_format,
                   {lit(self.period_kind)} AS period_kind, {lit(self.period_label)} AS period_label,
                   {'NULL' if self.is_points else "'Matchup'"} AS matchup_label,
                   {seasons[0]} AS first_season, {seasons[1]} AS last_season,
                   {seasons[2]} AS season_count,
                   DATE {lit(self.horizon)} AS data_horizon,
                   {lit(self.rule_version)} AS scoring_rule_version""")

        copy("seasons", f"""
            SELECT c.season_year, c.opener, c.last_day, c.game_days, s.is_final,
                   (SELECT COUNT(*) FROM periods_out p WHERE p.season_year = c.season_year) AS period_count,
                   m.rows, m.captured_rows, m.reconstructed_rows, m.estimated_rows, m.sentinel_rows
            FROM season_cal c JOIN season_state s USING (season_year)
            JOIN (SELECT season_year, COUNT(*) AS rows,
                         COUNT(*) FILTER (WHERE provenance = 'captured') AS captured_rows,
                         COUNT(*) FILTER (WHERE provenance = 'reconstructed_day') AS reconstructed_rows,
                         COUNT(*) FILTER (WHERE provenance LIKE 'estimated%') AS estimated_rows,
                         COUNT(*) FILTER (WHERE provenance = 'sentinel') AS sentinel_rows
                  FROM read_parquet({atom}) GROUP BY 1) m USING (season_year)
            ORDER BY 1""")

        copy("periods", """
            SELECT p.season_year, p.period_key, p.period_kind, p.period_label,
                   p.start_date, p.end_date, p.is_playoff, p.phase, p.is_complete, p.period_source,
                   (SELECT COUNT(*) FROM day_map d WHERE d.season_year = p.season_year
                      AND d.period_key = p.period_key) AS days_with_data
            FROM periods_out p ORDER BY 1, 2""")

        copy("calendar", "SELECT * FROM day_map ORDER BY scoring_day")

        # Team-seasons come from the atom, so no team that played can drop.
        # Names resolve settings-first with the source recorded: the
        # standings dim, then the franchise-season dim, then the daily
        # fact's own label (the holding-pen sentinel has only that).
        copy("teams", f"""
            WITH ts AS (
                SELECT season_year, team_id, franchise_id, COUNT(*) AS atom_rows
                FROM read_parquet({atom}) WHERE team_id IS NOT NULL GROUP BY ALL
            ),
            fact_label AS (
                SELECT CAST(season_year AS INTEGER) AS season_year, CAST(team_id AS INTEGER) AS team_id,
                       arg_max(team_name, game_date) AS team_name,
                       arg_max(team_abbrev, game_date) AS team_abbrev
                FROM {self.fact} WHERE team_id IS NOT NULL GROUP BY ALL
            )
            SELECT ts.season_year, ts.team_id, ts.franchise_id,
                   COALESCE(st.team_name, fs.configured_name, fs.canonical_name, fl.team_name) AS team_name,
                   COALESCE(st.team_abbrev, fs.configured_abbrev, fs.canonical_abbrev, fl.team_abbrev) AS team_abbrev,
                   CASE WHEN st.team_name IS NOT NULL THEN 'dim_team_season_standing'
                        WHEN COALESCE(fs.configured_name, fs.canonical_name) IS NOT NULL THEN 'dim_franchise_season'
                        WHEN fl.team_name IS NOT NULL THEN 'fct_player_daily_performance'
                   END AS name_source,
                   fs.canonical_name AS franchise_canonical_name,
                   fs.canonical_abbrev AS franchise_canonical_abbrev,
                   CAST(st.standings_rank AS INTEGER) AS standings_rank,
                   CAST(st.playoff_seed AS INTEGER) AS playoff_seed,
                   CAST(st.final_rank AS INTEGER) AS final_rank,
                   st.is_champion, st.made_playoffs,
                   CAST(st.wins AS INTEGER) AS wins, CAST(st.losses AS INTEGER) AS losses,
                   CAST(st.ties AS INTEGER) AS ties,
                   st.platform_points, st.total_points, st.batting_points, st.pitching_points,
                   st.lens AS standings_lens,
                   CAST(st.teams_in_season AS INTEGER) AS teams_in_season,
                   ts.atom_rows
            FROM ts
            LEFT JOIN {src}.dim_team_season_standing st
              ON st.league_key = {lit(self.key)} AND CAST(st.season_year AS INTEGER) = ts.season_year
             AND CAST(st.team_id AS INTEGER) = ts.team_id
            LEFT JOIN {src}.dim_franchise_season fs
              ON fs.league_key = {lit(self.key)} AND CAST(fs.season_year AS INTEGER) = ts.season_year
             AND fs.franchise_id = CAST(ts.team_id AS VARCHAR)
            LEFT JOIN fact_label fl ON fl.season_year = ts.season_year AND fl.team_id = ts.team_id
            ORDER BY 1, 2""")

        # Slot vocabulary with a capacity per season. In order of authority:
        # the league's settings (dim_roster_slot_counts); else the MODAL
        # number of players in that slot per team-day across OBSERVED lineups
        # (captured game rows plus a platform's daily roster capture -- the
        # mode, because a maximum picks up intraday changes and uncredited
        # snapshot rows); else, for a season with neither, the league's
        # CURRENT shape (its latest season with a known shape), labelled
        # current_shape_from_<season> so no page can pass it off as observed.
        # Kyle, 2026-09-26: historic views show how players would fit today's
        # league, with that caveat, until historic shapes are recovered.
        copy("lineup_slot", f"""
            WITH seasons AS (SELECT DISTINCT season_year FROM read_parquet({atom})),
            used AS (
                SELECT DISTINCT season_year, platform_lineup_slot AS lineup_slot
                FROM read_parquet({atom}) WHERE platform_lineup_slot IS NOT NULL
            ),
            observed AS (
                SELECT season_year, lineup_slot, mode(n) AS observed_mode
                FROM (SELECT season_year, team_id, scoring_day, platform_lineup_slot AS lineup_slot, COUNT(*) AS n
                      FROM read_parquet({atom})
                      WHERE team_id IS NOT NULL AND platform_lineup_slot IS NOT NULL
                        AND provenance IN ('captured', 'platform_roster_snapshot')
                      GROUP BY ALL) GROUP BY ALL
            ),
            settings AS (
                SELECT CAST(season_year AS INTEGER) AS season_year, lineup_slot,
                       CAST(starter_count AS INTEGER) AS starter_count
                FROM {src}.dim_roster_slot_counts WHERE league_key = {lit(self.key)}
            ),
            direct AS (
                SELECT COALESCE(u.season_year, st.season_year) AS season_year,
                       COALESCE(u.lineup_slot, st.lineup_slot) AS lineup_slot,
                       COALESCE(st.starter_count, o.observed_mode) AS capacity,
                       CASE WHEN st.starter_count IS NOT NULL THEN 'settings'
                            WHEN o.observed_mode IS NOT NULL THEN 'observed_mode'
                       END AS capacity_source
                FROM used u
                FULL JOIN settings st ON st.season_year = u.season_year AND st.lineup_slot = u.lineup_slot
                LEFT JOIN observed o ON o.season_year = COALESCE(u.season_year, st.season_year)
                                    AND o.lineup_slot = COALESCE(u.lineup_slot, st.lineup_slot)
            ),
            has_cap AS (SELECT DISTINCT season_year FROM direct WHERE capacity IS NOT NULL),
            donor AS (
                SELECT s.season_year, (SELECT MAX(season_year) FROM has_cap) AS donor
                FROM seasons s
                WHERE s.season_year NOT IN (SELECT season_year FROM has_cap)
            ),
            carried AS (
                SELECT d.season_year, x.lineup_slot, x.capacity,
                       'current_shape_from_' || CAST(d.donor AS VARCHAR) AS capacity_source
                FROM donor d JOIN direct x ON x.season_year = d.donor AND x.capacity IS NOT NULL
            )
            SELECT COALESCE(d.season_year, c.season_year) AS season_year,
                   COALESCE(d.lineup_slot, c.lineup_slot) AS lineup_slot,
                   s.canonical_slot_key, s.display_slot, s.dim_slot_category AS slot_category,
                   s.is_starting_slot, CAST(s.sort_order AS INTEGER) AS sort_order,
                   COALESCE(d.capacity, c.capacity) AS capacity,
                   CASE WHEN d.capacity IS NOT NULL THEN d.capacity_source ELSE c.capacity_source END AS capacity_source,
                   u.lineup_slot IS NOT NULL AS appears_in_data
            FROM direct d
            FULL JOIN carried c ON c.season_year = d.season_year AND c.lineup_slot = d.lineup_slot
            LEFT JOIN used u ON u.season_year = COALESCE(d.season_year, c.season_year)
                            AND u.lineup_slot = COALESCE(d.lineup_slot, c.lineup_slot)
            LEFT JOIN slot_map s ON s.lineup_slot = COALESCE(d.lineup_slot, c.lineup_slot)
            ORDER BY 1, s.sort_order NULLS LAST, 2""")

        # Team-grain outcomes (contract 5.3: beside the atom, not on it).
        # Every format has matchups (Kyle, 2026-09-26). Head-to-head: the
        # platform's own weekly pairings. Season points: ONE matchup per
        # season against every other team -- finishing above a team is a
        # win, level a tie -- on the same season-points basis the rivalry
        # mart uses (int_franchise_season_points, the platform's own totals),
        # plus the delivered period standings for a season still in flight.
        needed = ({"int_franchise_season_points", "mart_period_standings"} if self.is_points
                  else {"mart_team_matchup"})
        if not needed <= self.rels:
            self.validations["matchup_record_invariant"] = "matchups absent: " + ", ".join(sorted(needed - self.rels))
            matchup_sql = None
        elif self.is_points:
            matchup_sql = f"""
                WITH season_points AS (
                    SELECT CAST(season_year AS INTEGER) AS season_year,
                           CAST(franchise_id AS INTEGER) AS team_id,
                           CAST(season_points AS DOUBLE) AS points, TRUE AS is_complete,
                           points_source AS result_source
                    FROM {src}.int_franchise_season_points
                    WHERE league_key = {lit(self.key)} AND is_season_complete
                    UNION ALL
                    SELECT CAST(ps.season_year AS INTEGER), CAST(ps.team_id AS INTEGER),
                           CAST(ps.points AS DOUBLE), FALSE, 'delivered_period_standings'
                    FROM {src}.mart_period_standings ps
                    WHERE ps.league_key = {lit(self.key)} AND ps.is_latest_period
                      AND CAST(ps.season_year AS INTEGER) NOT IN (
                          SELECT CAST(season_year AS INTEGER) FROM {src}.int_franchise_season_points
                          WHERE league_key = {lit(self.key)} AND is_season_complete)
                )
                SELECT a.season_year, 1 AS matchup_period, a.team_id, b.team_id AS opponent_id,
                       a.points AS team_points, b.points AS opponent_points,
                       CASE WHEN a.points > b.points THEN 'W' WHEN a.points < b.points THEN 'L'
                            ELSE 'T' END AS result,
                       FALSE AS is_playoff, a.is_complete, a.result_source
                FROM season_points a JOIN season_points b
                  ON b.season_year = a.season_year AND b.team_id <> a.team_id"""
        else:
            matchup_sql = f"""
                SELECT CAST(m.season_year AS INTEGER) AS season_year,
                       CAST(m.matchup_period AS INTEGER) AS matchup_period,
                       CAST(m.team_id AS INTEGER) AS team_id, CAST(m.opponent_id AS INTEGER) AS opponent_id,
                       CAST(m.platform_points AS DOUBLE) AS team_points,
                       CAST(m.opponent_points AS DOUBLE) AS opponent_points,
                       m.result, COALESCE(m.is_playoff, FALSE) AS is_playoff,
                       COALESCE(p.is_complete, FALSE) AS is_complete, 'platform_matchup' AS result_source
                FROM {src}.mart_team_matchup m
                LEFT JOIN periods_out p ON p.season_year = CAST(m.season_year AS INTEGER)
                                       AND p.period_key = CAST(m.matchup_period AS INTEGER)
                WHERE m.league_key = {lit(self.key)}"""
        if matchup_sql is not None:
            copy("matchups", f"SELECT * FROM ({matchup_sql}) ORDER BY 1, 2, 3, 4")
        # The invariant that makes both formats checkable: per team per
        # matchup, W+L+T = teams in it - 1 (season points) or 1 (H2H).
        mpath = lit((dims / "matchups.parquet").as_posix())
        if matchup_sql is not None:
            expected = "n - 1" if self.is_points else "1"
            self.validations["matchup_record_invariant"] = {
                "rule": "per team per matchup W+L+T = "
                        + ("teams in the season - 1" if self.is_points else "1"),
                "violations": q(con, f"""
                    WITH per_team AS (SELECT season_year, matchup_period, team_id, COUNT(*) AS games
                                      FROM read_parquet({mpath}) GROUP BY ALL),
                         teams AS (SELECT season_year, COUNT(DISTINCT team_id) AS n
                                   FROM read_parquet({mpath}) GROUP BY 1)
                    SELECT COUNT(*) FROM per_team JOIN teams USING (season_year)
                    WHERE games <> {expected}""")[0][0]}

        # As-of team names: the label a team carried over each stretch of a
        # season, so a per-day view can show the name of that day while the
        # names still live in one dim (never on the atom).
        copy("team_names", f"""
            SELECT CAST(season_year AS INTEGER) AS season_year, CAST(team_id AS INTEGER) AS team_id,
                   team_name, team_abbrev, MIN(game_date) AS first_day, MAX(game_date) AS last_day
            FROM {self.fact} WHERE team_id IS NOT NULL
            GROUP BY ALL ORDER BY 1, 2, 5""")

        copy("scoring_rule", f"""
            SELECT league_key, stat_name, canonical_key, stat_category,
                   CAST(settings_season AS INTEGER) AS settings_season,
                   platform_stat_key, points_per_unit, is_scored
            FROM {src}.dim_league_scoring_rule WHERE league_key = {lit(self.key)}
            ORDER BY stat_category, stat_name""")
        return written

    def pii_guard(self, paths):
        """Emails and phone numbers never leave the warehouse. Any column
        named like one, or any string value shaped like one, stops the run."""
        con = self.con
        for path in paths:
            ref = f"read_parquet({lit(path.as_posix())})"
            cols = q(con, f"DESCRIBE SELECT * FROM {ref}")
            for name, dtype, *_ in cols:
                if FORBIDDEN_COLUMN_RE.search(name):
                    raise ExtractError(f"{path.name}: forbidden column {name!r}")
                if dtype == "VARCHAR" and name not in ("player_key",):
                    for (value,) in q(con, f"SELECT DISTINCT {name} FROM {ref} WHERE {name} IS NOT NULL"):
                        if EMAIL_RE.search(value) or PHONE_RE.search(value):
                            raise ExtractError(f"{path.name}.{name}: a value looks like an "
                                               f"email or phone number; refusing to write")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def file_entry(con, league_dir, path, season=None):
    ref = f"read_parquet({lit(path.as_posix())})"
    entry = {"path": path.relative_to(league_dir).as_posix(),
             "rows": q(con, f"SELECT COUNT(*) FROM {ref}")[0][0],
             "bytes": path.stat().st_size, "sha256": sha256(path),
             "columns": len(q(con, f"DESCRIBE SELECT * FROM {ref}"))}
    if season is not None:
        lo, hi = q(con, f"SELECT MIN(scoring_day), MAX(scoring_day) FROM {ref}")[0]
        entry.update({"season": season, "min_scoring_day": str(lo), "max_scoring_day": str(hi)})
    return entry


def publish_league(con, src, league_key, horizon, out_root, generated_at, commit, db_path):
    league = League(con, src, league_key, horizon)
    league_dir = out_root / league.slug
    league_dir.mkdir(parents=True)
    print(f"\n== {league_key}: platform={league.platform} format={league.format} "
          f"horizon={league.horizon} -> {league_dir}")
    league.build_slot_map()
    league.build_calendar()
    atoms = league.write_atoms(league_dir)
    rollup = league.write_rollup(league_dir)
    dims = league.write_dims(league_dir)
    league.pii_guard(dims + [rollup])

    files = [file_entry(con, league_dir, p, s) for s, p, _ in atoms]
    files.append(file_entry(con, league_dir, rollup))
    files += [file_entry(con, league_dir, p) for p in dims]
    manifest = {
        "schema_version": 1,
        "contract": "MLB-285 master-table contract v0.3, Appendix A order; "
                    "extension columns period_key, slot_provenance; raw_ group last",
        "league_key": league_key, "league_slug": league.slug, "platform": league.platform,
        "league_format": league.format, "period_kind": league.period_kind,
        "period_label": league.period_label,
        "generated_at": generated_at,
        "source": {**commit, "warehouse": str(db_path), "schema": src.split(".", 1)[1]},
        "data_horizon": str(league.horizon),
        "seasons": [s for s, _, _ in atoms],
        "names_note": "team names are user data and are carried verbatim; they come from "
                      "whichever warehouse was read. Publication is decided outside this writer.",
        "validations": league.validations,
        "files": files,
    }
    (league_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str),
                                             encoding="utf-8")
    for f in files:
        print(f"  {f['path']:<28} {f['rows']:>9,} rows {f['bytes']:>11,} bytes")
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", required=True, help="DuckDB warehouse file (opened read-only)")
    ap.add_argument("--schema", default="ANALYTICS", help="schema holding the dbt models")
    ap.add_argument("--out", default=str(REPO_ROOT / "data" / "exports" / "dashboard"),
                    help="root; each run writes a new stamped directory under it")
    ap.add_argument("--league", action="append",
                    help="league_key to emit (repeatable; default every league in the fact)")
    ap.add_argument("--horizon", type=date.fromisoformat,
                    help="clip every league to game dates on or before this day")
    args = ap.parse_args(argv)

    db_path = Path(args.db).resolve()
    if not db_path.exists():
        sys.exit(f"no warehouse at {db_path}")
    generated_at = datetime.now(timezone.utc)
    stamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    out_root = Path(args.out) / stamp
    if out_root.exists():
        sys.exit(f"{out_root} already exists; the writer never overwrites")

    con = duckdb.connect()
    con.execute(f"ATTACH {lit(db_path.as_posix())} AS wh (READ_ONLY)")
    src = f"wh.{args.schema}"
    leagues = args.league or [r[0] for r in q(con, f"SELECT DISTINCT league_key FROM "
                                                   f"{src}.fct_player_daily_performance ORDER BY 1")]
    commit = source_commit()
    out_root.mkdir(parents=True)
    summary = {}
    for key in leagues:
        m = publish_league(con, src, key, args.horizon, out_root, generated_at.isoformat(),
                           commit, db_path)
        summary[key] = {"files": len(m["files"]),
                        "bytes": sum(f["bytes"] for f in m["files"])}
    print(f"\nwrote {out_root}")
    for key, s in summary.items():
        print(f"  {key}: {s['files']} files, {s['bytes']:,} bytes")
    return out_root


if __name__ == "__main__":
    main()
