-- Singular test: BE and IL must never appear as position codes in
-- int_player_position_pts. They are roster slots, not lineup positions;
-- the model's LATERAL FLATTEN explicitly filters them out of the exploded
-- eligible_slots array. Rows here mean that filter regressed and the
-- optimal-team selector could "deploy" a player to the bench.
--
-- Returns one row per leaked position code; zero rows = pass.
-- (Converted from the hard-fail branch of
-- analyses/check_position_pts_invariants.sql.)

select
    position,
    count(*) as leaked_rows
from {{ ref('int_player_position_pts') }}
where position in ('BE', 'IL')
group by 1
