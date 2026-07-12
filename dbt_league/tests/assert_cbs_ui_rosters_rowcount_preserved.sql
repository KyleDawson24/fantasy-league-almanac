-- Singular test: the year-end roster staging preserves EVERY raw anchor
-- row (MLB-55). stg_cbs__ui_rosters resolves franchise identity through
-- an INNER join to the per-season name map -- a team name that fails to
-- resolve would silently drop a whole roster, which is exactly the class
-- of loss the anchors can't afford. Zero rows = pass.

with raw_count as (
    select count(*) as n from {{ source('raw', 'cbs_ui_rosters') }}
),

stg_count as (
    select count(*) as n from {{ ref('stg_cbs__ui_rosters') }}
)

select r.n as raw_rows, s.n as staged_rows,
       'name-map resolution dropped roster rows' as failed_invariant
from raw_count r, stg_count s
where r.n != s.n
