{% macro cbs_name_key(col) %}
{#- The ONE normalization every CBS-UI identity join speaks (MLB-63).
    Steps, in order:
      1. periods drop ('B.J.' == 'BJ', 'Jonathan O.' == 'Jonathan O');
      2. the packed era's trailing POS + MLB-team tokens strip
         ('Teagarden, Taylor C TEX' -> 'Teagarden, Taylor') -- only
         forms with a comma ever carry them;
      3. 'Last, First' flips to 'First Last';
      4. case folds, whitespace collapses.
    The two-way pseudo suffixes '(Batter)'/'(Pitcher)' deliberately
    SURVIVE -- they are distinct CBS players. -#}
trim(regexp_replace(lower(
    regexp_replace(
        regexp_replace(
            replace({{ col }}, '.', ''),
            '^([^,]+,.+?)\\s+[A-Z0-9]{1,3}\\s+[A-Z]{2,4}$', '\\1'
        ),
        '^([^,]+),\\s*(.+)$', '\\2 \\1'
    )
), ' +', ' '))
{% endmacro %}
