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
    JSONPath-based extraction. -#}
(select unnest(map_keys(m)) as key, unnest(map_values(m)) as value
     from (select cast({{ expr }} as map(varchar, json)) as m)) as {{ alias }}
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
{{ expr }}{% for seg in path %}->'{{ seg }}'{% endfor %}
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


-- Scalar-function gaps --------------------------------------------------

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
