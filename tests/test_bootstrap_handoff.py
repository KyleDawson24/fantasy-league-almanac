"""Guided-setup handoff to the existing public almanac runner."""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from config.bootstrap import (
    BootstrapErrorCode,
    BootstrapRequest,
    BootstrapValidationError,
)
import config.bootstrap_runner as handoff
import tools.setup_league as setup_cli


def _synthetic(label: str) -> str:
    return "synthetic-" + label + "-not-a-real-credential"


def _request():
    return BootstrapRequest(
        platform="espn",
        league_id="24681357",
        espn_s2=_synthetic("espn-s2"),
        swid="{" + _synthetic("swid") + "}",
        first_season=2015,
        final_season=None,
    )


def _profile():
    return type(
        "Profile",
        (),
        {
            "league_name": "Synthetic Handoff League",
            "team_count": 10,
            "league_format": "points",
            "format_evidence": "synthetic evidence",
            "first_season": 2015,
            "final_season": None,
            "validated_through_season": 2026,
        },
    )()


def test_handoff_invokes_only_existing_public_runner_without_secrets(tmp_path):
    root = tmp_path / "release"
    runner_path = root / "tools" / "create_public_almanac.py"
    runner_path.parent.mkdir(parents=True)
    runner_path.write_text("# synthetic runner\n", encoding="utf-8")
    calls = []

    result = handoff.run_public_almanac(
        process_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
        python_executable="PYTHON",
        repo_root=root,
        runner_path=runner_path,
        environment={
            "PATH": "synthetic-path",
            "OTHER_SETTING": "preserve-me",
            "LEAGUE_ID": _synthetic("stale-league-id"),
            "ESPN_S2": _synthetic("stale-espn-s2"),
            "SWID": _synthetic("stale-swid"),
        },
    )

    assert result.completed is True
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == (("PYTHON", str(runner_path)),)
    assert kwargs["cwd"] == root
    assert kwargs["check"] is True
    assert kwargs["env"] == {
        "PATH": "synthetic-path",
        "OTHER_SETTING": "preserve-me",
    }
    rendered = repr(calls) + repr(result)
    assert "extract/extract.py" not in rendered
    assert "dbt" not in rendered.lower()
    assert "google" not in rendered.lower()
    for secret in (_synthetic("espn-s2"), _synthetic("swid")):
        assert secret not in rendered


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("synthetic missing runtime"),
        subprocess.CalledProcessError(7, ["synthetic-runner"]),
        OSError("synthetic local refusal"),
    ],
)
def test_handoff_failures_are_actionable_and_credential_free(
    tmp_path, failure, capsys
):
    root = tmp_path / "release"
    runner_path = root / "tools" / "create_public_almanac.py"
    runner_path.parent.mkdir(parents=True)
    runner_path.write_text("# synthetic runner\n", encoding="utf-8")

    def fail(*args, **kwargs):
        raise failure

    with pytest.raises(BootstrapValidationError) as error:
        handoff.run_public_almanac(
            process_runner=fail,
            python_executable="PYTHON",
            repo_root=root,
            runner_path=runner_path,
            environment={"PATH": "synthetic-path"},
        )

    assert error.value.code == BootstrapErrorCode.RUN_FAILED
    assert "saved setup" in str(error.value).lower()
    captured = capsys.readouterr()
    for secret in (_synthetic("espn-s2"), _synthetic("swid")):
        assert secret not in str(error.value)
        assert secret not in captured.out + captured.err


def test_successful_setup_offers_and_runs_existing_handoff(monkeypatch, capsys):
    request = _request()
    profile = _profile()
    events = []
    monkeypatch.setattr(setup_cli, "require_supported_python", lambda: None)
    monkeypatch.setattr(
        setup_cli, "collect_request", lambda: events.append("collect") or request
    )
    monkeypatch.setattr(
        setup_cli,
        "validate_espn_league",
        lambda supplied: events.append("validate") or profile,
    )
    monkeypatch.setattr(
        setup_cli,
        "write_validated_configuration",
        lambda supplied_request, supplied_profile: events.append("persist")
        or type("Result", (), {"changed": True})(),
    )
    monkeypatch.setattr(
        setup_cli,
        "prompt_create_almanac",
        lambda: events.append("offer") or True,
    )
    monkeypatch.setattr(
        setup_cli, "run_public_almanac", lambda: events.append("run")
    )

    assert setup_cli.main([]) == 0
    assert events == ["collect", "validate", "persist", "offer", "run"]
    output = capsys.readouterr().out
    assert "Local setup saved" in output
    assert "Starting the existing complete-history almanac runner" in output
    assert "completed successfully" in output
    for secret in (request.league_id, request.espn_s2, request.swid):
        assert secret not in output


def test_failed_setup_never_offers_or_invokes_runner(monkeypatch, capsys):
    request = _request()
    events = []
    monkeypatch.setattr(setup_cli, "require_supported_python", lambda: None)
    monkeypatch.setattr(setup_cli, "collect_request", lambda: request)
    monkeypatch.setattr(
        setup_cli,
        "validate_espn_league",
        lambda supplied: (_ for _ in ()).throw(
            BootstrapValidationError(
                BootstrapErrorCode.AUTH_EXPIRED,
                "Refresh both ESPN cookies; nothing was written.",
            )
        ),
    )
    monkeypatch.setattr(
        setup_cli,
        "prompt_create_almanac",
        lambda: events.append("offer") or True,
    )
    monkeypatch.setattr(
        setup_cli, "run_public_almanac", lambda: events.append("run")
    )

    assert setup_cli.main([]) == 2
    assert events == []
    captured = capsys.readouterr()
    assert "auth_expired" in captured.err
    for secret in (request.league_id, request.espn_s2, request.swid):
        assert secret not in captured.out + captured.err


def test_failed_persistence_never_offers_or_invokes_runner(monkeypatch, capsys):
    request = _request()
    profile = _profile()
    events = []
    monkeypatch.setattr(setup_cli, "require_supported_python", lambda: None)
    monkeypatch.setattr(setup_cli, "collect_request", lambda: request)
    monkeypatch.setattr(setup_cli, "validate_espn_league", lambda value: profile)
    monkeypatch.setattr(
        setup_cli,
        "write_validated_configuration",
        lambda *args: (_ for _ in ()).throw(
            BootstrapValidationError(
                BootstrapErrorCode.WRITE_FAILED,
                "The prior local state was restored.",
            )
        ),
    )
    monkeypatch.setattr(
        setup_cli,
        "prompt_create_almanac",
        lambda: events.append("offer") or True,
    )
    monkeypatch.setattr(
        setup_cli, "run_public_almanac", lambda: events.append("run")
    )

    assert setup_cli.main([]) == 2
    assert events == []
    captured = capsys.readouterr()
    assert "write_failed" in captured.err
    for secret in (request.league_id, request.espn_s2, request.swid):
        assert secret not in captured.out + captured.err


def test_runner_failure_keeps_setup_saved_and_returns_distinct_status(
    monkeypatch, capsys
):
    request = _request()
    profile = _profile()
    monkeypatch.setattr(setup_cli, "require_supported_python", lambda: None)
    monkeypatch.setattr(setup_cli, "collect_request", lambda: request)
    monkeypatch.setattr(setup_cli, "validate_espn_league", lambda value: profile)
    monkeypatch.setattr(
        setup_cli,
        "write_validated_configuration",
        lambda *args: type("Result", (), {"changed": True})(),
    )
    monkeypatch.setattr(setup_cli, "prompt_create_almanac", lambda: True)
    monkeypatch.setattr(
        setup_cli,
        "run_public_almanac",
        lambda: (_ for _ in ()).throw(
            BootstrapValidationError(
                BootstrapErrorCode.RUN_FAILED,
                "Review the runner message; saved setup is still intact.",
            )
        ),
    )

    assert setup_cli.main([]) == 3
    captured = capsys.readouterr()
    assert "Setup is saved" in captured.err
    assert "run_failed" in captured.err
    for secret in (request.league_id, request.espn_s2, request.swid):
        assert secret not in captured.out + captured.err


def test_declining_offer_leaves_a_clear_later_command(monkeypatch, capsys):
    request = _request()
    profile = _profile()
    runs = []
    monkeypatch.setattr(setup_cli, "require_supported_python", lambda: None)
    monkeypatch.setattr(setup_cli, "collect_request", lambda: request)
    monkeypatch.setattr(setup_cli, "validate_espn_league", lambda value: profile)
    monkeypatch.setattr(
        setup_cli,
        "write_validated_configuration",
        lambda *args: type("Result", (), {"changed": False})(),
    )
    monkeypatch.setattr(setup_cli, "prompt_create_almanac", lambda: False)
    monkeypatch.setattr(
        setup_cli, "run_public_almanac", lambda: runs.append(True)
    )

    assert setup_cli.main([]) == 0
    assert runs == []
    output = capsys.readouterr().out
    assert "python tools/create_public_almanac.py" in output
