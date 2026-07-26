-- stg_cbs__standings.sql
-- F7 standings for CBS (adapter #2), the platform-DELIVERED feed: a non-H2H
-- points league has no matchups to derive standings from, so CBS serves its
-- own period-end standings (contract F7, non-H2H required). ESPN derives
-- standings from matchups instead -- these two never converge into one model;
-- F7 is format-conditional by design.
--
-- Grain: one row per (league_key, season_year, period, team_id) -- a team's
-- cumulative season-to-date standing at the close of each scoring period.
-- The `points` are the platform's own season FPTS totals (the standings
-- currency of a points league); movement across periods is the mart's job.
--
-- Source: raw.cbs_standings, one captured file per scoring period
-- (period_NN.json). Each file's payload echoes the period it served and was
-- verified by content at capture (extract/cbs_capture.py). If a period was
-- re-captured, the latest snapshot per (league_key, season_year, period) wins
-- -- captures are append-only, so recency is the tiebreak.

with latest_per_period as (
    select
        league_key,
        season_year,
        period,
        payload
    from {{ source('raw', 'cbs_standings') }}
    qualify row_number() over (
        partition by league_key, season_year, period
        order by loaded_at desc
    ) = 1
),

teams as (
    select
        s.league_key,
        s.season_year,
        s.period,
        d.value:name::string                              as division_name,
        t.value:id::string                                as team_id,
        t.value:name::string                              as team_name,
        t.value:Total:rank::integer                       as standings_rank,
        -- Season points can carry thousands separators in the feed; strip
        -- before casting so a "5,519" never lands as NULL.
        replace(t.value:Total:points::string, ',', '')::double as points
    from latest_per_period s,
        lateral flatten(input => s.payload:body:overall_standings:divisions) d,
        lateral flatten(input => d.value:teams) t
)

select
    league_key,
    season_year,
    period,
    team_id,
    team_name,
    division_name,
    standings_rank,
    points
from teams
