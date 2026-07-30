-- stg_transactions.sql
-- MLB-16: per-season transaction event legs, flattened from the append-only
-- RAW.TRANSACTIONS snapshots (one VARIANT array of verbatim ESPN
-- ACTIVITY_TRANSACTIONS topics per extract). Latest snapshot per season,
-- mirroring stg_draft over RAW.DRAFT_PICKS.
--
-- ==========================================================================
-- GRAIN: one row per transaction message leg
--   (league_key, season_year, transaction_id, player_id, source_message_type_id).
-- ==========================================================================
--
-- ESPN keeps baseball's durable, full-season transaction log on the league
-- message board (the communication endpoint's ACTIVITY_TRANSACTIONS topics),
-- NOT in the mTransactions2 view (a current-scoring-period decoy for flb --
-- MLB-16 spike). Each topic groups the legs of one transaction; each message
-- is one player movement. We keep the acquisition-relevant vocabulary and
-- drop the lineup start/sit chatter (messageTypeId 188) that shares the feed:
--   178 add    (player enters a team from free agency / waivers)
--   179 drop   (player leaves a team to free agency)
--   224/239/244 trade legs (player moves team -> team; the 239 variant carries
--              the counterparty in `for` and a sentinel -1 in `to`)
-- ESPN's 0 / -1 team ids are the "free agency / none" sentinels -> NULL.
--
-- This is the platform-neutral event shape a future stg_cbs__transactions
-- (over RAW.CBS_TRANSACTIONS, league/transaction-list/log) converges onto:
-- directed player movement with a NULL side meaning free agency.

{{ config(materialized='view') }}

with latest_extraction as (
    select
        league_key,
        season_year,
        raw_json
    from {{ source('raw', 'transactions') }}
    qualify row_number() over (
        partition by league_key, season_year
        -- MLB-134 -- total order. extracted_at alone ties whenever one
        -- extract stamps two payload versions of the same entity (a re-run
        -- or a double-capture). RAW carries no load sequence id, so the
        -- payload hash is the only discriminator available; it can only ever
        -- choose between byte-identical payloads, which makes the VALUE
        -- deterministic even though the row choice is arbitrary.
        order by extracted_at desc, hash(raw_json) desc
    ) = 1
),

legs as (
    select
        le.league_key,
        le.season_year,
        topic.value:id::string                            as transaction_id,
        msg.value:messageTypeId::integer                  as source_message_type_id,
        msg.value:targetId::integer                       as player_id,
        to_timestamp_ntz(msg.value:date::number / 1000)   as event_ts,
        nullif(nullif(msg.value:"from"::integer, 0), -1)  as raw_from_team_id,
        nullif(nullif(msg.value:"to"::integer, 0), -1)    as raw_to_team_id,
        nullif(nullif(msg.value:"for"::integer, 0), -1)   as raw_for_team_id
    from latest_extraction le,
        {{ flatten_array('le.raw_json', 'topic') }},
        {{ flatten_array('topic.value:messages', 'msg') }}
    where msg.value:messageTypeId::integer in (178, 179, 224, 239, 244)
)

select
    league_key,
    season_year,
    transaction_id,
    source_message_type_id,
    case source_message_type_id
        when 178 then 'add'
        when 179 then 'drop'
        else 'trade'
    end as event_type,
    player_id,
    event_ts,
    -- directed movement: losing team -> acquiring team (NULL side = free agency)
    case source_message_type_id
        when 178 then null::integer          -- add:  arrives from free agency
        when 179 then raw_to_team_id         -- drop: leaves this team
        else raw_from_team_id                -- trade: losing team
    end as from_team_id,
    case source_message_type_id
        when 178 then raw_to_team_id                       -- add:  joins this team
        when 179 then null::integer                        -- drop: departs to free agency
        else coalesce(raw_to_team_id, raw_for_team_id)     -- trade: gaining team (239 -> for)
    end as to_team_id,
    raw_for_team_id as counterparty_team_id
from legs
-- Defensive dedupe: one leg per (transaction, player, message type). ESPN
-- occasionally repeats a message within a topic; a repeat is a true duplicate.
qualify row_number() over (
    partition by league_key, season_year, transaction_id, player_id, source_message_type_id
    order by event_ts
) = 1
