{% macro cbs_draft_label(col) %}
{#- The display label for a CBS draft, derived from its draft_key (MLB-90).

    A draft_key is 'season:period[:name]'. Where a name segment exists it
    IS the label ('2026:2:Mega Draft' -> 'Mega Draft'); the two-segment
    early-era keys ('2011:Pre-season') never carried one, and every one of
    those drafts is a pre-season draft.

    Kept as a macro rather than inlined because the zip year builds its
    label from BOTH of its parts, so the derivation is used three times in
    one model. -#}
case
    when length({{ col }}) - length(replace({{ col }}, ':', '')) = 2
        then split_part({{ col }}, ':', 3)
    else 'Pre-season'
end
{% endmacro %}
