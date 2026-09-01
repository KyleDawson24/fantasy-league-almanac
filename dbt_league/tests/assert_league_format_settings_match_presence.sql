-- Singular test: where a platform STATES its league format and the data
-- SHAPE also implies one, the two must agree (MLB-263, ledger S-34).
--
-- dim_league_format now reads the platform's own setting first and falls
-- back to feed presence. That reordering is only a no-op while the two
-- methods agree -- the moment they diverge, the model's verdict changes and
-- every format-dispatched surface (the Rivalry Matrix above all) changes
-- with it. This test is what makes that divergence stop the build instead of
-- quietly re-verdicting a league.
--
-- Rows where either side is silent are NOT failures: settings absent is the
-- ordinary state for a league whose config was never captured, and
-- 'unknown' is the honest presence answer for a league with neither feed.
-- Only a genuine contradiction counts.
--
-- Returns one row per contradicting league; zero rows = pass.

select
    league_key,
    settings_league_format,
    presence_league_format
from {{ ref('dim_league_format') }}
where settings_league_format is not null
  and presence_league_format is not null
  and presence_league_format <> 'unknown'
  and settings_league_format <> presence_league_format
