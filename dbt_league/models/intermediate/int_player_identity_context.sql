-- int_player_identity_context.sql
-- FRANCHISE-CONTEXT identity resolution (MLB-120): the earned second pass for
-- names dim_player_identity leaves ambiguous. "Will Smith 2021" is genuinely
-- two people -- but "Will Smith 2021 AS ROSTERED BY FRANCHISE 28, logged
-- 'P (bullet) KC'" is exactly one, because the franchise's own paperwork
-- carries the discriminators: position and MLB club on every transaction line
-- (player_pos_team_raw) and on every year-end anchor row (primary_pos +
-- mlb_team). Matched against the MLB spine -- the closest thing to an
-- official record (Kyle 2026-07-24): per-season clubs and disciplines from
-- stg_mlb__player_game, fielding positions from stg_mlb__fielding_seasons +
-- stg_mlb__game_positions -- each franchise's sighting of the name resolves
-- independently.
--
-- Grain: (platform, league_key, season_year, franchise_id, name_key) ->
-- mlbam_id, populated ONLY when exactly one candidate survives every
-- evidence type the franchise actually produced. The dim's contract holds:
-- a resolution is EARNED by evidence or not made at all --
--   * club evidence passes on ANY overlap between sighted clubs and the
--     candidate's real clubs that season (mid-season MLB trades legitimately
--     put multiple clubs on one franchise's paperwork);
--   * EVERY distinct sighted position must be consistent -- at POSITION-CLASS
--     grain, not exact position: P-class (P/SP/RP) passes on pitching games;
--     any other sighted position passes when the candidate is a position
--     player (a non-P fielding row, or hitting games where fielding coverage
--     is absent). Exact-position matching was tried and dropped: CBS's listed
--     position is its ELIGIBILITY label ('1B' for the Nats infielder MLB's
--     fielding data logs at 2B), so requiring the exact position eliminated
--     true candidates. The club is the fine discriminator; position only
--     separates pitchers from position players;
--   * no evidence at all -> no resolution (everything survives = nothing
--     earned). Residue stays ambiguous and surfaces for the human seed.
--
-- THE CLUB-CONTRADICTION VETO (is_club_veto): the inverse ruling, for names
-- the season-grain dim RESOLVES to one id that is provably wrong for a
-- specific franchise. Alex Gonzalez 2007: the elder (114924) played no MLB
-- that season, so presence leaves one candidate and the dim hands 136460
-- (CIN all year) to BOTH franchises -- but franchise 11's own paperwork says
-- 'SS KC'. When a franchise's sighted clubs are all KNOWN abbrevs and NO
-- candidate overlaps any of them, every candidate is refuted: that
-- franchise's stint carries no id and must not be reachable by the
-- name-fallback either (its person never took an MLB field that season --
-- the roster-sat class, Jeremi's shape generalized). The veto is CLUB-ONLY
-- and vocabulary-guarded: position strings are too noisy to refute on, and
-- an evidence club the seed doesn't know is a data gap, never a veto.
--
-- The club vocabulary bridge is the mlb_team_abbrevs seed (mlbam team_id ->
-- the CBS abbrev(s) for that club, era variants included: TB/TBD, FLA/MIA,
-- MON/WAS, ANA/LAA, CHW/CWS, LA/LAD, OAK/ATH).

{{ config(materialized='table') }}

with

-- Every name the dim knows, ambiguous or resolved: resolution rows are
-- emitted only for the ambiguous ones; the veto evaluates resolved names too.
dim_names as (
    select platform, name_key, season_year, is_ambiguous
    from {{ ref('dim_player_identity') }}
),

game_presence as (
    select distinct mlbam_id, season_year from {{ ref('stg_mlb__player_game') }}
),

-- Candidates of each ambiguous (name, season), presence-filtered exactly as
-- the dim counts them, decorated with their season discipline facts.
disciplines as (
    select mlbam_id, season_year,
           max({{ iff("stat_group = 'pitching'", '1', '0') }}) as has_pitching,
           max({{ iff("stat_group = 'hitting'", '1', '0') }}) as has_hitting
    from {{ ref('stg_mlb__player_game') }}
    group by 1, 2
),

mlb_positions as (
    select distinct mlbam_id, season_year, cbs_position
    from {{ ref('stg_mlb__fielding_seasons') }}
    union
    select distinct mlbam_id, season_year, cbs_position
    from {{ ref('stg_mlb__game_positions') }}
),

fielding_coverage as (
    select mlbam_id, season_year,
           1                                              as has_fielding,
           max({{ iff("cbs_position not in ('P','SP','RP')", '1', '0') }}) as has_nonp_pos
    from mlb_positions
    group by 1, 2
),

cands as (
    select a.platform, a.name_key, a.season_year, a.is_ambiguous,
           c.mlbam_id, c.stat_group_scope,
           coalesce(d.has_pitching, 0)  as has_pitching,
           coalesce(d.has_hitting, 0)   as has_hitting,
           coalesce(f.has_fielding, 0)  as has_fielding,
           coalesce(f.has_nonp_pos, 0)  as has_nonp_pos
    from dim_names a
    join {{ ref('int_player_identity_candidates') }} c
        on c.platform = a.platform and c.name_key = a.name_key
    join game_presence gp
        on gp.mlbam_id = c.mlbam_id and gp.season_year = a.season_year
    left join disciplines d
        on d.mlbam_id = c.mlbam_id and d.season_year = a.season_year
    left join fielding_coverage f
        on f.mlbam_id = c.mlbam_id and f.season_year = a.season_year
),

-- ============================ CBS evidence ============================
-- One row per sighting; the pos/club separator renders as an HTML bullet
-- entity ('P &#149; KC') in most eras and a pipe in others -- normalize
-- both before splitting.
evidence as (
    select
        league_key, season_year, franchise_id,
        {{ cbs_name_key('player_name_raw') }} as name_key,
        nullif(trim(split_part(replace(player_pos_team_raw, '&#149;', '|'), '|', 1)), '') as ev_pos,
        nullif(trim(split_part(replace(player_pos_team_raw, '&#149;', '|'), '|', 2)), '') as ev_club
    from {{ ref('stg_cbs__ui_transactions') }}
    where player_pos_team_raw is not null and franchise_id is not null

    union all

    select
        league_key, season_year, franchise_id,
        {{ cbs_name_key('player_name_raw') }} as name_key,
        nullif(trim(primary_pos), '') as ev_pos,
        nullif(trim(mlb_team), '')    as ev_club
    from {{ ref('stg_cbs__ui_rosters') }}
    where player_name_raw is not null
),

ev_pos as (
    select distinct league_key, season_year, franchise_id, name_key, ev_pos
    from evidence where ev_pos is not null
),

ev_club as (
    select distinct league_key, season_year, franchise_id, name_key, ev_club
    from evidence where ev_club is not null
),

sightings as (
    select distinct league_key, season_year, franchise_id, name_key
    from evidence
),

-- Every (franchise-sighting x candidate) pair.
pairs as (
    select
        c.platform, s.league_key, c.season_year, s.franchise_id, c.name_key,
        c.is_ambiguous,
        c.mlbam_id, c.stat_group_scope,
        c.has_pitching, c.has_hitting, c.has_fielding, c.has_nonp_pos
    from cands c
    join sightings s
        on s.season_year = c.season_year and s.name_key = c.name_key
),

mlb_clubs as (
    select distinct g.mlbam_id, g.season_year, ab.cbs_abbrev
    from {{ ref('stg_mlb__player_game') }} g
    join {{ ref('mlb_team_abbrevs') }} ab on ab.team_id = g.team_id
),

-- The abbrev vocabulary the seed knows -- an evidence club OUTSIDE it is a
-- data gap and can never justify a veto.
known_abbrevs as (
    select distinct cbs_abbrev from {{ ref('mlb_team_abbrevs') }}
),

-- Club check per pair: ANY sighted club among the candidate's real clubs.
club_eval as (
    select
        p.platform, p.league_key, p.season_year, p.franchise_id, p.name_key,
        p.mlbam_id,
        count(ec.ev_club)                          as n_club_ev,
        count(ka.cbs_abbrev)                       as n_club_ev_known,
        count(mc.cbs_abbrev)                       as n_club_hits
    from pairs p
    left join ev_club ec
        on  ec.league_key = p.league_key and ec.season_year = p.season_year
        and ec.franchise_id = p.franchise_id and ec.name_key = p.name_key
    left join known_abbrevs ka
        on  ka.cbs_abbrev = ec.ev_club
    left join mlb_clubs mc
        on  mc.mlbam_id = p.mlbam_id and mc.season_year = p.season_year
        and mc.cbs_abbrev = ec.ev_club
    group by 1, 2, 3, 4, 5, 6
),

-- Position check per pair: EVERY distinct sighted position must be
-- consistent with the candidate.
pos_eval as (
    select
        p.platform, p.league_key, p.season_year, p.franchise_id, p.name_key,
        p.mlbam_id,
        count(ep.ev_pos) as n_pos_ev,
        count_if(not (
            case
                when ep.ev_pos in ('P', 'SP', 'RP') then p.has_pitching = 1
                -- any non-pitcher sighting: the candidate is a position
                -- player (class check only -- CBS positions are eligibility
                -- labels, the club does the fine discrimination)
                when p.has_fielding = 1             then p.has_nonp_pos = 1
                else p.has_hitting = 1
            end
        )) as n_pos_fails
    from pairs p
    left join ev_pos ep
        on  ep.league_key = p.league_key and ep.season_year = p.season_year
        and ep.franchise_id = p.franchise_id and ep.name_key = p.name_key
    group by 1, 2, 3, 4, 5, 6
),

scored as (
    select
        p.platform, p.league_key, p.season_year, p.franchise_id, p.name_key,
        p.is_ambiguous,
        p.mlbam_id, p.stat_group_scope,
        (coalesce(ce.n_club_ev, 0) = 0 or coalesce(ce.n_club_hits, 0) > 0) as club_ok,
        (coalesce(pe.n_pos_ev, 0) = 0 or coalesce(pe.n_pos_fails, 0) = 0)  as pos_ok,
        (coalesce(ce.n_club_ev, 0) > 0 or coalesce(pe.n_pos_ev, 0) > 0)    as has_evidence,
        -- veto ammunition: the pair has known-vocabulary club evidence and
        -- the candidate overlaps none of it
        (coalesce(ce.n_club_ev_known, 0) > 0
            and coalesce(ce.n_club_hits, 0) = 0)                           as club_refuted
    from pairs p
    left join club_eval ce
        on  ce.platform = p.platform and ce.league_key = p.league_key
        and ce.season_year = p.season_year and ce.franchise_id = p.franchise_id
        and ce.name_key = p.name_key and ce.mlbam_id = p.mlbam_id
    left join pos_eval pe
        on  pe.platform = p.platform and pe.league_key = p.league_key
        and pe.season_year = p.season_year and pe.franchise_id = p.franchise_id
        and pe.name_key = p.name_key and pe.mlbam_id = p.mlbam_id
),

survivors as (
    select
        platform, league_key, season_year, franchise_id, name_key,
        max({{ iff('is_ambiguous', '1', '0') }})                         as was_ambiguous,
        count(*)                                             as n_candidates,
        count_if(club_ok and pos_ok)                         as n_survivors,
        max({{ iff('club_ok and pos_ok', 'mlbam_id', 'null') }})         as survivor_mlbam,
        max({{ iff('club_ok and pos_ok', 'stat_group_scope', 'null') }}) as survivor_scope,
        max({{ iff('has_evidence', '1', '0') }})                         as had_evidence,
        min({{ iff('club_refuted', '1', '0') }})                         as all_club_refuted
    from scored
    group by 1, 2, 3, 4, 5
),

-- EARNED RESOLUTIONS only: the name was ambiguous at season grain and this
-- franchise's evidence discriminated to exactly one candidate.
--
-- A club-contradiction VETO (refuting a resolved id when no candidate
-- overlaps the franchise's sighted clubs) was built here and REMOVED
-- 2026-07-24 after inspection: games-played data cannot distinguish "never
-- on that club" from "on the club's roster but never appeared" -- Carl
-- Crawford's 2012 anchor correctly reads LAD (the August trade) while his
-- only GAMES are BOS (he rehabbed all fall), and every traded-while-injured
-- player or September stash shares that signature; 1,574 vetoes fired, most
-- of them that shape. The roster-sat contradiction class (Alex Gonzalez
-- 2007 on franchise 11, 'SS KC' vs the CIN shortstop's id) goes to the
-- human residue seed instead, where it belongs.
machine_earned as (
    select
        platform, league_key, season_year, franchise_id, name_key,
        survivor_mlbam   as mlbam_id,
        survivor_scope   as stat_group_scope,
        n_candidates,
        n_survivors
    from survivors
    where was_ambiguous = 1 and had_evidence = 1 and n_survivors = 1
),

-- The HUMAN layer (Kyle's fill-in column): franchise-scoped assignments for
-- residue the evidence could not decide -- "which Luis Garcia did BWS roster
-- in 2025?" is answerable by a person reading the team page even when no
-- machine evidence discriminates. A human row REPLACES the machine row at
-- the same key.
human as (
    select
        'cbs' as platform,
        league_key,
        season_year::int    as season_year,
        franchise_id::int   as franchise_id,
        cbs_name_key        as name_key,
        mlbam_id::int       as mlbam_id,
        cast(null as varchar) as stat_group_scope,
        1 as n_candidates,
        1 as n_survivors
    from {{ ref('player_identity_context_overrides') }}
    where mlbam_id is not null
)

select * from human

union all

select m.*
from machine_earned m
left join human h
    on  h.platform = m.platform and h.league_key = m.league_key
    and h.season_year = m.season_year and h.franchise_id = m.franchise_id
    and h.name_key = m.name_key
where h.name_key is null
