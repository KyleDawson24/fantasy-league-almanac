"""UI-agnostic league bootstrap preflight (MLB-145).

This module owns the read-only validation boundary of guided setup.  It
validates user-supplied ESPN access against the exact season range the registry
entry will request and returns a small, platform-neutral profile.  It does not
write ``.env``, ``config/leagues.yml``, local data, or Google state;
``config.bootstrap_writer`` consumes its successful result in the next layer.

The separation is deliberate: a CLI can prompt today and a future web shell
can call the same functions without moving credential or validation logic.
Credentials are accepted as values, never logged, and excluded from reprs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
import hashlib
import hmac
import sys
from typing import Callable, Mapping, Optional, Sequence

import requests


ESPN_API_BASE = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons"
)
SUPPORTED_PYTHON = (3, 13)
_ESPN_VIEWS = ("mSettings", "mTeam", "mMatchupScore")
_SEASON_POINTS_LEAGUE_TYPE = 5
_VALIDATED_PROFILE_TOKEN = object()


class BootstrapErrorCode(str, Enum):
    """Stable categories a CLI or future UI can present differently."""

    BAD_INPUT = "bad_input"
    UNSUPPORTED_PLATFORM = "unsupported_platform"
    UNSUPPORTED_PYTHON = "unsupported_python"
    AUTH_EXPIRED = "auth_expired"
    ACCESS_DENIED = "access_denied"
    LEAGUE_NOT_FOUND = "league_not_found"
    HISTORY_UNAVAILABLE = "history_unavailable"
    NETWORK = "network"
    UPSTREAM = "upstream"
    UNSUPPORTED_FORMAT = "unsupported_format"
    UNVALIDATED_PROFILE = "unvalidated_profile"
    CONFIG_CONFLICT = "config_conflict"
    CONFIG_MALFORMED = "config_malformed"
    CONFIRMATION_DECLINED = "confirmation_declined"
    RUN_FAILED = "run_failed"
    WRITE_FAILED = "write_failed"


class BootstrapValidationError(RuntimeError):
    """A safe, user-actionable preflight failure.

    Messages deliberately omit league ids, URLs, cookies, and raw response
    bodies.  ``code`` is stable enough for a second UI shell to map to its own
    copy without parsing prose.
    """

    def __init__(self, code: BootstrapErrorCode, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class BootstrapRequest:
    """Values supplied by a setup UI.

    Credential fields use ``repr=False`` so an exception, debugger, or test
    failure cannot casually print them.  The writer consumes the same object
    only after this preflight succeeds.
    """

    platform: str
    league_id: str = field(repr=False)
    espn_s2: str = field(repr=False)
    swid: str = field(repr=False)
    first_season: int
    final_season: Optional[int] = None


@dataclass(frozen=True)
class LeagueProfile:
    """Validated facts safe for a setup UI to display."""

    platform: str
    league_name: str
    team_count: int
    league_format: str
    format_evidence: str
    available_seasons: tuple[int, ...]
    first_season: int
    # ``None`` preserves the registry's meaning: an ongoing league.  The
    # concrete year validated in this run lives separately so the writer
    # does not accidentally freeze an ongoing league at today's season.
    final_season: Optional[int]
    validated_through_season: int
    league_id: str = field(repr=False)
    _request_fingerprint: bytes = field(
        default=b"", repr=False, compare=False
    )
    _validation_token: object = field(
        default=None, repr=False, compare=False
    )


def is_validated_profile(profile: object) -> bool:
    """Whether ``profile`` came from this process's successful preflight.

    The writer intentionally refuses a look-alike dataclass assembled by a UI
    or test.  A CLI and a future web shell must both pass through the same live
    validation boundary before credentials or registry metadata can land.
    """

    if not isinstance(profile, LeagueProfile):
        return False
    token = profile._validation_token
    return (
        isinstance(token, tuple)
        and len(token) == 2
        and token[0] is _VALIDATED_PROFILE_TOKEN
        and token[1] == _profile_fingerprint(profile)
    )


def is_validated_profile_for_request(
    profile: object, request: BootstrapRequest
) -> bool:
    """Whether the sealed profile was validated with these exact values."""

    return (
        is_validated_profile(profile)
        and isinstance(profile, LeagueProfile)
        and hmac.compare_digest(
            profile._request_fingerprint,
            _fingerprint_request(request),
        )
    )


def _fingerprint_request(request: BootstrapRequest) -> bytes:
    digest = hashlib.sha256()
    values = (
        (request.platform or "").strip().lower(),
        str(request.league_id),
        str(request.espn_s2),
        str(request.swid),
        str(request.first_season),
        "ongoing" if request.final_season is None else str(request.final_season),
    )
    for value in values:
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.digest()


def _profile_fingerprint(profile: LeagueProfile) -> tuple[object, ...]:
    return (
        profile.platform,
        profile.league_name,
        profile.team_count,
        profile.league_format,
        profile.format_evidence,
        profile.available_seasons,
        profile.first_season,
        profile.final_season,
        profile.validated_through_season,
        profile.league_id,
        profile._request_fingerprint,
    )


def require_supported_python(version: Optional[Sequence[int]] = None) -> None:
    """Fail before heavyweight imports can produce an unrelated traceback."""

    actual = tuple(version or sys.version_info[:3])
    if actual[:2] != SUPPORTED_PYTHON:
        rendered = ".".join(str(part) for part in actual[:3])
        wanted = ".".join(str(part) for part in SUPPORTED_PYTHON)
        raise BootstrapValidationError(
            BootstrapErrorCode.UNSUPPORTED_PYTHON,
            f"This setup requires Python {wanted}.x; this process is running "
            f"Python {rendered}. Install Python {wanted}, create a fresh "
            "virtual environment, and run setup again.",
        )


def validate_request(
    request: BootstrapRequest, *, today: Optional[date] = None
) -> tuple[int, ...]:
    """Validate non-secret fields and return the exact requested year range."""

    platform = (request.platform or "").strip().lower()
    if platform != "espn":
        raise BootstrapValidationError(
            BootstrapErrorCode.UNSUPPORTED_PLATFORM,
            "Guided setup currently supports ESPN only. CBS remains an "
            "immediate follow and does not delay the ESPN journey.",
        )
    if not str(request.league_id or "").strip():
        raise BootstrapValidationError(
            BootstrapErrorCode.BAD_INPUT,
            "League ID is required. Copy the number from the ESPN league URL.",
        )
    if (
        not str(request.espn_s2 or "").strip()
        or not str(request.swid or "").strip()
    ):
        raise BootstrapValidationError(
            BootstrapErrorCode.BAD_INPUT,
            "Both ESPN_S2 and SWID are required for the supported private-"
            "league path.",
        )
    if not isinstance(request.first_season, int) or isinstance(
        request.first_season, bool
    ):
        raise BootstrapValidationError(
            BootstrapErrorCode.BAD_INPUT, "First season must be a four-digit year."
        )

    through = request.final_season
    if through is None:
        through = (today or date.today()).year
    if not isinstance(through, int) or isinstance(through, bool):
        raise BootstrapValidationError(
            BootstrapErrorCode.BAD_INPUT, "Final season must be a four-digit year."
        )
    if request.first_season < 1900 or through < 1900:
        raise BootstrapValidationError(
            BootstrapErrorCode.BAD_INPUT, "Season years must use four digits."
        )
    if request.first_season > through:
        raise BootstrapValidationError(
            BootstrapErrorCode.BAD_INPUT,
            "First season cannot be later than final season.",
        )
    return tuple(range(request.first_season, through + 1))


def validate_espn_league(
    request: BootstrapRequest,
    *,
    http_get: Callable[..., object] = requests.get,
    timeout: float = 15.0,
    today: Optional[date] = None,
) -> LeagueProfile:
    """Validate ESPN access and requested history without writing anything.

    Every requested season is checked because the existing public runner is
    fail-closed and will later request every registry-bounded season.  A green
    preflight must therefore mean that exact range is reachable, not merely
    that one recent league page opened.
    """

    require_supported_python()
    seasons = validate_request(request, today=today)
    snapshots: list[Mapping[str, object]] = []
    missing: list[int] = []

    for season in seasons:
        response = _request_espn_season(
            request, season, http_get=http_get, timeout=timeout
        )
        if response is None:
            missing.append(season)
            continue
        snapshots.append(response)

    if not snapshots:
        raise BootstrapValidationError(
            BootstrapErrorCode.LEAGUE_NOT_FOUND,
            "ESPN did not serve this league in any requested season. Check "
            "the league ID and season range, then try again.",
        )
    if missing:
        years = ", ".join(str(year) for year in missing)
        raise BootstrapValidationError(
            BootstrapErrorCode.HISTORY_UNAVAILABLE,
            f"ESPN did not serve the requested league for: {years}. Adjust "
            "the first/final season so every year is available; nothing was "
            "written.",
        )

    latest = snapshots[-1]
    latest_season = seasons[-1]
    name, team_count = _league_identity(latest, latest_season)
    league_format, evidence = _format_from_platform_evidence(latest)
    if league_format == "unknown":
        raise BootstrapValidationError(
            BootstrapErrorCode.UNSUPPORTED_FORMAT,
            "ESPN served the league, but did not provide season-points status "
            "or paired head-to-head matchups. The supported points-format "
            "workbook cannot be selected safely before the long run, so "
            "nothing was written.",
        )

    profile = LeagueProfile(
        platform="espn",
        league_name=name,
        team_count=team_count,
        league_format=league_format,
        format_evidence=evidence,
        available_seasons=seasons,
        first_season=seasons[0],
        final_season=request.final_season,
        validated_through_season=seasons[-1],
        league_id=str(request.league_id).strip(),
        _request_fingerprint=_fingerprint_request(request),
    )
    object.__setattr__(
        profile,
        "_validation_token",
        (_VALIDATED_PROFILE_TOKEN, _profile_fingerprint(profile)),
    )
    return profile


def _request_espn_season(
    request: BootstrapRequest,
    season: int,
    *,
    http_get: Callable[..., object],
    timeout: float,
) -> Optional[Mapping[str, object]]:
    league_id = str(request.league_id).strip()
    url = f"{ESPN_API_BASE}/{season}/segments/0/leagues/{league_id}"
    try:
        response = http_get(
            url,
            params=[("view", view) for view in _ESPN_VIEWS],
            cookies={"swid": request.swid, "espn_s2": request.espn_s2},
            timeout=timeout,
        )
    except (requests.ConnectionError, requests.Timeout):
        raise BootstrapValidationError(
            BootstrapErrorCode.NETWORK,
            "ESPN could not be reached. Check the connection and try again; "
            "nothing was written.",
        ) from None
    except requests.RequestException:
        raise BootstrapValidationError(
            BootstrapErrorCode.NETWORK,
            "The ESPN request failed before access could be validated. Try "
            "again; nothing was written.",
        ) from None

    status = getattr(response, "status_code", None)
    if status == 404:
        return None
    if status == 401:
        raise BootstrapValidationError(
            BootstrapErrorCode.AUTH_EXPIRED,
            "ESPN rejected the session. Refresh both ESPN_S2 and SWID from a "
            "signed-in browser and try again; nothing was written.",
        )
    if status == 403:
        raise BootstrapValidationError(
            BootstrapErrorCode.ACCESS_DENIED,
            "ESPN denied access. Confirm the league ID belongs to the signed-"
            "in account and refresh both cookies; nothing was written.",
        )
    if not isinstance(status, int) or status >= 400:
        rendered = status if isinstance(status, int) else "unknown"
        raise BootstrapValidationError(
            BootstrapErrorCode.UPSTREAM,
            f"ESPN returned HTTP {rendered} while validating access. Try "
            "again later; nothing was written.",
        )

    try:
        payload = response.json()
    except (TypeError, ValueError):
        raise BootstrapValidationError(
            BootstrapErrorCode.UPSTREAM,
            "ESPN returned an unreadable response. Try again later; nothing "
            "was written.",
        ) from None
    if not isinstance(payload, Mapping):
        raise BootstrapValidationError(
            BootstrapErrorCode.UPSTREAM,
            "ESPN returned an unexpected response shape. Nothing was written.",
        )
    return payload


def _league_identity(payload: Mapping[str, object], season: int) -> tuple[str, int]:
    settings = payload.get("settings")
    teams = payload.get("teams")
    if not isinstance(settings, Mapping) or not isinstance(teams, list):
        raise BootstrapValidationError(
            BootstrapErrorCode.UPSTREAM,
            f"ESPN served season {season}, but its settings/team shape was "
            "not usable. Nothing was written.",
        )

    name = str(settings.get("name") or "").strip()
    served_teams = [team for team in teams if isinstance(team, Mapping)]
    if not name or not served_teams:
        raise BootstrapValidationError(
            BootstrapErrorCode.UPSTREAM,
            f"ESPN served season {season}, but no usable league name or "
            "teams. Nothing was written.",
        )
    return name, len(served_teams)


def _format_from_platform_evidence(payload: Mapping[str, object]) -> tuple[str, str]:
    """Use only explicit/pairing evidence; never platform-name dispatch.

    This is a preflight label, not workbook-field normalization.  The build's
    canonical decision remains ``dim_league_format``.  Keeping the evidence in
    the result makes drift visible and prevents a silent default to H2H.
    """

    status = payload.get("status")
    if isinstance(status, Mapping):
        league_type = status.get("currentLeagueType")
        if (
            isinstance(league_type, int)
            and not isinstance(league_type, bool)
            and league_type == _SEASON_POINTS_LEAGUE_TYPE
        ):
            return "points", "ESPN status.currentLeagueType=5"

    schedule = payload.get("schedule")
    if isinstance(schedule, list):
        for matchup in schedule:
            if not isinstance(matchup, Mapping):
                continue
            home = matchup.get("home")
            away = matchup.get("away")
            if (
                isinstance(home, Mapping)
                and isinstance(away, Mapping)
                and home.get("teamId") is not None
                and away.get("teamId") is not None
            ):
                return "h2h", "ESPN served paired home/away matchups"

    return "unknown", "ESPN supplied neither supported format signal"
