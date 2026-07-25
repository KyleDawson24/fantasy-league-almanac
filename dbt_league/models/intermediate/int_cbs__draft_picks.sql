-- CBS draft picks, ASSEMBLED (MLB-90 L2). The Python port of build_picks()
-- + zip_2024() from output/cbs_draft_recap_data.py, which this replaces.
--
-- ==========================================================================
-- GRAIN: one row per assembled pick -- (league_key, season_year, assembly_seq).
-- ==========================================================================
--
-- stg_cbs__draft is a pure reshape and keeps EVERYTHING RAW holds: both the
-- `round` and `team` renderings of each draft, plus playerless order-only
-- rows. This model makes the judgement calls that turns that into picks:
-- which recording of a season to trust, what furniture to drop, and how to
-- stitch the seasons that ran two drafts. That judgement is data, not code --
-- it lives in the draft_assembly_plan seed, one row per part.
--
-- Four assembly shapes, all driven by the seed's part_role / order_tier:
--
--   part / order_tier 'none'   -- per-team lists only. NO round or pick
--       number is invented: the 'k-th listed player = round k' shortcut is
--       PROVEN for online drafts and DISPROVEN for offline imports (2020:
--       9/240; the lists ride roster order). These years reach order-free
--       surfaces only.
--   part / 'rounds_suspect'    -- rounds recorded but shaped oddly (2020's
--       import holds same-team runs inside one round).
--   part / 'true'              -- round + pick straight off the page. The
--       2025/26 seasons run two real drafts (Mini then Mega, disjoint
--       players) and the Mega's rounds RENUMBER to continue the Mini's.
--   zip_skeleton + zip_players -- 2024 is split-brain: an order skeleton
--       with no players, and per-team player lists with no order. They are
--       married on (team, k). The player side rides roster order, so the
--       marriage is NOT draft order -- hence its own tier, and the renderer
--       keeps it off ordered surfaces.
--
-- The 2020 and 2021 duplicate recordings need no dedupe logic: the plan
-- simply names one part, and the other stays in staging as evidence.
--
-- NO FRANCHISE RESOLUTION HERE, deliberately (Kyle 2026-07-25). Draft pages
-- print a team NAME only, and team_name_raw is what the renderer keys on end
-- to end. Resolving it would mean a season-scoped join against the map in
-- stg_cbs__ui_standings -- correct, but a new consumer of exactly the edge
-- MLB-130 is retiring, and it buys this chain nothing. Do NOT reach for the
-- JSON team blob on the draft pages instead: it is byte-identical on every
-- page (the CURRENT 16 franchises as navigation furniture) and 18 of the
-- league's 34 franchises never appear in it, so name-matching it would
-- silently mis-credit every historical pick whose name was later reused.
--
-- assembly_seq preserves the exact order the Python assembly emitted picks
-- in, and that is load-bearing, not cosmetic: the all-time board's Top Pick
-- is `max(round_rows[rnd], key=lambda p: p[lens])`, and max() with no
-- tiebreak returns the FIRST maximum in list order. Two picks tying on
-- season points would render a different player under a different row
-- order. SQL guarantees none without an order by, so the order is carried
-- as a column.

{{ config(materialized='view') }}

with plan as (

    select
        league_key,
        season_year,
        part_seq,
        draft_key,
        view,
        part_role,
        order_tier
    from {{ ref('draft_assembly_plan') }}

),

staged as (

    select * from {{ ref('stg_cbs__draft') }}

),

-- Each part's rows, already narrowed to real picks. The label is the page's
-- own trailing segment where the draft_key carries one ('2026:2:Mega Draft'
-- -> 'Mega Draft'); the early two-segment keys are all pre-season.
part_rows as (

    select
        p.league_key,
        p.season_year,
        p.part_seq,
        p.order_tier,
        {{ cbs_draft_label('p.draft_key') }} as draft_label,
        d.page_seq,
        d.round_num as page_round_num,
        d.pick_no,
        d.team_name_raw,
        d.player_cbs_id,
        d.player_name_raw,
        d.pos_team_raw,
        d.page_total_fpts,
        d.page_active_fpts
    from plan p
    join staged d
      on  d.league_key = p.league_key
      and d.draft_key  = p.draft_key
      and d.view       = p.view
    where p.part_role = 'part'
      and not d.is_playerless

),

-- The continuation rule: a stitched part's rounds resume where the previous
-- part's highest round left off. Parts that recorded no round contribute 0,
-- so an unordered year's offset stays 0 and is never used.
part_round_offsets as (

    select
        league_key,
        season_year,
        part_seq,
        coalesce(
            sum(max_round) over (
                partition by league_key, season_year
                order by part_seq
                rows between unbounded preceding and 1 preceding
            ), 0
        ) as round_offset
    from (
        select
            league_key,
            season_year,
            part_seq,
            coalesce(max(page_round_num), 0) as max_round
        from part_rows
        group by league_key, season_year, part_seq
    )

),

assembled_parts as (

    select
        r.league_key,
        r.season_year,
        r.draft_label,
        r.order_tier,

        -- Pick coordinates only where the era actually recorded them.
        case when r.order_tier in ('true', 'rounds_suspect')
             then r.page_round_num + o.round_offset end as round_num,
        case when r.order_tier in ('true', 'rounds_suspect')
             then r.pick_no end                         as round_pick,
        case when r.order_tier in ('true', 'rounds_suspect')
             then row_number() over (
                      partition by r.league_key, r.season_year
                      order by r.part_seq, r.page_seq
                  ) end                                 as overall_pick,

        -- Position within this team's list, restarting each part -- exactly
        -- what the Python does (team_seq is rebuilt per part).
        row_number() over (
            partition by r.league_key, r.season_year, r.part_seq, r.team_name_raw
            order by r.page_seq
        ) as list_seq,

        r.team_name_raw,
        r.player_cbs_id,
        r.player_name_raw,
        r.pos_team_raw,
        r.page_total_fpts,
        r.page_active_fpts,

        r.part_seq,
        r.page_seq as in_part_seq
    from part_rows r
    join part_round_offsets o
      on  o.league_key  = r.league_key
      and o.season_year = r.season_year
      and o.part_seq    = r.part_seq

),

-- ---------------------------------------------------------------------
-- The zip: an order skeleton married to per-team player lists on (team, k).
-- ---------------------------------------------------------------------

-- Every printed slot, playerless BY NATURE -- this part is the order, not
-- the players, so the is_playerless filter must NOT be applied to it.
zip_skeleton as (

    select
        p.league_key,
        p.season_year,
        {{ cbs_draft_label('p.draft_key') }} as skeleton_label,
        d.team_name_raw,
        d.round_num,
        d.pick_no,
        row_number() over (
            partition by p.league_key, p.season_year, d.team_name_raw
            order by d.round_num, d.pick_no
        ) as k,
        row_number() over (
            partition by p.league_key, p.season_year
            order by d.round_num, d.pick_no
        ) as slot_seq
    from plan p
    join staged d
      on  d.league_key = p.league_key
      and d.draft_key  = p.draft_key
      and d.view       = p.view
    where p.part_role = 'zip_skeleton'
      and d.pick_no is not null

),

zip_players as (

    select
        p.league_key,
        p.season_year,
        p.order_tier,
        {{ cbs_draft_label('p.draft_key') }} as players_label,
        d.team_name_raw,
        d.player_cbs_id,
        d.player_name_raw,
        d.pos_team_raw,
        d.page_total_fpts,
        d.page_active_fpts,
        row_number() over (
            partition by p.league_key, p.season_year, d.team_name_raw
            order by d.page_seq
        ) as k
    from plan p
    join staged d
      on  d.league_key = p.league_key
      and d.draft_key  = p.draft_key
      and d.view       = p.view
    where p.part_role = 'zip_players'
      and not d.is_playerless

),

-- An inner join drops both leftovers honestly: skeleton slots past the end
-- of a team's list (passed late picks) and any listed player with no slot.
assembled_zip as (

    select
        s.league_key,
        s.season_year,
        s.skeleton_label || ' + ' || z.players_label as draft_label,
        z.order_tier,
        s.round_num,
        s.pick_no as round_pick,
        row_number() over (
            partition by s.league_key, s.season_year
            order by s.slot_seq
        ) as overall_pick,
        z.k as list_seq,
        z.team_name_raw,
        z.player_cbs_id,
        z.player_name_raw,
        z.pos_team_raw,
        z.page_total_fpts,
        z.page_active_fpts,
        1 as part_seq,
        s.slot_seq as in_part_seq
    from zip_skeleton s
    join zip_players z
      on  z.league_key    = s.league_key
      and z.season_year   = s.season_year
      and z.team_name_raw = s.team_name_raw
      and z.k             = s.k

),

combined as (

    select
        league_key, season_year, draft_label, order_tier,
        round_num, round_pick, overall_pick, list_seq,
        team_name_raw, player_cbs_id, player_name_raw, pos_team_raw,
        page_total_fpts, page_active_fpts,
        part_seq, in_part_seq
    from assembled_parts

    union all

    select
        league_key, season_year, draft_label, order_tier,
        round_num, round_pick, overall_pick, list_seq,
        team_name_raw, player_cbs_id, player_name_raw, pos_team_raw,
        page_total_fpts, page_active_fpts,
        part_seq, in_part_seq
    from assembled_zip

)

select
    league_key,
    season_year,
    draft_label,
    order_tier,
    round_num,
    round_pick,
    overall_pick,
    list_seq,
    team_name_raw,
    player_cbs_id,
    player_name_raw,
    pos_team_raw,
    page_total_fpts,
    page_active_fpts,

    -- The emission order the downstream tie-break depends on. See the header.
    row_number() over (
        partition by league_key
        order by season_year, part_seq, in_part_seq
    ) as assembly_seq
from combined
