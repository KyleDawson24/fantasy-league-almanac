"""Static check: SQL identifiers in the output layer that collide with
DuckDB keywords.

WHY THIS EXISTS. The `at` alias (`LEFT JOIN active_totals at`) was found
only by rendering until DuckDB refused to parse -- one divergence at a
time, each visible only once the previous was cleared. Reserved-word
collisions do not need to be discovered that way: DuckDB publishes its
keyword list as `duckdb_keywords()`, so the whole class can be checked
statically, in a second, in parallel.

Categories, and why not all of them matter equally:
  reserved      -- cannot be an identifier at all. Always a finding.
  type_function -- names a type or function; unusable as a table alias.
                   This is the category `at` is in.
  unreserved / column_name -- legal as identifiers. Not flagged.

Scope: table/CTE aliases and AS-aliases in SQL embedded in output/*.py.
It reads source text rather than a parsed AST because the SQL lives in
f-strings, and a lint that only works on fully-rendered SQL cannot run
until the render already works -- which is the problem it exists to
avoid.
"""
import ast
import re
import sys
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parents[1]
TARGETS = sorted((REPO / "output").glob("*.py"))

# `FROM tbl alias` / `JOIN tbl alias` -- the bare-alias form, which is
# where a keyword collision actually bites.
ALIAS_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_.]*)\s+(?!AS\b)([A-Za-z_][A-Za-z0-9_]*)\b",
    re.IGNORECASE,
)
# Words that follow a relation but are syntax, not an alias.
NOT_ALIASES = {
    "on", "where", "group", "order", "having", "limit", "inner", "left",
    "right", "full", "cross", "join", "union", "select", "and", "or",
    "using", "qualify", "window", "lateral", "offset", "for", "as",
}


SQL_COMMENT_RE = re.compile(r"--[^\n]*")
LOOKS_LIKE_SQL_RE = re.compile(r"\bselect\b[\s\S]*?\bfrom\b", re.IGNORECASE)


def sql_strings(path):
    """Yield (line_no, sql) for every string literal that looks like SQL.

    Scanning raw file text does not work: a first cut flagged six
    "aliases" that were all English prose in comments -- "from fitting TO
    the long note text" parses as `FROM fitting to`. So walk the AST and
    look only at string literals, f-strings included, then drop `--`
    comments inside them.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        raw = None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            raw = node.value
        elif isinstance(node, ast.JoinedStr):
            # Interpolations become a neutral token so `FROM {tbl} alias`
            # still presents an alias in the right position.
            parts = []
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    parts.append(v.value)
                else:
                    parts.append(" _EXPR_ ")
            raw = "".join(parts)
        if not raw:
            continue
        # Word-boundaried, and SELECT must precede FROM. Substring matching
        # flagged a docstring containing "gap-based selection" and "switched
        # from active_points" as SQL.
        if not LOOKS_LIKE_SQL_RE.search(raw):
            continue
        yield node.lineno, SQL_COMMENT_RE.sub(" ", raw)


def keyword_map():
    con = duckdb.connect()
    rows = con.execute(
        "select lower(keyword_name), keyword_category from duckdb_keywords()"
    ).fetchall()
    con.close()
    return dict(rows)


def main():
    kw = keyword_map()
    risky = {"reserved", "type_function"}
    findings = []
    seen = set()

    for path in TARGETS:
        for line_no, sql in sql_strings(path):
            for m in ALIAS_RE.finditer(sql):
                alias = m.group(2)
                low = alias.lower()
                if low in NOT_ALIASES:
                    continue
                cat = kw.get(low)
                if cat in risky:
                    # ast.walk yields a JoinedStr AND its child Constants, so
                    # the same SQL is scanned twice -- dedupe by the finding
                    # itself rather than reporting one site as two.
                    item = (path.relative_to(REPO).as_posix(), alias, cat,
                            " ".join(m.group(0).split())[:88])
                    if item not in seen:
                        seen.add(item)
                        findings.append((item[0], line_no, alias, cat, item[3]))

    print(f"scanned {len(TARGETS)} files, {len(kw)} DuckDB keywords")
    if not findings:
        print("\nOK -- no table alias collides with a reserved or "
              "type_function keyword.")
        return 0

    print(f"\n{len(findings)} FINDING(S):\n")
    for f, ln, alias, cat, src in findings:
        print(f"  {f}:{ln}  alias `{alias}` is a DuckDB {cat} keyword")
        print(f"      {src}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
