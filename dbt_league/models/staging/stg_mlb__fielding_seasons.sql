-- stg_mlb__fielding_seasons.sql
-- Season-grain games-by-position from the MLB Stats API fielding yearByYear
-- sweep: one row per raw (player, season, team, position) split, typed and
-- mapped into the CBS position vocabulary. Platform-neutral baseball layer
-- (no league_key) -- like the other stg_mlb__ models, league semantics
-- attach where a league's rules consume it (int_cbs__eligibility_windows).
--
-- cbs_position collapses the outfield (LF/CF/RF/OF -> OF) and the pitching
-- roles (P/SP/RP -> P) because CBS prices eligibility at the SLOT vocabulary
-- ({C,1B,2B,3B,SS,OF,P} + DH; the 2026 captures show pitchers as flat 'P'
-- and outfielders as flat 'OF'). Non-position codes (PH/PR/etc.) map NULL
-- and are dropped by consumers.
--
-- Grain: raw split grain -- a traded player carries one split per team per
-- season, so (mlbam_id, season_year, position) is NOT unique here. Consumers
-- aggregate (SUM games) to their grain; no uniqueness is asserted at staging.

select
    mlbam_id,
    season_year,
    position                          as position_raw,
    case
        when position in ('LF', 'CF', 'RF', 'OF') then 'OF'
        when position in ('P', 'SP', 'RP')        then 'P'
        when position in ('C', '1B', '2B', '3B', 'SS', 'DH') then position
    end                               as cbs_position,
    stat:games::integer               as games,
    stat:gamesStarted::integer        as games_started,
    team:name::string                 as team_name
from {{ source('raw', 'mlb_fielding') }}
where season_year is not null
