{% macro latest_by(value, order_by) %}
{#- The LATEST NON-NULL value of a label column within a group (MLB-168).

    The problem this solves: `max(label)` over a group spanning more than
    one value returns whichever string sorts last -- `TB` beats `SD` beats
    `LAA` beats `BOS`. That is lexicographic and order-independent by
    definition, so no upstream ORDER BY, clustering key or insertion order
    can make it mean "most recent". It has no relationship to recency, to
    games played, or to anything a reader would expect.

    On `pro_team` specifically this was correct only by accident: MLB-159's
    defect stamped every row of a player's history with one club, so the
    column was constant within every group and a string maximum over a set
    of identical values returns that value. The alphabetical rule never
    fired because there was never more than one candidate. The moment the
    column reflects reality -- a player traded mid-season carrying two
    clubs -- `max` starts picking alphabetically for real, on live surfaces.

    THE NULL GUARD IS THE LOAD-BEARING PART, and it is not decoration.
    Snowflake's MAX_BY does NOT skip a NULL payload: it finds the row with
    the greatest ordering value and returns whatever sits there, NULL
    included. Post-flip, `pro_team` is NULL on every day a player did not
    appear (52-55% of rostered player-days, measured) -- so a plain
    `max_by(pro_team, scoring_period)` returns NULL for every player whose
    last day in scope was a rest day, blanking the label across the book.
    Measured on the real fact before this macro was written: all 34,738
    ESPN groups would have gone NULL.

    Nulling the ORDERING expression instead of the value is what fixes it.
    A row whose label is NULL gets a NULL sort key, both engines drop it
    from the ordering, and the aggregate lands on the latest row that
    actually carries a label. A group with no labelled row at all still
    returns NULL, which is the honest answer and the one the Unattributed
    band renders from.

    NOT dispatched by adapter, deliberately -- and the reason is worth
    recording, because the naive spelling IS divergent. Verified on both
    engines, same three cases:

        latest row NULL      Snowflake max_by -> NULL   DuckDB -> 'TB'
        (guarded, as below)  Snowflake        -> 'TB'   DuckDB -> 'TB'
        all rows NULL        both -> NULL
        tie on the sort key  both -> same row

    So plain `max_by` is a silent cross-engine divergence and the guarded
    form is the one spelling that agrees. This macro exists to make the
    agreeing spelling the only one anybody writes; it lives here rather
    than in dialect.sql because after the guard the two engines do NOT
    disagree, and dialect.sql is for the constructs where they do.

    TIES ARE NOT BROKEN HERE, and that is a measurement rather than an
    oversight. Two labels sharing the greatest sort key inside one group
    would need a genuine two-club DAY; `clubOfGame` is one scalar per
    player-day entry, so the shape is structurally unreachable on ESPN,
    and it is measured absent today (0 such groups across both books).
    Anyone adding a tie-break should confirm it is load-bearing first --
    the thing doing the work here is the null guard above it. -#}
max_by({{ value }}, case when {{ value }} is null then null else {{ order_by }} end)
{% endmacro %}
