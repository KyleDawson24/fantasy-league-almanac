-- stg_box_scores__team_rosters.sql
-- One row per fantasy team per scoring day for ESPN season-long points.
--
-- League type 5 has no H2H box-score object for the wrapper to deserialize.
-- The extract therefore stores mRoster's day-specific team attribution in a
-- parallel raw_json.team_rosters array rather than manufacturing an opponent
-- or a score. This materialization is the same memory boundary as
-- stg_box_scores__matchups: project the narrow sub-document, flatten once,
-- and drop the parent before stg_box_scores flattens each lineup.
--
-- H2H rows carry no team_rosters key and produce zero rows here. Season-long
-- rows carry an empty matchups array, so matchup-pair/W-L models produce zero
-- rows while the format-agnostic player and team season facts still populate.

{{ config(materialized='table') }}

with roster_source as (
    select
        league_key,
        season_year,
        scoring_period,
        matchup_period,
        {{ json_get('raw_json', 'team_rosters') }} as team_rosters_json
    from {{ source('raw', 'box_scores') }}
),

team_rosters as (
    select
        league_key,
        season_year,
        scoring_period,
        matchup_period,
        r.value as team_roster
    from roster_source,
        {{ flatten_array('team_rosters_json', 'r') }}
)

select
    league_key,
    season_year,
    scoring_period,
    matchup_period,
    {{ json_text('team_roster', 'owner') }}::string       as owner_name,
    {{ json_text('team_roster', 'team_name') }}::string   as team_name,
    {{ json_text('team_roster', 'team_id') }}::integer    as team_id,
    {{ json_text('team_roster', 'team_abbrev') }}::string as team_abbrev,
    {{ json_get('team_roster', 'lineup') }}               as lineup
from team_rosters
