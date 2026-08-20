"""Atomic local configuration writer for validated MLB-145 preflights.

The public setup has two existing configuration destinations and only two:
the gitignored repo-root ``.env`` for credentials, and
``config/leagues.yml`` for non-secret league metadata.  This module builds and
validates both desired byte streams before creating a temporary file.  Each
destination then lands through a same-directory ``os.replace``; if the second
replace fails, the first destination is restored from its staged backup.

The writer intentionally does not run extraction, dbt, Google, Snowflake, or
the public-almanac command.  It is UI-agnostic so the CLI and a future web shell
share the same conflict and rollback rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import codecs
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Callable, Mapping, Optional

import yaml
from yaml.nodes import MappingNode, ScalarNode

from config.bootstrap import (
    BootstrapErrorCode,
    BootstrapRequest,
    BootstrapValidationError,
    LeagueProfile,
    is_validated_profile_for_request,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATH = REPO_ROOT / ".env"
DEFAULT_ENV_TEMPLATE_PATH = REPO_ROOT / ".env.example"
DEFAULT_REGISTRY_PATH = REPO_ROOT / "config" / "leagues.yml"
DEFAULT_LEAGUE_KEY = "espn-main"

_CREDENTIAL_KEYS = ("LEAGUE_ID", "ESPN_S2", "SWID")
_EXPECTED_CREDENTIAL_ENV = ["ESPN_S2", "SWID", "LEAGUE_ID"]
_PLATFORM_CREDENTIAL_KEYS = {
    "espn": ("ESPN_S2", "SWID"),
}
_ENV_ASSIGNMENT = re.compile(
    r"^(?P<prefix>\s*(?:export\s+)?)"
    r"(?P<key>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?P<separator>\s*=\s*)"
    r"(?P<raw_value>.*)$"
)
_SHIPPED_PROFILE = {
    "display_name": "ESPN main league",
    "first_season": 2025,
    "final_season": None,
}


@dataclass(frozen=True)
class ConfigurationWriteResult:
    """Safe result metadata for a CLI or future UI."""

    env_path: Path
    registry_path: Path
    env_changed: bool
    registry_changed: bool

    @property
    def changed(self) -> bool:
        return self.env_changed or self.registry_changed


@dataclass(frozen=True)
class CredentialRotationNotice:
    """Credential-free copy a CLI or future UI must show for rotation."""

    platform: str
    credential_keys: tuple[str, ...]
    message: str


@dataclass(frozen=True)
class _EnvAssignment:
    line_index: int
    key: str
    prefix: str
    separator: str
    comment_suffix: str
    newline: str
    value: str = field(repr=False)


@dataclass(frozen=True)
class _EnvDocument:
    lines: tuple[str, ...]
    assignments: Mapping[str, _EnvAssignment]
    newline: str
    bom: bool


@dataclass(frozen=True)
class _WritePlan:
    env_path: Path
    registry_path: Path
    env_original: Optional[bytes] = field(repr=False)
    registry_original: bytes = field(repr=False)
    env_desired: bytes = field(repr=False)
    registry_desired: bytes = field(repr=False)

    @property
    def env_changed(self) -> bool:
        return self.env_original != self.env_desired

    @property
    def registry_changed(self) -> bool:
        return self.registry_original != self.registry_desired


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that refuses duplicate mapping keys."""


def _construct_unique_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            line = key_node.start_mark.line + 1
            raise BootstrapValidationError(
                BootstrapErrorCode.CONFIG_MALFORMED,
                f"League registry contains a duplicate key near line {line}. "
                "Remove the duplicate before running setup again; nothing "
                "was written.",
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def write_validated_configuration(
    request: BootstrapRequest,
    profile: LeagueProfile,
    *,
    league_key: str = DEFAULT_LEAGUE_KEY,
    env_path: Path = DEFAULT_ENV_PATH,
    env_template_path: Path = DEFAULT_ENV_TEMPLATE_PATH,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
) -> ConfigurationWriteResult:
    """Persist a successful preflight as one logical local transaction.

    Nonempty credentials are never replaced unless they already equal the
    validated request.  The existing public ``espn-main`` template slot may be
    filled; any other metadata is changed only when ``LEAGUE_ID`` already
    proves the slot represents this same league.
    """

    plan = _build_write_plan(
        request,
        profile,
        league_key=league_key,
        env_path=Path(env_path),
        env_template_path=Path(env_template_path),
        registry_path=Path(registry_path),
    )
    if not plan.env_changed and not plan.registry_changed:
        return ConfigurationWriteResult(
            env_path=plan.env_path,
            registry_path=plan.registry_path,
            env_changed=False,
            registry_changed=False,
        )

    _apply_write_plan(plan)
    return ConfigurationWriteResult(
        env_path=plan.env_path,
        registry_path=plan.registry_path,
        env_changed=plan.env_changed,
        registry_changed=plan.registry_changed,
    )


def credential_rotation_notice(platform: str) -> CredentialRotationNotice:
    """Return the mandatory shared-scope warning without accepting secrets."""

    normalized = (platform or "").strip().lower()
    keys = _PLATFORM_CREDENTIAL_KEYS.get(normalized)
    if keys is None:
        raise BootstrapValidationError(
            BootstrapErrorCode.UNSUPPORTED_PLATFORM,
            "Credential rotation currently supports ESPN only. No local "
            "credentials were changed.",
        )
    label = normalized.upper()
    return CredentialRotationNotice(
        platform=normalized,
        credential_keys=keys,
        message=(
            f"{label} credentials are shared by all configured {label} "
            f"leagues on this computer. Replacing them can affect every "
            f"configured {label} league."
        ),
    )


def rotate_validated_credentials(
    request: BootstrapRequest,
    profile: LeagueProfile,
    *,
    confirm: Callable[[CredentialRotationNotice], bool],
    league_key: str = DEFAULT_LEAGUE_KEY,
    env_path: Path = DEFAULT_ENV_PATH,
    env_template_path: Path = DEFAULT_ENV_TEMPLATE_PATH,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
) -> ConfigurationWriteResult:
    """Explicitly rotate one platform's credentials after live validation.

    The complete replacement plan is built before confirmation, and the
    callback receives only credential-free scope information. Ordinary setup
    continues to refuse every nonempty replacement.
    """

    notice = credential_rotation_notice(request.platform)
    plan = _build_write_plan(
        request,
        profile,
        league_key=league_key,
        env_path=Path(env_path),
        env_template_path=Path(env_template_path),
        registry_path=Path(registry_path),
        rotation_platform=notice.platform,
    )
    if not plan.env_changed:
        return ConfigurationWriteResult(
            env_path=plan.env_path,
            registry_path=plan.registry_path,
            env_changed=False,
            registry_changed=False,
        )

    try:
        approved = confirm(notice)
    except Exception:
        raise BootstrapValidationError(
            BootstrapErrorCode.CONFIRMATION_DECLINED,
            "Credential rotation confirmation could not be completed. The "
            "existing credentials are unchanged.",
        ) from None
    if approved is not True:
        raise BootstrapValidationError(
            BootstrapErrorCode.CONFIRMATION_DECLINED,
            "Credential rotation was declined. The existing credentials are "
            "unchanged.",
        )

    _apply_write_plan(plan)
    return ConfigurationWriteResult(
        env_path=plan.env_path,
        registry_path=plan.registry_path,
        env_changed=plan.env_changed,
        registry_changed=False,
    )


def _build_write_plan(
    request: BootstrapRequest,
    profile: LeagueProfile,
    *,
    league_key: str,
    env_path: Path,
    env_template_path: Path,
    registry_path: Path,
    rotation_platform: Optional[str] = None,
) -> _WritePlan:
    _validate_preflight_pair(request, profile)
    if league_key != DEFAULT_LEAGUE_KEY:
        raise BootstrapValidationError(
            BootstrapErrorCode.CONFIG_CONFLICT,
            "This setup rung can fill only the existing espn-main public "
            "registry slot. No alternate configuration root or league slot "
            "was created; nothing was written.",
        )
    if (
        env_path.name != ".env"
        or env_template_path.name != ".env.example"
        or env_template_path.parent != env_path.parent
        or registry_path.name != "leagues.yml"
        or registry_path.parent.name != "config"
        or registry_path.parent.parent != env_path.parent
    ):
        raise BootstrapValidationError(
            BootstrapErrorCode.CONFIG_CONFLICT,
            "Setup may write only the established repo-root .env and "
            "config/leagues.yml destinations. No alternate configuration "
            "root was used; nothing was written.",
        )
    if env_path.is_symlink() or registry_path.is_symlink():
        raise BootstrapValidationError(
            BootstrapErrorCode.CONFIG_CONFLICT,
            "Setup refuses symbolic-link configuration destinations because "
            "their final location is ambiguous. Use the release's ordinary "
            "local .env and config/leagues.yml files; nothing was written.",
        )
    for parent, label in (
        (env_path.parent, "credential file"),
        (registry_path.parent, "league registry"),
    ):
        if not parent.is_dir():
            raise BootstrapValidationError(
                BootstrapErrorCode.CONFIG_MALFORMED,
                f"The parent folder for the {label} is missing. Restore the "
                "release folder structure and run setup again; nothing was "
                "written.",
            )

    env_original = _read_optional_bytes(env_path, "credential file")
    if env_original is None:
        if rotation_platform is not None:
            raise BootstrapValidationError(
                BootstrapErrorCode.CONFIG_CONFLICT,
                "Credential rotation requires an existing configured .env. "
                "Run ordinary guided setup first; nothing was written.",
            )
        env_source = _read_required_bytes(
            env_template_path, "credential template"
        )
        source_is_template = True
    else:
        env_source = env_original
        source_is_template = False

    env_document = _parse_env_document(env_source)
    existing_values = (
        {}
        if source_is_template
        else {
            key: env_document.assignments[key].value
            for key in _CREDENTIAL_KEYS
            if key in env_document.assignments
        }
    )
    if source_is_template:
        for key in _CREDENTIAL_KEYS:
            assignment = env_document.assignments.get(key)
            if assignment is None or assignment.value:
                raise BootstrapValidationError(
                    BootstrapErrorCode.CONFIG_MALFORMED,
                    "The credential template no longer contains one blank, "
                    "unambiguous slot for every ESPN setup value. Restore "
                    ".env.example from the release; nothing was written.",
                )

    desired_credentials = {
        "LEAGUE_ID": profile.league_id,
        "ESPN_S2": request.espn_s2,
        "SWID": request.swid,
    }
    if rotation_platform is None:
        _refuse_credential_conflicts(existing_values, desired_credentials)
        env_updates = desired_credentials
    else:
        rotation_keys = _PLATFORM_CREDENTIAL_KEYS.get(rotation_platform)
        if rotation_keys is None:
            raise BootstrapValidationError(
                BootstrapErrorCode.UNSUPPORTED_PLATFORM,
                "Credential rotation currently supports ESPN only. Nothing "
                "was written.",
            )
        _validate_rotation_source(
            existing_values,
            desired_credentials,
            rotation_keys=rotation_keys,
        )
        env_updates = {
            key: desired_credentials[key] for key in rotation_keys
        }
    env_desired = _render_env(env_document, env_updates)

    registry_original = _read_required_bytes(registry_path, "league registry")
    registry_text, registry_bom = _decode_utf8(
        registry_original, "league registry"
    )
    registry_data, registry_node = _load_registry_document(registry_text)
    target = _validate_registry_target(
        registry_data,
        league_key=league_key,
        existing_league_id=existing_values.get("LEAGUE_ID", ""),
        profile=profile,
    )
    if rotation_platform is None:
        registry_rendered = _render_registry_profile(
            registry_text,
            registry_node,
            league_key=league_key,
            current=target,
            profile=profile,
        )
        rendered_data, _ = _load_registry_document(registry_rendered)
        _assert_rendered_target(rendered_data, league_key, profile)
    else:
        registry_rendered = registry_text

    for key in ("ESPN_S2", "SWID"):
        value = desired_credentials[key]
        if len(value) >= 8 and value in registry_rendered:
            raise BootstrapValidationError(
                BootstrapErrorCode.CONFIG_MALFORMED,
                "A credential value would appear in the league registry. "
                "Setup refused the plan; nothing was written.",
            )

    registry_desired = _encode_utf8(registry_rendered, registry_bom)
    return _WritePlan(
        env_path=env_path,
        registry_path=registry_path,
        env_original=env_original,
        registry_original=registry_original,
        env_desired=env_desired,
        registry_desired=registry_desired,
    )


def _validate_preflight_pair(
    request: BootstrapRequest, profile: LeagueProfile
) -> None:
    if not is_validated_profile_for_request(profile, request):
        raise BootstrapValidationError(
            BootstrapErrorCode.UNVALIDATED_PROFILE,
            "Configuration can be written only from a successful live "
            "preflight result. Run validation again; nothing was written.",
        )

    matches = (
        (request.platform or "").strip().lower() == profile.platform == "espn"
        and str(request.league_id).strip() == profile.league_id
        and request.first_season == profile.first_season
        and request.final_season == profile.final_season
        and profile.available_seasons
        == tuple(
            range(profile.first_season, profile.validated_through_season + 1)
        )
        and (
            profile.final_season is None
            or profile.final_season == profile.validated_through_season
        )
    )
    if not matches:
        raise BootstrapValidationError(
            BootstrapErrorCode.UNVALIDATED_PROFILE,
            "The setup values no longer match the successful preflight. Run "
            "validation again with the intended values; nothing was written.",
        )

    for value in (
        profile.league_id,
        request.espn_s2,
        request.swid,
    ):
        if not isinstance(value, str) or not value.strip() or any(
            marker in value for marker in ("\x00", "\r", "\n")
        ):
            raise BootstrapValidationError(
                BootstrapErrorCode.BAD_INPUT,
                "A setup value is blank or contains an unsupported line "
                "break. Refresh the values and run preflight again; nothing "
                "was written.",
            )


def _read_optional_bytes(path: Path, label: str) -> Optional[bytes]:
    try:
        return path.read_bytes() if path.exists() else None
    except OSError:
        raise BootstrapValidationError(
            BootstrapErrorCode.CONFIG_MALFORMED,
            f"The existing {label} could not be read. Check local file "
            "permissions and run setup again; nothing was written.",
        ) from None


def _read_required_bytes(path: Path, label: str) -> bytes:
    value = _read_optional_bytes(path, label)
    if value is None:
        raise BootstrapValidationError(
            BootstrapErrorCode.CONFIG_MALFORMED,
            f"The {label} is missing. Restore it from the release and run "
            "setup again; nothing was written.",
        )
    return value


def _decode_utf8(payload: bytes, label: str) -> tuple[str, bool]:
    bom = payload.startswith(codecs.BOM_UTF8)
    try:
        return payload.decode("utf-8-sig" if bom else "utf-8"), bom
    except UnicodeDecodeError:
        raise BootstrapValidationError(
            BootstrapErrorCode.CONFIG_MALFORMED,
            f"The {label} is not valid UTF-8 text. Restore or resave it as "
            "UTF-8; nothing was written.",
        ) from None


def _encode_utf8(text: str, bom: bool) -> bytes:
    encoded = text.encode("utf-8")
    return codecs.BOM_UTF8 + encoded if bom else encoded


def _parse_env_document(payload: bytes) -> _EnvDocument:
    text, bom = _decode_utf8(payload, "credential file")
    lines = tuple(text.splitlines(keepends=True))
    newline = _first_newline(lines) or os.linesep
    assignments: dict[str, _EnvAssignment] = {}
    normalized_keys: dict[str, str] = {}

    for index, complete_line in enumerate(lines):
        body, line_newline = _split_newline(complete_line)
        stripped = body.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ENV_ASSIGNMENT.match(body)
        if not match:
            raise BootstrapValidationError(
                BootstrapErrorCode.CONFIG_MALFORMED,
                f"Credential file line {index + 1} is not a supported "
                "KEY=value assignment. Fix that line; nothing was written.",
            )
        key = match.group("key")
        normalized = key.upper()
        if normalized in _CREDENTIAL_KEYS and key != normalized:
            raise BootstrapValidationError(
                BootstrapErrorCode.CONFIG_MALFORMED,
                f"Credential file key {key!r} must use the shipped uppercase "
                "spelling. Fix that assignment; nothing was written.",
            )
        if normalized in normalized_keys:
            raise BootstrapValidationError(
                BootstrapErrorCode.CONFIG_MALFORMED,
                f"Credential file contains duplicate key {key!r}. Keep one "
                "assignment per key; nothing was written.",
            )
        normalized_keys[normalized] = key
        value_part, comment_suffix = _split_env_comment(
            match.group("raw_value")
        )
        value = _decode_env_value(value_part, index + 1)
        assignments[key] = _EnvAssignment(
            line_index=index,
            key=key,
            prefix=match.group("prefix"),
            separator=match.group("separator"),
            comment_suffix=comment_suffix,
            newline=line_newline,
            value=value,
        )

    return _EnvDocument(
        lines=lines,
        assignments=assignments,
        newline=newline,
        bom=bom,
    )


def _first_newline(lines: tuple[str, ...]) -> str:
    for line in lines:
        _, newline = _split_newline(line)
        if newline:
            return newline
    return ""


def _split_newline(line: str) -> tuple[str, str]:
    for newline in ("\r\n", "\n", "\r"):
        if line.endswith(newline):
            return line[: -len(newline)], newline
    return line, ""


def _split_env_comment(raw: str) -> tuple[str, str]:
    quote: Optional[str] = None
    escaped = False
    for index, char in enumerate(raw):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if char in ("'", '"'):
            quote = None if quote == char else char if quote is None else quote
            continue
        if char == "#" and quote is None and (
            index == 0 or raw[index - 1].isspace()
        ):
            start = index
            while start > 0 and raw[start - 1] in " \t":
                start -= 1
            return raw[:start], raw[start:]
    return raw, ""


def _decode_env_value(raw: str, line_number: int) -> str:
    value = raw.strip()
    if not value:
        return ""
    if value[0] == "'":
        if len(value) < 2 or value[-1] != "'":
            _raise_bad_env_quote(line_number)
        return value[1:-1]
    if value[0] != '"':
        return value
    if len(value) < 2 or value[-1] != '"':
        _raise_bad_env_quote(line_number)

    result: list[str] = []
    index = 1
    escapes = {
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "\\": "\\",
        '"': '"',
    }
    while index < len(value) - 1:
        char = value[index]
        if char == "\\" and index + 1 < len(value) - 1:
            next_char = value[index + 1]
            result.append(escapes.get(next_char, "\\" + next_char))
            index += 2
            continue
        result.append(char)
        index += 1
    return "".join(result)


def _raise_bad_env_quote(line_number: int) -> None:
    raise BootstrapValidationError(
        BootstrapErrorCode.CONFIG_MALFORMED,
        f"Credential file line {line_number} has an unclosed quoted value. "
        "Fix that line; nothing was written.",
    )


def _refuse_credential_conflicts(
    existing: Mapping[str, str], desired: Mapping[str, str]
) -> None:
    for key, requested in desired.items():
        current = existing.get(key, "")
        if current and current != requested:
            if key == "LEAGUE_ID":
                raise BootstrapValidationError(
                    BootstrapErrorCode.CONFIG_CONFLICT,
                    "This extracted folder is already configured for a "
                    "different ESPN league. Guided v2.0 supports one league "
                    "per extracted folder. Extract a fresh copy into a "
                    "different folder and double-click START_ALMANAC.cmd "
                    "there; nothing was written.",
                )
            label = "league identity" if key == "LEAGUE_ID" else key
            raise BootstrapValidationError(
                BootstrapErrorCode.CONFIG_CONFLICT,
                f"The local credential file already has a different nonempty "
                f"{label} value. Ordinary setup never replaces saved "
                "credentials, so nothing was written. For an expired ESPN "
                "session, close this window and double-click "
                "ROTATE_ESPN_CREDENTIALS.cmd; the replacement is validated "
                "and explicitly confirmed before either value changes.",
            )


def _validate_rotation_source(
    existing: Mapping[str, str],
    desired: Mapping[str, str],
    *,
    rotation_keys: tuple[str, ...],
) -> None:
    current_league_id = existing.get("LEAGUE_ID", "")
    if not current_league_id or current_league_id != desired["LEAGUE_ID"]:
        raise BootstrapValidationError(
            BootstrapErrorCode.CONFIG_CONFLICT,
            "Credential rotation can update cookies only for the ESPN league "
            "already configured in this extracted folder. Guided v2.0 "
            "supports one league per extracted folder. To configure another "
            "league, extract a fresh copy into a different folder and "
            "double-click START_ALMANAC.cmd there; nothing was written.",
        )
    if any(not existing.get(key, "") for key in rotation_keys):
        raise BootstrapValidationError(
            BootstrapErrorCode.CONFIG_CONFLICT,
            "Credential rotation requires existing nonempty ESPN credentials. "
            "Use ordinary guided setup to fill blank values; nothing was "
            "written.",
        )


def _render_env(
    document: _EnvDocument, desired: Mapping[str, str]
) -> bytes:
    lines = list(document.lines)
    missing: list[str] = []
    for key, value in desired.items():
        assignment = document.assignments.get(key)
        if assignment is None:
            missing.append(key)
            continue
        if assignment.value == value:
            continue
        lines[assignment.line_index] = (
            f"{assignment.prefix}{assignment.key}{assignment.separator}"
            f"{_quote_env_value(value)}{assignment.comment_suffix}"
            f"{assignment.newline}"
        )

    if missing:
        if lines and not _split_newline(lines[-1])[1]:
            lines[-1] += document.newline
        for key in missing:
            lines.append(
                f"{key}={_quote_env_value(desired[key])}{document.newline}"
            )

    return _encode_utf8("".join(lines), document.bom)


def _quote_env_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _load_registry_document(text: str) -> tuple[dict, MappingNode]:
    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)
        node = yaml.compose(text, Loader=_UniqueKeyLoader)
    except BootstrapValidationError:
        raise
    except yaml.YAMLError:
        raise BootstrapValidationError(
            BootstrapErrorCode.CONFIG_MALFORMED,
            "League registry is not valid YAML. Fix its structure or restore "
            "it from the release; nothing was written.",
        ) from None
    if not isinstance(data, dict) or not isinstance(node, MappingNode):
        raise BootstrapValidationError(
            BootstrapErrorCode.CONFIG_MALFORMED,
            "League registry must be a mapping with default_league and "
            "leagues entries; nothing was written.",
        )
    leagues = data.get("leagues")
    default = data.get("default_league")
    if not isinstance(leagues, dict) or not leagues or default not in leagues:
        raise BootstrapValidationError(
            BootstrapErrorCode.CONFIG_MALFORMED,
            "League registry must contain at least one league and a valid "
            "default_league; nothing was written.",
        )
    for key, entry in leagues.items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            raise BootstrapValidationError(
                BootstrapErrorCode.CONFIG_MALFORMED,
                "Every league registry entry must be a named mapping; nothing "
                "was written.",
            )
        if entry.get("platform") not in ("espn", "cbs", "yahoo", "fantrax"):
            raise BootstrapValidationError(
                BootstrapErrorCode.CONFIG_MALFORMED,
                f"League registry entry {key!r} has an unsupported platform; "
                "nothing was written.",
            )
        credential_env = entry.get("credential_env") or []
        if not isinstance(credential_env, list) or not all(
            isinstance(item, str) for item in credential_env
        ):
            raise BootstrapValidationError(
                BootstrapErrorCode.CONFIG_MALFORMED,
                f"League registry entry {key!r} has malformed credential_env; "
                "nothing was written.",
            )
        if not isinstance(entry.get("sinks") or {}, dict):
            raise BootstrapValidationError(
                BootstrapErrorCode.CONFIG_MALFORMED,
                f"League registry entry {key!r} has malformed sinks; nothing "
                "was written.",
            )
    return data, node


def _validate_registry_target(
    data: dict,
    *,
    league_key: str,
    existing_league_id: str,
    profile: LeagueProfile,
) -> dict:
    if data.get("default_league") != league_key:
        raise BootstrapValidationError(
            BootstrapErrorCode.CONFIG_CONFLICT,
            "The registry default is no longer the public espn-main slot. "
            "Setup refused to retarget it; nothing was written.",
        )
    leagues = data["leagues"]
    target = leagues.get(league_key)
    if not isinstance(target, dict):
        raise BootstrapValidationError(
            BootstrapErrorCode.CONFIG_CONFLICT,
            "The public espn-main registry slot is missing. Restore the "
            "release template; nothing was written.",
        )
    if (
        target.get("platform") != "espn"
        or target.get("league_id_env") != "LEAGUE_ID"
        or target.get("credential_env") != _EXPECTED_CREDENTIAL_ENV
        or "league_id" in target
    ):
        raise BootstrapValidationError(
            BootstrapErrorCode.CONFIG_CONFLICT,
            "The espn-main registry slot no longer has the shipped local "
            "credential references. Setup refused to reinterpret it; nothing "
            "was written.",
        )

    current_profile = {
        "display_name": target.get("display_name") or league_key,
        "first_season": target.get("first_season"),
        "final_season": target.get("final_season"),
    }
    desired_profile = {
        "display_name": profile.league_name,
        "first_season": profile.first_season,
        "final_season": profile.final_season,
    }
    if not existing_league_id and current_profile not in (
        _SHIPPED_PROFILE,
        desired_profile,
    ):
        raise BootstrapValidationError(
            BootstrapErrorCode.CONFIG_CONFLICT,
            "The espn-main registry metadata is neither the shipped template "
            "nor this validated league, and no existing league ID proves its "
            "identity. Nothing was written.",
        )
    return target


def _render_registry_profile(
    text: str,
    root: MappingNode,
    *,
    league_key: str,
    current: dict,
    profile: LeagueProfile,
) -> str:
    leagues_node = _mapping_value(root, "leagues")
    target_node = _mapping_value(leagues_node, league_key)
    if not isinstance(target_node, MappingNode):
        _raise_registry_shape()

    desired = {
        "display_name": profile.league_name,
        "first_season": profile.first_season,
        "final_season": profile.final_season,
    }
    replacements: list[tuple[int, int, str]] = []
    for field_name, value in desired.items():
        value_node = _mapping_value(target_node, field_name)
        if not isinstance(value_node, ScalarNode):
            _raise_registry_shape()
        if current.get(field_name) == value:
            continue
        rendered = (
            json.dumps(value, ensure_ascii=False)
            if isinstance(value, str)
            else "null" if value is None else str(value)
        )
        replacements.append(
            (value_node.start_mark.index, value_node.end_mark.index, rendered)
        )

    for start, end, rendered in sorted(replacements, reverse=True):
        text = text[:start] + rendered + text[end:]
    return text


def _mapping_value(node, key: str):
    if not isinstance(node, MappingNode):
        _raise_registry_shape()
    for key_node, value_node in node.value:
        if isinstance(key_node, ScalarNode) and key_node.value == key:
            return value_node
    _raise_registry_shape()


def _raise_registry_shape() -> None:
    raise BootstrapValidationError(
        BootstrapErrorCode.CONFIG_MALFORMED,
        "The espn-main registry entry does not contain the shipped scalar "
        "fields. Restore its structure from the release; nothing was written.",
    )


def _assert_rendered_target(
    data: dict, league_key: str, profile: LeagueProfile
) -> None:
    entry = data["leagues"].get(league_key)
    expected = {
        "display_name": profile.league_name,
        "first_season": profile.first_season,
        "final_season": profile.final_season,
    }
    if not isinstance(entry, dict) or any(
        entry.get(key) != value for key, value in expected.items()
    ):
        raise BootstrapValidationError(
            BootstrapErrorCode.CONFIG_MALFORMED,
            "The planned registry update did not validate in memory. Nothing "
            "was written.",
        )


def _apply_write_plan(plan: _WritePlan) -> None:
    destinations = []
    if plan.env_changed:
        destinations.append(
            (plan.env_path, plan.env_original, plan.env_desired, True)
        )
    if plan.registry_changed:
        destinations.append(
            (
                plan.registry_path,
                plan.registry_original,
                plan.registry_desired,
                False,
            )
        )

    staged: dict[Path, Path] = {}
    backups: dict[Path, Path] = {}
    try:
        for destination, original, desired, secret in destinations:
            staged[destination] = _write_temp(
                destination, desired, secret=secret, purpose="staged"
            )
            if original is not None:
                backups[destination] = _write_temp(
                    destination, original, secret=secret, purpose="backup"
                )
    except OSError:
        _cleanup_temps(*staged.values(), *backups.values())
        raise BootstrapValidationError(
            BootstrapErrorCode.WRITE_FAILED,
            "Setup could not stage both local configuration files. The "
            "original files are unchanged; check free space and permissions, "
            "then try again.",
        ) from None

    applied: list[tuple[Path, Optional[bytes]]] = []
    try:
        for destination, original, _desired, _secret in destinations:
            _replace_file(staged[destination], destination)
            applied.append((destination, original))
    except OSError:
        rollback_failed = False
        for destination, original in reversed(applied):
            try:
                backup = backups.get(destination)
                if original is None:
                    destination.unlink(missing_ok=True)
                elif backup is not None:
                    _replace_file(backup, destination)
            except OSError:
                rollback_failed = True
        _cleanup_temps(*staged.values(), *backups.values())
        if rollback_failed:
            raise BootstrapValidationError(
                BootstrapErrorCode.WRITE_FAILED,
                "Setup could not finish the two-file update and automatic "
                "rollback also failed. Stop without rerunning and restore the "
                "local .env and config/leagues.yml from backup.",
            ) from None
        raise BootstrapValidationError(
            BootstrapErrorCode.WRITE_FAILED,
            "Setup could not update both local configuration files. The prior "
            "state was restored; check permissions and try again.",
        ) from None

    _cleanup_temps(*backups.values())


def _write_temp(
    destination: Path, payload: bytes, *, secret: bool, purpose: str
) -> Path:
    suffix = ".env" if secret else ".tmp"
    fd, raw_path = tempfile.mkstemp(
        prefix=f".bootstrap-{purpose}-",
        suffix=suffix,
        dir=destination.parent,
    )
    path = Path(raw_path)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if destination.exists():
            os.chmod(path, destination.stat().st_mode)
        elif secret:
            os.chmod(path, 0o600)
        return path
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        path.unlink(missing_ok=True)
        raise


def _replace_file(source: Path, destination: Path) -> None:
    os.replace(source, destination)


def _cleanup_temps(*paths: Path) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
