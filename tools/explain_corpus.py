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
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO / "output"
CORPUS = REPO / "scratchpad" / "query_corpus.json"
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

    CORPUS.write_text(json.dumps(uniq, indent=1), encoding="utf-8")
    print(f"  captured {len(seen)} statements, {len(uniq)} distinct -> {CORPUS.name}")


def explain():
    import duckdb

    if not CORPUS.exists():
        raise SystemExit("no corpus; run `capture` first")
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))

    con = duckdb.connect(str(DB_PATH), read_only=True)
    con.execute("USE ESPN_FANTASY.ANALYTICS")

    ok = 0
    failures = []
    for i, q in enumerate(corpus):
        sql = q["sql"]
        # Bind params are pyformat in the source; EXPLAIN only needs a
        # parseable statement, so give the placeholders a literal.
        probe = sql.replace("%s", "NULL")
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
    return explain()


if __name__ == "__main__":
    sys.exit(main())
