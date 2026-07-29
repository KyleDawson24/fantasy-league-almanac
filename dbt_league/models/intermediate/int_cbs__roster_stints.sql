-- int_cbs__roster_stints.sql
-- THE WALK-BACK (MLB-63): historic roster membership reconstructed from
-- move edges + year-end anchors. One row per contiguous stint a player
-- spent on a franchise: (league_key, season_year, franchise_id, name_key,
-- stint_start, stint_end], with how it opened, how it closed, and the
-- anomaly flags that grade it (Kyle's rule: fidelity is per-row and
-- flows, never a hidden aggregate).
--
-- THE ALGORITHM (why anchors, why backward-capable):
--   Drafts were never logged (loaded offline every year), so a season's
--   OPENING rosters exist in no transaction log. The year-end anchor
--   closes the season from the other end: any player on the anchor -- or
--   departing mid-season -- WITHOUT a prior in-season acquisition must
--   have opened the season on that roster. Assembly is a last-event-wins
--   state machine per (season, franchise, player):
--     acquisition = add | trade_in            -> ON at event date
--     departure   = drop | trade-OUT (derived: the counterparty of every
--                   trade_in LOSES that player the same instant) -> OFF
--     synthetic opening acquisition at season start where the record
--     demands one (first event is a departure, or anchored with no
--     acquisition at all);
--     anchored players whose last event says OFF reopen at that event
--     (the log missed a re-add) -- flagged anchor_reopened;
--     ON at season end without anchor confirmation -> missing_departure
--     flag (2003-2025; the 2001-2002 no-anchor era can't check).
--
--   LAW 2 (Kyle, 2026-07-14): you cannot lineup-move a player you don't
--   roster -- every lineup event (activate/reserve/slot_move) is
--   MEMBERSHIP EVIDENCE at its date, though never an acquisition or
--   departure verb. Two mechanisms ride it:
--     lineup_opening   the player-season's first event ANYWHERE is a
--                      lineup event -> he was already rostered when the
--                      log opens -> season-start opening on that
--                      franchise. The draft-and-hold class the membership
--                      verbs alone lose entirely (Randy Johnson 2001-02:
--                      one P->SP move, no add, no anchor -> NO stint, his
--                      1,142-pt 2002 invisible to the active lens).
--     lineup_evidence  a lineup event while this franchise's membership
--                      state machine says OFF -> a log-missed
--                      (re-)acquisition, reopening AT the evidence date
--                      (not season start: his earlier season may belong
--                      to another franchise).
--
-- Chronology: effective_date wins over txn_date (retroactive edits);
-- same-day event ORDER falls back to the report's own row order. The two
-- sequence columns run in OPPOSITE directions and it matters (MLB-118):
--   row_seq   walks NEWEST-first across the report  -> DESC is forward
--   entry_seq counts UP within ONE multi-line row   -> ASC  is forward
-- so forward chronology is (event_date, row_seq DESC, entry_seq ASC) and
-- reverse is its exact mirror. Sorting entry_seq DESC inverted the lines
-- inside a single transaction: verified across all 22 multi-entry rows in
-- the corpus, entry 1 is always the membership verb and entry 2 its
-- consequence (trade_in->reserve x10, add->drop x9, trade_in->activate
-- x2, trade_in->slot_move x1). Read backwards those become "bench a
-- player you don't roster" and "drop before adding" -- and the damage was
-- real: Joe Ryan 2023's trade_in sorted BEHIND its own bench line, so his
-- first 2023 event read as lineup evidence and opened a phantom
-- season-start stint on the ACQUIRING team, handing Kansas City the 3.5
-- months he actually spent on Kimball Drives.
-- Season bounds = the MLB season's actual game-date span (universal
-- layer), so stints speak real dates.
--
-- Identity: name_key (the cbs_name_key normalization) -- anchors carry
-- no ids, so names are the join spine; resolved_cbs_player_id rides
-- where the league's own dictionary is unambiguous, and is_ambiguous_name
-- flags the Will Smith / Luis Garcia class for downstream provenance.
-- 2026 is EXCLUDED: the daily captures own the live season.

{{ config(materialized='table') }}

with season_bounds as (
    select
        season_year,
        min(game_date) as season_start,
        max(game_date) as season_end
    from {{ ref('stg_mlb__player_game') }}
    where season_year between 2001 and 2025
    group by season_year
),

-- Move edges, identity-normalized. Trade-outs are derived rows: the
-- counterparty of a trade_in loses the player at the same moment.
moves_raw as (
    select
        league_key,
        season_year,
        franchise_id,
        {{ cbs_name_key('player_name_raw') }}        as name_key,
        coalesce(effective_date, txn_date)           as event_date,
        -- MLB-118: the EXECUTION instant, deliberately distinct from
        -- event_date (the STATE date). A manager can queue a move days
        -- before it takes effect, so the two orderings are not the same
        -- stream -- and LAW 2 below needs to know which came first.
        coalesce(txn_ts, txn_date::timestamp)        as exec_ts,
        row_seq,
        entry_seq,
        case when move_type in ('add', 'trade_in')
             then 'acquisition' else 'departure' end as event_kind,
        move_type                                    as event_detail,
        counterparty_franchise_id
    from {{ ref('stg_cbs__ui_transactions') }}
    where season_year between 2001 and 2025
        and move_type in ('add', 'trade_in', 'drop')
        and franchise_id is not null

    union all

    select
        league_key,
        season_year,
        counterparty_franchise_id,
        {{ cbs_name_key('player_name_raw') }},
        coalesce(effective_date, txn_date),
        coalesce(txn_ts, txn_date::timestamp),
        row_seq,
        entry_seq,
        'departure',
        'trade_out',
        franchise_id
    from {{ ref('stg_cbs__ui_transactions') }}
    where season_year between 2001 and 2025
        and move_type = 'trade_in'
        and counterparty_franchise_id is not null
),

anchors as (
    select
        league_key,
        season_year,
        franchise_id,
        {{ cbs_name_key('player_name_raw') }} as name_key
    from {{ ref('stg_cbs__ui_rosters') }}
    group by 1, 2, 3, 4
),

-- ANCHOR-ARBITRATED VOIDS (the mirror-pair class, 2026-07-13): the log
-- carries trades that never stuck -- vetoed/reversed swaps, and one
-- 2003 deal logged in BOTH directions under two effective dates. The
-- signature: the player's chronologically LAST acquisition is a
-- trade_in whose receiver's year-end anchor does NOT hold the player
-- while the SENDER's anchor does. The anchor is ground truth, so that
-- final leg is voided -- the trade_in row and its derived trade_out
-- leave the stream together (they share row_seq). Genuine rentals
-- (swap and swap back, both logged, anchor agreeing with the net
-- result) never match the signature and pair normally.
last_acquisitions as (
    select *
    from moves_raw
    where event_kind = 'acquisition'
    qualify row_number() over (
        partition by league_key, season_year, name_key
        order by event_date desc nulls last, row_seq asc, entry_seq desc nulls last
    ) = 1
),

voided_legs as (
    select l.league_key, l.season_year, l.name_key, l.row_seq
    from last_acquisitions l
    left join anchors recv
        on recv.league_key = l.league_key and recv.season_year = l.season_year
        and recv.franchise_id = l.franchise_id and recv.name_key = l.name_key
    inner join anchors send
        on send.league_key = l.league_key and send.season_year = l.season_year
        and send.franchise_id = l.counterparty_franchise_id
        and send.name_key = l.name_key
    where l.event_detail = 'trade_in'
        and recv.name_key is null
),

moves as (
    select m.*
    from moves_raw m
    left join voided_legs v
        on v.league_key = m.league_key and v.season_year = m.season_year
        and v.name_key = m.name_key and v.row_seq = m.row_seq
    where v.name_key is null
),

-- LAW 2 membership evidence: lineup events carry the acting franchise
-- directly (no derived/phantom class), so each one proves the player was
-- on that roster that day.
lineup_events as (
    select
        league_key,
        season_year,
        franchise_id,
        {{ cbs_name_key('player_name_raw') }}        as name_key,
        coalesce(effective_date, txn_date)           as event_date,
        coalesce(txn_ts, txn_date::timestamp)        as exec_ts,
        row_seq,
        entry_seq
    from {{ ref('stg_cbs__ui_transactions') }}
    where season_year between 2001 and 2025
        and move_type in ('activate', 'reserve', 'slot_move')
        and franchise_id is not null
),

-- (a) First event of the player-season ANYWHERE is a lineup event ->
-- rostered since the log opened -> season-start opening there. row_seq
-- 999999 loses same-day ties to the anchored/departure openings' 1000000
-- (row_seq walks DESC) so double-opened players resolve deterministically.
lineup_openings as (
    select league_key, season_year, franchise_id, name_key,
           'lineup_opening' as opening_reason
    from (
        select league_key, season_year, franchise_id, name_key,
               event_date, row_seq, entry_seq, 'evidence' as kind
        from lineup_events
        union all
        select league_key, season_year, franchise_id, name_key,
               event_date, row_seq, entry_seq, 'move'
        from moves
    )
    qualify row_number() over (
        partition by league_key, season_year, name_key
        order by event_date, row_seq desc nulls last, entry_seq asc
    ) = 1 and kind = 'evidence'
),

-- (b) Lineup evidence while this franchise's membership state is OFF (or
-- never established) -> reopen at the evidence date. The alternation
-- machine downstream dedupes overlap with (a)'s season-start opening and
-- collapses consecutive evidence rows to the first.
lineup_reopens as (
    select league_key, season_year, franchise_id, name_key,
           event_date, exec_ts, row_seq, entry_seq
    from (
        select
            e.*,
            last_value(case when e.kind != 'evidence' then e.kind end)
                ignore nulls over (
                    partition by e.league_key, e.season_year,
                                 e.franchise_id, e.name_key
                    order by e.event_date, e.row_seq desc nulls last, e.entry_seq asc
                    rows between unbounded preceding and 1 preceding
                ) as prev_membership_kind,
            last_value(case when e.kind != 'evidence' then e.exec_ts end)
                ignore nulls over (
                    partition by e.league_key, e.season_year,
                                 e.franchise_id, e.name_key
                    order by e.event_date, e.row_seq desc nulls last, e.entry_seq asc
                    rows between unbounded preceding and 1 preceding
                ) as prev_membership_exec_ts
        from (
            select league_key, season_year, franchise_id, name_key,
                   event_date, exec_ts, row_seq, entry_seq, event_kind as kind
            from moves
            union all
            select league_key, season_year, franchise_id, name_key,
                   event_date, exec_ts, row_seq, entry_seq, 'evidence'
            from lineup_events
        ) e
    )
    where kind = 'evidence'
        and coalesce(prev_membership_kind, 'departure') = 'departure'
        -- MLB-118 (Kyle's rule 3, from the Brad Lord 2025 trace): a
        -- departure CANCELS a lineup move that was already queued for a
        -- later date. Evidence only counts if it was EXECUTED while the
        -- player was still rostered -- i.e. at or after the departure that
        -- turned membership OFF. A move queued BEFORE that departure is a
        -- stale instruction the drop pre-empted, not proof of a re-add.
        --
        -- Mitch Spence 2024 is the canonical case: BWS queued an activate
        -- on 06-13 effective 06-24, then dropped him on 06-16 effective
        -- 06-17. Honoring the stale 06-24 activate reopened a phantom
        -- stint that ran to season end and contested El Gran Gato's real
        -- 06-24 acquisition. Ordering by state date alone cannot see this;
        -- only the execution instant separates the two.
        --
        -- NULL-permissive on purpose: an unknown timestamp falls back to
        -- the old behaviour, so this only ever fires on positive evidence
        -- of staleness. LAW 2's recovery power is untouched -- a genuine
        -- re-acquisition's lineup move is executed AFTER the drop.
        and (prev_membership_exec_ts is null
             or exec_ts is null
             or exec_ts >= prev_membership_exec_ts)
),

-- The state machine wants one ordered event stream per (season,
-- franchise, player). Synthetic season-start acquisitions cover the
-- unlogged opening rosters.
first_events as (
    select
        league_key, season_year, franchise_id, name_key,
        event_kind   as first_kind,
        event_detail as first_detail,
        event_date   as first_date
    from moves
    qualify row_number() over (
        partition by league_key, season_year, franchise_id, name_key
        order by event_date, row_seq desc nulls last, entry_seq asc
    ) = 1
),

-- Anchored, no acquisition-first history: on the roster since day 1.
anchored_openings as (
    select
        a.league_key, a.season_year, a.franchise_id, a.name_key,
        'opening' as opening_reason
    from anchors a
    left join first_events f
        on a.league_key = f.league_key and a.season_year = f.season_year
        and a.franchise_id = f.franchise_id and a.name_key = f.name_key
    where f.name_key is null
),

openings as (
    select * from anchored_openings

    union all

    -- LAW 2(a): first event of the player-season is a lineup move.
    select * from lineup_openings

    union all

    -- First recorded event is a DEPARTURE: they must have opened the
    -- season here (anchored or not -- covers 2001-2002 too).
    -- PHANTOM GUARD (2026-07-13): when that first departure is a
    -- DERIVED trade_out, it presumes the counterparty ever held the
    -- player. If the player demonstrably opened or was acquired on a
    -- DIFFERENT franchise on/before that date, the derived departure
    -- is the residue of a voided/double-logged trade leg -- synthesizing
    -- an opening here would hand a phantom stint the player's early
    -- season (the 2003 Reed/K-Rod counter-legs). A real logged drop
    -- stays a legitimate opening signal in every era.
    select
        f.league_key, f.season_year, f.franchise_id, f.name_key, 'opening'
    from first_events f
    where f.first_kind = 'departure'
        and not (
            f.first_detail = 'trade_out'
            and (
                exists (
                    -- STRICTLY earlier: a same-day acquisition elsewhere
                    -- is almost always this very trade's other leg (the
                    -- derived pair shares the effective date) -- it must
                    -- not suppress the opening its own trade_out implies
                    -- (Vlad Jr's traded-mid-2023 Meteors half).
                    select 1 from moves m2
                    where m2.league_key = f.league_key
                        and m2.season_year = f.season_year
                        and m2.name_key = f.name_key
                        and m2.franchise_id != f.franchise_id
                        and m2.event_kind = 'acquisition'
                        and m2.event_date < f.first_date
                )
                or exists (
                    select 1 from anchored_openings ao
                    where ao.league_key = f.league_key
                        and ao.season_year = f.season_year
                        and ao.name_key = f.name_key
                        and ao.franchise_id != f.franchise_id
                )
            )
        )
),

events as (
    select
        league_key, season_year, franchise_id, name_key,
        event_date, exec_ts, row_seq, entry_seq, event_kind, event_detail
    from moves

    union all

    -- Synthetic openings are the BASELINE assertion: in execution order
    -- they must precede every real transaction so the log always outranks
    -- them (Kyle: the walk-back's openings sit next to the log, as the
    -- floor the log overwrites). A season-dawn instant is NOT early
    -- enough -- PRE-SEASON transactions execute before opening day
    -- (Konerko 2007: traded 03-30, effective opening day 04-01; a dawn-
    -- executed synthetic outranked the real trade_out and the sender
    -- kept him all season). A year before season start predates any
    -- real activity in the league's annual cycle.
    --
    -- At the baseline tie, row_seq DESC runs first->1000000 then
    -- ->999999. The stint LABEL is taken from the EARLIEST-executed
    -- acquisition at the flip boundary (first_assertion below), so the
    -- plain 'opening' carries the bigger sentinel to sort first and keep
    -- its label precedence over 'lineup_opening' -- the old assembly's
    -- choice. (State is unaffected either way: both are acquisitions.)
    select
        o.league_key, o.season_year, o.franchise_id, o.name_key,
        b.season_start,
        dateadd(year, -1, b.season_start)::timestamp,
        iff(o.opening_reason = 'lineup_opening', 999999, 1000000),
        0, 'acquisition', o.opening_reason
    from openings o
    inner join season_bounds b on o.season_year = b.season_year

    union all

    -- LAW 2(b): evidence-backed reopenings at the evidence date. exec_ts
    -- is the lineup move's own execution instant (evidence proves
    -- membership when the manager ACTED -- Kyle 2026-07-24).
    select
        r.league_key, r.season_year, r.franchise_id, r.name_key,
        r.event_date, r.exec_ts, r.row_seq, r.entry_seq,
        'acquisition', 'lineup_evidence'
    from lineup_reopens r
),

-- THE RESOLUTION (MLB-118, Kyle's rule): "a player's status on day D is
-- set by the most recently EXECUTED transaction whose EFFECTIVE date is
-- on or before D." The walk-back itself is unchanged -- synthetic
-- openings and LAW 2 evidence enter the stream as season-dawn /
-- their-own-instant executions, so every real transaction outranks them.
-- What changes is the assembly: instead of pairing adjacent events in
-- effective order (which silently assumes execution order and effective
-- order agree -- the bug), each distinct effective date becomes a
-- BOUNDARY, the governing event at a boundary is the max-execution event
-- effective by then, and a stint is a maximal run of governing-ON
-- boundaries.
--
-- Execution recency (Kyle 2026-07-24): exec date -> hour -> minute ->
-- report row order (row_seq: closer to 1 = newer -> DESC walks forward;
-- entry_seq ASC within one multi-line row).
--
-- Where the two orders agree -- every normal season -- this is IDENTICAL
-- to the old pairing, including the same-kind-run cases (an opening
-- followed by a logged add with no drop between still yields one stint
-- from season start: the governing event changes, the state does not).
-- It differs exactly where they invert:
--   Hamilton 2024   drop eff 06-10 then re-add eff 06-10 cancel (a 0-day
--                   absence is not an absence); the last-executed retro
--                   drop eff 06-03 governs -> FF stint ends 06-03.
--   Gardner 2016    the re-forced add (eff 04-03) outranks the stale
--                   queued one (eff 04-11) -> rostered opening week.
--   Kemp 2010       a 38-minute trade flurry collapses to its final
--                   version (eff 08-09).
ordered as (
    select
        e.*,
        row_number() over (
            partition by league_key, season_year, franchise_id, name_key
            order by exec_ts, row_seq desc nulls last, entry_seq asc
        ) as exec_seq
    from events e
),

-- One row per (partition, effective date): who governs as of end of that
-- day. RANGE includes every event sharing the boundary's effective date.
boundaries as (
    select distinct
        league_key, season_year, franchise_id, name_key,
        event_date as boundary_date,
        max(exec_seq) over (
            partition by league_key, season_year, franchise_id, name_key
            order by event_date
            range between unbounded preceding and current row
        ) as gov_exec_seq
    from ordered
),

-- A stint's ORIGIN is its earliest-executed assertion; its BOUNDARIES are
-- governed by the latest. At an ON flip the boundary date always equals
-- the governing acquisition's effective date, and the earliest-executed
-- acquisition asserting that same date names the channel -- so a drafted
-- player whose opening-day lineup move out-executes his synthetic opening
-- still reads 'opening', while a mid-season evidence reopen (nothing else
-- asserting its date) honestly reads 'lineup_evidence'. Departure detail
-- stays with the governor: a reprocessed drop's final version is the one
-- that closed the stint.
first_assertion as (
    select
        league_key, season_year, franchise_id, name_key,
        event_date,
        min_by(event_detail, exec_seq) as first_acq_detail
    from ordered
    where event_kind = 'acquisition'
    group by 1, 2, 3, 4, 5
),

-- State at each boundary = the governing event's kind; keep only
-- boundaries where the STATE flips (a governing handover that stays ON
-- does not break a stint).
changes as (
    select *
    from (
        select
            b.league_key, b.season_year, b.franchise_id, b.name_key,
            b.boundary_date as event_date,
            g.event_kind,
            iff(g.event_kind = 'acquisition',
                coalesce(fa.first_acq_detail, g.event_detail),
                g.event_detail) as event_detail,
            lag(g.event_kind) over (
                partition by b.league_key, b.season_year, b.franchise_id,
                             b.name_key
                order by b.boundary_date
            ) as prev_kind
        from boundaries b
        join ordered g
            on  g.league_key  = b.league_key
            and g.season_year = b.season_year
            and g.franchise_id = b.franchise_id
            and g.name_key     = b.name_key
            and g.exec_seq     = b.gov_exec_seq
        left join first_assertion fa
            on  fa.league_key  = b.league_key
            and fa.season_year = b.season_year
            and fa.franchise_id = b.franchise_id
            and fa.name_key     = b.name_key
            and fa.event_date   = b.boundary_date
    )
    where event_kind != coalesce(prev_kind, 'departure')
),

paired as (
    -- The lead() must see BOTH event kinds: changes strictly alternate,
    -- so an acquisition's next change IS its departure (or stream end).
    -- The acquisition filter therefore sits OUTSIDE the window --
    -- filtering inside the same select would run lead() over
    -- acquisitions only, skipping every departure, and no stint could
    -- ever close on a drop (the 2026-07-13 pairing bug: all 20,003
    -- stints closed season_end/next-add and missing_departure flooded
    -- with false positives like a logged-and-ignored Wheeler drop).
    select *
    from (
        select
            c.league_key,
            c.season_year,
            c.franchise_id,
            c.name_key,
            c.event_kind,
            c.event_date   as stint_start,
            c.event_detail as open_channel,
            -- changes is one row per boundary DATE (post-resolution), so
            -- the date alone orders it; the old row_seq/entry_seq
            -- tiebreaks no longer exist at this grain.
            lead(c.event_date) over (
                partition by c.league_key, c.season_year, c.franchise_id, c.name_key
                order by c.event_date
            )              as next_change_date,
            lead(c.event_detail) over (
                partition by c.league_key, c.season_year, c.franchise_id, c.name_key
                order by c.event_date
            )              as next_change_detail
        from changes c
    )
    where event_kind = 'acquisition'
),

stints as (
    select
        p.league_key,
        p.season_year,
        p.franchise_id,
        p.name_key,
        row_number() over (
            partition by p.league_key, p.season_year, p.franchise_id, p.name_key
            order by p.stint_start
        )                                              as stint_index,
        p.stint_start,
        coalesce(p.next_change_date, b.season_end)     as stint_end,
        p.open_channel,
        coalesce(p.next_change_detail, 'season_end')   as close_type,
        (a.name_key is not null)                       as on_year_end_anchor
    from paired p
    inner join season_bounds b on p.season_year = b.season_year
    left join anchors a
        on p.league_key = a.league_key and p.season_year = a.season_year
        and p.franchise_id = a.franchise_id and p.name_key = a.name_key
),

-- Anomalies grade each (player, franchise, season) record.
flags as (
    select
        league_key, season_year, franchise_id, name_key,
        -- Anchored but the log's last word was a departure: the log
        -- missed a re-acquisition. (Their season_end stint is absent --
        -- surfaced as a flag, not silently invented.)
        boolor_agg(close_type = 'season_end') as ends_on_roster
    from stints
    group by 1, 2, 3, 4
)

select
    s.league_key,
    s.season_year,
    s.franchise_id,
    s.name_key,
    s.stint_index,
    s.stint_start,
    s.stint_end,
    s.open_channel,
    s.close_type,
    s.on_year_end_anchor,
    -- Fidelity flags (Kyle's provenance rule -- carried per row):
    (s.close_type = 'season_end' and not s.on_year_end_anchor
        and s.season_year >= 2003)                     as missing_departure,
    (s.on_year_end_anchor and not f.ends_on_roster)    as anchor_reopen_needed,
    (s.season_year < 2003)                             as no_anchor_era,
    -- MLB-81 identity: mlbam is the join spine downstream (name equality
    -- silently broke K-Rod's Angels peak, the middle-initial class, etc.).
    -- pid resolves the season's name form to its mlbam; NULL where the name
    -- is ambiguous that season or has no candidate -- the attribution fact
    -- falls back to the name-join for exactly those rows.
    -- MLB-120: where the season-grain dim stays ambiguous, ctx may still have
    -- EARNED a per-franchise resolution from this franchise's own pos/club
    -- paperwork (the two Will Smiths each resolve on their own franchise).
    -- A stint is franchise-scoped, so the context id applies exactly here.
    coalesce(ctx.mlbam_id, pid.mlbam_id)               as mlbam_id,
    coalesce(ctx.stat_group_scope, pid.stat_group_scope) as stat_group_scope,
    coalesce(pid.is_ambiguous, false)
        and ctx.mlbam_id is null                       as is_ambiguous_name,
    case when d.n_ids = 1 then d.any_id end            as resolved_cbs_player_id
from stints s
inner join flags f
    on s.league_key = f.league_key and s.season_year = f.season_year
    and s.franchise_id = f.franchise_id and s.name_key = f.name_key
left join {{ ref('int_cbs__player_name_ids') }} d
    on s.league_key = d.league_key and s.name_key = d.name_key
left join {{ ref('dim_player_identity') }} pid
    on pid.platform = 'cbs'
    and pid.name_key = s.name_key
    and pid.season_year = s.season_year
left join {{ ref('int_player_identity_context') }} ctx
    on ctx.platform = 'cbs'
    and ctx.league_key = s.league_key
    and ctx.season_year = s.season_year
    and ctx.franchise_id = s.franchise_id
    and ctx.name_key = s.name_key
