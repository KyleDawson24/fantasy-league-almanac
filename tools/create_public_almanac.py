"""Reusable complete-history build and Google workbook publish.

This command starts after local credential and registry configuration. It can
be run directly or launched by the guided setup after that setup succeeds.

For the selected ESPN registry entry, the default path asks the registry's
existing ``seasons_to_request`` policy for the exact season range.  It then
runs one complete, fail-closed extract per season: membership/calendar,
settings, standings/draft/owners, transactions, and every matchup period ESPN
proves closed.  Only after every season succeeds does it load Parquet into
DuckDB, run dbt, and create the Google workbook.

Snowflake is reachable only through ``--advanced-snowflake``.  Ambient
Snowflake credentials and ``EXTRACT_RAW_TARGET`` cannot change the default
because every extract command passes ``--raw-target local`` explicitly.
"""

import argparse
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
_EXTRACT_DIR = REPO_ROOT / 'extract'
if str(_EXTRACT_DIR) not in sys.path:
    sys.path.insert(0, str(_EXTRACT_DIR))

from config.league_registry import LeagueRegistryError, get_league  # noqa: E402
from matchup_membership import seasons_to_request  # noqa: E402


class PublicAlmanacPlanError(RuntimeError):
    """The configured league cannot produce a complete public history."""


def _dbt_for_python(python):
    sibling = Path(python).resolve().parent / (
        'dbt.exe' if sys.platform == 'win32' else 'dbt')
    if sibling.exists():
        return str(sibling)
    return shutil.which('dbt') or 'dbt'


def history_seasons(league, through_season=None):
    """Registry-owned applicable seasons, or a clear refusal."""
    if league.platform != 'espn':
        raise PublicAlmanacPlanError(
            f"The public workbook orchestration supports ESPN leagues only; "
            f"registry entry {league.key!r} uses platform {league.platform!r}."
        )
    through_season = through_season or date.today().year
    try:
        seasons = seasons_to_request(
            league.first_season, league.final_season, through_season)
    except ValueError as exc:
        raise PublicAlmanacPlanError(
            f"League {league.key!r} has no derivable season range: {exc}"
        ) from exc
    if not seasons:
        raise PublicAlmanacPlanError(
            f"League {league.key!r} has no applicable seasons through "
            f"{through_season}; check first_season/final_season in "
            "config/leagues.yml. No extraction or workbook was started."
        )
    return seasons


def command_plan(args, python=None, dbt=None, league=None,
                 through_season=None):
    """Return the exact subprocess plan without executing or opening data."""
    python = python or sys.executable
    dbt = dbt or _dbt_for_python(python)
    try:
        league = league or get_league(args.league)
    except LeagueRegistryError as exc:
        raise PublicAlmanacPlanError(str(exc)) from exc
    seasons = history_seasons(league, through_season=through_season)

    raw_target = 'snowflake' if args.advanced_snowflake else 'local'
    extracts = [
        [
            python, 'extract/extract.py', '--league', league.key,
            '--year', str(season), '--all', '--include-settings',
            '--include-transactions', '--require-season-calendar',
            '--raw-target', raw_target,
        ]
        for season in seasons
    ]

    generator = [
        python, 'output/generate_almanac_sheet.py', '--league', league.key,
        '--new-public-workbook',
    ]
    if args.public_workbook_title:
        generator += ['--public-workbook-title', args.public_workbook_title]
    if args.confirm_link_sharing:
        generator.append('--confirm-link-sharing')
    if args.new_public_workbook_force:
        generator.append('--new-public-workbook-force')

    if args.advanced_snowflake:
        generator.append('--advanced-snowflake')
        transforms = [
            [dbt, 'deps', '--project-dir', 'dbt_league'],
            [dbt, 'seed', '--project-dir', 'dbt_league'],
            [dbt, 'build', '--project-dir', 'dbt_league'],
        ]
    else:
        local = ['--project-dir', 'dbt_league',
                 '--profiles-dir', 'dbt_league/profiles']
        transforms = [
            [python, 'tools/load_parquet_to_duckdb.py'],
            [dbt, 'deps', *local],
            # The public path targets ordinary laptops. Keep dbt itself at
            # one concurrent node in addition to the profile's one DuckDB
            # engine thread; four simultaneous JSON-heavy models exhausted
            # a measured 5.5 GiB machine even though each model fits alone.
            [dbt, 'build', *local, '--threads', '1'],
        ]
    return [*extracts, *transforms, generator]


def execute_plan(plan, runner=subprocess.run):
    """Execute sequentially; the first failure prevents every later step."""
    for command in plan:
        print('[public-almanac] running:', ' '.join(command))
        try:
            runner(command, cwd=REPO_ROOT, check=True)
        except FileNotFoundError as exc:
            raise PublicAlmanacPlanError(
                f"Required command {command[0]!r} was not found. Activate "
                "the release virtual environment and install requirements.txt."
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise PublicAlmanacPlanError(
                f"Complete-history preparation stopped because this command "
                f"failed with exit code {exc.returncode}: {' '.join(command)}. "
                "The Google workbook step was not run; fix the reported "
                "extraction or build error and retry."
            ) from exc


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            'Complete-history orchestration: extract every configured ESPN '
            'season completely to local Parquet/DuckDB, build the almanac, '
            'then publish one new Google workbook. Run tools/setup_league.py '
            'first when local configuration has not been created yet.'
        )
    )
    parser.add_argument(
        '--league', default=None, metavar='LEAGUE_KEY',
        help='Configured ESPN league registry key. Defaults to '
             'config/leagues.yml default_league.',
    )
    parser.add_argument('--public-workbook-title', metavar='TITLE')
    parser.add_argument('--confirm-link-sharing', action='store_true')
    parser.add_argument('--new-public-workbook-force', action='store_true')
    parser.add_argument(
        '--advanced-snowflake', action='store_true',
        help='Deliberately use the selected league owner\'s configured '
             'Snowflake path instead of local Parquet/DuckDB.',
    )
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        # This entrypoint validates the registry before launching any child
        # process. Load the release folder's .env first; otherwise a stranger
        # can fill in all three ESPN values correctly and still be told that
        # every one is missing. Use the explicit repository path so invoking
        # this script from another working directory cannot change which file
        # supplies the configuration.
        load_dotenv(REPO_ROOT / '.env')
        league = get_league(args.league)
        plan = command_plan(args, league=league)
        # Validate the registry shape and derive the complete season range before
        # asking for platform-specific credentials. Unsupported shapes should
        # fail with the relevant configuration error, not an unrelated secret.
        league.require_credentials()
        execute_plan(plan)
    except (LeagueRegistryError, PublicAlmanacPlanError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == '__main__':
    main()
