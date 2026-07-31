"""Phase 5: surface output-layer divergences in PARALLEL, not serially.

THE PROBLEM THIS SOLVES. Rendering against DuckDB finds one divergence
per attempt: the render stops at the first query DuckDB refuses, and the
next one is invisible until that is fixed. The `at` alias took three
render cycles to get through a SINGLE query (reserved word, then LISTAGG,
then TO_VARCHAR). Serial discovery makes the remaining count unknowable
and every estimate optimistic.

THE APPROACH. Two phases, so control flow is never the thing under test:

  capture -- run an entry point against SNOWFLAKE (which works), with
             query_snowflake wrapped to record every statement it issues.
             Real data, real branching, real corpus.
  explain -- EXPLAIN each captured statement against DuckDB. EXPLAIN
             parses and binds without executing, so every parser/binder
             divergence in the whole corpus reports at once, in seconds,
             and none of them can hide behind another.

What EXPLAIN does NOT catch: anything that only shows up at runtime --
value differences, float width, ordering, type coercion. Those need the
render A/B. This finds the ones that would stop the render; the A/B finds
the ones that would quietly change a cell.

Usage:
  python explain_corpus.py capture --entry espn   [-- extra args]
  python explain_corpus.py explain
"""
import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO / "output"
CORPUS_DIR = REPO / "scratchpad"
DB_PATH = REPO / "data" / "duckdb" / "ESPN_FANTASY.duckdb"

sys.path.insert(0, str(OUTPUT_DIR))
sys.path.insert(0, str(REPO))


def capture(entry, extra):
    """Run an entry point against Snowflake, recording every statement."""
    import db as _db

    real = _db.query_snowflake
    seen = []

    def recording(sql, params=None):
        seen.append({"sql": sql, "has_params": bool(params)})
        return real(sql, params)

    _db.query_snowflake = recording

    if entry == "espn":
        sys.argv = ["generate_almanac_sheet.py", "--no-sheets"] + extra
        import generate_almanac_sheet as mod
        fn = mod.main

    elif entry == "cbs":
        # The CBS almanac has no CLI anchor: the points path ignores
        # season/matchup flags by design, so it renders IN-PROCESS and the
        # live inputs are frozen from inside. Mirrors
        # tests/test_cbs_almanac_byte_diff.py exactly, because a corpus
        # captured through a different freeze is a corpus of a different
        # render.
        import datetime
        import os

        import cbs_almanac_sheets

        _db.set_league("cbs-bsb")
        # _month_window is the ONE deliberately-live board (it reads today's
        # date). Pinned to a COMPLETED month so the corpus depends on
        # warehouse state alone.
        cbs_almanac_sheets._month_window = lambda: (
            datetime.date(2026, 6, 1), datetime.date(2026, 6, 30)
        )
        os.environ["SUPPRESS_UPDATED_STAMP"] = "1"
        fn = cbs_almanac_sheets.build_all_tabs

    else:
        raise SystemExit(f"unknown entry {entry!r}")

    try:
        fn()
    except BaseException as exc:  # noqa: BLE001 - a partial corpus is still a corpus
        print(f"  entry point raised (corpus kept): {type(exc).__name__}: {exc}")

    # Deduplicate: the same statement text issued per-team is one divergence.
    uniq, keys = [], set()
    for q in seen:
        k = " ".join(q["sql"].split())
        if k in keys:
            continue
        keys.add(k)
        uniq.append(q)

    path = corpus_path(entry)
    path.write_text(json.dumps(uniq, indent=1), encoding="utf-8")
    print(f"  captured {len(seen)} statements, {len(uniq)} distinct -> {path.name}")


def to_duckdb_dialect(sql):
    """Re-emit a captured statement the way the DuckDB path would emit it.

    WHY THIS IS NECESSARY, and it is not a fudge. The corpus is captured by
    running against SNOWFLAKE -- that is what gives real control flow and
    real data. So anything the output layer dispatches per engine lands in
    the corpus in its SNOWFLAKE spelling, and EXPLAINing that against DuckDB
    reports a divergence that the DuckDB path would never actually issue.
    Without this step the CBS corpus reported two LISTAGG failures for call
    sites that already route through db.listagg() and are fixed.

    So this models the dialect layer over already-materialised SQL. It must
    mirror db.listagg(): DuckDB takes the ORDER BY inside the call and
    rejects WITHIN GROUP outright.

    KEEP IN SYNC with db.listagg(). If the dialect layer grows a second
    construct, it grows a second clause here, or this tool starts lying in
    the comfortable direction -- reporting clean where the real path
    diverges.
    """
    def match_paren(s, open_idx):
        depth = 0
        for i in range(open_idx, len(s)):
            if s[i] == "(":
                depth += 1
            elif s[i] == ")":
                depth -= 1
                if depth == 0:
                    return i
        return -1

    low = sql.lower()
    out, pos = [], 0
    while True:
        hit = low.find("listagg(", pos)
        if hit == -1:
            out.append(sql[pos:])
            break
        args_open = hit + len("listagg")
        args_close = match_paren(sql, args_open)
        if args_close == -1:
            out.append(sql[pos:])
            break
        m = re.compile(r"\s*within\s+group\s*\(", re.I).match(sql, args_close + 1)
        if not m:
            out.append(sql[pos:args_close + 1])
            pos = args_close + 1
            continue
        wg_open = m.end() - 1
        wg_close = match_paren(sql, wg_open)
        if wg_close == -1:
            out.append(sql[pos:args_close + 1])
            pos = args_close + 1
            continue
        keys = re.sub(r"^order\s+by\s+", "",
                      sql[wg_open + 1:wg_close].strip(), flags=re.I)
        out.append(sql[pos:hit])
        out.append(f"LISTAGG({sql[args_open + 1:args_close].strip()} ORDER BY {keys})")
        pos = wg_close + 1
    return "".join(out)


def corpus_path(entry):
    """One corpus per entry point -- ESPN and CBS issue different SQL, and
    explaining one against the other's expectations proves nothing."""
    return CORPUS_DIR / f"query_corpus_{entry}.json"


def explain(entry):
    import duckdb

    path = corpus_path(entry)
    if not path.exists():
        raise SystemExit(f"no corpus for {entry!r}; run `capture --entry {entry}` first")
    corpus = json.loads(path.read_text(encoding="utf-8"))

    con = duckdb.connect(str(DB_PATH), read_only=True)
    con.execute("USE ESPN_FANTASY.ANALYTICS")

    ok = 0
    failures = []
    for i, q in enumerate(corpus):
        sql = q["sql"]
        # Bind params are pyformat in the source; EXPLAIN only needs a
        # parseable statement, so give the placeholders a literal.
        probe = to_duckdb_dialect(sql).replace("%s", "NULL")
        try:
            con.execute("EXPLAIN " + probe)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            failures.append({
                "i": i,
                "type": type(exc).__name__,
                "msg": str(exc).strip().splitlines()[0][:200],
                "sql": " ".join(sql.split())[:180],
            })
    con.close()

    print(f"  EXPLAIN: {ok} of {len(corpus)} distinct statements bind cleanly on DuckDB")
    if not failures:
        print("  no parser/binder divergences remain in this corpus.")
        return 0

    print(f"\n  {len(failures)} DIVERGENCE(S) -- all of them, not just the first:\n")
    for f in failures:
        print(f"  [{f['type']}] {f['msg']}")
        print(f"      {f['sql']}\n")
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["capture", "explain"])
    ap.add_argument("--entry", default="espn")
    args, extra = ap.parse_known_args()
    if args.mode == "capture":
        capture(args.entry, extra)
        return 0
    return explain(args.entry)


if __name__ == "__main__":
    sys.exit(main())
