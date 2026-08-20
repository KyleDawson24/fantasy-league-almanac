"""Thin CLI over the UI-agnostic MLB-145 bootstrap core.

The CLI validates access and history before asking the atomic writer to fill
the existing local ``.env`` and ``config/leagues.yml`` destinations. After a
successful setup or credential rotation it offers to start the existing
``create_public_almanac.py`` orchestration in a fresh local process.
"""

from __future__ import annotations

import argparse
from getpass import getpass
from pathlib import Path
import sys
import webbrowser


REPO_ROOT = Path(__file__).resolve().parents[1]
ESPN_GUIDE = REPO_ROOT / "docs" / "espn-cookie-guide.html"
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
from config.bootstrap_runner import run_public_almanac  # noqa: E402


def _prompt_year(
    label: str,
    *,
    prompt: str | None = None,
    default: int | None = None,
) -> int:
    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{prompt or label}{suffix}: ").strip()
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
    value = input(
        "Final season (press Enter if ongoing; otherwise enter a 4-digit year): "
    ).strip()
    if not value or value.lower() == "ongoing":
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise BootstrapValidationError(
            code=BootstrapErrorCode.BAD_INPUT,
            message="Final season must be a four-digit year or 'ongoing'.",
        ) from exc


def offer_illustrated_guide(
    *,
    input_fn=input,
    opener=webbrowser.open,
    guide_path: Path = ESPN_GUIDE,
) -> bool:
    """Offer the bundled offline guide without making setup depend on a GUI."""

    print(
        "You will need the ESPN league ID plus the espn_s2 and SWID session "
        "cookies from a browser where you signed in yourself."
    )
    print(
        "The Almanac never asks for your ESPN password or 2FA code. The two "
        "cookie values are pasted into hidden prompts and remain local."
    )
    response = input_fn("Open the illustrated ESPN setup guide now? [Y/n]: ")
    if response.strip().lower() not in ("", "y", "yes"):
        print("Guide skipped. You can open docs\\espn-cookie-guide.html later.\n")
        return False
    resolved = guide_path.resolve()
    if not resolved.is_file():
        print(
            "The illustrated guide is missing. Extract the complete release "
            "ZIP into one folder, then start again."
        )
        return False
    try:
        opened = bool(opener(resolved.as_uri()))
    except (OSError, ValueError):
        opened = False
    if opened:
        print("The local guide opened in your browser. Return here when ready.\n")
        return True
    print(f"Open this local file in your browser, then return here:\n{resolved}\n")
    return False


def collect_request() -> BootstrapRequest:
    """Collect secrets without echoing or accepting them on the command line."""

    offer_illustrated_guide()
    platform = input("Platform (press Enter for ESPN): ").strip() or "espn"
    if platform.strip().lower() != "espn":
        raise BootstrapValidationError(
            BootstrapErrorCode.UNSUPPORTED_PLATFORM,
            "Guided setup currently supports ESPN only. CBS remains an "
            "immediate follow and does not delay the ESPN journey.",
        )
    league_id = input(
        "ESPN league ID (the number after leagueId= in the league URL): "
    ).strip()
    first = _prompt_year(
        "First season",
        prompt=(
            "First season this ESPN league existed "
            "(4-digit year, for example 2015)"
        ),
    )
    final = _prompt_final_year()
    print(
        "The next two entries are hidden. Paste each value and press Enter; "
        "nothing will appear on screen."
    )
    espn_s2 = getpass(
        "espn_s2 cookie value (input hidden; nothing will appear): "
    )
    swid = getpass(
        "SWID cookie value, including { and } (input hidden; nothing will appear): "
    )
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


def prompt_create_almanac() -> bool:
    """Offer the existing complete-history runner in beginner language."""

    print(
        "\nSetup is complete. Creating the almanac can take a while and may "
        "open a browser for Google sign-in."
    )
    response = input("Create the almanac now? [Y/n]: ").strip().lower()
    return response in ("", "y", "yes")


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
    if not prompt_create_almanac():
        print(
            "The almanac was not started. Your setup is saved; later run "
            "python tools/create_public_almanac.py from this folder."
        )
        return 0

    print("\nStarting the existing complete-history almanac runner...")
    try:
        run_public_almanac()
    except BootstrapValidationError as exc:
        print(
            f"Setup is saved, but almanac creation stopped "
            f"[{exc.code.value}]: {exc}",
            file=sys.stderr,
        )
        return 3
    print("Almanac creation completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
