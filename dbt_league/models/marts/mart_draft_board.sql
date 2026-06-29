-- mart_draft_board.sql
-- v1.2: the draft board joined to season production + drafting-team labels.
-- One wide consumer row per pick: who was drafted, by whom, where, and how
-- much that player produced that season (the draft-value join).
--
-- ==========================================================================
-- GRAIN: one row per (season_year, overall_pick).
-- ==========================================================================
--
-- Per BRAINTHOUGHTS [ARCH] "cross-row / relational derivations go in a thin
-- layer over a fact": the pick facts live in stg_draft; this mart is the
-- relational layer that attaches the drafting team's labels and the player's
-- total season production -- SUM of calculated_points across every
-- team / slot / status row, i.e. the player's full IRL output, which is the
-- natural "did this pick pan out" measure. season_points is COALESCEd to 0
-- for a drafted player who never appears in box scores (never played, or not
-- yet this in-progress season) -- a genuine zero for value / bust ranking.
--
-- Value leaderboards (best value, biggest bust, best keeper) are a consumer
-- presentation computed in the output layer, not here -- this mart just
-- supplies the joined facts.
--
-- Materialization: view. Small (one row per pick); mirrors the other thin
-- mart views (mart_team_matchup, fct_player_season_performance).

{{ config(materialized='view') }}

with picks as (
    select * from {{ ref('stg_draft') }}
),

-- (season, team_id) -> latest team labels, same convention as
-- fct_player_season_performance.latest_team_labels.
team_labels as (
    select
        season_year,
        team_id,
        team_name,
        team_abbrev
    from {{ ref('fct_weekly_player_performance') }}
    where team_id is not null
    qualify row_number() over (
        partition by season_year, team_id
        order by matchup_period desc
    ) = 1
),

-- (season, player_id) -> total season production. SUM across every
-- team / slot / status row = the player's full IRL output that season.
player_season as (
    select
        season_year,
        player_id,
        max(display_name)      as display_name,
        sum(calculated_points) as season_points,
        sum(games_played)      as games_played
    from {{ ref('fct_player_season_performance') }}
    group by 1, 2
)

select
    p.season_year,
    p.overall_pick,
    p.round_num,
    p.round_pick,
    p.keeper,

    p.team_id,
    tl.team_name,
    tl.team_abbrev,
    owner.owner_display,

    p.player_id,
    -- Prefer the nickname-resolved display_name (consistent with the rest of
    -- the almanac); fall back to the ESPN draft name for players with no
    -- performance row.
    coalesce(ps.display_name, p.player_name) as player_name,
    -- The official ESPN draft name, kept apart from the display above so the
    -- almanac can link each pick to the player's Baseball Reference page
    -- (bref's search resolves the official name) while still showing a nickname.
    p.player_name                            as official_player_name,

    -- Round the re-summed total (per-row calculated_points is already
    -- rounded; SUM reintroduces float drift -- same fix as the facts).
    round(coalesce(ps.season_points, 0), 1) as season_points,
    coalesce(ps.games_played, 0)            as games_played
from picks p
left join team_labels tl
    on p.season_year = tl.season_year
    and p.team_id    = tl.team_id
left join {{ ref('int_team_owner_display') }} owner
    on p.season_year = owner.season_year
    and p.team_id    = owner.team_id
left join player_season ps
    on p.season_year = ps.season_year
    and p.player_id  = ps.player_id
