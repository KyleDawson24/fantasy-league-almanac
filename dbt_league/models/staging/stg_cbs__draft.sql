-- CBS draft picks, reshaped from the parsed draft-results pages (MLB-90).
--
-- GRAIN: one row per (league_key, source_path, page_seq) -- i.e. one row per
-- line as it appeared on a captured draft page.
--
-- A PURE RESHAPE, deliberately. RAW holds both the `round` and `team` views
-- of every draft plus playerless order-only rows, and this model keeps all of
-- it: types the columns, nothing else. Choosing which view to trust per
-- season, dropping furniture, marrying the 2024 order skeleton to its player
-- lists, and stitching the 2025/26 two-part drafts are ASSEMBLY decisions --
-- they carry judgement about which evidence is trustworthy, so they live in
-- the intermediate layer, the same split every other CBS UI family uses.
--
-- Two seams the ESPN branch does not have, both real and both left honest
-- here rather than papered over:
--
--   player_cbs_id is NULL across the plain-text early era (2009-2015 pages
--   carry names only). It is VARCHAR, not INTEGER: CBS's two-way pseudo-ids
--   (900 '(Batter)' / 901 '(Pitcher)') are ids in their own right, and the
--   identity seam that resolves them is name/id-aware downstream.
--
--   There is no franchise id on a draft page at all -- only team_name_raw.
--   Resolution runs through the per-season name -> franchise_id map in
--   stg_cbs__ui_standings, which is per-season BY DESIGN (names drift and get
--   reused across franchises), so it cannot be done here without a season
--   scope. Also intermediate's job.

{{ config(materialized='view') }}

with latest_load as (
    select *
    from {{ source('raw', 'cbs_draft') }}
    -- The loader is idempotent by source_path, so a second row for the same
    -- page only exists after a --force reload. Keep the newest.
    qualify row_number() over (
        partition by league_key, source_path, page_seq
        order by loaded_at desc
    ) = 1
)

select
    league_key,
    season_year::integer          as season_year,

    -- Draft identity. draft_key is the stable join key ('2026:2:Mega Draft');
    -- draft_label is the display string; period/period_order separate the
    -- pre-season and in-season drafts a single year can hold.
    draft_key::varchar            as draft_key,
    draft_label::varchar          as draft_label,
    period::varchar               as period,
    period_order::integer         as period_order,

    -- Which rendering of the draft this row came from. 'round' pages carry
    -- the pick sequence; 'team' pages carry per-franchise lists. Most seasons
    -- have both and they do not agree on what is knowable.
    view::varchar                 as view,

    section_seq::integer          as section_seq,
    section_kind::varchar         as section_kind,
    section_label::varchar        as section_label,
    row_seq::integer              as row_seq,
    page_seq::integer             as page_seq,

    -- Pick coordinates AS PRINTED. NULL where the era's pages never recorded
    -- them -- true order exists for 2025-26 only. Not synthesised here: a
    -- fabricated pick number would be indistinguishable downstream from a
    -- real one.
    pick_no::integer              as pick_no,
    round_num::integer            as round_num,
    round_pick::integer           as round_pick,

    team_name_raw::varchar        as team_name_raw,
    player_cbs_id::varchar        as player_cbs_id,
    player_name_raw::varchar      as player_name_raw,
    pos_team_raw::varchar         as pos_team_raw,
    elig_raw::varchar             as elig_raw,
    salary_raw::varchar           as salary_raw,
    elapsed_raw::varchar          as elapsed_raw,
    rank_raw::varchar             as rank_raw,

    -- The page's OWN fantasy-point columns. Kept for reconciliation only --
    -- the record book prices production from the universal stats layer, so
    -- these are evidence about the platform, not a source of truth.
    total_fpts::double             as page_total_fpts,
    active_fpts::double            as page_active_fpts,

    -- True for order-only rows: a pick slot printed with no player attached
    -- (2009's round pages are entirely these). Real rows, not corruption.
    is_playerless::boolean        as is_playerless,

    parsed_at::varchar            as parsed_at,
    source_path::varchar          as source_path,
    loaded_at::timestamp_ntz      as loaded_at
from latest_load
