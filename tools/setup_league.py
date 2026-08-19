"""Thin CLI over the read-only MLB-145 bootstrap core.

This first rung validates access and history only.  It intentionally writes no
configuration or credentials; a later rung will commit the validated profile
to the safe config locations and hand off to ``create_public_almanac.py``.
"""

from __future__ import annotations

import argparse
from getpass import getpass
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.bootstrap import (  # noqa: E402
    BootstrapErrorCode,
    BootstrapRequest,
    BootstrapValidationError,
    require_supported_python,
    validate_espn_league,
)


def _prompt_year(label: str, *, default: int | None = None) -> int:
    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{label}{suffix}: ").strip()
    if not value and default is not None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise BootstrapValidationError(
            code=BootstrapErrorCode.BAD_INPUT,
            message=f"{label} must be a four-digit year.",
        ) from exc


def _prompt_final_year() -> int | None:
    value = input("Final season [ongoing]: ").strip()
    if not value or value.lower() == "ongoing":
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise BootstrapValidationError(
            code=BootstrapErrorCode.BAD_INPUT,
            message="Final season must be a four-digit year or 'ongoing'.",
        ) from exc


def collect_request() -> BootstrapRequest:
    """Collect secrets without echoing or accepting them on the command line."""

    platform = input("Platform [ESPN]: ").strip() or "espn"
    if platform.strip().lower() != "espn":
        raise BootstrapValidationError(
            BootstrapErrorCode.UNSUPPORTED_PLATFORM,
            "Guided setup currently supports ESPN only. CBS remains an "
            "immediate follow and does not delay the ESPN journey.",
        )
    league_id = input("ESPN league ID: ").strip()
    first = _prompt_year("First season")
    final = _prompt_final_year()
    espn_s2 = getpass("ESPN_S2 (hidden): ")
    swid = getpass("SWID (hidden): ")
    return BootstrapRequest(
        platform=platform,
        league_id=league_id,
        espn_s2=espn_s2,
        swid=swid,
        first_season=first,
        final_season=final,
    )


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description=(
            "Read-only ESPN setup preflight. Validates credentials, requested "
            "history, league identity, and format before any files are written."
        )
    )


def main(argv=None) -> int:
    build_parser().parse_args(argv)
    print("Fantasy League Almanac setup preflight")
    print("No credentials or configuration will be written in this rung.\n")
    try:
        require_supported_python()
        profile = validate_espn_league(collect_request())
    except BootstrapValidationError as exc:
        print(f"Setup stopped [{exc.code.value}]: {exc}", file=sys.stderr)
        return 2

    through = profile.validated_through_season
    seasons = (
        str(profile.first_season)
        if profile.first_season == through
        else f"{profile.first_season}-{through}"
    )
    if profile.final_season is None:
        seasons += " (ongoing)"
    print("\nValidated successfully:")
    print(f"  League: {profile.league_name}")
    print(f"  Teams: {profile.team_count}")
    print(f"  Format: {profile.league_format} ({profile.format_evidence})")
    print(f"  Available seasons: {seasons}")
    print(
        "\nNo files were written. The validated profile is ready for the "
        "next setup rung."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
