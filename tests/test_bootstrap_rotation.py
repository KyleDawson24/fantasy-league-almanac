"""Explicit local credential-rotation contract (MLB-145 rung three).

All values and ESPN responses are synthetic. Tests operate only in temporary
release-shaped folders and never call a live service or the repo-root files.
"""

from __future__ import annotations

from datetime import date
import os
from pathlib import Path

from dotenv import dotenv_values
import pytest

from config.bootstrap import (
    BootstrapErrorCode,
    BootstrapRequest,
    BootstrapValidationError,
    validate_espn_league,
)
import config.bootstrap_writer as writer
import tools.setup_league as setup_cli


REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_TEMPLATE = (REPO_ROOT / ".env.example").read_bytes()
REGISTRY_TEMPLATE = (REPO_ROOT / "config" / "leagues.yml").read_bytes()


class _Response:
    status_code = 200

    def json(self):
        return {
            "settings": {"name": "Synthetic Rotation League"},
            "teams": [{"id": value} for value in range(1, 11)],
            "status": {"currentLeagueType": 5},
            "schedule": [],
        }


def _synthetic(label: str) -> str:
    return "synthetic-" + label + "-not-a-real-credential"


def _validated_pair(
    *,
    league_id: str = "24681357",
    espn_s2: str | None = None,
    swid: str | None = None,
):
    request = BootstrapRequest(
        platform="espn",
        league_id=league_id,
        espn_s2=espn_s2 or _synthetic("new-espn-s2"),
        swid=swid or ("{" + _synthetic("new-swid") + "}"),
        first_season=2015,
        final_season=None,
    )
    profile = validate_espn_league(
        request,
        http_get=lambda *args, **kwargs: _Response(),
        today=date(2026, 8, 19),
    )
    return request, profile


def _workspace(tmp_path: Path, *, env_bytes: bytes):
    root = tmp_path / "release"
    config = root / "config"
    config.mkdir(parents=True)
    env_path = root / ".env"
    template_path = root / ".env.example"
    registry_path = config / "leagues.yml"
    env_path.write_bytes(env_bytes)
    template_path.write_bytes(ENV_TEMPLATE)
    registry_path.write_bytes(REGISTRY_TEMPLATE)
    return env_path, template_path, registry_path


def _existing_env(*, league_id: str = "24681357") -> bytes:
    return (
        "# preserve rotation structure\r\n"
        f"LEAGUE_ID={league_id}\r\n"
        f"ESPN_S2={_synthetic('old-espn-s2')} # shared ESPN\r\n"
        f"SWID={{{_synthetic('old-swid')}}}\r\n"
        f"CBS_API_KEY={_synthetic('cbs-key')}\r\n"
        "WINDOWS_PATH=C:\\Users\\Example\\almanac\r\n"
    ).encode()


def _rotate(request, profile, paths, *, confirm):
    env_path, template_path, registry_path = paths
    return writer.rotate_validated_credentials(
        request,
        profile,
        confirm=confirm,
        env_path=env_path,
        env_template_path=template_path,
        registry_path=registry_path,
    )


def test_rotation_warns_then_replaces_only_espn_cookie_keys(tmp_path, capsys):
    request, profile = _validated_pair()
    env_before = _existing_env()
    paths = _workspace(tmp_path, env_bytes=env_before)
    registry_before = paths[2].read_bytes()
    confirmations = []

    def confirm(notice):
        assert paths[0].read_bytes() == env_before
        assert paths[2].read_bytes() == registry_before
        confirmations.append(notice)
        return True

    result = _rotate(request, profile, paths, confirm=confirm)

    assert result.env_changed is True
    assert result.registry_changed is False
    assert result.registry_path == paths[2]
    assert len(confirmations) == 1
    notice = confirmations[0]
    assert notice.platform == "espn"
    assert notice.credential_keys == ("ESPN_S2", "SWID")
    assert "shared by all configured ESPN leagues" in notice.message
    assert "every configured ESPN league" in notice.message

    values = dotenv_values(paths[0], interpolate=False)
    assert values["LEAGUE_ID"] == profile.league_id
    assert values["ESPN_S2"] == request.espn_s2
    assert values["SWID"] == request.swid
    assert values["CBS_API_KEY"] == _synthetic("cbs-key")
    assert values["WINDOWS_PATH"] == r"C:\Users\Example\almanac"
    after = paths[0].read_bytes()
    assert b"# preserve rotation structure\r\n" in after
    assert b" # shared ESPN\r\n" in after
    assert b"\n" not in after.replace(b"\r\n", b"")
    assert paths[2].read_bytes() == registry_before

    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
    for secret in (
        request.espn_s2,
        request.swid,
        _synthetic("old-espn-s2"),
        _synthetic("old-swid"),
    ):
        assert secret not in repr(result)
        assert secret not in repr(notice)
        assert secret not in captured.out + captured.err


def test_declined_confirmation_preserves_both_files_byte_for_byte(
    tmp_path, capsys
):
    request, profile = _validated_pair()
    env_before = _existing_env()
    paths = _workspace(tmp_path, env_bytes=env_before)
    registry_before = paths[2].read_bytes()

    with pytest.raises(BootstrapValidationError) as error:
        _rotate(request, profile, paths, confirm=lambda notice: False)

    assert error.value.code == BootstrapErrorCode.CONFIRMATION_DECLINED
    assert paths[0].read_bytes() == env_before
    assert paths[2].read_bytes() == registry_before
    captured = capsys.readouterr()
    for secret in (request.espn_s2, request.swid):
        assert secret not in str(error.value)
        assert secret not in captured.out + captured.err


def test_unvalidated_or_wrong_league_never_reaches_confirmation(tmp_path):
    request, profile = _validated_pair()
    env_before = _existing_env()
    paths = _workspace(tmp_path, env_bytes=env_before)
    registry_before = paths[2].read_bytes()
    confirmations = []
    other_request, _ = _validated_pair(league_id="13572468")

    for candidate_request, candidate_profile in (
        (request, type(profile)(**{
            field: getattr(profile, field)
            for field in profile.__dataclass_fields__
            if not field.startswith("_")
        })),
        (other_request, profile),
    ):
        with pytest.raises(BootstrapValidationError) as error:
            _rotate(
                candidate_request,
                candidate_profile,
                paths,
                confirm=lambda notice: confirmations.append(notice) or True,
            )
        assert error.value.code == BootstrapErrorCode.UNVALIDATED_PROFILE

    assert confirmations == []
    assert paths[0].read_bytes() == env_before
    assert paths[2].read_bytes() == registry_before


@pytest.mark.parametrize("source", ["blank", "malformed", "wrong-league"])
def test_invalid_existing_state_refuses_before_confirmation(tmp_path, source):
    request, profile = _validated_pair()
    env_before = _existing_env()
    if source == "blank":
        env_before = env_before.replace(
            f"ESPN_S2={_synthetic('old-espn-s2')}".encode(), b"ESPN_S2="
        )
    elif source == "malformed":
        env_before += b"this is not an assignment\r\n"
    else:
        env_before = _existing_env(league_id="13572468")
    paths = _workspace(tmp_path, env_bytes=env_before)
    registry_before = paths[2].read_bytes()
    confirmations = []

    with pytest.raises(BootstrapValidationError) as error:
        _rotate(
            request,
            profile,
            paths,
            confirm=lambda notice: confirmations.append(notice) or True,
        )

    assert error.value.code in (
        BootstrapErrorCode.CONFIG_CONFLICT,
        BootstrapErrorCode.CONFIG_MALFORMED,
    )
    assert confirmations == []
    assert paths[0].read_bytes() == env_before
    assert paths[2].read_bytes() == registry_before


def test_rotation_write_failure_preserves_old_credentials(tmp_path, monkeypatch):
    request, profile = _validated_pair()
    env_before = _existing_env()
    paths = _workspace(tmp_path, env_bytes=env_before)
    registry_before = paths[2].read_bytes()

    monkeypatch.setattr(
        writer,
        "_replace_file",
        lambda source, destination: (_ for _ in ()).throw(
            OSError("synthetic replacement refusal")
        ),
    )

    with pytest.raises(BootstrapValidationError) as error:
        _rotate(request, profile, paths, confirm=lambda notice: True)

    assert error.value.code == BootstrapErrorCode.WRITE_FAILED
    assert paths[0].read_bytes() == env_before
    assert paths[2].read_bytes() == registry_before
    for secret in (request.espn_s2, request.swid):
        assert secret not in str(error.value)


def test_rotation_noop_does_not_ask_for_confirmation_or_replace(
    tmp_path, monkeypatch
):
    request, profile = _validated_pair()
    env_before = _existing_env().replace(
        _synthetic("old-espn-s2").encode(), request.espn_s2.encode()
    ).replace(
        ("{" + _synthetic("old-swid") + "}").encode(),
        request.swid.encode(),
    )
    paths = _workspace(tmp_path, env_bytes=env_before)
    confirmations = []
    monkeypatch.setattr(
        writer,
        "_replace_file",
        lambda *args: pytest.fail("no-op rotation attempted replacement"),
    )

    result = _rotate(
        request,
        profile,
        paths,
        confirm=lambda notice: confirmations.append(notice) or True,
    )

    assert result.changed is False
    assert confirmations == []
    assert paths[0].read_bytes() == env_before


def test_cli_rotation_orders_warning_collection_validation_and_confirmation(
    monkeypatch, capsys
):
    request, profile = _validated_pair()
    notice = writer.credential_rotation_notice("espn")
    events = []

    monkeypatch.setattr(setup_cli, "require_supported_python", lambda: None)
    monkeypatch.setattr(
        setup_cli,
        "credential_rotation_notice",
        lambda platform: events.append("warning") or notice,
    )
    monkeypatch.setattr(
        setup_cli,
        "collect_request",
        lambda: events.append("collect") or request,
    )
    monkeypatch.setattr(
        setup_cli,
        "validate_espn_league",
        lambda supplied: events.append("validate") or profile,
    )
    monkeypatch.setattr(
        setup_cli,
        "confirm_credential_rotation",
        lambda supplied: events.append("confirm") or True,
    )

    def rotate(supplied_request, supplied_profile, *, confirm):
        assert (supplied_request, supplied_profile) == (request, profile)
        assert confirm(notice) is True
        events.append("replace")
        return type("Result", (), {"changed": True})()

    monkeypatch.setattr(setup_cli, "rotate_validated_credentials", rotate)

    assert setup_cli.main(["--rotate-credentials"]) == 0
    assert events == ["warning", "collect", "validate", "confirm", "replace"]
    output = capsys.readouterr().out
    assert "EXPLICIT CREDENTIAL ROTATION" in output
    assert "shared by all configured ESPN leagues" in output
    assert "Credential rotation completed" in output
    for secret in (request.league_id, request.espn_s2, request.swid):
        assert secret not in output


def test_cli_failed_validation_never_invokes_rotation(monkeypatch, capsys):
    request, _profile = _validated_pair()
    rotations = []
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
        "rotate_validated_credentials",
        lambda *args, **kwargs: rotations.append(True),
    )

    assert setup_cli.main(["--rotate-credentials"]) == 2
    assert rotations == []
    captured = capsys.readouterr()
    assert "auth_expired" in captured.err
    for secret in (request.league_id, request.espn_s2, request.swid):
        assert secret not in captured.out + captured.err
