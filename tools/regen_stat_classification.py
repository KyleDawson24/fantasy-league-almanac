"""tools/regen_stat_classification.py

Phase 7 Step B1: regenerate dbt_league/seeds/stat_classification.csv from
the current Python source-of-truth.

Two structural changes vs pre-Phase-7 seed:
  1. Renamed column: stat_description -> espn_stat_label (platform-neutral
     naming; v1.x might add Yahoo/Sleeper rows with different labels).
  2. Six new columns: display_name, abbrev, polarity, is_record_candidate,
     is_derived, derivation_expr.

And 17 new rows for stats that appear in the mart leaderboard UNPIVOT but
weren't in the seed (sub-chunk F will rewrite that UNPIVOT as a Jinja loop
over the seed; for the loop to produce the same 56 leaderboard columns as
today, every column needs a seed row).

  - 4 derived counting (PA, SB_CS, W_L, SV_BLSV) -- inline formula at mart
  - 6 rate (ERA/WHIP/K_PER_9/K_PER_BB/HR_PER_9/BB_PER_9) -- team-grain only
  - 1 mart-only (WASTED_POINTS) -- roster-decision metric
  - 6 score (CALCULATED_*, PLATFORM_*) -- aggregate point totals

Derivation logic for the 6 new columns on existing rows:
  - display_name: formatters.STAT_DISPLAY (leaderboard-name keyed; fall
    back to existing espn_stat_label if no display override)
  - abbrev: formatters.STAT_ABBREV (fall back to stat_name)
  - polarity: records.get_effective_polarity() merges
    stg_scoring_settings (sign of points_per_unit) with _IMPLICIT_POLARITY
    overrides. Stats absent from both -> 'neutral'.
  - is_record_candidate: should_track_record('team', name, 'most',
    polarity_map, always_tracked_set). Team-grain only -- player-grain
    surfaces a narrower SCORE_STAT_NAMES set already.
  - is_derived / derivation_expr: hardcoded for the 4 derived stats below.

Modes:
  python tools/regen_stat_classification.py           # print CSV + gap report
  python tools/regen_stat_classification.py --write   # overwrite seed CSV

The script is idempotent: rerunning against the same Python truth produces
the same CSV. Re-run after STAT_DISPLAY / STAT_ABBREV / _IMPLICIT_POLARITY
changes to keep the seed in sync.
"""
import csv
import sys
from io import StringIO
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SEED = REPO / "dbt_league" / "seeds" / "stat_classification.csv"
sys.path.insert(0, str(REPO / "output"))

import db  # noqa: E402
db.init()

import formatters  # noqa: E402
import records  # noqa: E402
from records import (  # noqa: E402
    _SEED_TO_LEADERBOARD,
    get_always_tracked_stats,
    get_effective_polarity,
    should_track_record,
)


# Output column order: identity -> classification -> display -> shape ->
# record-surfacing rules -> metadata. Reader scans top-to-bottom for context.
SCHEMA = [
    'stat_name', 'espn_stat_id', 'stat_category',
    'espn_stat_label', 'display_name', 'abbrev',
    'is_counting', 'is_derived', 'derivation_expr',
    'is_always_tracked', 'is_record_candidate', 'polarity',
    'notes',
]


# ---- New row specifications -------------------------------------------------
# Per Phase 7 B1 design table. Each tuple is (stat_name, stat_category,
# polarity, is_counting, is_derived, derivation_expr, notes). espn_stat_id +
# espn_stat_label always blank (not ESPN-native or no clean ESPN match). is_
# always_tracked + is_record_candidate always true (they all surface as
# records today). display_name + abbrev come from STAT_DISPLAY / STAT_ABBREV.
#
# IMPORTANT: stats that already have a seed row (PA id=16, ERA id=47, WHIP
# id=41) are NOT added here — they get in-place edits via EXISTING_ROW_OVERRIDES
# below. Re-adding them would create duplicate stat_names which would break
# the stg_player_stat_breakdowns -> stat_classification FK uniqueness.
# K/9 (id=49) and K/BB (id=82) stay as ESPN-raw rows in the seed (FK requires
# them); the leaderboard-equivalent rows K_PER_9 / K_PER_BB are added below
# as separate rows for the team-level computed metric.

DERIVED_ROWS = [
    # PA omitted — existing seed row (id=16) gets is_derived/derivation_expr
    # via EXISTING_ROW_OVERRIDES (in-place repurpose, not duplicate).
    ('SB_CS',   'hitting',  'positive', True,  True,  'sb - cs',
     'Phase 7 B1: derived counting (net stolen bases). ESPN-raw "SB-CS" (id 25) catalogs the per-player equivalent separately.'),
    ('W_L',     'pitching', 'positive', True,  True,  'w - l',
     'Phase 7 B1: derived counting (net wins). Not in ESPN seed.'),
    ('SV_BLSV', 'pitching', 'positive', True,  True,  'sv - blsv',
     'Phase 7 B1: derived counting (net saves). Not in ESPN seed.'),
]

RATE_ROWS = [
    # ERA / WHIP omitted — existing seed rows (ids 47, 41) get repurposed in
    # place via EXISTING_ROW_OVERRIDES. K/9 / K/BB existing rows get RENAMED
    # to K_PER_9 / K_PER_BB (also via overrides). Only HR_PER_9 / BB_PER_9
    # are genuinely new rows here.
    ('HR_PER_9', 'pitching', 'negative', False, False, '',
     'Phase 7 B1: team-level rate. Not in ESPN seed. Migrating mart-inline -> fct in sub-chunk E.'),
    ('BB_PER_9', 'pitching', 'negative', False, False, '',
     'Phase 7 B1: team-level rate. Not in ESPN seed. Migrating mart-inline -> fct in sub-chunk E.'),
]

WASTED_ROWS = [
    ('WASTED_POINTS', 'total', 'negative', False, False, '',
     'Phase 7 B1: mart-only roster-decision metric. Higher = worse. Rolls up from fct_weekly_team_inactive_performance post-rearchitect.'),
]

SCORE_ROWS = [
    ('CALCULATED_POINTS',        'total',    'positive', False, False, '',
     'Phase 7 B1: rules-normalized total under current scoring weights. Surfaced as records since Phase 5.'),
    ('CALCULATED_HITTING_PTS',   'hitting',  'positive', False, False, '',
     'Phase 7 B1: calculated hitting points.'),
    ('CALCULATED_PITCHING_PTS',  'pitching', 'positive', False, False, '',
     'Phase 7 B1: calculated pitching points.'),
    ('PLATFORM_POINTS',          'total',    'positive', False, False, '',
     'Phase 7 B1: ESPN platform total. Includes commissioner adjustments + ESPN W/L authority.'),
    ('PLATFORM_HITTING_PTS',     'hitting',  'positive', False, False, '',
     'Phase 7 B1: ESPN platform hitting points.'),
    ('PLATFORM_PITCHING_PTS',    'pitching', 'positive', False, False, '',
     'Phase 7 B1: ESPN platform pitching points.'),
]

# Authoritative is_always_tracked set. The script sets is_always_tracked
# based on membership in this set rather than carrying over the CSV value,
# so the regen is deterministic from Python truth and re-runs cannot
# accumulate state from prior runs.
#
# Semantic note (TODO for sub-chunk G or v1.x): is_always_tracked currently
# does double duty in records.py:
#   (a) "force-surface in recap's new-records section even if polarity rule
#        wouldn't" — short-circuits should_track_record to True.
#   (b) "this stat is meaningful and shouldn't be filtered as noise."
# Many new-in-B1 stats are (b)-tracked (meaningful leaderboard columns) but
# we don't want (a) for them in v1.0 — they shouldn't auto-surface in the
# recap's new-records section. Setting is_always_tracked=false correctly
# disables (a) but loses (b)'s semantic.
#
# Open work item: split is_always_tracked into is_record_force_surface +
# is_tracked so the semantics aren't conflated. Until then, the flag is set
# based on (a) since that's the active downstream effect; (b) lives in the
# "stat is in the leaderboard UNPIVOT" implicit knowledge.
#
# Current membership matches pre-B1 behavior exactly: only the 6 stats that
# had is_always_tracked=true in the original seed.
ALWAYS_TRACKED_STATS = {'H', 'TB', 'XBH', 'SF', 'ER', 'PA'}


# In-place repurposes of existing seed rows. Keyed by current stat_name;
# values are field overrides applied after standard derivation. Avoids
# duplicating stat_names (which would break the breakdowns FK).
#
# Two flavors:
#   - PA/ERA/WHIP keep their stat_name; we just adjust is_derived,
#     derivation_expr, or is_always_tracked.
#   - K/9 / K/BB get RENAMED to K_PER_9 / K_PER_BB via the 'stat_name'
#     override field. The stat_name in stg_player_stat_breakdowns is filtered
#     out separately in stg_player_stat_breakdowns.sql (these rows were
#     already dropped at int via is_counting=false; the stg filter is what
#     makes the FK test pass after the seed-side rename).
#
# Behavior change note for ERA/WHIP/K_PER_9/K_PER_BB/HR_PER_9/BB_PER_9
# (negative + positive-polarity rates now is_always_tracked=true): negative-
# polarity rates previously surfaced records only in direction='most' via
# polarity rule; with is_always_tracked=true they short-circuit
# should_track_record and surface in BOTH directions. The recap's New Records
# section may grow accordingly. Golden baseline gets regenerated.
EXISTING_ROW_OVERRIDES = {
    'PA': {
        # PA is not aggregatable for us (per-player stat); we re-derive at
        # mart from AB+B_BB+HBP+SF. PA stays is_always_tracked=true (was
        # already true pre-B1 — no behavior change).
        'is_counting': 'false',
        'is_derived': 'true',
        'derivation_expr': 'ab + b_bb + hbp + sf',
        'notes': 'Phase 7 B1: ESPN stat ID 16 repurposed. Pipeline re-derives at mart (AB+B_BB+HBP+SF) because ESPN PA value is not aggregatable for us. One catalog row covers both senses.',
    },
    # ERA / WHIP: NO is_always_tracked override. They stay at the existing
    # is_always_tracked=false to preserve pre-B1 recap behavior (surface in
    # direction='most' only via polarity-negative rule). Widening to both
    # directions is a deliberate consumer-facing change, not a B1 effect.
    'K/9': {
        # RENAME: existing seed stat_name K/9 becomes K_PER_9 (matches the
        # leaderboard column). Stg filter drops K/9 breakdown rows. No
        # is_always_tracked override — existing false preserved.
        'stat_name': 'K_PER_9',
        'notes': 'Phase 7 B1: renamed from K/9 to match leaderboard column. Stg filter drops raw K/9 breakdown rows (no downstream consumer; is_counting=false anyway).',
    },
    'K/BB': {
        'stat_name': 'K_PER_BB',
        'notes': 'Phase 7 B1: renamed from K/BB to match leaderboard column. Stg filter drops raw K/BB breakdown rows.',
    },
    '30': {
        # Stat 30 = Hit for the Cycle (15 pts/unit; 2 observed rows across
        # 2 seasons in production data, both rare-but-believable cycle
        # candidates -- same archaeological pattern as PG/SHO per HANDOFF
        # §7). The polarity-driven derivation marks it is_record_candidate=
        # true (since it has a positive scoring weight in stg_scoring_
        # settings), but no fact has a wide '30' column for F's seed-driven
        # UNPIVOT loop to reference. Excluding from is_record_candidate
        # keeps F's loop matching the existing team UNPIVOT exactly.
        # v1.x follow-up: promote cycles to a tracked stat -- add wide
        # column on the daily/weekly/fact layers, fix the mislabeled 'CYC'
        # row at stat_id 31 (148 non-zero rows over 2 seasons, no scoring
        # weight, clearly some other ESPN daily-achievement flag).
        'is_record_candidate': 'false',
    },
}


# ---- Helpers ----------------------------------------------------------------

def _bool(b):
    """Normalize to lowercase 'true'/'false' string matching the existing CSV."""
    if isinstance(b, str):
        return b.strip().lower()
    return 'true' if b else 'false'


def _leaderboard_name(seed_stat):
    """Translate seed stat_name to leaderboard column name (1B->SINGLES etc.).

    Falls back to the input for stats with the same name on both sides.
    """
    return _SEED_TO_LEADERBOARD.get(seed_stat, seed_stat)


def _existing_row(seed_row, polarity_map, always_tracked):
    """Convert an existing seed row (dict from csv.DictReader) to SCHEMA shape.

    Preserves existing values for unchanged columns; renames stat_description
    -> espn_stat_label; derives the 6 new columns from Python truth.

    If the row has an EXISTING_ROW_OVERRIDES entry that renames stat_name
    (e.g. K/9 -> K_PER_9), derivation lookups use the NEW name so that
    display_name/abbrev/polarity land against the leaderboard side rather
    than the legacy ESPN name.
    """
    old_name = seed_row['stat_name']
    overrides = EXISTING_ROW_OVERRIDES.get(old_name, {})
    name = overrides.get('stat_name', old_name)
    lb_name = _leaderboard_name(name)
    polarity = polarity_map.get(lb_name, 'neutral')
    track = should_track_record('team', lb_name, 'most', polarity_map, always_tracked)

    # Read espn_stat_label first (current column name post-B1); fall back to
    # stat_description (pre-B1 name) for forward-compat when running against
    # an unmigrated CSV. The B1 rename + subsequent regen runs accidentally
    # nuked espn_stat_label values for several commits because this lookup
    # was only checking stat_description; the post-F cleanup commit restored
    # the values via a one-shot script and fixed the read here.
    espn_label = seed_row.get('espn_stat_label') or seed_row.get('stat_description', '')

    out = {
        'stat_name':           name,
        'espn_stat_id':        seed_row.get('espn_stat_id', ''),
        'stat_category':       seed_row.get('stat_category', ''),
        'espn_stat_label':     espn_label,
        'display_name':        formatters.STAT_DISPLAY.get(lb_name, espn_label),
        'abbrev':              formatters.STAT_ABBREV.get(lb_name, name),
        'is_counting':         _bool(seed_row.get('is_counting', 'false')),
        'is_derived':          'false',
        'derivation_expr':     '',
        # is_always_tracked is sourced from ALWAYS_TRACKED_STATS (not the CSV)
        # so re-runs don't accumulate state from prior writes. CSV value is
        # intentionally ignored.
        'is_always_tracked':   _bool(name in ALWAYS_TRACKED_STATS),
        'is_record_candidate': _bool(track),
        'polarity':            polarity,
        'notes':               seed_row.get('notes', ''),
    }
    out.update(overrides)
    # Always-tracked stats are by definition record candidates (force-surface).
    if out['is_always_tracked'] == 'true':
        out['is_record_candidate'] = 'true'
    return out


def _new_row(stat_name, stat_category, polarity, is_counting, is_derived, derivation_expr, notes):
    """Build a new (non-ESPN-native) row. is_always_tracked is sourced from
    ALWAYS_TRACKED_STATS — currently none of the new rows are in that set,
    matching pre-B1 behavior for these stats (they were already surfacing
    via SCORE_STAT_NAMES short-circuit for CALCULATED_*, or not surfacing
    at all for PLATFORM_* / WASTED_POINTS / rates / derived). is_record_
    candidate stays 'true' because these stats DO appear on the leaderboard.
    """
    return {
        'stat_name':           stat_name,
        'espn_stat_id':        '',
        'stat_category':       stat_category,
        'espn_stat_label':     '',
        'display_name':        formatters.STAT_DISPLAY.get(stat_name, stat_name),
        'abbrev':              formatters.STAT_ABBREV.get(stat_name, stat_name),
        'is_counting':         _bool(is_counting),
        'is_derived':          _bool(is_derived),
        'derivation_expr':     derivation_expr,
        'is_always_tracked':   _bool(stat_name in ALWAYS_TRACKED_STATS),
        'is_record_candidate': 'true',
        'polarity':            polarity,
        'notes':               notes,
    }


# ---- Build + report ---------------------------------------------------------

def build_rows():
    """Return list of dicts (SCHEMA order): existing rows + new rows.

    Idempotent: re-running against an already-updated CSV yields the same
    result rather than duplicating. Any existing row whose stat_name (or
    post-rename name) collides with a new-row spec is dropped from the
    existing-rows pass and the new-row spec wins.
    """
    polarity_map = get_effective_polarity()
    always_tracked = get_always_tracked_stats()

    new_specs = DERIVED_ROWS + RATE_ROWS + WASTED_ROWS + SCORE_ROWS
    new_names = {spec[0] for spec in new_specs}

    rows = []
    with open(SEED, 'r', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            old_name = r['stat_name']
            overrides = EXISTING_ROW_OVERRIDES.get(old_name, {})
            effective_name = overrides.get('stat_name', old_name)
            if effective_name in new_names:
                # A prior --write already inserted this as a new row; the
                # new-row spec below will re-emit it. Skip here to stay
                # idempotent.
                continue
            rows.append(_existing_row(r, polarity_map, always_tracked))

    for spec in new_specs:
        rows.append(_new_row(*spec))

    return rows


def render_csv(rows):
    """Render rows as a CSV string in SCHEMA order."""
    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=SCHEMA, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def gap_report(rows):
    """Surface mismatches between seed and Python truth so reviewer can spot
    stats that should be added/removed or labels that drifted."""
    seed_lb_names = {_leaderboard_name(r['stat_name']) for r in rows}
    print("\n=== GAP REPORT ===")

    display_missing = sorted(set(formatters.STAT_DISPLAY) - seed_lb_names)
    if display_missing:
        print(f"\nIn STAT_DISPLAY but not in seed ({len(display_missing)}):")
        for s in display_missing:
            print(f"  {s}: {formatters.STAT_DISPLAY[s]}")
    else:
        print("\nSTAT_DISPLAY fully covered by seed.")

    abbrev_missing = sorted(set(formatters.STAT_ABBREV) - seed_lb_names)
    if abbrev_missing:
        print(f"\nIn STAT_ABBREV but not in seed ({len(abbrev_missing)}):")
        for s in abbrev_missing:
            print(f"  {s}: {formatters.STAT_ABBREV[s]}")
    else:
        print("\nSTAT_ABBREV fully covered by seed.")

    # Seed rows that have no STAT_DISPLAY entry — these are catalogued but
    # not surfaced as records (rate stats not tracked, fielding, unused IDs).
    seed_unsurfaced = []
    for r in rows:
        lb = _leaderboard_name(r['stat_name'])
        if lb not in formatters.STAT_DISPLAY:
            seed_unsurfaced.append((r['stat_name'], r['is_record_candidate']))
    if seed_unsurfaced:
        print(f"\nIn seed but not in STAT_DISPLAY ({len(seed_unsurfaced)}): "
              "(catalogued; not surfaced as records)")
        for name, candidate in seed_unsurfaced:
            print(f"  {name}  is_record_candidate={candidate}")

    # Counts
    n_total = len(rows)
    n_candidates = sum(1 for r in rows if r['is_record_candidate'] == 'true')
    n_derived = sum(1 for r in rows if r['is_derived'] == 'true')
    n_always = sum(1 for r in rows if r['is_always_tracked'] == 'true')
    print(f"\nTotals: {n_total} rows, {n_candidates} record candidates, "
          f"{n_derived} derived, {n_always} always_tracked.")


def main():
    rows = build_rows()
    write_mode = '--write' in sys.argv

    if write_mode:
        SEED.write_text(render_csv(rows), encoding='utf-8')
        print(f"Wrote {len(rows)} rows to {SEED.relative_to(REPO)}")
        gap_report(rows)
    else:
        sys.stdout.write(render_csv(rows))
        gap_report(rows)


if __name__ == '__main__':
    main()
