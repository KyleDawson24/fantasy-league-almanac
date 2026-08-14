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
--
-- TEAM IDENTITY COMES FROM THE IDENTITY FEED, NOT FROM THIS PAYLOAD (MLB-243).
--
-- The type-5 mRoster response does not carry team labels -- no `location`, no
-- `nickname`, no `abbrev` -- so the extract's display-name helper fell through
-- to its last resort and wrote "Team 1" with abbrev "1" for every team. Those
-- placeholders then flowed into int_franchise_registry (which reads observed
-- names off the box scores) and became the franchise's canonical name, so the
-- first stranger rehearsal published a workbook of numbered teams over a league
-- whose real names ESPN had served all along.
--
-- It served them on a DIFFERENT feed. mTeam -- the adapter contract's F8, "team
-- & owner identity" -- lands in RAW.TEAM_STANDINGS and flattens to
-- stg_team_standings at exactly the grain identity wants: one row per
-- (league_key, season_year, team_id), present for every team whether or not it
-- has an owner. So identity is read from the identity feed and the roster
-- payload's label is kept only as the fallback.
--
-- WHY THE JOIN IS SCOPED TO THIS MODEL rather than applied in the registry.
-- Doing it there would re-key EVERY league's franchise names, including the H2H
-- ones whose box scores already carry real labels -- and where the two feeds
-- disagree (the undiagnosed 2025 team-7 '####'-vs-'CPU Team 1' drift) that
-- would move published output for reasons nobody has diagnosed yet. This model
-- produces rows ONLY for season-long points leagues, so the repair reaches
-- exactly the feed that lost the data and no other league's names can move.
--
-- Keyed on team_id, never on the display string: a team that renames is the
-- same franchise, and two teams may legitimately share an abbrev -- the
-- rehearsal league has a duplicate pair.

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
),

flattened as (
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
),

-- F8: the platform's own team record, one row per (league, season, team).
identity as (
    select
        league_key,
        season_year,
        team_id,
        nullif(trim(team_name), '')   as team_name,
        nullif(trim(team_abbrev), '') as team_abbrev
    from {{ ref('stg_team_standings') }}
)

select
    f.league_key,
    f.season_year,
    f.scoring_period,
    f.matchup_period,
    f.owner_name,
    -- The identity feed wins; the roster payload's label is the fallback for
    -- a season captured before mTeam was, and only then.
    coalesce(i.team_name, f.team_name)     as team_name,
    f.team_id,
    coalesce(i.team_abbrev, f.team_abbrev) as team_abbrev,
    f.lineup
from flattened f
left join identity i
    on  f.league_key  = i.league_key
    and f.season_year = i.season_year
    and f.team_id     = i.team_id
