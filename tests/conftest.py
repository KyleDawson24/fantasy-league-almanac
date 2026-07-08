"""Shared pytest scaffolding.

Adds output/ to sys.path so test files can `import records`, `import
formatters`, etc. — matching how the output scripts import each other
as siblings.

Phase 7 test scope is pure-function: every test in this directory must
run without a Snowflake connection. Functions that hit the warehouse
(query_snowflake, anything that calls it directly) are out of scope
here; covering those would need a fixture warehouse (DuckDB POC,
deferred to v1.x).
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_OUTPUT_DIR = _REPO_ROOT / 'output'

if str(_OUTPUT_DIR) not in sys.path:
    sys.path.insert(0, str(_OUTPUT_DIR))

# Repo root on the path too, so tests can import the league registry the
# same way the edge scripts do: `from config.league_registry import ...`
# (config/ is a namespace package; no __init__.py needed).
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
