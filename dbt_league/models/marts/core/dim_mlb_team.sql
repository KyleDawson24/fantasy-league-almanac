-- dim_mlb_team.sql
-- The MLB club spine: one row per MLB Stats API team id (MLB-263, ledger
-- S-41). Platform-general: it exists so a consumer can key a club on ONE
-- numeric id whichever platform the roster row came from.
--
-- WHY. The affinity presenter keys the shared club spine on CBS's numeric
-- `mlb_team_id`; ESPN rows only ever carried `pro_team`, an abbreviation,
-- so espn_points_data.presenter_affinity synthesised a number with
-- DENSE_RANK() over the abbreviations -- a stable-enough sort key that was
-- never the club's identity. fct_player_daily_performance now carries the
-- real id on both platforms (ESPN via this seed, CBS via the MLB gamelog
-- spine it already joins), and this dim is the lookup beside it.
--
-- Columns:
--   mlb_team_id     the MLB Stats API team id (the spine key)
--   mlb_team_name   the club's LATEST-era name from the gamelog spine
--                   (Expos rows read Nationals; the id is the spine) --
--                   the same rule the CBS book's affinity query applies.
--                   NULL when the spine holds no games for the club (a
--                   clone without MLB gamelogs); the id row still exists.
--   abbrevs         every spelling the mlb_team_abbrevs seed maps to the
--                   club, comma-joined and sorted. A LOOKUP AID, not a
--                   display label: which spelling a book prints is a
--                   vocabulary decision the seed does not yet carry (it
--                   lists ANA and LAA for 108 without ranking them), and
--                   it rides the label work (MLB-279-class), not this
--                   additive move.
--
-- Materialization: view. The seed is ~40 rows; the spine aggregate is a
-- 30-row GROUP BY.

{{ config(materialized='view') }}

with clubs as (
    select distinct team_id as mlb_team_id
    from {{ ref('mlb_team_abbrevs') }}
),

spellings as (
    select
        team_id as mlb_team_id,
        {{ listagg_ordered('cbs_abbrev', ',', 'cbs_abbrev') }} as abbrevs
    from {{ ref('mlb_team_abbrevs') }}
    group by 1
),

latest_names as (
    select
        team_id                        as mlb_team_id,
        max_by(team_name, game_date)   as mlb_team_name
    from {{ ref('stg_mlb__player_game') }}
    where team_id is not null
    group by 1
)

select
    c.mlb_team_id,
    n.mlb_team_name,
    s.abbrevs
from clubs c
left join spellings s    on s.mlb_team_id = c.mlb_team_id
left join latest_names n on n.mlb_team_id = c.mlb_team_id
