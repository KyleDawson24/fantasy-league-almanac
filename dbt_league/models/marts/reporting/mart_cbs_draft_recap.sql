-- mart_cbs_draft_recap.sql
-- The CBS Draft Recap tab's consumer row: every assembled pick, priced at
-- the season the player was drafted into (MLB-90 L2). This is the SQL port
-- of attach_values() from output/cbs_draft_recap_data.py, and retiring that
-- stopgap is the ticket -- it was the last output-layer read of parsed
-- NDJSON off disk AND of an intermediate model.
--
-- ==========================================================================
-- GRAIN: one row per (league_key, assembly_seq) -- i.e. per pick, in the
-- order the assembly emits them. Unchanged from int_cbs__draft_picks; this
-- model only attaches value.
-- ==========================================================================
--
-- WHY A CBS BRANCH RATHER THAN A UNION INTO mart_draft_board
--
-- The one-builder-for-all-leagues architecture is the north star, but the
-- union is not free here and buys nothing yet: CBS never joined the ESPN
-- chain (it has a parallel builder, build_draft_recap_rows), and
-- mart_draft_board's grain test is
-- unique_combination_of_columns(league_key, season_year, overall_pick) --
-- which NULLs collapse into one group, and overall_pick is NULL on 9 of 13
-- CBS seasons. Unioning today would put the ESPN book's golden at risk to
-- deliver no product change. Convergence gets its own ticket; the column
-- names below stay deliberately close to mart_draft_board's contract
-- (season_year / round_num / round_pick / overall_pick) so it starts from
-- an easy diff.
--
-- The names that do NOT match ESPN's are the ones where matching would lie:
-- team_name_raw is the string PRINTED on the page, not a resolved franchise
-- label like ESPN's team_name; player_cbs_id is a varchar because the
-- two-way split pseudo-ids are ids in their own right; and the calc_* lenses
-- are CBS's calculated-points family where ESPN carries a single
-- season_points.
--
-- VALUE RESOLUTION -- the part that has to be exact
--
-- Ported behaviour-for-behaviour from attach_values(), because the gate is
-- the CBS almanac golden and any drift is a bug until proven otherwise:
--
--   * A pick naming a SPLIT half ('... (Batter)') is credited that half's
--     points only -- the id the page carries IS the split pseudo-id.
--   * A pick naming a UNIFIED player sums every CBS id its mlbam maps to
--     that season. That is one id for everybody except two-way players, and
--     both halves for the Ohtani class, whose production hides under the
--     pseudo-ids. Where discipline-scoped ids exist the unified id is
--     EXCLUDED, so a unified row that also carries points cannot double
--     count a half.
--   * A page id is authoritative identity, so an ambiguous NAME never blocks
--     it; ambiguity only bites name-only resolution, and an ambiguous
--     name-only pick is flagged and left unvalued, never guessed.
--   * A pick that resolves to somebody who simply did not play is a genuine
--     ZERO (the never-played pick, matching mart_draft_board's COALESCE-0
--     precedent). A pick that resolves to nobody at all stays NULL and says
--     so. The two are different answers and the resolution column keeps
--     them apart.
--
-- The identity join mirrors the stopgap exactly, including that it scopes on
-- platform = 'cbs' rather than on league_key: dim_player_identity is keyed
-- by platform, and a second CBS league would need that revisited (MLB-129 is
-- the universal-crosswalk ticket where it belongs).
--
-- SUMS ARE EXACT DECIMAL, NOT FLOAT, ON PURPOSE. Fixed-scale decimal
-- addition is exact and therefore order-independent, so the two-way sum
-- cannot drift with row order the way a float SUM can (MLB-128's class).
-- Today every CBS calculated total happens to be a whole number, which hides
-- the issue -- but that is this league's scoring settings, not a property of
-- the chain, and the ESPN book is already fractional on 5,276 of 5,857
-- rows. 6dp is carried so nothing rounds here: round(value, 1) in the
-- renderer stays the ONLY rounding step (MLB-121/123).

{{ config(materialized='view') }}

{% set first_draft_season = 2011 %}
{% set last_draft_season = 2026 %}

with picks as (

    select * from {{ ref('int_cbs__draft_picks') }}

),

-- (cbs_player_id, season) -> the calculated lenses. Exact decimal from here
-- down; see the header.
season_points as (

    select
        league_key,
        cbs_player_id,
        season_year,
        max(case when stat_name = 'CALCULATED_POINTS'
                 then cast(stat_value as decimal(18, 6)) end) as calc_total,
        max(case when stat_name = 'CALCULATED_HITTING_PTS'
                 then cast(stat_value as decimal(18, 6)) end) as calc_hitting,
        max(case when stat_name = 'CALCULATED_PITCHING_PTS'
                 then cast(stat_value as decimal(18, 6)) end) as calc_pitching
    from {{ ref('int_cbs__player_season_stats') }}
    where season_year between {{ first_draft_season }} and {{ last_draft_season }}
      and stat_name in ('CALCULATED_POINTS', 'CALCULATED_HITTING_PTS',
                        'CALCULATED_PITCHING_PTS')
    group by league_key, cbs_player_id, season_year

),

identity as (

    select
        name_key,
        season_year,
        platform_player_id,
        mlbam_id,
        is_ambiguous,
        stat_group_scope
    from {{ ref('dim_player_identity') }}
    where platform = 'cbs'
      and season_year between {{ first_draft_season }} and {{ last_draft_season }}

),

-- The mlbam inversion: every CBS id a name form resolved to that season,
-- which is exactly the set of split pseudo-ids a two-way player's production
-- hides under. Discipline-scoped ids win where they exist.
mlbam_members as (

    select
        mlbam_id,
        season_year,
        platform_player_id,
        stat_group_scope,
        max(case when stat_group_scope is not null then 1 else 0 end) over (
            partition by mlbam_id, season_year
        ) as has_scoped
    from identity
    where mlbam_id is not null
      and platform_player_id is not null

),

mlbam_value_ids as (

    select distinct mlbam_id, season_year, platform_player_id
    from mlbam_members
    where (has_scoped = 1 and stat_group_scope is not null)
       or  has_scoped = 0

),

-- What a resolvable mlbam is worth that season: the sum across its ids that
-- actually carry points, plus how many did (n_ids > 1 IS the two-way sum).
mlbam_points as (

    select
        v.mlbam_id,
        v.season_year,
        sp.league_key,
        -- coalesce INSIDE the sum: a row that carries a hitting total but no
        -- CALCULATED_POINTS contributes 0, it does not null the sum. Matches
        -- _sum_points' `float(r[...] or 0)` per row.
        sum(coalesce(sp.calc_total, 0))    as calc_total,
        sum(coalesce(sp.calc_hitting, 0))  as calc_hitting,
        sum(coalesce(sp.calc_pitching, 0)) as calc_pitching,
        count(*)                           as n_ids
    from mlbam_value_ids v
    join season_points sp
      on  sp.cbs_player_id = v.platform_player_id
      and sp.season_year   = v.season_year
    group by v.mlbam_id, v.season_year, sp.league_key

),

resolved as (

    select
        p.*,
        {{ cbs_name_key('p.player_name_raw') }} as name_key,

        -- A '(Batter)'/'(Pitcher)' marker survives cbs_name_key by design.
        regexp_like({{ cbs_name_key('p.player_name_raw') }},
                    '.*\\((batter|pitcher)\\)') as is_split,

        i.name_key is not null                  as has_ident,
        coalesce(i.is_ambiguous, false)         as ident_ambiguous,

        -- Only an UNAMBIGUOUS identity may lend its ids to a pick.
        case when i.is_ambiguous then null else i.platform_player_id end as ident_pid,
        case when i.is_ambiguous then null else i.mlbam_id end           as ident_mlbam
    from picks p
    left join identity i
      on  i.name_key    = {{ cbs_name_key('p.player_name_raw') }}
      and i.season_year = p.season_year

),

candidates as (

    select
        r.*,

        -- The page id's own points row.
        pid_pts.calc_total    as pid_total,
        pid_pts.calc_hitting  as pid_hitting,
        pid_pts.calc_pitching as pid_pitching,
        pid_pts.cbs_player_id is not null as has_pid_pts,

        -- The unambiguous identity's mapped id, for a split half whose page
        -- id carries nothing.
        map_pts.calc_total    as map_total,
        map_pts.calc_hitting  as map_hitting,
        map_pts.calc_pitching as map_pitching,
        map_pts.cbs_player_id is not null as has_map_pts,

        -- The mlbam spine's summed production.
        mp.calc_total    as mlbam_total,
        mp.calc_hitting  as mlbam_hitting,
        mp.calc_pitching as mlbam_pitching,
        mp.n_ids         as mlbam_n_ids,
        mp.mlbam_id is not null as has_mlbam_pts
    from resolved r
    left join season_points pid_pts
      on  pid_pts.league_key    = r.league_key
      and pid_pts.cbs_player_id = r.player_cbs_id
      and pid_pts.season_year   = r.season_year
    left join season_points map_pts
      on  map_pts.league_key    = r.league_key
      and map_pts.cbs_player_id = r.ident_pid
      and map_pts.season_year   = r.season_year
    left join mlbam_points mp
      on  mp.league_key  = r.league_key
      and mp.mlbam_id    = r.ident_mlbam
      and mp.season_year = r.season_year

),

-- The ladder, in the stopgap's order. Split halves take their own half;
-- unified names prefer the mlbam spine (production may hide under pseudo-
-- ids) and fall back to the page id.
priced as (

    select
        c.*,
        case
            when c.is_split and c.has_pid_pts                       then 'id'
            when c.is_split and c.ident_pid is not null
                 and c.has_map_pts                                  then 'name'
            when not c.is_split and c.has_mlbam_pts
                 then case when c.has_pid_pts then 'id' else 'name' end
            when not c.is_split and c.has_pid_pts                   then 'id'
            when not c.is_split and c.player_cbs_id is null
                 and c.has_ident and c.ident_ambiguous              then 'ambiguous'
        end as resolution_step,

        -- Whether a value was FOUND, which is not the same question as
        -- whether it summed to something: _sum_points returns a dict as soon
        -- as a points ROW exists, coalescing missing components to 0. Keying
        -- the branches on the row's existence keeps a player who has hitting
        -- points but no CALCULATED_POINTS out of the zero_fill bucket.
        case
            when c.is_split and c.has_pid_pts                       then true
            when c.is_split and c.ident_pid is not null
                 and c.has_map_pts                                  then true
            when not c.is_split and c.has_mlbam_pts                 then true
            when not c.is_split and c.has_pid_pts                   then true
            else false
        end as has_value,

        case
            when c.is_split and c.has_pid_pts                  then coalesce(c.pid_total, 0)
            when c.is_split and c.ident_pid is not null
                 and c.has_map_pts                             then coalesce(c.map_total, 0)
            when not c.is_split and c.has_mlbam_pts            then coalesce(c.mlbam_total, 0)
            when not c.is_split and c.has_pid_pts              then coalesce(c.pid_total, 0)
        end as value_total,

        case
            when c.is_split and c.has_pid_pts                  then coalesce(c.pid_hitting, 0)
            when c.is_split and c.ident_pid is not null
                 and c.has_map_pts                             then coalesce(c.map_hitting, 0)
            when not c.is_split and c.has_mlbam_pts            then coalesce(c.mlbam_hitting, 0)
            when not c.is_split and c.has_pid_pts              then coalesce(c.pid_hitting, 0)
        end as value_hitting,

        case
            when c.is_split and c.has_pid_pts                  then coalesce(c.pid_pitching, 0)
            when c.is_split and c.ident_pid is not null
                 and c.has_map_pts                             then coalesce(c.map_pitching, 0)
            when not c.is_split and c.has_mlbam_pts            then coalesce(c.mlbam_pitching, 0)
            when not c.is_split and c.has_pid_pts              then coalesce(c.pid_pitching, 0)
        end as value_pitching,

        case
            when c.is_split and c.has_pid_pts                  then 1
            when c.is_split and c.ident_pid is not null
                 and c.has_map_pts                             then 1
            when not c.is_split and c.has_mlbam_pts            then c.mlbam_n_ids
            when not c.is_split and c.has_pid_pts              then 1
        end as value_n_ids
    from candidates c

),

final as (

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

        -- 'id' / 'name' resolved and valued; 'zero_fill' identified but
        -- never played; 'ambiguous' a shared name with no id to break the
        -- tie; 'unresolved' nobody at all. Only the first two carry value
        -- that came from somewhere.
        coalesce(
            resolution_step,
            case when player_cbs_id is not null or has_ident
                 then 'zero_fill' else 'unresolved' end
        ) as resolution,

        -- The zero is only for zero_fill. An 'ambiguous' pick reached a
        -- resolution WITHOUT a value and stays honestly NULL -- crediting it
        -- 0 would assert the shared name produced nothing.
        case
            when has_value then value_total
            when resolution_step is null
                 and (player_cbs_id is not null or has_ident)
                 then cast(0 as decimal(18, 6))
        end as calc_total,

        case
            when has_value then value_hitting
            when resolution_step is null
                 and (player_cbs_id is not null or has_ident)
                 then cast(0 as decimal(18, 6))
        end as calc_hitting,

        case
            when has_value then value_pitching
            when resolution_step is null
                 and (player_cbs_id is not null or has_ident)
                 then cast(0 as decimal(18, 6))
        end as calc_pitching,

        coalesce(has_value and value_n_ids > 1, false) as twoway_sum,

        assembly_seq
    from priced

)

select * from final
