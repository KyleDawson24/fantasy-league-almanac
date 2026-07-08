"""League registry loader (MLB-48 / MLB-57).

Single reader for config/leagues.yml -- the one place a league's platform,
identity, credentials, seasons, and output sinks are declared. Both edge
layers consume it:

  - extract: which league to pull, which env credentials it needs, and the
    league_key to stamp into every RAW row.
  - output:  which league to render (league_key filter on every mart
    query); sink resolution rides MLB-58.

The transform layer (dbt) deliberately does NOT read this file: one dbt
run builds every league, and league_key arrives in the models from the
RAW rows themselves. --league targeting exists only at the edges.

Import pattern: the repo has no installed package, so scripts add the
repo root to sys.path and import `config.league_registry` (config/ is a
namespace package). Example, from extract/ or output/:

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from config.league_registry import get_league

Failure philosophy: everything raises LeagueRegistryError with a message
that names the file, the entry, and the fix. A league run should never
limp forward on a half-configured registry -- clear failure beats a
mis-stamped warehouse.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

# Default registry location: alongside this module.
_REGISTRY_PATH = Path(__file__).resolve().parent / "leagues.yml"

# Adapter names the pipeline recognizes. yahoo/fantrax are reserved by
# the adapter contract (MLB-14 / MLB-42) so a future entry doesn't get
# rejected, but nothing implements them yet.
_KNOWN_PLATFORMS = ("espn", "cbs", "yahoo", "fantrax")


class LeagueRegistryError(Exception):
    """Raised for any registry problem: missing file, malformed YAML,
    unknown league key, invalid entry, or unresolvable league id."""


@dataclass(frozen=True)
class League:
    """One registry entry, validated. league_key (the `key` field) is the
    value stamped into RAW and filtered on in every consumer query."""

    key: str
    platform: str
    display_name: str
    credential_env: Tuple[str, ...] = ()
    league_id_env: Optional[str] = None
    league_id_literal: Optional[str] = None
    first_season: Optional[int] = None
    final_season: Optional[int] = None
    sinks: Dict[str, object] = field(default_factory=dict)

    def resolve_league_id(self) -> str:
        """The platform league id/slug. A literal `league_id` in the
        registry wins; otherwise read the env var named by
        `league_id_env`. Raises with the fix spelled out when neither
        yields a value (ids live in .env because this repo is public)."""
        if self.league_id_literal is not None:
            return str(self.league_id_literal)
        if self.league_id_env:
            value = os.getenv(self.league_id_env)
            if value:
                return value
            raise LeagueRegistryError(
                f"League '{self.key}': league_id_env names {self.league_id_env!r} "
                f"but that variable is unset or empty. Add it to the .env at the "
                f"repo root (or set a literal league_id in config/leagues.yml)."
            )
        raise LeagueRegistryError(
            f"League '{self.key}' declares neither league_id nor league_id_env "
            f"in config/leagues.yml."
        )

    def missing_credentials(self) -> List[str]:
        """Names from credential_env that are unset/empty in the
        environment. Empty list means the adapter can run."""
        return [name for name in self.credential_env if not os.getenv(name)]

    def require_credentials(self) -> None:
        """Fail loudly when any declared credential is absent."""
        missing = self.missing_credentials()
        if missing:
            raise LeagueRegistryError(
                f"League '{self.key}' ({self.platform}) is missing required "
                f"environment variables: {', '.join(missing)}. These are "
                f"declared in config/leagues.yml under credential_env and "
                f"belong in the .env at the repo root."
            )


def _read_yaml(path: Path) -> dict:
    if not path.is_file():
        raise LeagueRegistryError(
            f"League registry not found at {path}. config/leagues.yml is "
            f"committed with the repo -- a missing file usually means the "
            f"working directory is wrong or the checkout is incomplete."
        )
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise LeagueRegistryError(f"League registry {path} is not valid YAML: {e}")
    if not isinstance(data, dict):
        raise LeagueRegistryError(
            f"League registry {path} must be a mapping with 'default_league' "
            f"and 'leagues' keys; got {type(data).__name__}."
        )
    return data


def _build_league(key: str, entry: dict, path: Path) -> League:
    if not isinstance(entry, dict):
        raise LeagueRegistryError(
            f"League '{key}' in {path} must be a mapping of fields; "
            f"got {type(entry).__name__}."
        )

    platform = entry.get("platform")
    if platform not in _KNOWN_PLATFORMS:
        raise LeagueRegistryError(
            f"League '{key}' in {path} has platform={platform!r}; expected one "
            f"of {', '.join(_KNOWN_PLATFORMS)}."
        )

    credential_env = entry.get("credential_env") or []
    if not isinstance(credential_env, list) or not all(
        isinstance(v, str) for v in credential_env
    ):
        raise LeagueRegistryError(
            f"League '{key}' in {path}: credential_env must be a list of "
            f"environment variable names."
        )

    sinks = entry.get("sinks") or {}
    if not isinstance(sinks, dict):
        raise LeagueRegistryError(
            f"League '{key}' in {path}: sinks must be a mapping (surface -> "
            f"destination); got {type(sinks).__name__}."
        )

    return League(
        key=key,
        platform=platform,
        display_name=entry.get("display_name") or key,
        credential_env=tuple(credential_env),
        league_id_env=entry.get("league_id_env"),
        league_id_literal=entry.get("league_id"),
        first_season=entry.get("first_season"),
        final_season=entry.get("final_season"),
        sinks=sinks,
    )


def load_registry(path: Optional[Path] = None) -> Dict[str, object]:
    """Parse + validate the full registry. Returns
    {'default_league': str, 'leagues': {key: League}}.

    Validation is strict across ALL entries (not just the one being
    used): a half-broken registry fails at load, before any extraction
    or output work starts. Cheap at two entries; still cheap at twenty.
    """
    path = Path(path) if path is not None else _REGISTRY_PATH
    data = _read_yaml(path)

    raw_leagues = data.get("leagues")
    if not isinstance(raw_leagues, dict) or not raw_leagues:
        raise LeagueRegistryError(
            f"League registry {path} has no 'leagues' entries."
        )

    leagues = {
        str(key): _build_league(str(key), entry, path)
        for key, entry in raw_leagues.items()
    }

    default_key = data.get("default_league")
    if default_key not in leagues:
        raise LeagueRegistryError(
            f"League registry {path}: default_league={default_key!r} does not "
            f"name a configured league. Known leagues: {', '.join(sorted(leagues))}."
        )

    return {"default_league": default_key, "leagues": leagues}


def get_league(key: Optional[str] = None, path: Optional[Path] = None) -> League:
    """Resolve one league. key=None -> the registry's default_league
    (the ESPN league, keeping the pre-registry runbook unchanged).
    Unknown keys fail with the known-key list in the message."""
    registry = load_registry(path)
    leagues: Dict[str, League] = registry["leagues"]  # type: ignore[assignment]
    if key is None:
        key = registry["default_league"]  # type: ignore[assignment]
    if key not in leagues:
        raise LeagueRegistryError(
            f"Unknown league key {key!r}. Known leagues: "
            f"{', '.join(sorted(leagues))} (see config/leagues.yml)."
        )
    return leagues[key]


def league_keys(path: Optional[Path] = None) -> List[str]:
    """All configured league keys, sorted. For --league flag help text
    and error messages."""
    registry = load_registry(path)
    return sorted(registry["leagues"])  # type: ignore[arg-type]


def default_league_key(path: Optional[Path] = None) -> str:
    """The registry's default league key."""
    registry = load_registry(path)
    return registry["default_league"]  # type: ignore[return-value]
