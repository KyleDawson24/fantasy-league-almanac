-- mart_franchise_rivalry.sql
-- The Rivalry Matrix contract (MLB-229). One row per ORDERED pair of team
-- IDENTITIES, carrying two independent ledgers of how the row team has fared
-- against the opponent: head-to-head MATCHUPS, and completed SEASONS on total
-- points. Renamed from mart_franchise_head_to_head, which held only the first.
--
-- LONG, NOT WIDE. The consumer surface is a matrix, but a matrix is a RENDER.
-- A wide table needs one column per team, so its schema changes whenever a
-- league gains or loses one -- dynamic SQL, a contract nothing can join to,
-- and no way to document or test a column that may not exist next week.
-- Pivoting and densifying belong to the renderer (almanac_logic).
--
-- ==========================================================================
-- IDENTITY: WHAT COUNTS AS ONE TEAM
-- ==========================================================================
-- Platform ids identify SOURCE RECORDS and are never rewritten. Aggregation
-- keys on dim_franchise_identity.identity_key, which is the explicitly
-- configured canonical name where the league gives one -- collapsing separate
-- platform ids AND separate canonical_franchise_ids into one team -- and the
-- canonical franchise id where it does not. Historical names and re-minted ids
-- roll into the configured identity; two unconfigured teams whose observed
-- names merely coincide stay apart. That asymmetry is the whole ruling, and
-- the reason nothing here groups on dim_franchise.canonical_name, which
-- coalesces the two cases into one string.
--
-- Resolution is SEASON-AWARE: each side of each meeting resolves through the
-- identity of the season it was played in, so a season the lineage seed parks
-- on the holding pen leaves the ledger while the franchise's other seasons
-- stay its own.
--
-- ==========================================================================
-- LEDGER ONE: MATCHUPS
-- ==========================================================================
-- A meeting counts when it has an opponent, the sides are different platform
-- teams, both carry a platform score, AND ITS MATCHUP PERIOD IS CLOSED.
--
-- CLOSED IS NOT "HAS TWO SCORES". A matchup in flight already carries running
-- totals on both sides, so scores alone counted a Tuesday as a win.
-- int_matchup_period_evidence.is_closed is the real signal: a period strictly
-- below the current one, or the final period of a season ESPN has finished
-- with (proven shape, membership reaching finalScoringPeriod). Playoff and
-- abnormal-length periods are closed like any other, so both stay in.
--
-- THE GATE APPLIES IN TWO LAYERS, AND IT FAILS CLOSED IN BOTH:
--
--   * A season the schedule capture REACHED must prove each period closed,
--     individually. Nothing else can rescue a period the pointer has not
--     passed.
--   * A season the capture never reached is retained only where the season
--     itself is independently proven finished (int_league_season_closure:
--     delivered final ranks, parsed final standings, or supersession by a
--     later season). An unproven season mints nothing.
--
-- The second layer is a correction, not a refinement. Treating "no capture" as
-- "historical, keep everything" meant a league that had never run the schedule
-- extract counted its live season's running scores as results -- the exact bug
-- the closure gate was added to remove, reintroduced one level up. Absence of
-- evidence is not evidence of completion.
--
-- CAPTURE PRESENCE COMES FROM THE CLOSURE MODEL, which reads
-- stg_matchup_schedule directly rather than the derived period evidence. A
-- capture that exists but is malformed produces zero evidence rows, and a gate
-- keying on the evidence would read that as "never captured" and fail OPEN on
-- precisely the season whose payload could not be understood.
--
-- assert_rivalry_matchups_have_closure_evidence is the tripwire: it fails if
-- any counted meeting sits in a season that is neither captured-and-closed nor
-- independently proven complete.
--
-- BOTH SCORES ARE STILL REQUIRED, for a different reason: the fact derives
-- `result` as W / L / else 'T', so a NULL score would enter a rivalry record
-- as a TIE. It also makes the filter SYMMETRIC -- one team's platform_points
-- is the other's opponent_points -- which is where reciprocity comes from.
--
-- Points for/against are platform_points, the same lens `result` is derived
-- from, so points can never contradict the record.
--
-- ==========================================================================
-- LEDGER TWO: COMPLETED SEASONS ON POINTS
-- ==========================================================================
-- One completed season = one win, loss or tie on RAW total points. No margin
-- weighting: outscoring a team by 1 and by 1,000 are both one win.
--
-- NO NORMALISATION FOR EXPOSURE, deliberately (Kyle's ruling). A team that
-- played fewer weeks scored fewer points, and that is part of what happened in
-- the season rather than a distortion to correct.
--
-- ONLY SEASONS BOTH TEAMS PLAYED. The pairwise join is an INNER join on
-- season, so a season one side sat out produces no verdict in either
-- direction -- an absent team neither outscored anyone nor was outscored.
-- Every completed season both played is compared; none is otherwise excluded.
--
-- TOTALS ARE THE PLATFORM'S OWN (int_franchise_season_points), not our summed
-- lens, and the identity's ids are summed BEFORE the comparison so a team that
-- fielded two platform ids in one season is compared once, on its whole
-- output.
--
-- ==========================================================================
-- SHARED RULES
-- ==========================================================================
-- The holding pen is excluded on BOTH sides of both ledgers, before
-- aggregation -- it is not a team (MLB-115), and dropping it after the fact
-- would leave an opponent holding a win nobody lost. Self-pairs are removed
-- AFTER identity resolution, which is the only place two platform ids
-- collapsing onto one team is visible; the matrix diagonal is therefore
-- ABSENT rather than 0-0, and a renderer draws it blank.
--
-- A pair appears once either ledger has something to say about it, and the
-- other ledger reads zero rather than NULL -- two teams that met in a matchup
-- but never completed a shared season are 0-0-0 on seasons, which is true.
-- Win percentages are NULL at zero meetings, because undefined is not 0.500.
--
-- ACTIVITY IS NOT APPLIED HERE. Every team that ever played is present;
-- mart_franchise_rivalry_axes decides which ones a matrix draws.
--
-- ==========================================================================
-- GRAIN: one row per (league_key, row_identity_key, opponent_identity_key).
-- ==========================================================================
-- Materialization: table. Float score sums (MLB-128 discipline), and a view
-- would re-sum them in a nondeterministic order on every read.

{{ config(materialized='table') }}

with identity as (
    select
        league_key,
        franchise_id,
        season_year,
        canonical_franchise_id,
        identity_key,
        identity_source,
        identity_name,
        identity_abbrev
    from {{ ref('dim_franchise_identity') }}
),

-- One row per identity: the labels a matrix axis and a cell tooltip display.
-- identity_name is already constant per key (resolved once in the dim), so
-- this is a dedupe rather than a vote.
identity_display as (
    select distinct
        league_key,
        identity_key,
        identity_source,
        identity_name,
        identity_abbrev
    from identity
),

-- ---------------------------------------------------------------------
-- Ledger one: matchups
-- ---------------------------------------------------------------------
period_evidence as (
    select
        league_key,
        season_year,
        matchup_period,
        is_closed
    from {{ ref('int_matchup_period_evidence') }}
),

-- Capture presence and season-level completion, from the model that reads the
-- snapshot table rather than the derived evidence -- see the header.
season_closure as (
    select
        league_key,
        season_year,
        has_schedule_capture,
        is_season_complete
    from {{ ref('int_league_season_closure') }}
),

meetings as (
    select
        m.league_key,
        m.season_year,
        m.team_id,
        m.opponent_id,
        m.platform_points,
        m.opponent_points,
        m.result
    from {{ ref('mart_team_matchup') }} m
    -- INNER: a season the closure model cannot speak about has no completion
    -- verdict, and a missing verdict is not permission.
    join season_closure sc
        on m.league_key = sc.league_key
        and m.season_year = sc.season_year
    left join period_evidence pe
        on m.league_key = pe.league_key
        and m.season_year = pe.season_year
        and m.matchup_period = pe.matchup_period
    where m.opponent_id is not null
      and m.team_id <> m.opponent_id
      and m.platform_points is not null
      and m.opponent_points is not null
      and m.result is not null
      and (
            case
                -- Captured season: the PERIOD must be proven closed. A period
                -- the evidence has no row for is not closed, it is unknown --
                -- which is what makes a malformed payload fail closed.
                when sc.has_schedule_capture then coalesce(pe.is_closed, false)
                -- Uncaptured season: retained only where the SEASON is
                -- independently proven finished.
                else sc.is_season_complete
            end
          )
),

meetings_resolved as (
    select
        m.league_key,
        m.season_year,
        row_i.identity_key            as row_identity_key,
        opp_i.identity_key            as opponent_identity_key,
        row_i.canonical_franchise_id  as row_canonical_franchise_id,
        opp_i.canonical_franchise_id  as opponent_canonical_franchise_id,
        m.platform_points,
        m.opponent_points,
        m.result
    from meetings m
    join identity row_i
        on m.league_key = row_i.league_key
        and cast(m.team_id as varchar) = row_i.franchise_id
        and m.season_year = row_i.season_year
    join identity opp_i
        on m.league_key = opp_i.league_key
        and cast(m.opponent_id as varchar) = opp_i.franchise_id
        and m.season_year = opp_i.season_year
),

meetings_countable as (
    select *
    from meetings_resolved
    where row_identity_key <> opponent_identity_key
      and row_canonical_franchise_id
          <> '{{ var("holding_pen_franchise_id") }}'
      and opponent_canonical_franchise_id
          <> '{{ var("holding_pen_franchise_id") }}'
),

matchup_ledger as (
    select
        league_key,
        row_identity_key,
        opponent_identity_key,

        count(*) as matchup_meetings,
        sum(case when result = 'W' then 1 else 0 end) as matchup_wins,
        sum(case when result = 'L' then 1 else 0 end) as matchup_losses,
        sum(case when result = 'T' then 1 else 0 end) as matchup_ties,

        -- Exact-decimal summation (MLB-128): float addition is not
        -- associative, so a plain sum can move between rebuilds.
        {{ stable_sum("platform_points") }} as points_for,
        {{ stable_sum("opponent_points") }} as points_against,

        -- Differenced IN DECIMAL rather than from the cast-back doubles: both
        -- inputs are exact at this scale, and margin is the column a
        -- reciprocity test compares against its own negation.
        cast(round(
            sum(cast(platform_points as decimal(18, 6)))
            - sum(cast(opponent_points as decimal(18, 6)))
        , 1) as double) as points_margin,

        min(season_year) as first_meeting_season,
        max(season_year) as last_meeting_season

    from meetings_countable
    group by 1, 2, 3
),

-- ---------------------------------------------------------------------
-- Ledger two: completed seasons on points
-- ---------------------------------------------------------------------
season_points as (
    select
        sp.league_key,
        sp.season_year,
        i.identity_key,
        i.canonical_franchise_id,
        sp.season_points
    from {{ ref('int_franchise_season_points') }} sp
    join identity i
        on sp.league_key = i.league_key
        and sp.franchise_id = i.franchise_id
        and sp.season_year = i.season_year
    where sp.is_season_complete
),

-- Identity-grain season totals. The SUM is the ruling: an identity fielding
-- two platform ids in one season is one team, so it is compared once on the
-- points all of its ids scored.
season_by_identity as (
    select
        league_key,
        season_year,
        identity_key,
        {{ stable_sum("season_points", none) }} as season_points
    from season_points
    where canonical_franchise_id <> '{{ var("holding_pen_franchise_id") }}'
    group by 1, 2, 3
),

-- INNER join on season: only seasons BOTH teams played produce a verdict. A
-- season one side sat out is not a win for the other -- an absent team neither
-- outscored anyone nor was outscored.
season_pairs as (
    select
        a.league_key,
        a.season_year,
        a.identity_key as row_identity_key,
        b.identity_key as opponent_identity_key,
        -- Compared in exact decimal so "equal totals" is a defined condition
        -- rather than a question about the last bit of two doubles.
        cast(a.season_points as decimal(18, 6)) as row_points,
        cast(b.season_points as decimal(18, 6)) as opponent_points
    from season_by_identity a
    join season_by_identity b
        on a.league_key = b.league_key
        and a.season_year = b.season_year
        and a.identity_key <> b.identity_key
),

season_ledger as (
    select
        league_key,
        row_identity_key,
        opponent_identity_key,

        count(*) as season_meetings,
        -- Raw totals, no margin weighting: one season outscored is one win.
        sum(case when row_points > opponent_points then 1 else 0 end) as season_wins,
        sum(case when row_points < opponent_points then 1 else 0 end) as season_losses,
        sum(case when row_points = opponent_points then 1 else 0 end) as season_ties,

        cast(round(sum(row_points), 1) as double)      as season_points_for,
        cast(round(sum(opponent_points), 1) as double) as season_points_against,

        min(season_year) as first_season_compared,
        max(season_year) as last_season_compared

    from season_pairs
    group by 1, 2, 3
),

-- A pair appears once EITHER ledger has something to say. The keys are
-- coalesced because a pair may exist on one side only -- two teams that met in
-- matchups but never completed a shared season, or the reverse.
pairs as (
    select
        coalesce(m.league_key, s.league_key)                     as league_key,
        coalesce(m.row_identity_key, s.row_identity_key)         as row_identity_key,
        coalesce(m.opponent_identity_key, s.opponent_identity_key)
            as opponent_identity_key,

        coalesce(m.matchup_meetings, 0) as matchup_meetings,
        coalesce(m.matchup_wins, 0)     as matchup_wins,
        coalesce(m.matchup_losses, 0)   as matchup_losses,
        coalesce(m.matchup_ties, 0)     as matchup_ties,
        coalesce(m.points_for, 0.0)     as points_for,
        coalesce(m.points_against, 0.0) as points_against,
        coalesce(m.points_margin, 0.0)  as points_margin,
        m.first_meeting_season,
        m.last_meeting_season,

        coalesce(s.season_meetings, 0)       as season_meetings,
        coalesce(s.season_wins, 0)           as season_wins,
        coalesce(s.season_losses, 0)         as season_losses,
        coalesce(s.season_ties, 0)           as season_ties,
        coalesce(s.season_points_for, 0.0)   as season_points_for,
        coalesce(s.season_points_against, 0.0) as season_points_against,
        s.first_season_compared,
        s.last_season_compared

    from matchup_ledger m
    full outer join season_ledger s
        on m.league_key = s.league_key
        and m.row_identity_key = s.row_identity_key
        and m.opponent_identity_key = s.opponent_identity_key
)

select
    p.league_key,
    p.row_identity_key,
    p.opponent_identity_key,

    row_d.identity_name    as row_team_name,
    row_d.identity_abbrev  as row_team_abbrev,
    row_d.identity_source  as row_identity_source,
    opp_d.identity_name    as opponent_team_name,
    opp_d.identity_abbrev  as opponent_team_abbrev,
    opp_d.identity_source  as opponent_identity_source,

    p.matchup_meetings,
    p.matchup_wins,
    p.matchup_losses,
    p.matchup_ties,
    p.points_for,
    p.points_against,
    p.points_margin,
    -- The project's own winning percentage (a tie is half a win), matching the
    -- all-time ordering in output/almanac_data.py. NULL at zero meetings:
    -- undefined is not 0.500. Unrounded -- display precision is the renderer's.
    case when p.matchup_meetings > 0
         then (p.matchup_wins + 0.5 * p.matchup_ties) / p.matchup_meetings
    end as matchup_win_pct,
    p.first_meeting_season,
    p.last_meeting_season,

    p.season_meetings,
    p.season_wins,
    p.season_losses,
    p.season_ties,
    p.season_points_for,
    p.season_points_against,
    case when p.season_meetings > 0
         then (p.season_wins + 0.5 * p.season_ties) / p.season_meetings
    end as season_win_pct,
    p.first_season_compared,
    p.last_season_compared

from pairs p
join identity_display row_d
    on p.league_key = row_d.league_key
    and p.row_identity_key = row_d.identity_key
join identity_display opp_d
    on p.league_key = opp_d.league_key
    and p.opponent_identity_key = opp_d.identity_key
