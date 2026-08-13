from argparse import Namespace
from subprocess import CalledProcessError

import pytest

import tools.create_public_almanac as public_almanac
from config.league_registry import League
from tools.create_public_almanac import (
    PublicAlmanacPlanError,
    command_plan,
    execute_plan,
    history_seasons,
)


def _args(**overrides):
    values = dict(
        league='espn-ten',
        public_workbook_title=None,
        confirm_link_sharing=False,
        new_public_workbook_force=False,
        advanced_snowflake=False,
    )
    values.update(overrides)
    return Namespace(**values)


def _league(first=2017, final=None, platform='espn'):
    return League(
        key='espn-ten', platform=platform, display_name='Ten seasons',
        first_season=first, final_season=final,
    )


def _extracts(plan):
    return [command for command in plan
            if command[1] == 'extract/extract.py']


def test_ten_season_registry_entry_plans_every_applicable_season():
    plan = command_plan(
        _args(), python='PY', dbt='DBT', league=_league(),
        through_season=2026)

    extracts = _extracts(plan)
    years = [int(command[command.index('--year') + 1])
             for command in extracts]
    assert years == list(range(2017, 2027))


def test_each_season_and_required_feed_is_planned_once():
    plan = command_plan(
        _args(), python='PY', dbt='DBT', league=_league(),
        through_season=2026)
    extracts = _extracts(plan)

    years = [command[command.index('--year') + 1] for command in extracts]
    assert len(years) == len(set(years)) == 10
    for command in extracts:
        # One invocation owns all feeds for that season. The extractor itself
        # guarantees one membership/calendar acquisition and one request per
        # settings/transaction feed, then expands --all from the same parse.
        for flag in ('--all', '--include-settings', '--include-transactions',
                     '--require-season-calendar'):
            assert command.count(flag) == 1


def test_default_forces_local_even_with_snowflake_credentials_in_environment(
        monkeypatch):
    monkeypatch.setenv('SNOWFLAKE_ACCOUNT', 'synthetic-account')
    monkeypatch.setenv('SNOWFLAKE_PASSWORD', 'synthetic-password')
    monkeypatch.setenv('EXTRACT_RAW_TARGET', 'snowflake')

    plan = command_plan(
        _args(), python='PY', dbt='DBT', league=_league(),
        through_season=2026)

    assert all(command[command.index('--raw-target') + 1] == 'local'
               for command in _extracts(plan))
    assert ['PY', 'tools/load_parquet_to_duckdb.py'] in plan
    assert '--advanced-snowflake' not in plan[-1]


def test_local_path_uses_build_instead_of_separate_seed_and_run():
    plan = command_plan(
        _args(), python='PY', dbt='DBT', league=_league(first=2026),
        through_season=2026)

    local = ['--project-dir', 'dbt_league',
             '--profiles-dir', 'dbt_league/profiles']
    assert ['DBT', 'build', *local, '--threads', '1'] in plan
    assert ['DBT', 'seed', *local] not in plan
    assert ['DBT', 'run', *local] not in plan


def test_current_season_uses_only_the_extractors_proven_closed_set():
    plan = command_plan(
        _args(), python='PY', dbt='DBT', league=_league(first=2026),
        through_season=2026)
    command = _extracts(plan)[0]

    assert '--all' in command
    assert not [part for part in command if part.isdigit() and part != '2026']
    # `extract.py --all` is covered at the selection boundary by
    # test_all_excludes_the_live_current_period.


def test_failed_historical_season_prevents_workbook_generation():
    plan = command_plan(
        _args(), python='PY', dbt='DBT', league=_league(first=2023),
        through_season=2026)
    called = []

    def _runner(command, cwd, check):
        called.append(command)
        if command[1:4] == ['extract/extract.py', '--league', 'espn-ten'] \
                and command[command.index('--year') + 1] == '2024':
            raise CalledProcessError(1, command)

    with pytest.raises(PublicAlmanacPlanError,
                       match='Google workbook step was not run'):
        execute_plan(plan, runner=_runner)

    assert [command[command.index('--year') + 1]
            for command in _extracts(called)] == ['2023', '2024']
    assert not any('generate_almanac_sheet.py' in command for command in called)


def test_failed_local_dbt_build_prevents_workbook_generation():
    plan = command_plan(
        _args(), python='PY', dbt='DBT', league=_league(first=2026),
        through_season=2026)
    called = []

    def _runner(command, cwd, check):
        called.append(command)
        if command[:2] == ['DBT', 'build']:
            raise CalledProcessError(1, command)

    with pytest.raises(PublicAlmanacPlanError,
                       match='Google workbook step was not run'):
        execute_plan(plan, runner=_runner)

    assert ['DBT', 'build', '--project-dir', 'dbt_league',
            '--profiles-dir', 'dbt_league/profiles',
            '--threads', '1'] in called
    assert not any('generate_almanac_sheet.py' in command for command in called)


@pytest.mark.parametrize('league,needle', [
    (_league(platform='cbs'), 'supports ESPN leagues only'),
    (_league(first=None), 'no derivable season range'),
    (_league(first=2030), 'no applicable seasons'),
])
def test_unsupported_or_underivable_shapes_refuse_clearly(league, needle):
    with pytest.raises(PublicAlmanacPlanError, match=needle):
        history_seasons(league, through_season=2026)


def test_snowflake_requires_the_deliberate_advanced_switch():
    plan = command_plan(
        _args(advanced_snowflake=True), python='PY', dbt='DBT',
        league=_league(first=2026), through_season=2026)

    assert _extracts(plan)[0][-1] == 'snowflake'
    assert ['PY', 'tools/load_parquet_to_duckdb.py'] not in plan
    assert '--advanced-snowflake' in plan[-1]


def test_registry_closed_final_season_caps_the_range():
    assert history_seasons(
        _league(first=2017, final=2024), through_season=2026
    ) == tuple(range(2017, 2025))


def test_automation_confirmation_is_forwarded_explicitly():
    plan = command_plan(
        _args(confirm_link_sharing=True), python='PY', dbt='DBT',
        league=_league(first=2026), through_season=2026)
    assert '--confirm-link-sharing' in plan[-1]


def test_main_loads_the_release_dotenv_before_validating_credentials(
        tmp_path, monkeypatch):
    """The sealed-rehearsal regression: every value was present in .env,
    yet the new public entrypoint reported all three missing because no child
    process had run yet and this process had never loaded the file itself.

    Pin both ordering and location. The registry validation must see the
    release root's .env before any command is executed, even when the caller's
    current directory is somewhere else.
    """
    (tmp_path / '.env').write_text(
        'TEST_S2=synthetic-cookie\n'
        'TEST_SWID={SYNTHETIC-GUID}\n'
        'TEST_LEAGUE_ID=8675309\n',
        encoding='utf-8',
    )
    for name in ('TEST_S2', 'TEST_SWID', 'TEST_LEAGUE_ID'):
        monkeypatch.delenv(name, raising=False)

    league = League(
        key='espn-ten', platform='espn', display_name='Ten seasons',
        credential_env=('TEST_S2', 'TEST_SWID', 'TEST_LEAGUE_ID'),
        league_id_env='TEST_LEAGUE_ID', first_season=2026,
    )
    executed = []
    monkeypatch.setattr(public_almanac, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(public_almanac, 'get_league', lambda key: league)
    monkeypatch.setattr(
        public_almanac, 'command_plan',
        lambda args, league: [['PY', 'synthetic-command']],
    )
    monkeypatch.setattr(
        public_almanac, 'execute_plan', lambda plan: executed.extend(plan),
    )

    assert public_almanac.main([]) == 0
    assert executed == [['PY', 'synthetic-command']]
