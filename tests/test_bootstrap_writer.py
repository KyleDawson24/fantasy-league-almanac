"""Atomic local configuration writer contract (MLB-145 rung two).

Every test uses a temporary release-shaped folder and synthetic ESPN
responses.  No live request, real credential, real registry, or repo-root
``.env`` participates.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
import os
from pathlib import Path

from dotenv import dotenv_values
import pytest
import yaml

from config.bootstrap import (
    BootstrapErrorCode,
    BootstrapRequest,
    BootstrapValidationError,
    LeagueProfile,
    validate_espn_league,
)
import config.bootstrap_writer as writer


REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_TEMPLATE = (REPO_ROOT / ".env.example").read_bytes()
REGISTRY_TEMPLATE = (REPO_ROOT / "config" / "leagues.yml").read_bytes()
_ABSENT = object()


class _Response:
    status_code = 200

    def json(self):
        return {
            "settings": {"name": "Synthetic Writers League"},
            "teams": [{"id": value} for value in range(1, 11)],
            "status": {"currentLeagueType": 5},
            "schedule": [],
        }


def _synthetic(label: str) -> str:
    return "synthetic-" + label + "-not-a-real-credential"


def _validated_pair(
    *,
    first_season: int = 2015,
    final_season: int | None = None,
    league_id: str | None = None,
    espn_s2: str | None = None,
    swid: str | None = None,
):
    request = BootstrapRequest(
        platform="espn",
        league_id=league_id or ("2468" + "1357"),
        espn_s2=espn_s2 or _synthetic("espn-s2"),
        swid=swid or ("{" + _synthetic("swid") + "}"),
        first_season=first_season,
        final_season=final_season,
    )
    profile = validate_espn_league(
        request,
        http_get=lambda *args, **kwargs: _Response(),
        today=date(2026, 8, 19),
    )
    return request, profile


def _workspace(
    tmp_path: Path,
    *,
    env_bytes=_ABSENT,
    registry_bytes: bytes = REGISTRY_TEMPLATE,
):
    root = tmp_path / "release"
    config = root / "config"
    config.mkdir(parents=True)
    env_path = root / ".env"
    template_path = root / ".env.example"
    registry_path = config / "leagues.yml"
    template_path.write_bytes(ENV_TEMPLATE)
    registry_path.write_bytes(registry_bytes)
    if env_bytes is not _ABSENT:
        env_path.write_bytes(env_bytes)
    return env_path, template_path, registry_path


def _write(request, profile, paths):
    env_path, template_path, registry_path = paths
    return writer.write_validated_configuration(
        request,
        profile,
        env_path=env_path,
        env_template_path=template_path,
        registry_path=registry_path,
    )


def test_new_configuration_writes_both_destinations_without_secret_registry_data(
    tmp_path, capsys
):
    request, profile = _validated_pair()
    paths = _workspace(tmp_path)

    result = _write(request, profile, paths)

    env_path, _template_path, registry_path = paths
    assert result.env_changed is True
    assert result.registry_changed is True
    values = dotenv_values(env_path, interpolate=False)
    assert values["LEAGUE_ID"] == profile.league_id
    assert values["ESPN_S2"] == request.espn_s2
    assert values["SWID"] == request.swid

    registry_bytes = registry_path.read_bytes()
    registry = yaml.safe_load(registry_bytes)
    espn = registry["leagues"]["espn-main"]
    assert espn["display_name"] == profile.league_name
    assert espn["first_season"] == 2015
    assert espn["final_season"] is None
    assert "league_id" not in espn
    for value in (profile.league_id, request.espn_s2, request.swid):
        assert value.encode() not in registry_bytes
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_unvalidated_or_mismatched_input_does_not_write(tmp_path):
    request, validated = _validated_pair()
    paths = _workspace(tmp_path)
    registry_before = paths[2].read_bytes()
    unvalidated = LeagueProfile(
        platform=validated.platform,
        league_name=validated.league_name,
        team_count=validated.team_count,
        league_format=validated.league_format,
        format_evidence=validated.format_evidence,
        available_seasons=validated.available_seasons,
        first_season=validated.first_season,
        final_season=validated.final_season,
        validated_through_season=validated.validated_through_season,
        league_id=validated.league_id,
    )

    with pytest.raises(BootstrapValidationError) as error:
        _write(request, unvalidated, paths)
    assert error.value.code == BootstrapErrorCode.UNVALIDATED_PROFILE
    assert not paths[0].exists()
    assert paths[2].read_bytes() == registry_before

    with pytest.raises(BootstrapValidationError) as error:
        _write(replace(request, first_season=2016), validated, paths)
    assert error.value.code == BootstrapErrorCode.UNVALIDATED_PROFILE
    assert not paths[0].exists()
    assert paths[2].read_bytes() == registry_before

    with pytest.raises(BootstrapValidationError) as error:
        _write(replace(request, espn_s2=_synthetic("not-validated")), validated, paths)
    assert error.value.code == BootstrapErrorCode.UNVALIDATED_PROFILE
    assert not paths[0].exists()
    assert paths[2].read_bytes() == registry_before

    with pytest.raises(BootstrapValidationError) as error:
        edited = replace(validated, league_name="Edited after validation")
        _write(request, edited, paths)
    assert error.value.code == BootstrapErrorCode.UNVALIDATED_PROFILE
    assert not paths[0].exists()
    assert paths[2].read_bytes() == registry_before


@pytest.mark.parametrize("failed_name", [".env", "leagues.yml"])
def test_destination_failure_restores_both_original_states(
    tmp_path, monkeypatch, failed_name
):
    request, profile = _validated_pair()
    paths = _workspace(tmp_path)
    registry_before = paths[2].read_bytes()
    real_replace = os.replace
    failed = False

    def fail_once(source, destination):
        nonlocal failed
        if Path(destination).name == failed_name and not failed:
            failed = True
            raise OSError("synthetic replacement failure")
        real_replace(source, destination)

    monkeypatch.setattr(writer, "_replace_file", fail_once)
    with pytest.raises(BootstrapValidationError) as error:
        _write(request, profile, paths)

    assert error.value.code == BootstrapErrorCode.WRITE_FAILED
    assert "synthetic" not in str(error.value).lower()
    assert not paths[0].exists()
    assert paths[2].read_bytes() == registry_before
    assert not list(paths[0].parent.rglob(".bootstrap-*"))


def test_staging_failure_precedes_every_destination_mutation(
    tmp_path, monkeypatch
):
    request, profile = _validated_pair()
    paths = _workspace(tmp_path)
    registry_before = paths[2].read_bytes()
    real_write_temp = writer._write_temp

    def fail_registry_stage(destination, payload, *, secret, purpose):
        if Path(destination).name == "leagues.yml" and purpose == "staged":
            raise OSError("synthetic staging failure")
        return real_write_temp(
            destination, payload, secret=secret, purpose=purpose
        )

    monkeypatch.setattr(writer, "_write_temp", fail_registry_stage)
    with pytest.raises(BootstrapValidationError) as error:
        _write(request, profile, paths)

    assert error.value.code == BootstrapErrorCode.WRITE_FAILED
    assert not paths[0].exists()
    assert paths[2].read_bytes() == registry_before
    assert not list(paths[0].parent.rglob(".bootstrap-*"))


def test_rerun_is_byte_idempotent_and_performs_no_replace(
    tmp_path, monkeypatch
):
    request, profile = _validated_pair()
    paths = _workspace(tmp_path)
    _write(request, profile, paths)
    env_before = paths[0].read_bytes()
    registry_before = paths[2].read_bytes()

    def unexpected_replace(*args, **kwargs):
        raise AssertionError("idempotent setup attempted a filesystem replace")

    monkeypatch.setattr(writer, "_replace_file", unexpected_replace)
    result = _write(request, profile, paths)

    assert result.changed is False
    assert paths[0].read_bytes() == env_before
    assert paths[2].read_bytes() == registry_before


def test_unrelated_env_structure_windows_paths_and_registry_leagues_survive(
    tmp_path,
):
    request, profile = _validated_pair()
    env_before = (
        b"# preserve this comment\r\n"
        b"LEAGUE_ID=\r\n"
        b"ESPN_S2=\r\n"
        b"SWID=\r\n"
        b"OTHER_KEY=leave-me-alone\r\n"
        b"WINDOWS_PATH=C:\\Users\\Example\\keys\\client.pem\r\n"
    )
    paths = _workspace(tmp_path, env_bytes=env_before)
    registry_before = paths[2].read_bytes()
    cbs_tail_before = registry_before.split(b"  # Entry #2", 1)[1]

    _write(request, profile, paths)

    env_after = paths[0].read_bytes()
    assert b"# preserve this comment\r\n" in env_after
    assert b"OTHER_KEY=leave-me-alone\r\n" in env_after
    assert (
        b"WINDOWS_PATH=C:\\Users\\Example\\keys\\client.pem\r\n"
        in env_after
    )
    assert b"\n" not in env_after.replace(b"\r\n", b"")
    assert dotenv_values(paths[0], interpolate=False)["WINDOWS_PATH"] == (
        r"C:\Users\Example\keys\client.pem"
    )

    registry_after = paths[2].read_bytes()
    assert registry_after.split(b"  # Entry #2", 1)[1] == cbs_tail_before
    assert yaml.safe_load(registry_after)["leagues"]["cbs-bsb"] == (
        yaml.safe_load(registry_before)["leagues"]["cbs-bsb"]
    )


def test_nonempty_credential_replacement_is_refused_without_leaking_values(
    tmp_path, capsys
):
    request, profile = _validated_pair()
    old_secret = _synthetic("old-cookie")
    env_before = (
        "LEAGUE_ID=\n"
        f"ESPN_S2={old_secret}\n"
        "SWID=\n"
    ).encode()
    paths = _workspace(tmp_path, env_bytes=env_before)
    registry_before = paths[2].read_bytes()

    with pytest.raises(BootstrapValidationError) as error:
        _write(request, profile, paths)

    message = str(error.value)
    for value in (old_secret, request.espn_s2, request.swid):
        assert value not in message
    captured = capsys.readouterr()
    assert old_secret not in captured.out + captured.err
    assert request.espn_s2 not in captured.out + captured.err
    assert paths[0].read_bytes() == env_before
    assert paths[2].read_bytes() == registry_before


def test_existing_different_league_identity_is_refused(tmp_path):
    request, profile = _validated_pair()
    other_id = "9753" + "1864"
    env_before = (
        f"LEAGUE_ID={other_id}\n"
        "ESPN_S2=\n"
        "SWID=\n"
    ).encode()
    paths = _workspace(tmp_path, env_bytes=env_before)
    registry_before = paths[2].read_bytes()

    with pytest.raises(BootstrapValidationError) as error:
        _write(request, profile, paths)

    assert error.value.code == BootstrapErrorCode.CONFIG_CONFLICT
    message = str(error.value)
    assert "one league per extracted folder" in message
    assert "fresh copy into a different folder" in message
    assert "START_ALMANAC.cmd" in message
    assert "ROTATE_ESPN_CREDENTIALS.cmd" not in message
    assert other_id not in message
    assert profile.league_id not in message
    assert paths[0].read_bytes() == env_before
    assert paths[2].read_bytes() == registry_before


def test_ordinary_setup_refuses_rotation_and_names_explicit_action(tmp_path):
    request, profile = _validated_pair()
    paths = _workspace(tmp_path)
    _write(request, profile, paths)
    env_before = paths[0].read_bytes()
    registry_before = paths[2].read_bytes()
    rotated_request, rotated_profile = _validated_pair(
        league_id=profile.league_id,
        espn_s2=_synthetic("rotated-espn-s2"),
        swid="{" + _synthetic("rotated-swid") + "}",
    )

    with pytest.raises(BootstrapValidationError) as error:
        _write(rotated_request, rotated_profile, paths)

    assert error.value.code == BootstrapErrorCode.CONFIG_CONFLICT
    assert "ROTATE_ESPN_CREDENTIALS.cmd" in str(error.value)
    assert "explicitly confirmed" in str(error.value)
    assert paths[0].read_bytes() == env_before
    assert paths[2].read_bytes() == registry_before


@pytest.mark.parametrize("malformed_source", ["env", "registry"])
def test_malformed_input_leaves_both_files_byte_identical(
    tmp_path, malformed_source
):
    request, profile = _validated_pair()
    env_before = ENV_TEMPLATE
    registry_before = REGISTRY_TEMPLATE
    if malformed_source == "env":
        env_before = b"LEAGUE_ID=\nthis is not an assignment\nESPN_S2=\nSWID=\n"
    else:
        registry_before = REGISTRY_TEMPLATE.replace(
            b"leagues:", b"leagues: [", 1
        )
    paths = _workspace(
        tmp_path, env_bytes=env_before, registry_bytes=registry_before
    )

    with pytest.raises(BootstrapValidationError) as error:
        _write(request, profile, paths)

    assert error.value.code == BootstrapErrorCode.CONFIG_MALFORMED
    assert paths[0].read_bytes() == env_before
    assert paths[2].read_bytes() == registry_before


@pytest.mark.parametrize("duplicate_source", ["env", "registry"])
def test_duplicate_keys_are_refused_without_mutation(tmp_path, duplicate_source):
    request, profile = _validated_pair()
    env_before = (
        b"LEAGUE_ID=\nESPN_S2=\nSWID=\n"
        b"ESPN_S2=\n"
        if duplicate_source == "env"
        else ENV_TEMPLATE
    )
    registry_before = (
        REGISTRY_TEMPLATE.replace(
            b"default_league: espn-main",
            b"default_league: espn-main"
            + (b"\r\n" if b"\r\n" in REGISTRY_TEMPLATE else b"\n")
            + b"default_league: espn-main",
            1,
        )
        if duplicate_source == "registry"
        else REGISTRY_TEMPLATE
    )
    paths = _workspace(
        tmp_path, env_bytes=env_before, registry_bytes=registry_before
    )

    with pytest.raises(BootstrapValidationError) as error:
        _write(request, profile, paths)

    assert error.value.code == BootstrapErrorCode.CONFIG_MALFORMED
    assert paths[0].read_bytes() == env_before
    assert paths[2].read_bytes() == registry_before


def test_ambiguous_registry_identity_is_refused_without_mutation(tmp_path):
    request, profile = _validated_pair()
    registry_before = REGISTRY_TEMPLATE.replace(
        b'display_name: "ESPN main league"',
        b'display_name: "Some previously configured league"',
        1,
    )
    paths = _workspace(
        tmp_path, env_bytes=ENV_TEMPLATE, registry_bytes=registry_before
    )
    env_before = paths[0].read_bytes()

    with pytest.raises(BootstrapValidationError) as error:
        _write(request, profile, paths)

    assert error.value.code == BootstrapErrorCode.CONFIG_CONFLICT
    assert paths[0].read_bytes() == env_before
    assert paths[2].read_bytes() == registry_before


def test_writer_refuses_a_second_configuration_root(tmp_path):
    request, profile = _validated_pair()
    paths = _workspace(tmp_path)
    registry_before = paths[2].read_bytes()
    alternate = paths[0].with_name("credentials.txt")

    with pytest.raises(BootstrapValidationError) as error:
        writer.write_validated_configuration(
            request,
            profile,
            env_path=alternate,
            env_template_path=paths[1],
            registry_path=paths[2],
        )

    assert error.value.code == BootstrapErrorCode.CONFIG_CONFLICT
    assert not alternate.exists()
    assert not paths[0].exists()
    assert paths[2].read_bytes() == registry_before
