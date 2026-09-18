-- rate_stats.sql
-- Grain-agnostic rate-stat macros. Each macro takes the column names of the
-- underlying counting stats as parameters and returns a SQL expression.
-- Defined once here, used wherever rates need to be computed (player-weekly
-- mart, team-weekly mart, or any future grain).
--
-- All macros apply NULLIF(denom, 0) to return NULL rather than divide by zero.
-- Explicit DOUBLE operands preserve floating-point division on both engines
-- after the daily union counting-stat casts to INTEGER (MLB-295).

-- Hitting rates ---------------------------------------------------------------

{% macro batting_avg(h='h', ab='ab') %}
    cast({{ h }} as double) * 1.0 / nullif(cast({{ ab }} as double), 0)
{% endmacro %}

{% macro on_base_pct(h='h', bb='b_bb', hbp='hbp', ab='ab', sf='sf') %}
    (cast({{ h }} as double) + cast({{ bb }} as double) + cast({{ hbp }} as double)) * 1.0
    / nullif(cast({{ ab }} as double) + cast({{ bb }} as double) + cast({{ hbp }} as double) + cast({{ sf }} as double), 0)
{% endmacro %}

{% macro slugging_pct(tb='tb', ab='ab') %}
    cast({{ tb }} as double) * 1.0 / nullif(cast({{ ab }} as double), 0)
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
    cast({{ er }} as double) * 9.0 / nullif(cast({{ outs }} as double) / 3.0, 0)
{% endmacro %}

{% macro whip(p_bb='p_bb', p_h='p_h', outs='outs') %}
    (cast({{ p_bb }} as double) + cast({{ p_h }} as double)) * 1.0 / nullif(cast({{ outs }} as double) / 3.0, 0)
{% endmacro %}

{% macro k_per_9(k='k', outs='outs') %}
    cast({{ k }} as double) * 9.0 / nullif(cast({{ outs }} as double) / 3.0, 0)
{% endmacro %}

{% macro k_per_bb(k='k', p_bb='p_bb') %}
    cast({{ k }} as double) * 1.0 / nullif(cast({{ p_bb }} as double), 0)
{% endmacro %}

-- Phase 7 E4: HR/9 and BB/9 migrated from inline-at-mart to fct columns.
-- F's seed-driven Jinja-loop UNPIVOT needs every rate column to live on
-- the fct so it can be selected by name in the loop. Algebraically
-- identical to the previous mart-inline form: p_hr * 27 / outs is the
-- same value as p_hr * 9 / (outs / 3); macros use the latter to match
-- the era/whip/k_per_9 convention. NULL when outs=0 via NULLIF.
{% macro hr_per_9(p_hr='p_hr', outs='outs') %}
    cast({{ p_hr }} as double) * 9.0 / nullif(cast({{ outs }} as double) / 3.0, 0)
{% endmacro %}

{% macro bb_per_9(p_bb='p_bb', outs='outs') %}
    cast({{ p_bb }} as double) * 9.0 / nullif(cast({{ outs }} as double) / 3.0, 0)
{% endmacro %}
