"""Thin CLI over the UI-agnostic MLB-145 bootstrap core.

The CLI validates access and history before asking the atomic writer to fill
the existing local ``.env`` and ``config/leagues.yml`` destinations.  It does
not hand off to extraction or ``create_public_almanac.py`` in this rung.
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
from config.bootstrap_writer import (  # noqa: E402
    CredentialRotationNotice,
    credential_rotation_notice,
    rotate_validated_credentials,
    write_validated_configuration,
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
    parser = argparse.ArgumentParser(
        description=(
            "Guided ESPN setup. Validates credentials, requested history, "
            "league identity, and format before atomically writing the existing "
            "local credential and registry files."
        )
    )
    parser.add_argument(
        "--rotate-credentials",
        action="store_true",
        help=(
            "Explicitly replace the shared ESPN cookie pair after a new "
            "successful validation and confirmation. Ordinary setup never "
            "overwrites nonempty credentials."
        ),
    )
    return parser


def confirm_credential_rotation(notice: CredentialRotationNotice) -> bool:
    """Require an unmistakable post-validation confirmation."""

    print("\nValidation succeeded. The existing credentials are unchanged.")
    print(notice.message)
    response = input("Type ROTATE to replace the ESPN credentials: ").strip()
    return response == "ROTATE"


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    print("Fantasy League Almanac guided setup")
    print("Nothing is written unless the complete preflight succeeds.\n")
    try:
        require_supported_python()
        rotation_notice = None
        if args.rotate_credentials:
            rotation_notice = credential_rotation_notice("espn")
            print("EXPLICIT CREDENTIAL ROTATION")
            print(rotation_notice.message)
            print("New values will be validated before replacement.\n")
        request = collect_request()
        profile = validate_espn_league(request)
        if rotation_notice is None:
            write_result = write_validated_configuration(request, profile)
        else:
            write_result = rotate_validated_credentials(
                request,
                profile,
                confirm=confirm_credential_rotation,
            )
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
    if args.rotate_credentials and write_result.changed:
        print(
            "\nCredential rotation completed. Only the shared ESPN cookie "
            "keys changed; league metadata and other local settings were "
            "preserved."
        )
    elif args.rotate_credentials:
        print("\nThe validated ESPN credentials already matched; no files changed.")
    elif write_result.changed:
        print(
            "\nLocal setup saved: credentials are in the gitignored .env, "
            "and non-secret league metadata is in config/leagues.yml."
        )
    else:
        print("\nLocal setup already matched; no files changed.")
    print("The almanac run was not started in this setup rung.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
