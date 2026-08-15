"""CBS draft-recap data provider (MLB-90 L2).

Reads the Draft Recap tab's pick rows from `mart_cbs_draft_recap` -- one
warehouse read, nothing else. This file used to be the project's loudest
stopgap: it parsed the draft pages' NDJSON off disk
(data/cbs_raw/.../drafts/parsed/) and read an INTERMEDIATE model
(int_cbs__player_season_stats) straight from Python, two logged exceptions
to the output-reads-marts rule. Both are gone; the output layer is back to
reading only league_key-grained marts.

What moved, and where it went:

  * The per-season assembly plan -- which recording of each draft to trust
    and how to stitch the seasons that ran two -- is now the
    `draft_assembly_plan` seed. It is curation, so it is data.
  * build_picks() / zip_2024(), the assembly itself, is
    `int_cbs__draft_picks`.
  * attach_values(), the identity + two-way value resolution, is
    `mart_cbs_draft_recap`. Its sums are exact decimal rather than float,
    so they cannot drift with row order.

The pick contract is UNCHANGED -- the same 18 keys, in the same emission
order, which tests/test_cbs_draft_recap_tab.py pins and the renderer's
all-time board depends on (its Top Pick is a max() with no tiebreak, so
row order decides ties). The migration was gated on reproducing the
stopgap's output at full precision, per pick: 4,269 picks x 18 fields,
positionally identical.

The value lenses stay switchable end-to-end:
  * calc_total / calc_hitting / calc_pitching -- the warehouse's calculated
    points for the draft season (the record book's lens, and the almanac's
    standing choice after the week-12 platform-points bug).
  * page_total / page_active -- CBS's own Total/Active Fpts columns (2022+
    pages only). Reconciliation evidence, and the only place pre-walk-back
    ACTIVE points exist; not the default lens.

Identity remains id-first, with a dim_player_identity name-key fallback,
and ambiguous names stay flagged and NEVER guessed -- see the mart's header
for the resolution ladder.
"""

from __future__ import annotations

import json

from db import query_for_presentation, league_predicate

FIRST_DRAFT_SEASON = 2011

# The pick contract the renderer and its layout tests are written against.
PICK_FIELDS = (
    "season_year", "draft_label", "round_num", "round_pick", "overall_pick",
    "list_seq", "team_name_raw", "player_cbs_id", "player_name_raw",
    "pos_team_raw", "page_total_fpts", "page_active_fpts", "order_tier",
    "calc_total", "calc_hitting", "calc_pitching", "resolution", "twoway_sum",
)

_INT_FIELDS = frozenset({"season_year", "round_num", "round_pick",
                         "overall_pick", "list_seq"})
# Warehouse numerics arrive as Decimal. The renderer formats with
# round(value, 1), and round(Decimal) renders '531' where round(float) gives
# '531.0' -- so the cast back to float is load-bearing, not cosmetic.
_FLOAT_FIELDS = frozenset({"page_total_fpts", "page_active_fpts",
                           "calc_total", "calc_hitting", "calc_pitching"})


def _coerce(field, value):
    if value is None:
        return None
    if field in _INT_FIELDS:
        return int(value)
    if field in _FLOAT_FIELDS:
        return float(value)
    if field == "twoway_sum":
        return bool(value)
    return value


def fetch_picks() -> list[dict]:
    """Every assembled pick, priced, in assembly order."""
    sql = f"""
        select {', '.join(PICK_FIELDS)}
        from mart_cbs_draft_recap
        where {league_predicate()}
        order by assembly_seq
    """
    return [{f: _coerce(f, row[f]) for f in PICK_FIELDS}
            for row in query_for_presentation(sql)]


def fetch_report() -> dict:
    """Per-year assembly + resolution provenance, the shape the Draft
    Classes digest reads: picks, order tier, rounds, note, resolution.

    `rounds` is suppressed for the zip year on purpose -- its round numbers
    come from the order skeleton while its players ride roster order, so
    reporting a round count would dress up a marriage that is not draft
    order. `note` is the season's provenance line and renders verbatim in
    the Notes column, so it lives in the seed beside the plan it explains.
    """
    sql = f"""
        select
            p.season_year,
            p.order_tier,
            count(*) as picks,
            case when p.order_tier <> 'zip' then max(p.round_num) end as rounds,
            max(n.note) as note
        from mart_cbs_draft_recap p
        left join draft_assembly_plan n
          on  n.league_key  = p.league_key
          and n.season_year = p.season_year
          and n.part_seq    = 1
        where {league_predicate('p')}
        group by p.season_year, p.order_tier
        order by p.season_year
    """
    report = {}
    for row in query_for_presentation(sql):
        info = {
            "picks": int(row["picks"]),
            "order": row["order_tier"],
        }
        # The zip year carries no round count at all rather than a null one:
        # its rounds come from the order skeleton while its players ride
        # roster order, so reporting one would dress up a marriage as a
        # draft order.
        if row["order_tier"] != "zip":
            info["rounds"] = int(row["rounds"]) if row["rounds"] is not None else None
        info["note"] = row["note"] or None
        report[int(row["season_year"])] = info

    # Fill-rate accounting for the marriage seasons -- the check that a
    # re-parse hasn't quietly changed how many slots found players.
    fill_sql = f"""
        select season_year, unfilled_slots, unslotted_players
        from mart_cbs_draft_zip_fill
        where {league_predicate()}
    """
    for row in query_for_presentation(fill_sql):
        year = int(row["season_year"])
        if year in report:
            report[year]["unfilled_slots"] = int(row["unfilled_slots"])
            report[year]["unslotted_players"] = int(row["unslotted_players"])

    tally_sql = f"""
        select season_year, resolution, count(*) as n
        from mart_cbs_draft_recap
        where {league_predicate()}
        group by season_year, resolution
    """
    for row in query_for_presentation(tally_sql):
        year = int(row["season_year"])
        report[year].setdefault("resolution", {})[row["resolution"]] = int(row["n"])
    return report


def get_draft_history() -> tuple[list[dict], dict]:
    """The provider entrypoint: enriched picks + assembly/QA report."""
    return fetch_picks(), fetch_report()


# ---------------------------------------------------------------------------
# QA harness (python output/cbs_draft_recap_data.py) -- prints the evidence
# the morning review needs; writes nothing anywhere.
# ---------------------------------------------------------------------------

def _spearman(xs, ys):
    def rank(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        ranks = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                ranks[order[k]] = avg
            i = j + 1
        return ranks
    rx, ry = rank(xs), rank(ys)
    n = len(rx)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    return cov / (vx * vy) if vx and vy else 0.0


def main() -> None:
    from collections import defaultdict

    import db
    db.set_league("cbs-bsb")
    picks, report = get_draft_history()
    print("== assembly + resolution by year ==")
    for year, info in sorted(report.items()):
        print("  %d: %s" % (year, json.dumps(info)))

    both = [p for p in picks if p.get("calc_total") is not None
            and p.get("page_total_fpts") is not None]
    if both:
        deltas = sorted(abs(p["calc_total"] - p["page_total_fpts"]) for p in both)
        within2 = sum(1 for d in deltas if d <= 2)
        print("\n== page Total Fpts vs calculated (%d picks) ==" % len(both))
        print("  within +/-2: %d (%.1f%%); median delta %.1f; p90 %.1f; max %.1f"
              % (within2, 100 * within2 / len(both), deltas[len(deltas) // 2],
                 deltas[int(len(deltas) * 0.9)], deltas[-1]))
        worst = sorted(both, key=lambda p: -abs(p["calc_total"] - p["page_total_fpts"]))[:8]
        for p in worst:
            print("    %d %-24s calc=%.1f page=%.1f (%s)"
                  % (p["season_year"], p["player_name_raw"], p["calc_total"],
                     p["page_total_fpts"], p["draft_label"]))

    print("\n== list-order vs value (no-order years; rho ~1 would mean lists are draft-ordered) ==")
    for year in sorted(report):
        year_picks = [p for p in picks if p["season_year"] == year
                      and p["order_tier"] == "none" and p.get("calc_total") is not None]
        by_team = defaultdict(list)
        for p in year_picks:
            by_team[p["team_name_raw"]].append(p)
        xs, ys = [], []
        for team_picks in by_team.values():
            for p in team_picks:
                xs.append(p["list_seq"])
                ys.append(-p["calc_total"])
        if xs:
            print("  %d: rho=%.2f over %d picks" % (year, _spearman(xs, ys), len(xs)))

    print("\n== unresolved / ambiguous samples ==")
    misses = [p for p in picks if p["resolution"] in ("unresolved", "ambiguous")]
    for p in misses[:12]:
        print("  %d %-10s %-28s id=%s (%s)" % (p["season_year"], p["resolution"],
                                               p["player_name_raw"], p["player_cbs_id"],
                                               p["team_name_raw"]))
    print("  total misses: %d of %d" % (len(misses), len(picks)))

    twoway = [p for p in picks if p.get("twoway_sum")]
    print("\n== two-way mlbam sums: %d picks ==" % len(twoway))
    for p in twoway[:6]:
        print("  %d %-28s calc=%.1f page=%s" % (p["season_year"], p["player_name_raw"],
                                                p["calc_total"], p["page_total_fpts"]))

    gaps = [p for p in picks if p.get("calc_total") == 0.0
            and (p.get("page_total_fpts") or 0) >= 10]
    print("\n== crosswalk-gap candidates (calc=0 but page credits >=10) ==")
    for p in sorted(gaps, key=lambda p: -(p.get("page_total_fpts") or 0))[:15]:
        print("  %d %-28s page=%.0f id=%s" % (p["season_year"], p["player_name_raw"],
                                              p["page_total_fpts"], p["player_cbs_id"]))
    print("  total: %d" % len(gaps))


if __name__ == "__main__":
    main()
