-- stg_cbs__rosters__teams.sql
-- The team grain of a captured CBS daily roster snapshot: one row per
-- (league_key, roster_date, team) carrying the team-level identity fields
-- and that team's player list as a sub-document for stg_cbs__rosters.
--
-- WHY THIS IS ITS OWN MODEL (MLB-10): the same MEMORY boundary as
-- stg_box_scores__matchups, and for the same reason -- see that model's
-- header for the full account. Short version: DuckDB's
-- `cast(json as json[])` materializes where Snowflake's LATERAL FLATTEN
-- streams, and an extracted element keeps its parent document alive, so a
-- nested flatten (teams, then each team's players) has every player row
-- transitively pinning a whole capture payload. Projecting the
-- sub-document first was not enough on its own here; the materialization
-- boundary is what drops the parent.
--
-- The latest-capture-wins arbitration moves here with the outer flatten:
-- the boundary has to sit between the two flattens, and the arbitration
-- precedes both. stg_cbs__rosters therefore reads an already-arbitrated
-- team grain and only flattens players.
--
-- No value moves: same grain, same fields, same latest-load-wins rule.
--
-- Grain: one row per (league_key, roster_date, team).

{{ config(materialized='table') }}

with latest_per_date as (
    select
        league_key,
        season_year,
        roster_date,
        payload
    from {{ source('raw', 'cbs_rosters') }}
    qualify row_number() over (
        partition by league_key, roster_date
        -- MLB-134 -- total order. loaded_at is the WAREHOUSING time: a
        -- backfill stamps many captures with one value, so it is not a total
        -- order on its own (6 such rows exist today on cbs_config). captured_at
        -- is the real recency signal and the one this model means. The hash is
        -- a final backstop that only separates byte-identical payloads.
        order by loaded_at desc, captured_at desc, hash(payload) desc
    ) = 1
),

-- Project the sub-document the flatten consumes; the arbitration above
-- needed the whole payload, this does not.
teams_source as (
    select
        league_key,
        season_year,
        roster_date,
        {{ json_get('payload', 'body', 'rosters', 'teams') }} as teams_json
    from latest_per_date
)

select
    r.league_key,
    r.season_year,
    r.roster_date,
    {{ json_text('t.value', 'id') }}::string        as team_id,
    {{ json_text('t.value', 'name') }}::string      as team_name,
    {{ json_text('t.value', 'abbr') }}::string      as team_abbr,
    {{ json_text('t.value', 'division') }}::string  as division_name,
    {{ json_get('t.value', 'players') }}            as players_json
from teams_source r,
    {{ flatten_array('r.teams_json', 't') }}
