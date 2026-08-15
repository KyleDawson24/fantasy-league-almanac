"""The owner-label presentation fallback (MLB-243).

THE RULE. A displayed Owner cell that would otherwise read
"Owner unavailable" -- or any of the other labels the warehouse falls back
to when a platform withholds every name field -- shows the franchise's full
canonical TEAM NAME instead.

WHY IT IS A PRESENTATION RULE AND NOT A WAREHOUSE ONE. `dim_owner` is
right to say "Owner unavailable": a public ESPN league serves the stable
member GUID and NULLs every first/last/display name, so that label is the
honest description of what the platform served. It is still a statement
about OUR ACCESS, and repeating it once per team down a column whose only
job is telling teams apart makes a workbook unreadable while telling the
reader nothing. The canonical team name does tell them apart, and it is
true.

Identity is untouched. The GUID still keys the owner, the team id still
keys the franchise, and every aggregation upstream is unchanged -- this
swaps a LABEL at the point of display and nothing else. No name is ever
invented: with no team name to fall back to, the sentinel stands.

WHERE IT IS APPLIED. By `db.query_for_presentation` -- and by nothing
else. `db.query_snowflake` returns warehouse truth unmodified; the modules
that read TO RENDER call the presentation wrapper by name instead, so
every tab of both workbooks gets the rule and the callers that resolve
identity, seed configuration, guard PII, extract or generate the schema
contract cannot be reached by it at all.

That split is deliberate and was arrived at the hard way. The rule first
went into `query_snowflake` itself, on the argument that one seam cannot
drift -- it cannot, but the wrong things flow through it, and the audit
that cleared them (the PII guard's owner inventory, the continuity sheet
that becomes owner seeds, the tracked row-count fixture) could only clear
them by reading their SELECT lists. A boundary that holds by coincidence
is not a boundary. An opt-OUT flag would have been worse, because it has
to be remembered by code that does not exist yet. So the default is raw
and rendering asks for the other behaviour explicitly; see `db` for the
two seams and the module lists on either side of them.

Applying it in each RENDERER, rather than at the wrapper they share, was
the shape before that, and it reached two surfaces out of six.
"""

# Labels OUR withheld-name fallback can produce. These are warehouse-side
# constants, not user data, so matching them by value is exact rather than
# heuristic. `dim_owner` emits the first via the `owner_unavailable_label`
# dbt var; the second is its predecessor, matched so a warehouse built
# before that rename behaves the same.
#
# BARE "Unknown" IS DELIBERATELY NOT HERE, and it is not an oversight.
# Measured 2026-08-15: `dim_owner` emits neither label anywhere in the ESPN
# league, and the only "Unknown" in the record book belongs to the unmanned
# sentinel team -- team 7, whose name is `####`. That string arrived from
# the platform, not from our fallback chain, so it is a fact about the
# league rather than a statement about our access. Swapping it for `####`
# would repeat the team column into the owner column and tell the reader
# nothing, while moving a pinned golden.
OWNER_UNAVAILABLE_LABELS = frozenset({
    'Owner unavailable',
    'Unknown owner',
})

# The column names an owner LABEL travels under. `owner_id` is deliberately
# absent: that is identity, and identity is never rewritten here.
OWNER_KEYS = ('owner_display', 'owner_name', 'owner')

# The column names a franchise's full canonical name travels under, in
# preference order. Deliberately narrow -- `display_name` and `name` are
# PLAYER columns on several of these row shapes, and falling back to a
# player's name in an Owner cell would be worse than the sentinel.
TEAM_NAME_KEYS = ('team_name', 'canonical_name', 'franchise_name')


# The DAILY FACT's own vocabulary. `fct_player_daily_performance.owner_name`
# predates dim_owner's resolution and falls back to a bare "Unknown", which
# IS our sentinel on that column -- the Home month board is built straight
# off that fact and printed it once per row. It is deliberately not in the
# set above, because on a dim_owner-resolved column ("owner_display" on the
# marts) a bare "Unknown" is the platform's own label for the unmanned
# sentinel team and is true. Same word, two provenances, one of them ours.
DAILY_FACT_UNAVAILABLE_LABELS = OWNER_UNAVAILABLE_LABELS | {'Unknown'}


def is_unavailable(value, labels=OWNER_UNAVAILABLE_LABELS):
    """Is this owner label one of our withheld-name sentinels?

    An empty owner is NOT one: a blank cell makes no claim, and blanking
    is already how every renderer spells "nothing to show". Substituting a
    team name there would invent a statement rather than replace one.
    (The CBS continuity harvest, which reads owner names and writes them
    to a seed, is safe from this function for a different and stronger
    reason -- it is on the raw seam and never calls it.)
    """
    return isinstance(value, str) and value.strip() in labels


def team_name_for(row, name_keys=TEAM_NAME_KEYS):
    """The row's own full canonical team name, or None."""
    for key in name_keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def apply_row(row, owner_keys=OWNER_KEYS, name_keys=TEAM_NAME_KEYS,
              labels=OWNER_UNAVAILABLE_LABELS):
    """Rewrite this row's sentinel owner labels IN PLACE. Returns the row.

    A no-op unless the row carries both a sentinel owner label and a
    usable team name, which is what keeps it from touching rows that are
    not about a franchise at all.
    """
    present = [k for k in owner_keys if is_unavailable(row.get(k), labels)]
    if not present:
        return row
    fallback = team_name_for(row, name_keys)
    if not fallback:
        return row
    for key in present:
        row[key] = fallback
    return row


def scrub_rows(rows, owner_keys=OWNER_KEYS, name_keys=TEAM_NAME_KEYS):
    """Apply the fallback across a query result set, in place.

    Called from `db.query_for_presentation`, and from there only -- a
    result set that came back through `db.query_snowflake` never reaches
    this function. Cheap where it does run: the fast path is one
    `dict.get` per owner column per row and stops there for the
    overwhelming majority of leagues, the ones whose platform served real
    owner names.
    """
    if not rows:
        return rows
    for row in rows:
        if isinstance(row, dict):
            apply_row(row, owner_keys, name_keys)
    return rows
