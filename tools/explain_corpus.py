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
  compare -- EXECUTE each captured statement on BOTH engines and diff the
             result sets. This is the RUNTIME class that EXPLAIN
             structurally cannot see.

EXPLAIN finds the divergences that would STOP the render; compare finds
the ones that would quietly CHANGE A CELL. Neither substitutes for the
other, and the CBS almanac needed both: EXPLAIN cleared its three
parser/binder classes, after which 7 tabs still differed on values alone.

Why `compare` rather than reading the renderer backwards: a divergent cell
gives you a number, not a query, and the CBS almanac issues ~200 of them.
Running the whole corpus localises every divergence in one pass, which is
the same argument that motivated `explain`, applied one class over.

Usage:
  python tools/explain_corpus.py capture --entry espn   [-- extra args]
  python tools/explain_corpus.py explain --entry espn
  python tools/explain_corpus.py compare --entry cbs
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
        # params are RECORDED, not merely counted. A bare has_params flag is
        # enough to EXPLAIN a statement (placeholders become NULL) and not
        # enough to RE-EXECUTE one, which `compare` must do. 50 of the CBS
        # corpus's statements are parameterised -- skipping them would leave
        # a hole exactly where a value divergence could hide.
        seen.append({"sql": sql, "params": list(params) if params else None})
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

    # Deduplicate on TEXT AND PARAMS. Text alone was right for `explain` --
    # one spelling is one parser divergence however many teams issue it --
    # but wrong for `compare`, where the same text with a different team id
    # is a different result set. Collapsing those hid 45 of the CBS corpus's
    # 196 statements behind their first binding.
    uniq, keys = [], set()
    for q in seen:
        k = (" ".join(q["sql"].split()), json.dumps(q["params"], default=str))
        if k in keys:
            continue
        keys.add(k)
        uniq.append(q)

    path = corpus_path(entry)
    path.write_text(json.dumps(uniq, indent=1, default=str), encoding="utf-8")
    n_par = sum(1 for q in uniq if q.get("params"))
    print(f"  captured {len(seen)} statements, {len(uniq)} distinct "
          f"({n_par} parameterised) -> {path.name}")


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


def _canon(v):
    """One comparable form per value, WITHOUT deciding equality by rounding.

    The driver contract differs where the values do not: Snowflake hands
    back Decimal where DuckDB hands back float, and dates arrive as
    different objects. Those are normalised. Numeric PRECISION is not --
    a 1e-16 delta is representation and 1e-7 is a real divergence, and
    picking a tolerance here would decide that in advance.
    """
    import datetime
    import decimal

    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, (decimal.Decimal, int, float)):
        return float(v)
    if isinstance(v, (datetime.datetime, datetime.date)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).hex()
    return str(v)


def compare(entry, limit_report):
    """Execute the corpus on both engines; diff values, then order."""
    import duckdb

    import db as _db

    path = corpus_path(entry)
    if not path.exists():
        raise SystemExit(f"no corpus for {entry!r}; run `capture --entry {entry}` first")
    corpus = json.loads(path.read_text(encoding="utf-8"))
    if corpus and "params" not in corpus[0]:
        raise SystemExit(
            "this corpus predates param capture and cannot be re-executed; "
            f"re-run `capture --entry {entry}`")

    _db.init()
    if entry == "cbs":
        _db.set_league("cbs-bsb")

    con = duckdb.connect(str(DB_PATH), read_only=True)
    con.execute("USE ESPN_FANTASY.ANALYTICS")

    def duck(sql, params):
        # to_duckdb_dialect, for the same reason `explain` needs it: the
        # corpus holds Snowflake's spelling of anything dispatched per
        # engine, and the DuckDB path would never issue that text.
        cur = con.execute(to_duckdb_dialect(sql).replace("%s", "?"),
                          list(params) if params else [])
        cols = [d[0].lower() for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def rowkey(row, cols):
        return tuple(repr(_canon(row.get(c))) for c in cols)

    clean, errors, diffs, order_only = 0, [], [], 0

    for i, q in enumerate(corpus):
        sql, params = q["sql"], q.get("params")
        try:
            sf = _db.query_snowflake(sql, params)
        except Exception as exc:  # noqa: BLE001
            errors.append((i, "SNOWFLAKE", type(exc).__name__, str(exc)[:150], sql))
            continue
        try:
            dd = duck(sql, params)
        except Exception as exc:  # noqa: BLE001
            errors.append((i, "DUCKDB", type(exc).__name__, str(exc)[:150], sql))
            continue

        sf_cols = list(sf[0]) if sf else []
        dd_cols = list(dd[0]) if dd else []
        if sf and dd and sf_cols != dd_cols:
            diffs.append({"i": i, "kind": "COLUMNS", "sql": sql,
                          "detail": f"{sf_cols} vs {dd_cols}"})
            continue
        if len(sf) != len(dd):
            diffs.append({"i": i, "kind": "ROWCOUNT", "sql": sql,
                          "detail": f"{len(sf)} vs {len(dd)} rows"})
            continue
        if not sf:
            clean += 1
            continue

        cols = sf_cols
        # Content is compared as a SORTED MULTISET and order reported
        # separately. Session 3's harness gotcha was four models reporting
        # thousands of mismatches purely because a partial ORDER BY tied and
        # the engines broke the tie differently -- a real finding, but not a
        # value finding, and conflating the two buries the values.
        a_sorted = sorted(sf, key=lambda r: rowkey(r, cols))
        b_sorted = sorted(dd, key=lambda r: rowkey(r, cols))

        bad = []
        for ri, (x, y) in enumerate(zip(a_sorted, b_sorted)):
            for c in cols:
                cx, cy = _canon(x.get(c)), _canon(y.get(c))
                if cx != cy:
                    bad.append((ri, c, cx, cy))

        if bad:
            diffs.append({"i": i, "kind": "VALUES", "sql": sql, "cells": bad,
                          "rows": len(sf), "cols": len(cols)})
        else:
            clean += 1
            if [rowkey(r, cols) for r in sf] != [rowkey(r, cols) for r in dd]:
                order_only += 1

    con.close()

    print(f"\n  corpus: {len(corpus)} distinct statements ({entry})")
    print(f"  value-identical : {clean}")
    print(f"  VALUE DIFFS     : {len(diffs)}")
    print(f"  errored         : {len(errors)}")
    print(f"  (of the identical, {order_only} returned the same rows in a "
          f"different ORDER -- not a value finding)")

    for e in errors:
        print(f"\n  [{e[1]} {e[2]}] stmt {e[0]}: {e[3]}")
        print(f"      {' '.join(e[4].split())[:200]}")

    for d in diffs[:limit_report]:
        print(f"\n  --- stmt {d['i']}  {d['kind']} ---")
        if d["kind"] != "VALUES":
            print(f"      {d['detail']}")
        else:
            bycol = {}
            for _, c, _, _ in d["cells"]:
                bycol[c] = bycol.get(c, 0) + 1
            print(f"      {len(d['cells'])} divergent cells over {d['rows']} rows "
                  f"x {d['cols']} cols; columns: {bycol}")
            for ri, c, cx, cy in d["cells"][:6]:
                print(f"        row {ri} [{c}]  SF={cx!r}  DD={cy!r}")
        print(f"      SQL: {' '.join(d['sql'].split())[:240]}")

    return 1 if (diffs or errors) else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["capture", "explain", "compare"])
    ap.add_argument("--entry", default="espn")
    ap.add_argument("--limit-report", type=int, default=12)
    args, extra = ap.parse_known_args()
    if args.mode == "capture":
        capture(args.entry, extra)
        return 0
    if args.mode == "compare":
        return compare(args.entry, args.limit_report)
    return explain(args.entry)


if __name__ == "__main__":
    sys.exit(main())
