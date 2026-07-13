-- stg_mlb__game_positions.sql
-- Per-game positions played, typed and mapped into the CBS position
-- vocabulary: one row per (player, season, game, raw position). The date
-- half of the eligibility derivation -- season-grain fielding says HOW MANY
-- games a player logged at a position; this model says WHEN the Nth one
-- happened, which the after-achievement semantics need ('eligible from the
-- 10th game this year').
--
-- Source rows come from the same gamelog files as mlb_gamelogs (loader
-- family 'gamepos'): hitting-group games emit their positionsPlayed splits,
-- pitching-group games are constant P. Two sources can emit the same game
-- (a position player's mop-up pitching rides both group files), and one
-- hitting game can emit two rows that are ONE game at the slot vocabulary
-- (LF then CF mid-game = OF twice) -- consumers therefore count DISTINCT
-- game_pk per (player, season, cbs_position), never raw rows.
--
-- Platform-neutral (no league_key), same as the other stg_mlb__ models.

select
    mlbam_id,
    season_year,
    stat_group,
    game_date,
    game_pk,
    game_index,
    position                          as position_raw,
    case
        when position in ('LF', 'CF', 'RF', 'OF') then 'OF'
        when position in ('P', 'SP', 'RP')        then 'P'
        when position in ('C', '1B', '2B', '3B', 'SS', 'DH') then position
    end                               as cbs_position
from {{ source('raw', 'mlb_game_positions') }}
where game_date is not null
