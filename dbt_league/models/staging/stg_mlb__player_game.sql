-- stg_mlb__player_game.sql
-- The universal baseball layer at game grain, LONG form and canonical-keyed:
-- one row per (mlbam_id, stat_group, game, canonical_key). What a player did,
-- per game, from the MLB Stats API -- platform-neutral (no league_key), the
-- stats spine every league's fantasy layer joins onto (MLB-70 pivot).
--
-- Each gamelog row's game:stat VARIANT is unpivoted and mapped
-- statsapi_key -> canonical_key via the mlb_stat_map seed, keyed on
-- (statsapi_key, stat_group) because the API reuses key names across
-- disciplines with different meanings (pitching hits = hits allowed,
-- pitching baseOnBalls = walks issued, pitching strikeOuts = the scored K).
-- Non-'mapped' dispositions drop at the INNER JOIN: feed-computed rate
-- strings (avg/era/whip...), the inningsPitched decimal-thirds notation
-- (outs is the integer primitive), the display summary, the duplicate
-- pitching hitByPitch, and counting keys with no canonical counterpart.
--
-- This model speaks canonical_key -- NOT the ESPN stat_name vocabulary --
-- because the universal layer must carry stats the ESPN bridge doesn't
-- (inherited_runners / inherited_runners_scored feed the IRSTR derivation;
-- at_bats_against, batters_faced...). Consumers that need ESPN vocabulary
-- bridge via dim_stat.canonical_key.
--
-- Grain notes:
--   - game_pk is the MLB game id; game_number distinguishes doubleheader
--     legs (both legs share a date, not a game_pk).
--   - One known feed quirk: a single (mlbam_id, stat_group, game_pk) pair
--     appears twice (643376, hitting, 2024-06-26 -- two partial lines of one
--     game, 0-for-0 + 4-AB). Rows land verbatim; game_index keeps them
--     distinct and additive stat sums stay correct.
--   - gamesPlayed is 1 on every gamelog row of BOTH groups: sum it
--     group-scoped downstream or a pre-universal-DH pitcher's batting rows
--     inflate a cross-group games count.

with gamelogs as (
    select
        mlbam_id,
        season_year,
        stat_group,
        game_date,
        game_index,
        game
    from {{ source('raw', 'mlb_gamelogs') }}
),

stat_rows as (
    select
        g.mlbam_id,
        g.season_year,
        g.stat_group,
        g.game_date,
        g.game_index,
        {{ json_text('g.game', 'game', 'gamePk') }}::integer         as game_pk,
        {{ json_text('g.game', 'game', 'gameNumber') }}::integer     as game_number,
        {{ json_text('g.game', 'team', 'id') }}::integer             as team_id,
        {{ json_text('g.game', 'team', 'name') }}::string            as team_name,
        {{ json_text('g.game', 'opponent', 'id') }}::integer         as opponent_team_id,
        {{ json_text('g.game', 'opponent', 'name') }}::string        as opponent_team_name,
        {{ json_text('g.game', 'isHome') }}::boolean              as is_home,
        {{ json_text('g.game', 'isWin') }}::boolean               as is_win,
        {{ streamed_object_key(json_get('g.game', 'stat'), 'kv') }}                              as statsapi_key,
        -- Mapped keys are all integer-typed in the feed; the cast guards
        -- against any stray non-numeric value becoming a silent error.
        {{ try_to_double(streamed_object_value(json_get('g.game', 'stat'), 'kv')) }}     as stat_value
    -- streamed_object_* rather than flatten_object because this flatten is
    -- 76.3M rows: no correlated spelling of it materializes on DuckDB even
    -- with a 40 GB spill cap, and the SELECT-list unnests do it in 15s.
    -- The macro header carries the measurements.
    from gamelogs g{{ streamed_object_join(json_get('g.game', 'stat'), 'kv') }}
)

select
    sr.mlbam_id,
    sr.season_year,
    sr.stat_group,
    sr.game_date,
    sr.game_pk,
    sr.game_number,
    sr.game_index,
    sr.team_id,
    sr.team_name,
    sr.opponent_team_id,
    sr.opponent_team_name,
    sr.is_home,
    sr.is_win,
    m.canonical_key,
    m.stat_category,
    sr.stat_value
from stat_rows sr
inner join {{ ref('mlb_stat_map') }} m
    on sr.statsapi_key = m.statsapi_key
    and sr.stat_group = m.stat_group
    and m.disposition = 'mapped'
where sr.stat_value is not null
