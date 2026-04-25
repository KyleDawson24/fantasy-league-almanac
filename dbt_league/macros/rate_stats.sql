-- rate_stats.sql
-- Grain-agnostic rate-stat macros. Each macro takes the column names of the
-- underlying counting stats as parameters and returns a SQL expression.
-- Defined once here, used wherever rates need to be computed (player-weekly
-- mart, team-weekly mart, or any future grain).
--
-- All macros apply NULLIF(denom, 0) to return NULL rather than divide by zero.
-- The * 1.0 / ... pattern forces float division in Snowflake (integer / integer
-- can yield integer truncation).

-- Hitting rates ---------------------------------------------------------------

{% macro batting_avg(h='h', ab='ab') %}
    {{ h }} * 1.0 / nullif({{ ab }}, 0)
{% endmacro %}

{% macro on_base_pct(h='h', bb='b_bb', hbp='hbp', ab='ab', sf='sf') %}
    ({{ h }} + {{ bb }} + {{ hbp }}) * 1.0
    / nullif({{ ab }} + {{ bb }} + {{ hbp }} + {{ sf }}, 0)
{% endmacro %}

{% macro slugging_pct(tb='tb', ab='ab') %}
    {{ tb }} * 1.0 / nullif({{ ab }}, 0)
{% endmacro %}

-- OPS is defined as OBP + SLG. Composes the two macros above so there's still
-- only one definition of each underlying formula.
{% macro ops(h='h', bb='b_bb', hbp='hbp', ab='ab', sf='sf', tb='tb') %}
    ({{ on_base_pct(h, bb, hbp, ab, sf) }})
    + ({{ slugging_pct(tb, ab) }})
{% endmacro %}

-- Pitching rates --------------------------------------------------------------
-- Innings pitched = outs / 3. Every pitching rate denominator uses IP.

{% macro era(er='er', outs='outs') %}
    {{ er }} * 9.0 / nullif({{ outs }} / 3.0, 0)
{% endmacro %}

{% macro whip(p_bb='p_bb', p_h='p_h', outs='outs') %}
    ({{ p_bb }} + {{ p_h }}) * 1.0 / nullif({{ outs }} / 3.0, 0)
{% endmacro %}

{% macro k_per_9(k='k', outs='outs') %}
    {{ k }} * 9.0 / nullif({{ outs }} / 3.0, 0)
{% endmacro %}

{% macro k_per_bb(k='k', p_bb='p_bb') %}
    {{ k }} * 1.0 / nullif({{ p_bb }}, 0)
{% endmacro %}
