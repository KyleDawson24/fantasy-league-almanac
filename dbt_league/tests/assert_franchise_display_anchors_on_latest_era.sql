-- MLB-113 guard: dim_franchise anchors IDENTITY on the earliest id in a lineage
-- but DISPLAY on the latest-seen one. The two used to be the same anchor, so a
-- regression that re-merged them would look like a no-op in code review while
-- silently showing every returning franchise the name it wore in its oldest era.
-- Nothing else would catch it: the CBS almanac has no byte-diff golden coverage
-- (MLB-110), and only a minority of lineages carry a visible rename.
--
-- Asserts the substantive half of the rule -- displayed name = the seed override
-- if given, else the observed name of the lineage's most-recently-seen member.
-- Lineages whose latest era is tied or absent are skipped: there the model falls
-- through to a deterministic tie-break, which is a guard rather than a rule and
-- is deliberately not pinned here.
with reg as (
    select
        league_key,
        franchise_id,
        observed_name,
        last_observed_season
    from {{ ref('int_franchise_registry') }}
),

override as (
    select
        league_key,
        cast(franchise_id as varchar)               as franchise_id,
        nullif(cast(canonical_name as varchar), '') as canonical_name
    from {{ ref('franchise_lineage') }}
),

-- Every franchise carrying its lineage id and its own recency.
members as (
    select
        d.league_key,
        d.canonical_franchise_id,
        d.franchise_id,
        r.observed_name,
        r.last_observed_season
    from {{ ref('dim_franchise') }} d
    join reg r
        on d.league_key = r.league_key
        and d.franchise_id = r.franchise_id
),

latest_era as (
    select
        league_key,
        canonical_franchise_id,
        max(last_observed_season) as last_season
    from members
    where last_observed_season is not null
    group by league_key, canonical_franchise_id
),

-- The name the latest era wore, plus how many members share that era (a tie
-- means the rule cannot single one out, so those lineages drop out below).
expected as (
    select
        l.league_key,
        l.canonical_franchise_id,
        min(m.observed_name) as expected_name,
        count(*)             as tied_members
    from latest_era l
    join members m
        on m.league_key = l.league_key
        and m.canonical_franchise_id = l.canonical_franchise_id
        and m.last_observed_season = l.last_season
    group by l.league_key, l.canonical_franchise_id
)

select
    d.league_key,
    d.franchise_id,
    d.canonical_franchise_id,
    d.canonical_name,
    x.expected_name
from {{ ref('dim_franchise') }} d
join expected x
    on d.league_key = x.league_key
    and d.canonical_franchise_id = x.canonical_franchise_id
left join override o
    on d.league_key = o.league_key
    and d.franchise_id = o.franchise_id
where x.tied_members = 1
  and d.canonical_name is distinct from coalesce(o.canonical_name, x.expected_name)
