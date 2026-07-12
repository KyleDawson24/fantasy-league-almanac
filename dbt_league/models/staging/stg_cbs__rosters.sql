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

with latest_per_date as (
    select
        league_key,
        season_year,
        roster_date,
        payload
    from {{ source('raw', 'cbs_rosters') }}
    qualify row_number() over (
        partition by league_key, roster_date
        order by loaded_at desc
    ) = 1
)

select
    r.league_key,
    r.season_year,
    r.roster_date,
    t.value:id::string                              as team_id,
    t.value:name::string                            as team_name,
    t.value:abbr::string                            as team_abbr,
    t.value:division::string                        as division_name,
    p.value:id::string                              as player_id,
    p.value:fullname::string                        as player_name,
    p.value:roster_pos::string                      as roster_pos,
    p.value:roster_status::string                   as roster_status,
    p.value:eligible_positions_display::string      as eligible_positions,
    p.value:pro_team::string                        as pro_team
from latest_per_date r,
    lateral flatten(input => r.payload:body:rosters:teams) t,
    lateral flatten(input => t.value:players) p
where p.value:id is not null
