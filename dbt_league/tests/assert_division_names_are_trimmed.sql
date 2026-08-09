-- assert_division_names_are_trimmed.sql
-- ESPN really does store at least one division name with a trailing space
-- (MLB-227).
--
-- A not_null test cannot see this and neither can a uniqueness test: the
-- padded name is non-null and, being the only spelling in the payload, is
-- unique too. It fails later and quietly instead -- as a duplicate-looking
-- label next to the hand-typed trimmed spelling, or as two groups where a
-- reader expects one. stg_divisions trims at the staging boundary; this
-- asserts the trim is actually still there.
--
-- Written against the STAGED column rather than the raw payload on purpose.
-- Testing RAW would assert that ESPN stopped padding the name, which is not
-- something this project controls or wants to be alerted about; testing the
-- staged value asserts the thing that is ours to keep true.
--
-- Any row returned is a failure.

select
    league_key,
    season_year,
    division_id,
    division_name
from {{ ref('stg_divisions') }}
where division_name <> trim(division_name)
   or division_name = ''
