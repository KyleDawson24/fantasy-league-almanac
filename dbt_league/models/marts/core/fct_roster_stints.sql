-- fct_roster_stints.sql
-- MLB-16/17: the platform-neutral roster-stint fact -- one row per contiguous
-- window a player spent on a single fantasy team, tagged with how the window
-- OPENED (the acquisition channel) and how it CLOSED (the departure type).
--
-- ==========================================================================
-- GRAIN: one row per (league_key, season_year, player_id, stint_index).
-- ==========================================================================
--
-- A stint is a maximal run of consecutive rostered scoring periods on the
-- SAME team_id. Roster membership (including bench / IL) is the ground truth
-- for the WHEN -- fct_player_daily_performance carries a row for every
-- rostered player every scoring period (team_id NULL == free agent). The
-- transaction log (stg_transactions) supplies directed TRADE edges, the only
-- thing roster state alone can't tell apart from a same-window drop+add. So:
--
--   open_channel  KEEPER / DRAFT  (stint starts on the opening-day roster,
--                                  reconciled against stg_draft's keeper flag)
--                 TRADE           (arrived directly from another team AND a
--                                  directed trade edge losing_team -> this_team
--                                  exists for the player)
--                 FA_ADD          (arrived from free agency, or a team->team
--                                  handoff NOT backed by a trade == drop+add)
--   close_type    TRADED_AWAY     (left directly to another team AND a directed
--                                  trade edge this_team -> next_team exists)
--                 DROPPED         (left to free agency, or an unbacked handoff)
--                 NULL            (still rostered at the latest loaded period)
--
-- Locked semantics (Per Offline Chat 2026-07-09): most-recent event governs;
-- no channel inheritance across a player's stints; a re-acquisition opens a
-- fresh stint. Contiguity is measured over a per-league dense index of the
-- scoring periods that actually exist in the fact, so an unloaded calendar gap
-- (the All-Star break) does not split a continuous stint, while a real
-- off-roster gap (dropped, then back) does.
--
-- Full season (no playoff filter): an acquisition's production counts whenever
-- it happened. CBS transaction data feeds this same shape once stg_cbs__-
-- transactions lands (RAW.CBS_TRANSACTIONS already declared).

{{ config(materialized='table') }}

-- Membership must be DENSE (a row for every rostered player every scoring
-- period, bench and IL included) or the island detection over-splits. That is
-- stg_box_scores (the raw lineup shell) -- NOT fct_player_daily_performance,
-- whose stat-breakdown path drops zero-stat rostered days. Production (points)
-- reads the daily fact downstream; membership reads the shell here.
with fact as (
    select
        league_key,
        season_year,
        scoring_period,
        team_id,
        player_id
    from {{ ref('stg_box_scores') }}
    -- Only seasons with a transaction log can classify trade vs drop+add
    -- honestly; a season without one would silently label every team->team
    -- move FA_ADD. Scope the fact to leagues/seasons that have transactions
    -- (ESPN: current season only for now -- prior seasons aren't reachable via
    -- leagueHistory's topics filter; they light up if/when a log lands).
    where (league_key, season_year) in (
        select distinct league_key, season_year from {{ ref('stg_transactions') }}
    )
),

-- Contiguous per-league index over the scoring periods present in the fact.
period_index as (
    select
        league_key,
        season_year,
        scoring_period,
        dense_rank() over (
            partition by league_key, season_year
            order by scoring_period
        ) as period_idx
    from (select distinct league_key, season_year, scoring_period from fact)
),

season_bounds as (
    select
        league_key,
        season_year,
        min(period_idx) as first_idx,
        max(period_idx) as last_idx
    from period_index
    group by 1, 2
),

-- Rostered daily membership: one row per (player, period) on a real team.
membership as (
    select distinct
        f.league_key,
        f.season_year,
        f.player_id,
        f.team_id,
        f.scoring_period,
        pi.period_idx
    from fact f
    join period_index pi using (league_key, season_year, scoring_period)
    where f.team_id is not null
),

-- Gaps-and-islands: a new stint begins when the team changes or the period
-- index is not the immediate successor of the previous rostered observation.
flagged as (
    select
        m.*,
        case
            when lag(team_id) over (
                     partition by league_key, season_year, player_id order by period_idx
                 ) = team_id
             and lag(period_idx) over (
                     partition by league_key, season_year, player_id order by period_idx
                 ) = period_idx - 1
            then 0 else 1
        end as is_new_stint
    from membership m
),

numbered as (
    select
        f.*,
        sum(is_new_stint) over (
            partition by league_key, season_year, player_id
            order by period_idx
            rows between unbounded preceding and current row
        ) as stint_index
    from flagged f
),

stints as (
    select
        league_key,
        season_year,
        player_id,
        team_id,
        stint_index,
        min(scoring_period) as stint_start_period,
        max(scoring_period) as stint_end_period,
        min(period_idx)     as stint_start_idx,
        max(period_idx)     as stint_end_idx,
        count(*)            as rostered_days
    from numbered
    group by 1, 2, 3, 4, 5
),

-- Neighbours in the player's own stint sequence (any team), to see whether a
-- stint boundary is a direct team->team handoff (adjacent) or crosses a free-
-- agency gap.
seq as (
    select
        s.*,
        lag(team_id) over (
            partition by league_key, season_year, player_id order by stint_start_idx
        ) as prev_team_id,
        lag(stint_end_idx) over (
            partition by league_key, season_year, player_id order by stint_start_idx
        ) as prev_end_idx,
        lead(team_id) over (
            partition by league_key, season_year, player_id order by stint_start_idx
        ) as next_team_id,
        lead(stint_start_idx) over (
            partition by league_key, season_year, player_id order by stint_start_idx
        ) as next_start_idx
    from stints s
),

-- Directed trade edges (losing team -> acquiring team) for the player. Only
-- the unambiguous 224/244 trade messages carry a clean from->to pair; the 239
-- variant encodes the destination in `for` with a -1 sentinel in `to`, and it
-- is fully redundant here (every real within-horizon trade is already covered
-- by a 224/244 edge -- verified: all 39 TRADE stints are 224/244-backed). So
-- we take the clean signal and don't gamble on the 239 direction guess.
trade_edges as (
    select distinct
        league_key,
        season_year,
        player_id,
        from_team_id,
        to_team_id
    from {{ ref('stg_transactions') }}
    where event_type = 'trade'
      and source_message_type_id in (224, 244)
      and from_team_id is not null
      and to_team_id   is not null
),

-- Draft / keeper attribution, collapsed to one flag per drafted (player, team).
draft as (
    select
        league_key,
        season_year,
        player_id,
        team_id,
        boolor_agg(keeper) as keeper
    from {{ ref('stg_draft') }}
    group by 1, 2, 3, 4
)

select
    seq.league_key,
    seq.season_year,
    seq.player_id,
    seq.stint_index,
    seq.team_id,
    seq.stint_start_period,
    seq.stint_end_period,
    seq.stint_start_idx,
    seq.stint_end_idx,
    seq.rostered_days,
    (seq.stint_start_idx = sb.first_idx) as opened_at_season_start,
    (seq.stint_end_idx   = sb.last_idx)  as open_at_season_end,

    -- OPEN channel: how the player arrived on this team for this stint.
    case
        when seq.stint_start_idx = sb.first_idx then
            case
                when dr.player_id is null then 'FA_ADD'   -- opening roster, undrafted (pre-season add)
                when dr.keeper            then 'KEEPER'
                else 'DRAFT'
            end
        when seq.prev_end_idx = seq.stint_start_idx - 1   -- direct team->team handoff
             and te_in.player_id is not null then 'TRADE'
        else 'FA_ADD'
    end as open_channel,

    -- CLOSE type: how the player left this team (NULL while still rostered).
    case
        when seq.stint_end_idx = sb.last_idx then null
        when seq.next_start_idx = seq.stint_end_idx + 1   -- direct team->team handoff
             and te_out.player_id is not null then 'TRADED_AWAY'
        else 'DROPPED'
    end as close_type

from seq
join season_bounds sb using (league_key, season_year)
left join draft dr
       on dr.league_key  = seq.league_key
      and dr.season_year = seq.season_year
      and dr.player_id   = seq.player_id
      and dr.team_id     = seq.team_id
      and seq.stint_start_idx = sb.first_idx
left join trade_edges te_in
       on te_in.league_key    = seq.league_key
      and te_in.season_year   = seq.season_year
      and te_in.player_id     = seq.player_id
      and te_in.from_team_id  = seq.prev_team_id
      and te_in.to_team_id    = seq.team_id
left join trade_edges te_out
       on te_out.league_key   = seq.league_key
      and te_out.season_year  = seq.season_year
      and te_out.player_id    = seq.player_id
      and te_out.from_team_id = seq.team_id
      and te_out.to_team_id   = seq.next_team_id
