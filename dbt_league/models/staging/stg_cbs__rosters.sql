-- stg_cbs__rosters.sql
-- F3 daily rosters for CBS (adapter #2): the perishable fantasy layer --
-- who was on which team, deployed where, started or benched, per captured
-- date. THE data the whole capture program exists to save: it dies at
-- rollover and only exists for dates the runbook captured (2026 season,
-- one snapshot per date from league/rosters?point=YYYYMMDD&team_id=all).
--
-- Grain: one row per (league_key, roster_date, team_id, player_id). The
-- deployed slot (roster_pos: C/1B/.../P/RS) and the started-vs-benched
-- split (roster_status: A = active/started, RS = reserve/benched) are the
-- scoring-relevant signals -- in a daily-lineup points league the active
-- set IS the lineup. eligible_positions and pro_team ride as context.
--
-- Consumers today: the CBS almanac's team tabs (latest-date roster story).
-- The active-set membership reconstruction (MLB-63) reads the full date
-- range. If a date was re-captured, the latest load wins.

-- MEMORY SHAPE (MLB-10): the latest-capture arbitration and the outer
-- (teams) flatten moved to stg_box_scores__matchups' sibling,
-- stg_cbs__rosters__teams, so the fat capture payload is dropped at a
-- materialization boundary before this model flattens players. Read that
-- model's header for why -- the short version is that DuckDB's json[] cast
-- materializes where Snowflake's FLATTEN streams, and nested flattens make
-- every leaf row retain its parent document. Grain and values are
-- unchanged; this model still emits one row per player per team per date.

select
    r.league_key,
    r.season_year,
    r.roster_date,
    r.team_id,
    r.team_name,
    r.team_abbr,
    r.division_name,
    {{ json_text('p.value', 'id') }}::string                              as player_id,
    {{ json_text('p.value', 'fullname') }}::string                        as player_name,
    {{ json_text('p.value', 'roster_pos') }}::string                      as roster_pos,
    {{ json_text('p.value', 'roster_status') }}::string                   as roster_status,
    {{ json_text('p.value', 'eligible_positions_display') }}::string      as eligible_positions,
    {{ json_text('p.value', 'pro_team') }}::string                        as pro_team
from {{ ref('stg_cbs__rosters__teams') }} r,
    {{ flatten_array('r.players_json', 'p') }}
where {{ json_get('p.value', 'id') }} is not null
