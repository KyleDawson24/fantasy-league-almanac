"""Read project-wide constants out of ``dbt_league/dbt_project.yml``.

Some values are shared between the warehouse and the Python that renders it
-- the holding-pen franchise id is the live example. dbt already declares it
under ``vars:``, and duplicating the literal on the Python side means a
change has to be made in two places, which is exactly the drift MLB-115 set
out to close.

So dbt_project.yml is the single source of truth and this module is the
Python read path. It is deliberately tiny: no schema, no validation beyond
"the var exists", because the file it reads is already validated by dbt on
every build.
"""
from functools import lru_cache
from pathlib import Path

import yaml

_PROJECT_YML = (Path(__file__).resolve().parents[1]
                / "dbt_league" / "dbt_project.yml")


class DbtVarError(RuntimeError):
    """dbt_project.yml is missing, unreadable, or lacks a requested var."""


@lru_cache(maxsize=1)
def _vars() -> dict:
    if not _PROJECT_YML.is_file():
        raise DbtVarError(
            f"dbt_project.yml not found at {_PROJECT_YML}. It is committed "
            f"with the repo -- a missing file usually means the checkout is "
            f"incomplete."
        )
    try:
        with open(_PROJECT_YML, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise DbtVarError(f"{_PROJECT_YML} is not valid YAML: {e}")
    if not isinstance(data, dict):
        raise DbtVarError(
            f"{_PROJECT_YML} must be a mapping; got {type(data).__name__}."
        )
    return data.get("vars") or {}


def get_dbt_var(name: str, default=None):
    """Return a dbt project var, or ``default`` when it is not declared.

    Pass no default to make a missing var loud -- a silently-defaulted
    sentinel would fence the wrong franchise out of the record boards.
    """
    found = _vars()
    if name not in found:
        if default is not None:
            return default
        raise DbtVarError(
            f"dbt var '{name}' is not declared in {_PROJECT_YML}. Add it "
            f"under 'vars:' -- that file is the single source of truth for "
            f"values the warehouse and the renderers share."
        )
    return found[name]
