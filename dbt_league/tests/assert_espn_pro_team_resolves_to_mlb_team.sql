-- Singular test: every ESPN player-day that names a club must resolve it to
-- an MLB Stats API team id (MLB-263, ledger S-41).
--
-- ESPN spells clubs its own way (`Ari`, `ChC`, `Wsh`, `StL`); the
-- mlb_team_abbrevs seed is the vocabulary that maps a spelling to the id,
-- and the daily intermediate joins on the upper-cased spelling. The seed
-- covered 30/30 spellings when the column landed. A spelling the seed does
-- not know -- a relocated club, a rebrand, a new platform quirk -- would
-- otherwise slip through as a NULL id and quietly drop the club from every
-- id-keyed surface. This test makes it loud instead.
--
-- Rows with NO club at all (pro_team NULL: the player did not appear that
-- day) are not violations; there is nothing to resolve.
--
-- Returns one row per unresolved (season, spelling); zero rows = pass.

select
    season_year,
    pro_team,
    count(*) as bad_rows
from {{ ref('fct_player_daily_performance') }}
where league_key like 'espn%'
  and pro_team is not null
  and mlb_team_id is null
group by 1, 2
