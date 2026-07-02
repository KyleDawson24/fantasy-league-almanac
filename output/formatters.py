"""
Shared player-stat-line formatters for the weekly recap (Top Hitter /
Top Pitcher) and for record callouts (#3 new-record callouts; #5 polish).

Renders position-aware lines:
  Hitter:  .391/.462/1.174 -- 23 AB, 5 HR, 6 RBI
  Pitcher: 2-0, 0.69 ERA, 1.23 WHIP -- 13.0 IP, 9 K, 2 QS

Top-N suffix is the player's biggest fantasy-point contributors (signed
*_pts descending by default — i.e., positive-only — since the primary
use case is celebratory "best/top" callouts where strikeouts and earned
runs allowed shouldn't dilute the highlight). Pass `positives_only=False`
to rank by absolute pts instead — appropriate for "worst performance"
or balanced-influence contexts.

Excluded from the top-N entirely (regardless of mode):
  Hitter:  AB (sample-size context shown leading the suffix)
  Pitcher: W, L, SV (in prefix), OUTS (shown as IP)

The functions take a dict-like player row (keys = lowercased column
names from fct_player_weekly_active_performance) and return just the stat
line; callers compose the surrounding "X.X pts by Player (Team) -- "
prefix to taste.
"""

# Eligible stats for the top-N suffix. Declaration order is the stable
# tie-breaker when multiple stats have equal |*_pts|.
HITTER_STAT_DISPLAY = {
    'h': 'H', 'b_bb': 'BB', 'b_so': 'K', 'hbp': 'HBP', 'sf': 'SF',
    'hr': 'HR', 'r': 'R', 'rbi': 'RBI', 'sb': 'SB', 'cs': 'CS',
    'tb': 'TB', 'singles': '1B', 'doubles': '2B', 'triples': '3B', 'xbh': 'XBH',
}

PITCHER_STAT_DISPLAY = {
    'k': 'K', 'er': 'ER', 'qs': 'QS', 'hld': 'HLD',
    'p_h': 'H', 'p_bb': 'BB', 'p_hr': 'HR', 'p_r': 'R',
    'cg': 'CG', 'blk': 'BK', 'wp': 'WP',
}

# Top Scorer combines both pools — no prefix, no excluded sample-size stat,
# so W/L/SV (excluded from PITCHER_STAT_DISPLAY because they live in the
# pitcher prefix) and AB / OUTS (excluded as sample columns) are all in
# play here. OUTS gets special-cased to display as IP. For hitters who
# happen to also have pitching pts (Ohtani-style two-way days), pitcher-
# side abbreviations may collide with hitter-side (BB, K, HR, H, R) — left
# unprefixed for now since the underlying keys are unique and two-way
# weeks are rare; revisit if it bites in production.
TOP_SCORER_STAT_DISPLAY = {
    # Hitting
    'h': 'H', 'ab': 'AB', 'b_bb': 'BB', 'b_so': 'K', 'hbp': 'HBP', 'sf': 'SF',
    'hr': 'HR', 'r': 'R', 'rbi': 'RBI', 'sb': 'SB', 'cs': 'CS',
    'tb': 'TB', 'singles': '1B', 'doubles': '2B', 'triples': '3B', 'xbh': 'XBH',
    # Pitching
    'w': 'W', 'l': 'L', 'k': 'K', 'er': 'ER', 'outs': 'IP', 'qs': 'QS',
    'sv': 'SV', 'hld': 'HLD', 'p_h': 'H', 'p_bb': 'BB',
    'p_hr': 'HR', 'p_r': 'R', 'cg': 'CG', 'blk': 'BK', 'wp': 'WP',
}


# Phase 7 G1: STAT_DISPLAY + STAT_ABBREV moved to the seed-driven
# stat_catalog helpers. Callers that used to import these constants from
# formatters now call stat_catalog.get_display_map() / get_abbrev_map()
# directly. Same dict shape (leaderboard_name -> label), same per-stat
# values; the source of truth shifted from a hardcoded Python dict to
# the stat_classification seed table. New tracked stats are now added
# in ONE place (the seed) instead of five.


# Phase 5 (#4): specific position slots we surface in eligibility displays.
# Excluded by absence: BE (bench), IL (injured list), UTIL (any-hitter
# flex), IF (infield flex), FA (free-agent slot), and slot-shape strings
# like '2B/SS' (those describe lineup combo slots, not player eligibility
# per se -- ESPN already lists each specific position separately when a
# player is genuinely multi-position).
_DISPLAYABLE_SLOTS = {
    'C', '1B', '2B', '3B', 'SS', 'LF', 'CF', 'RF', 'OF', 'DH',
    'SP', 'RP', 'P',
}


def filter_eligible_slots(slots):
    """Trim an eligibleSlots array to specific positions for display.

    - Drops BE / IL / UTIL / IF / FA and slash-style flex slots.
    - Collapses the generic OF when a specific outfield slot (LF/CF/RF)
      is present (since LF implies OF eligibility).
    - Collapses the generic P when SP or RP is present (same rationale).
    - Preserves the source order of the remaining slots.

    `slots` is the VARIANT-array column from stg_box_scores; coming back
    from snowflake.connector it's typically a JSON-serialized string, so
    callers should json.loads() before passing in. None or empty input
    returns an empty list.
    """
    if not slots:
        return []
    kept = [s for s in slots if s in _DISPLAYABLE_SLOTS]
    has_specific_of = any(s in {'LF', 'CF', 'RF'} for s in kept)
    has_specific_p  = any(s in {'SP', 'RP'} for s in kept)
    out = []
    for s in kept:
        if s == 'OF' and has_specific_of:
            continue
        if s == 'P' and has_specific_p:
            continue
        out.append(s)
    return out


def fmt_value(v):
    """Integer-style for whole numbers, 1 decimal for floats. None -> 0.
    Used for stat counts in record callouts where a whole-number stat
    (HR=5) shouldn't read as '5.0' but a float (37.3 pts) should keep
    its decimal."""
    if v is None:
        return "0"
    if float(v) == int(v):
        return str(int(v))
    return f"{v:.1f}"


# Score-stat keys that fmt_record_value renders with 1-decimal precision
# regardless of whole-number-ness (e.g., 415.0 -> "415.0", not "415").
# Hardcoded here rather than imported from records.py to keep formatters
# free of cross-module deps.
_SCORE_STAT_KEYS = frozenset({
    'CALCULATED_POINTS', 'CALCULATED_HITTING_PTS', 'CALCULATED_PITCHING_PTS',
    'PLATFORM_POINTS', 'PLATFORM_HITTING_PTS', 'PLATFORM_PITCHING_PTS',
})


def fmt_record_value(stat_name, value):
    """Phase 6.3.3 chunk 6.5: stat-aware value display for record output.

    Returns a string. Three cases:
      - OUTS:        baseball IP notation via fmt_ip (51 outs -> "17.0")
      - Score stats: 1-decimal precision (forces "415.0", not "415")
      - everything else: fmt_value's int-or-1decimal heuristic

    Rate stats (ERA, WHIP, K/9, etc.) fall through to fmt_value which
    keeps their float precision. The label (stat_catalog.get_display_map())
    carries the unit context, so this returns a bare number string without
    suffix.
    """
    if value is None:
        return ""
    if stat_name == 'OUTS':
        return fmt_ip(value)
    if stat_name in _SCORE_STAT_KEYS:
        return f"{value:.1f}"
    return fmt_value(value)


def format_contributors(contributors, max_n=3, value_fmt=None):
    """Top-N stat contributors with tie-handling and zero-tail.

    Each contributor dict has 'display_name' and 'stat_value'. Returns a
    comma-separated string, or None if no non-zero contributors exist.

    Tie handling: if a tie group at position k would push the listed
    count past max_n, switch that group to '{group_size} others with X'.

    Zero-tail: if fewer than max_n positive contributors exist and there
    are zero-valued teammates, append 'N others with 0'.

    Phase 6.3.3 chunk 6.5: `value_fmt` callable lets the caller swap the
    value formatter (e.g., pass fmt_ip for OUTS so contributor counts
    display as baseball IP notation rather than raw outs). Defaults to
    fmt_value to preserve the pre-6.3.3 behavior.

    max_n defaults to 3 (records report); new-record callouts pass max_n=5.
    """
    value_fmt = value_fmt or fmt_value
    sorted_p = sorted(contributors, key=lambda p: p['stat_value'] or 0, reverse=True)
    non_zero = [p for p in sorted_p if (p['stat_value'] or 0) > 0]
    zero_count = len(sorted_p) - len(non_zero)

    if not non_zero:
        return None

    parts = []
    used = 0
    i = 0
    while i < len(non_zero) and used < max_n:
        val = non_zero[i]['stat_value']
        # Find end of this tie group
        j = i
        while j < len(non_zero) and non_zero[j]['stat_value'] == val:
            j += 1
        group = non_zero[i:j]
        group_size = len(group)

        if used + group_size <= max_n:
            for p in group:
                parts.append(f"{p['display_name']}: {value_fmt(val)}")
            used += group_size
        else:
            # Tie group would overflow -- switch to count format
            parts.append(f"{group_size} others with {value_fmt(val)}")
            used = max_n
        i = j

    # Append 'N others with 0' if there's room and zero-valued teammates exist
    if used < max_n and zero_count > 0:
        parts.append(f"{zero_count} others with {value_fmt(0)}")

    return ", ".join(parts)


def fmt_avg(x):
    """Baseball-style rate (.350 not 0.350). NULL -> .000."""
    if x is None:
        return ".000"
    s = f"{x:.3f}"
    return s.lstrip("0") if s.startswith("0.") else s


def fmt_ip(outs):
    """Innings pitched, baseball notation: 7 outs -> 2.1 (NOT 2.333)."""
    if outs is None or outs == 0:
        return "0.0"
    outs = int(outs)
    return f"{outs // 3}.{outs % 3}"


def _top_n_stats(player, display_map, n, positives_only=True):
    """Top N stats by point contribution, skipping zero-count stats.

    positives_only=True (default): filter to pts > 0, sort descending by
    signed pts. Right for celebratory callouts.
    positives_only=False: keep all non-zero, sort descending by |pts|.
    Right for balanced-influence or "worst performance" callouts.

    Stable sort -- ties broken by display_map declaration order.
    """
    candidates = []
    for stat_key, abbrev in display_map.items():
        count = player.get(stat_key) or 0
        if count == 0:
            continue
        pts = player.get(f'{stat_key}_pts') or 0
        if positives_only and pts <= 0:
            continue
        candidates.append((abbrev, count, pts))
    if positives_only:
        candidates.sort(key=lambda t: t[2], reverse=True)
    else:
        candidates.sort(key=lambda t: abs(t[2]), reverse=True)
    return [(a, c) for (a, c, _) in candidates[:n]]


def format_hitter_stats_line(player, top_n=2, positives_only=True):
    """Hitter line: .AVG/.OBP/.SLG -- AB, [top-N counts]"""
    rate = f"{fmt_avg(player['avg'])}/{fmt_avg(player['obp'])}/{fmt_avg(player['slg'])}"
    parts = [f"{int(player['ab'] or 0)} AB"]
    for abbrev, count in _top_n_stats(player, HITTER_STAT_DISPLAY, top_n, positives_only):
        parts.append(f"{int(count)} {abbrev}")
    return f"{rate} -- {', '.join(parts)}"


def format_pitcher_stats_line(player, top_n=2, positives_only=True):
    """Pitcher line: [W-L,] [SV,] ERA, WHIP -- IP, [top-N counts]"""
    prefix_parts = []
    w = int(player['w'] or 0)
    l = int(player['l'] or 0)
    if w > 0 or l > 0:
        prefix_parts.append(f"{w}-{l}")
    sv = int(player['sv'] or 0)
    if sv > 0:
        prefix_parts.append(f"{sv} SV")
    era = player['era']
    whip = player['whip']
    prefix_parts.append(f"{era:.2f} ERA" if era is not None else "-- ERA")
    prefix_parts.append(f"{whip:.2f} WHIP" if whip is not None else "-- WHIP")

    suffix_parts = [f"{fmt_ip(player['outs'])} IP"]
    for abbrev, count in _top_n_stats(player, PITCHER_STAT_DISPLAY, top_n, positives_only):
        suffix_parts.append(f"{int(count)} {abbrev}")

    return f"{', '.join(prefix_parts)} -- {', '.join(suffix_parts)}"


def format_top_scorer_stats_line(player, top_n=5, positives_only=True):
    """Top-scorer line: [top-N counts across hitting + pitching pools].

    No prefix or sample-size context — this is the standalone "what did
    they actually do" list. OUTS surfaces as IP (the only stat-display
    transformation); everything else shows as raw count + abbreviation.
    """
    parts = []
    for abbrev, count in _top_n_stats(player, TOP_SCORER_STAT_DISPLAY, top_n, positives_only):
        if abbrev == 'IP':
            parts.append(f"{fmt_ip(count)} IP")
        else:
            parts.append(f"{int(count)} {abbrev}")
    return ", ".join(parts)
