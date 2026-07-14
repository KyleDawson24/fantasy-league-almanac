-- MLB-81 F1 guard: rekeying the anchor / lineup LEFT JOINs to the mlbam spine
-- could silently FAN OUT (two era name-forms of one player each carrying an
-- anchor row) -- and the one-row-per-game QUALIFY would HIDE the duplicate
-- while corrupting active_weight and flipping attribution_contested true en
-- masse. The fact's unique-grain test can't catch it (it runs after the
-- QUALIFY). This asserts contested stays negligible: genuine contention (two
-- franchises' stints matching one game -- an ambiguous name, a same-day trade)
-- is rare, so > 0.5% signals a fan-out regression, not real ambiguity.
-- (Currently 0.000%.)
select 1
from (
    select
        sum(iff(attribution_contested, 1, 0))::float as contested,
        count(*)                                      as total
    from {{ ref('fct_cbs_player_game_attribution') }}
)
where total > 0 and contested / total > 0.005
