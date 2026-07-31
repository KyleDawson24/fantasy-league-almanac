-- dialect.sql
-- Adapter-dispatched spellings for every construct where Snowflake and
-- DuckDB disagree (MLB-10 phase 2).
--
-- House rule 3 from the MLB-9 audit: DIALECT LIVES IN MACROS, SEMANTICS
-- LIVE IN MODELS. A model says "flatten this array"; which of
-- `lateral flatten` or `unnest(cast(... as json[]))` that becomes is not
-- the model's business. rate_stats.sql established the pattern; this file
-- extends it to the JSON reshape and the scalar-function gaps.
--
-- The `default__` implementations emit the EXACT text the models carried
-- before the port, character for character. That is deliberate and it is
-- the safety property the whole sprint rests on: the compiled Snowflake
-- SQL is unchanged, so the frozen goldens cannot move. Verified by
-- diffing `target/compiled` across the conversion.
--
-- Every DuckDB spelling below was verified against the real landed RAW
-- tables before it was written down, not inferred from documentation.

-- JSON reshape ----------------------------------------------------------

{% macro flatten_array(expr, alias) -%}
    {{ return(adapter.dispatch('flatten_array', 'dbt_league')(expr, alias)) }}
{%- endmacro %}

{% macro default__flatten_array(expr, alias) -%}
lateral flatten(input => {{ expr }}) {{ alias }}
{%- endmacro %}

{% macro duckdb__flatten_array(expr, alias) -%}
{#- Matches LATERAL FLATTEN's array behaviour on the two cases that matter:
    a NULL input yields zero rows, and an empty array yields zero rows.
    Both verified against RAW.

    TRAP, verified live: where Snowflake's flatten quietly yields nothing
    for a JSON OBJECT, this cast RAISES (`Conversion Error: Expected
    ARRAY, but got OBJECT`). That makes the legacy-shape guard in
    stg_box_scores / stg_matchup_pairs -- coalesce(raw_json:matchups,
    raw_json) -- safe only because BOTH arms are arrays: the Phase-4 shape
    resolves to the matchups array, and on the pre-Phase-4 shape
    :matchups is NULL so the coalesce falls through to raw_json, which is
    itself the array. If a future raw shape puts an object on either arm,
    this errors loudly rather than silently returning nothing -- which is
    the better failure, but it is a failure, so it is written down. -#}
unnest(cast({{ expr }} as json[])) as {{ alias }}(value)
{%- endmacro %}


{% macro flatten_object(expr, alias) -%}
    {{ return(adapter.dispatch('flatten_object', 'dbt_league')(expr, alias)) }}
{%- endmacro %}

{% macro default__flatten_object(expr, alias) -%}
lateral flatten(input => {{ expr }}) {{ alias }}
{%- endmacro %}

{% macro duckdb__flatten_object(expr, alias) -%}
{#- Snowflake's flatten over an OBJECT exposes `.key` and `.value`; call
    sites read both. The obvious DuckDB translation --
    `unnest(map_entries(...))` -- exposes a single struct column instead,
    which would force every call site to learn a new shape. Unnesting
    map_keys and map_values in parallel inside a correlated subquery keeps
    `.key` / `.value` intact, so the models do not move.

    (`unnest(list, max_depth := 2)` does NOT exist in DuckDB 1.5.5 --
    unnest binds as `unnest(ANY)` there and the two-argument form is a
    binder error. Checked, not assumed.)

    Verified: NULL input and '{}' both yield zero rows, and keys
    containing '/' survive intact -- the 'K/9' case that rules out any
    JSONPath-based extraction.

    The value type is map(varchar, VARCHAR), not map(varchar, json), and
    that is load-bearing. Snowflake's flatten hands back a VARIANT whose
    `::string` unwraps to bare text; DuckDB's `cast(json as varchar)`
    SERIALIZES instead, so a JSON string keeps its quotes and every text
    cast at a call site silently diverges. Casting the map's values to
    varchar up front reproduces Snowflake's unwrap exactly -- verified
    across all five JSON value shapes:

      1 -> '1'   "2.5" -> '2.5'   null -> NULL
      {"x":1} -> '{"x":1}'        [1,2] -> '[1,2]'

    i.e. scalars unwrap, containers serialize, which is VARIANT::string's
    behaviour term for term. Numeric casts (`.value::double`,
    `.value::integer`) are unaffected -- they parse the text the same way
    they parsed the JSON. This is why object-flatten call sites need no
    json_unwrap_text; the ARRAY flatten below still does, because its
    elements must stay JSON for further path steps. -#}
(select unnest(map_keys(m)) as key, unnest(map_values(m)) as value
     from (select cast({{ expr }} as map(varchar, varchar)) as m)) as {{ alias }}
{%- endmacro %}


{% macro streamed_object_join(expr, alias) -%}
{#- The same flatten as flatten_object, for the one place where the output
    is too large to materialize. Read the three streamed_object_* macros
    as a set: _join goes in the FROM clause, _key and _value in the SELECT
    list, all three naming the same (expr, alias).

    WHY IT EXISTS (measured on stg_mlb__player_game, 1.8M gamelogs x 42.2
    stat keys = 76.3M rows). DuckDB CANNOT materialize a correlated
    flatten of that size in ANY lateral spelling. All three failed at the
    6 GB cap, and not marginally -- raising the spill cap to 40 GB still
    failed, at 104s:

      correlated subquery (flatten_object's shape)     FAIL
      unnest(map_entries(...)) as kv(entry)            FAIL
      ... with map values cast to varchar              FAIL

    while the SAME 76.3M rows written from parallel unnests in the SELECT
    list succeed in 15.2s. It is the lateral correlation that cannot
    stream, not the row count and not the value type: DuckDB writes a
    synthetic 42M x 16 table under the same cap in 33.5s.

    So the two engines need different query SHAPES here, not different
    spellings, and the shape is what these macros dispatch. Snowflake
    keeps its lateral (the _join emits it, _key/_value emit `alias.key` /
    `alias.value`) and its compiled text does not move; DuckDB puts the
    unnests in the SELECT list and emits nothing in the FROM clause.

    Parallel unnests in one SELECT list are zipped positionally, and
    map_keys/map_values share an order for the same map -- the same
    assumption flatten_object already makes inside its subquery. Proven
    rather than assumed by the cell-level A/B against Snowflake.

    Cost of the shape: the flattened alias cannot be referenced in that
    SELECT's own WHERE clause on DuckDB, so a call site that filters on
    the key needs an outer query. flatten_object stays the default for
    everything that fits -- reach for this only when a flatten measurably
    will not materialize. -#}
    {{ return(adapter.dispatch('streamed_object_join', 'dbt_league')(expr, alias)) }}
{%- endmacro %}

{% macro default__streamed_object_join(expr, alias) -%}
,
        lateral flatten(input => {{ expr }}) {{ alias }}
{%- endmacro %}

{% macro duckdb__streamed_object_join(expr, alias) -%}
{%- endmacro %}


{% macro streamed_object_key(expr, alias) -%}
    {{ return(adapter.dispatch('streamed_object_key', 'dbt_league')(expr, alias)) }}
{%- endmacro %}

{% macro default__streamed_object_key(expr, alias) -%}
{{ alias }}.key
{%- endmacro %}

{% macro duckdb__streamed_object_key(expr, alias) -%}
unnest(map_keys(cast({{ expr }} as map(varchar, varchar))))
{%- endmacro %}


{% macro streamed_object_value(expr, alias) -%}
    {{ return(adapter.dispatch('streamed_object_value', 'dbt_league')(expr, alias)) }}
{%- endmacro %}

{#- Returns TEXT on both engines, matching flatten_object's varchar values:
    Snowflake unwraps the VARIANT with ::string, DuckDB's map is already
    map(varchar, varchar) so the unnest is text already. -#}
{% macro default__streamed_object_value(expr, alias) -%}
{{ alias }}.value::string
{%- endmacro %}

{% macro duckdb__streamed_object_value(expr, alias) -%}
unnest(map_values(cast({{ expr }} as map(varchar, varchar))))
{%- endmacro %}


{% macro json_get(expr) -%}
{#- Sub-document access: the value STAYS JSON, for feeding a flatten or a
    further path step. Snowflake `x:a:b`, DuckDB `x->'a'->'b'`. -#}
    {{ return(adapter.dispatch('json_get', 'dbt_league')(expr, varargs)) }}
{%- endmacro %}

{% macro default__json_get(expr, path) -%}
{{ expr }}{% for seg in path %}:{{ seg }}{% endfor %}
{%- endmacro %}

{% macro duckdb__json_get(expr, path) -%}
{#- The wrapping parens are load-bearing, not decoration. DuckDB binds `->`
    LOOSER than `is not null`, so an unparenthesized `x->'id' is not null`
    parses as `x->('id' is not null)` and dies with
    `No function matches json_extract(JSON, BOOLEAN)`. Staging filters on
    exactly that shape (`where p.value:id is not null`), so the macro
    parenthesizes once here instead of asking 100-odd call sites to
    remember. Snowflake's `:` binds tightly and needs no help. -#}
({{ expr }}{% for seg in path %}->'{{ seg }}'{% endfor %})
{%- endmacro %}


{% macro json_text(expr) -%}
{#- Scalar access: the value comes back as TEXT, ready for a `::type`
    cast. Snowflake `x:a:b`, DuckDB `(x->'a'->>'b')`.

    The final step is `->>` rather than `->` on purpose: `->` would hand
    back JSON, so a string value would keep its quotes and `::varchar`
    would preserve them. A missing key returns SQL NULL on both engines
    (verified against RAW), which is what the models' casts already
    assume. -#}
    {{ return(adapter.dispatch('json_text', 'dbt_league')(expr, varargs)) }}
{%- endmacro %}

{% macro default__json_text(expr, path) -%}
{{ expr }}{% for seg in path %}:{{ seg }}{% endfor %}
{%- endmacro %}

{% macro duckdb__json_text(expr, path) -%}
({{ expr }}{% for seg in path[:-1] %}->'{{ seg }}'{% endfor %}->>'{{ path[-1] }}')
{%- endmacro %}


{% macro json_unwrap_text(expr) -%}
{#- The text of a value that has ALREADY been extracted -- the element a
    flatten handed back, not a path step. Snowflake's VARIANT::string
    unwraps a JSON string to its bare text; DuckDB's `cast(json as
    varchar)` SERIALIZES it instead and keeps the quotes.

    That difference is silent and it changes values, which is why this
    macro exists. Measured on the real payloads:

      CBS  season stats  BFP  = "0"      -> '"0"'   -> try_to_double NULL
      MLB  gamelogs      avg  = ".294"   -> '".294"'-> try_to_double NULL
      ESPN eligible_slots     = [21,"1B","BE",...]  -> '"BE"' != 'BE'

    The last one is the nastiest: `not in ('BE','IL')` stops filtering and
    `in ('SP','RP','P')` stops matching, so bench rows leak into the
    position lens and every pitcher is priced as a hitter -- with no error
    anywhere. Numeric casts off JSON are NOT affected (`cast('"5"'::json
    as double)` = 5.0, verified), so only the text casts route through here.

    `->>` returns SQL NULL for JSON null and serializes objects/arrays,
    both matching VARIANT::string. Parenthesized for the same
    binding reason as json_get. -#}
    {{ return(adapter.dispatch('json_unwrap_text', 'dbt_league')(expr)) }}
{%- endmacro %}

{% macro default__json_unwrap_text(expr) -%}
{{ expr }}::string
{%- endmacro %}

{% macro duckdb__json_unwrap_text(expr) -%}
({{ expr }}->>'$')
{%- endmacro %}


{% macro json_keys_count(expr) -%}
{#- "how many keys does this object have", the games_played fallback in
    stg_box_scores. Snowflake array_size(object_keys(x)); DuckDB
    len(json_keys(x)).

    Both return NULL rather than 0 for a NULL/absent input, and both call
    sites wrap this in a `> 0` comparison whose else-branch catches the
    NULL, so the fallback still resolves to 0 games played. Verified on
    RAW: absent breakdown -> NULL -> 0, '{}' -> 0. -#}
    {{ return(adapter.dispatch('json_keys_count', 'dbt_league')(expr)) }}
{%- endmacro %}

{% macro default__json_keys_count(expr) -%}
array_size(object_keys({{ expr }}))
{%- endmacro %}

{% macro duckdb__json_keys_count(expr) -%}
len(json_keys({{ expr }}))
{%- endmacro %}


-- Mechanical renames ----------------------------------------------------

{% macro iff(condition, if_true, if_false) -%}
{#- Snowflake IFF, DuckDB IF -- identical three-argument semantics
    including NULL handling, so this really is just a rename.

    House rule 2 says to write the portable intersection (`case when`)
    when it is equal-cost. It is NOT equal-cost here: rewriting 71 call
    sites into CASE would change the compiled Snowflake SQL at every one
    of them, which throws away the byte-identity gate that is proving the
    goldens cannot move. A dispatch macro keeps the Snowflake output
    character-identical and still gets the port its spelling. If the
    CASE rewrite is wanted later it is a golden-neutral pass of its own,
    which is exactly the shape MLB-158-B is already reserved for. -#}
    {{ return(adapter.dispatch('iff', 'dbt_league')(condition, if_true, if_false)) }}
{%- endmacro %}

{% macro default__iff(condition, if_true, if_false) -%}
iff({{ condition }}, {{ if_true }}, {{ if_false }})
{%- endmacro %}

{% macro duckdb__iff(condition, if_true, if_false) -%}
if({{ condition }}, {{ if_true }}, {{ if_false }})
{%- endmacro %}


{% macro to_varchar(expr) -%}
{#- Snowflake TO_VARCHAR(x) with no format argument is a plain cast, and
    that is how all 6 call sites use it. Spelled `varchar` rather than
    `string` on both sides so the two engines agree on the type name as
    well as the value. -#}
    {{ return(adapter.dispatch('to_varchar', 'dbt_league')(expr)) }}
{%- endmacro %}

{% macro default__to_varchar(expr) -%}
to_varchar({{ expr }})
{%- endmacro %}

{% macro duckdb__to_varchar(expr) -%}
cast({{ expr }} as varchar)
{%- endmacro %}


-- Types and time --------------------------------------------------------

{% macro type_timestamp_ntz() -%}
{#- The wall-clock timestamp type. DuckDB has no `timestamp_ntz` spelling
    at all (`Catalog Error: Type with name timestamp_ntz does not exist`)
    -- its plain `timestamp` IS the no-timezone type, so this is a rename
    of the NAME, not a change of type. Emitted as a bare type name so the
    `x::type` shape at the call sites does not move. -#}
    {{ return(adapter.dispatch('type_timestamp_ntz', 'dbt_league')()) }}
{%- endmacro %}

{% macro default__type_timestamp_ntz() -%}
timestamp_ntz
{%- endmacro %}

{% macro duckdb__type_timestamp_ntz() -%}
timestamp
{%- endmacro %}


{% macro epoch_ms_to_timestamp(ms_expr) -%}
{#- Epoch MILLISECONDS -> wall-clock timestamp. The macro takes the raw ms
    value and each engine spells its own conversion, rather than the call
    site pre-dividing: Snowflake's TO_TIMESTAMP_NTZ picks its unit from
    the magnitude of the number it is handed, which is a heuristic worth
    keeping on exactly the shape it was verified against, and DuckDB's
    `epoch_ms` wants the undivided value anyway. Dividing at the call site
    would force DuckDB to multiply back and round.

    Snowflake output text is unchanged from the pre-port call site,
    division and all. -#}
    {{ return(adapter.dispatch('epoch_ms_to_timestamp', 'dbt_league')(ms_expr)) }}
{%- endmacro %}

{% macro default__epoch_ms_to_timestamp(ms_expr) -%}
to_timestamp_ntz({{ ms_expr }}::number / 1000)
{%- endmacro %}

{% macro duckdb__epoch_ms_to_timestamp(ms_expr) -%}
epoch_ms({{ ms_expr }}::bigint)
{%- endmacro %}


{% macro char_from_code(code) -%}
{#- Snowflake CHAR(n), DuckDB CHR(n). Used for char(160), the non-breaking
    space CBS renders between date and time in some eras. -#}
    {{ return(adapter.dispatch('char_from_code', 'dbt_league')(code)) }}
{%- endmacro %}

{% macro default__char_from_code(code) -%}
char({{ code }})
{%- endmacro %}

{% macro duckdb__char_from_code(code) -%}
chr({{ code }})
{%- endmacro %}


{% macro to_date_of(expr) -%}
{#- Timestamp -> date. Snowflake TO_DATE(ts); DuckDB has no to_date at all
    (`Catalog Error: Scalar Function with name to_date does not exist`),
    so it takes the plain cast, which is what TO_DATE does here anyway. -#}
    {{ return(adapter.dispatch('to_date_of', 'dbt_league')(expr)) }}
{%- endmacro %}

{% macro default__to_date_of(expr) -%}
to_date({{ expr }})
{%- endmacro %}

{% macro duckdb__to_date_of(expr) -%}
cast({{ expr }} as date)
{%- endmacro %}


{% macro epoch_s_to_wallclock(expr, tz) -%}
{#- Epoch SECONDS -> wall-clock time in `tz`. CBS renders most eras'
    timestamps client-side as formatTime('...', <unix epoch>), so the epoch
    is the timezone-exact record and the league's wall clock is ET.

    Snowflake: TO_TIMESTAMP_TZ(seconds) then CONVERT_TIMEZONE then drop the
    offset. DuckDB: to_timestamp(seconds) is already TIMESTAMPTZ, and
    timezone(tz, ts) returns the naive wall clock directly -- one step
    fewer for the same answer. Verified equal on a live epoch. -#}
    {{ return(adapter.dispatch('epoch_s_to_wallclock', 'dbt_league')(expr, tz)) }}
{%- endmacro %}

{% macro default__epoch_s_to_wallclock(expr, tz) -%}
convert_timezone('{{ tz }}', to_timestamp_tz({{ expr }}))::timestamp_ntz
{%- endmacro %}

{% macro duckdb__epoch_s_to_wallclock(expr, tz) -%}
timezone('{{ tz }}', to_timestamp({{ expr }}))
{%- endmacro %}


{% macro try_parse_timestamp(expr, sf_format, duckdb_format) -%}
{#- Parse text to a timestamp, NULL when it does not match. Snowflake
    TRY_TO_TIMESTAMP_NTZ; DuckDB TRY_STRPTIME. The format strings are
    different languages, so both are passed explicitly rather than
    translated -- a silent mistranslation here would move dates, and the
    call site is the honest place to see both spellings side by side.

    Two-digit-year pivot: Snowflake's default TWO_DIGIT_CENTURY_START is
    1970 (00-69 -> 2000s), DuckDB's %y follows the 1969 pivot. They differ
    only at literal '69', and this log runs 2001-2026, so no row can
    reach the disagreement. Noted because it is invisible, not because
    it bites. -#}
    {{ return(adapter.dispatch('try_parse_timestamp', 'dbt_league')(expr, sf_format, duckdb_format)) }}
{%- endmacro %}

{% macro default__try_parse_timestamp(expr, sf_format, duckdb_format) -%}
try_to_timestamp_ntz({{ expr }}, '{{ sf_format }}')
{%- endmacro %}

{% macro duckdb__try_parse_timestamp(expr, sf_format, duckdb_format) -%}
try_strptime({{ expr }}, '{{ duckdb_format }}')
{%- endmacro %}


{% macro try_parse_date(expr, sf_format, duckdb_format) -%}
{#- As try_parse_timestamp, landing a DATE. DuckDB has no try_to_date, so
    it parses then casts; try_strptime already returns NULL rather than
    raising on a non-match, so the cast never sees garbage. -#}
    {{ return(adapter.dispatch('try_parse_date', 'dbt_league')(expr, sf_format, duckdb_format)) }}
{%- endmacro %}

{% macro default__try_parse_date(expr, sf_format, duckdb_format) -%}
try_to_date({{ expr }}, '{{ sf_format }}')
{%- endmacro %}

{% macro duckdb__try_parse_date(expr, sf_format, duckdb_format) -%}
cast(try_strptime({{ expr }}, '{{ duckdb_format }}') as date)
{%- endmacro %}


{% macro re_literal(pattern) -%}
{#- A regex written once, spelled as a correct SQL string literal on both
    engines. Snowflake processes backslash escapes inside string literals
    and DuckDB does not, so the SAME source text is two different regexes
    (see regexp_capture's header). Callers pass the logical pattern --
    `\s*ET$` -- and this decides the escaping. Use it for every regex
    argument to a function that IS spelled the same on both engines,
    regexp_replace above all; functions that also need renaming get their
    own macro and handle the literal themselves. -#}
    {{ return(adapter.dispatch('re_literal', 'dbt_league')(pattern)) }}
{%- endmacro %}

{% macro default__re_literal(pattern) -%}
'{{ pattern | replace('\\', '\\\\') }}'
{%- endmacro %}

{% macro duckdb__re_literal(pattern) -%}
'{{ pattern }}'
{%- endmacro %}


{% macro regexp_capture(subject, pattern, case_insensitive=False) -%}
{#- Return capture group 1, or NULL if the pattern does not match.
    Snowflake spells it REGEXP_SUBSTR(subject, pattern, 1, 1, flags) with
    'e' meaning "give me the submatch, not the whole match"; DuckDB spells
    it REGEXP_EXTRACT(subject, pattern, 1).

    THE ESCAPING TRAP, and the reason `pattern` is the LOGICAL regex
    rather than a SQL literal: Snowflake processes backslash escapes
    INSIDE string literals, DuckDB does not. The same source text
    '(\\d{10})\\)' is therefore the regex (\d{10})\) on Snowflake and
    (\\d{10})\\) -- a literal backslash followed by 'd' -- on DuckDB,
    which matches nothing and silently returns NULL for every row. So the
    caller passes one unescaped regex and each engine doubles the
    backslashes or not, according to its own literal rules.

    Case-insensitivity is a flag on Snowflake and an inline (?i) on
    DuckDB's RE2. -#}
    {{ return(adapter.dispatch('regexp_capture', 'dbt_league')(subject, pattern, case_insensitive)) }}
{%- endmacro %}

{% macro default__regexp_capture(subject, pattern, case_insensitive) -%}
regexp_substr({{ subject }}, '{{ pattern | replace('\\', '\\\\') }}', 1, 1, '{{ 'ie' if case_insensitive else 'e' }}')
{%- endmacro %}

{% macro duckdb__regexp_capture(subject, pattern, case_insensitive) -%}
regexp_extract({{ subject }}, '{{ '(?i)' if case_insensitive else '' }}{{ pattern }}', 1)
{%- endmacro %}


-- Scalar-function gaps --------------------------------------------------

{% macro try_to_double(expr) -%}
{#- "parse this text as a float, NULL if it isn't one". Snowflake
    TRY_TO_DOUBLE; DuckDB has no TRY_TO_* family but TRY_CAST is exactly
    the same idea. Both call sites feed it text that is expected to be
    numeric but is not guaranteed to be (CBS 'PPos' is 'SS', MLB's
    atBatsPerHomeRun is '-.--'), so the NULL-not-error behaviour is the
    whole point.

    Equivalence is not assumed from the name -- it is A/B'd against
    Snowflake over the real stat values in both models. -#}
    {{ return(adapter.dispatch('try_to_double', 'dbt_league')(expr)) }}
{%- endmacro %}

{% macro default__try_to_double(expr) -%}
try_to_double({{ expr }})
{%- endmacro %}

{% macro duckdb__try_to_double(expr) -%}
try_cast({{ expr }} as double)
{%- endmacro %}


{% macro boolor_agg(expr) -%}
{#- "did any row in this group say true", NULL-tolerant. Snowflake spells
    it BOOLOR_AGG, DuckDB (and the SQL standard) BOOL_OR. -#}
    {{ return(adapter.dispatch('boolor_agg', 'dbt_league')(expr)) }}
{%- endmacro %}

{% macro default__boolor_agg(expr) -%}
boolor_agg({{ expr }})
{%- endmacro %}

{% macro duckdb__boolor_agg(expr) -%}
bool_or({{ expr }})
{%- endmacro %}


{% macro title_case(expr) -%}
    {{ return(adapter.dispatch('title_case', 'dbt_league')(expr)) }}
{%- endmacro %}

{% macro default__title_case(expr) -%}
initcap({{ expr }})
{%- endmacro %}

{% macro duckdb__title_case(expr) -%}
{#- DuckDB 1.5.5 has NO initcap -- it is not a rename, it is a missing
    function (`Catalog Error: Scalar Function with name initcap does not
    exist`), so this is a shim.

    It reproduces Snowflake's actual rule rather than approximating it:
    a character is uppercased when it is the first character or follows a
    non-alphanumeric, and lowercased otherwise. The tempting shortcut --
    split on spaces, capitalize each word -- is WRONG on the real data:
    it disagrees with Snowflake on 2 of the 98 owner name parts, because
    the seed contains initials like "J.R." where the period is also a word
    boundary. The character-wise form is not being clever, it is being
    correct.

    A regex form is not available: DuckDB's RE2 engine has no lookbehind
    (`invalid perl operator: (?<`), and regexp_replace cannot case-fold a
    capture group anyway.

    PROVEN, not argued: A/B'd against Snowflake's native INITCAP over all
    98 non-empty owner name parts -- 0 mismatches. Nulls pass through as
    NULL, '' returns ''. The names are short and there are dozens of them,
    so the per-character list build costs nothing measurable. -#}
case when {{ expr }} is null then null else coalesce(list_aggregate(
        list_transform(
            range(1, length({{ expr }}) + 1),
            i -> case
                   when i = 1
                     or not regexp_matches(substr(lower({{ expr }}), i - 1, 1), '[a-z0-9]')
                   then upper(substr({{ expr }}, i, 1))
                   else lower(substr({{ expr }}, i, 1))
                 end
        ),
        'string_agg', ''
    ), '') end
{%- endmacro %}
