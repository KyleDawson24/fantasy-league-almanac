{% macro stable_sum(expr, scale=1) %}
{#- An order-INDEPENDENT SUM of a float column (MLB-128).

    The problem this solves: SQL engines do not guarantee summation order,
    and IEEE float addition is not associative, so `sum(float_col)` can
    return a slightly different value from one rebuild to the next with no
    code and no data change. Rounding the RESULT does not fix it -- a total
    whose true value sits near a display boundary still lands either side of
    it, which is how a rendered cell moves by 1 between two identical
    builds. Materializing as a table does not fix it either: that freezes a
    value until the next rebuild rather than making it deterministic
    (measured -- fct_player_position_pts is already a table, and a rebuild
    with no code change moved 14 of its 22,587 rows).

    The fix is to sum in EXACT DECIMAL. Fixed-scale decimal addition carries
    no representation error, so it is associative, so the result does not
    depend on the order the engine chose. 6dp is far beyond any scoring
    setting's precision (real ones are 1-3dp), so the cast is lossless on
    real data -- verified 0 changed values across 76,428 CBS and 5,857 ESPN
    points rows.

    The result casts back to a 64-bit float deliberately. The determinism is
    already won at sum time, and keeping the column's type means consumers
    keep receiving floats rather than Decimals -- Python formats the two
    differently (round(Decimal, 1) renders '531' where round(float, 1)
    gives '531.0'), so changing the type would move rendered output for no
    benefit.

    The cast is spelled DOUBLE, not FLOAT (MLB-134). On Snowflake the two
    names are the same 64-bit type, so this spelling is a no-op here. On
    engines where they differ -- DuckDB's FLOAT is 32-bit -- FLOAT would
    silently narrow the result (12345.6789 -> 12345.6787109375) and throw
    away the precision this macro exists to protect.

    scale=none skips the rounding step for sites that round later. -#}
{%- if scale is none -%}
cast(sum(cast({{ expr }} as decimal(18, 6))) as double)
{%- else -%}
cast(round(sum(cast({{ expr }} as decimal(18, 6))), {{ scale }}) as double)
{%- endif -%}
{% endmacro %}
