{% macro cbs_name_key(col) %}
{#- The ONE normalization every CBS-UI identity join speaks (MLB-63).
    Steps, in order:
      1. periods drop ('B.J.' == 'BJ', 'Jonathan O.' == 'Jonathan O');
      2. the packed era's trailing POS + MLB-team tokens strip
         ('Teagarden, Taylor C TEX' -> 'Teagarden, Taylor') -- only
         forms with a comma ever carry them;
      3. 'Last, First' flips to 'First Last';
      4. case folds, whitespace collapses;
      5. generational suffixes strip ('vladimir guerrero jr' ==
         'vladimir guerrero') -- CBS's report families disagree on them
         (the 2023 roster report drops Vlad's Jr while the transaction
         report keeps it, splitting one player into two identities and
         orphaning his pre-trade months). Same-season Jr/Sr collisions
         don't occur in the league era, and the ambiguity flag catches
         any that ever do.
    The two-way pseudo suffixes '(Batter)'/'(Pitcher)' deliberately
    SURVIVE -- they are distinct CBS players. -#}
trim(regexp_replace(
    trim(regexp_replace(lower(
        regexp_replace(
            regexp_replace(
                replace({{ col }}, '.', ''),
                '^([^,]+,.+?)\\s+[A-Z0-9]{1,3}\\s+[A-Z]{2,4}$', '\\1'
            ),
            '^([^,]+),\\s*(.+)$', '\\2 \\1'
        )
    ), ' +', ' ')),
    ' (jr|sr|ii|iii|iv)$', ''
))
{% endmacro %}
