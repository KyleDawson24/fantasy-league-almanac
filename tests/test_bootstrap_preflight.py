from __future__ import annotations

from datetime import date

import pytest
import requests

import tools.setup_league as setup_cli
from config.bootstrap import (
    BootstrapErrorCode,
    BootstrapRequest,
    BootstrapValidationError,
    require_supported_python,
    validate_espn_league,
    validate_request,
)


S2 = "synthetic-secret-s2"
SWID = "{SYNTHETIC-SECRET-SWID}"
LEAGUE_ID = "8675309"


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _request(**overrides):
    values = dict(
        platform="espn",
        league_id=LEAGUE_ID,
        espn_s2=S2,
        swid=SWID,
        first_season=2025,
        final_season=2026,
    )
    values.update(overrides)
    return BootstrapRequest(**values)


def _payload(*, name="Synthetic League", teams=10, league_type=5, schedule=None):
    return {
        "settings": {"name": name},
        "teams": [{"id": team_id} for team_id in range(1, teams + 1)],
        "status": {"currentLeagueType": league_type},
        "schedule": schedule or [],
    }


def test_request_repr_never_contains_credentials_or_league_id():
    rendered = repr(_request())
    assert S2 not in rendered
    assert SWID not in rendered
    assert LEAGUE_ID not in rendered


def test_version_guard_names_actual_and_supported_versions():
    require_supported_python((3, 13, 7))
    with pytest.raises(BootstrapValidationError) as exc:
        require_supported_python((3, 14, 0))
    assert exc.value.code == BootstrapErrorCode.UNSUPPORTED_PYTHON
    assert "3.14.0" in str(exc.value)
    assert "3.13" in str(exc.value)


def test_requested_history_defaults_to_current_year():
    years = validate_request(
        _request(first_season=2024, final_season=None),
        today=date(2026, 8, 18),
    )
    assert years == (2024, 2025, 2026)


@pytest.mark.parametrize(
    "overrides, code",
    [
        ({"platform": "cbs"}, BootstrapErrorCode.UNSUPPORTED_PLATFORM),
        ({"league_id": ""}, BootstrapErrorCode.BAD_INPUT),
        ({"espn_s2": ""}, BootstrapErrorCode.BAD_INPUT),
        ({"first_season": 2027}, BootstrapErrorCode.BAD_INPUT),
    ],
)
def test_bad_inputs_fail_before_network(overrides, code):
    with pytest.raises(BootstrapValidationError) as exc:
        validate_request(_request(**overrides), today=date(2026, 8, 18))
    assert exc.value.code == code


def test_every_requested_season_is_checked_and_points_evidence_is_reported(
    monkeypatch,
):
    monkeypatch.setattr("config.bootstrap.require_supported_python", lambda: None)
    calls = []

    def get(url, *, params, cookies, timeout):
        calls.append((url, params, cookies, timeout))
        year = int(url.split("/seasons/")[1].split("/")[0])
        return _Response(payload=_payload(name=f"League {year}"))

    profile = validate_espn_league(_request(), http_get=get)

    assert profile.league_name == "League 2026"
    assert profile.team_count == 10
    assert profile.league_format == "points"
    assert profile.available_seasons == (2025, 2026)
    assert profile.final_season == 2026
    assert profile.validated_through_season == 2026
    assert LEAGUE_ID not in repr(profile)
    assert [call[0].split("/seasons/")[1].split("/")[0] for call in calls] == [
        "2025",
        "2026",
    ]
    assert calls[0][1] == [
        ("view", "mSettings"),
        ("view", "mTeam"),
        ("view", "mMatchupScore"),
    ]
    assert calls[0][2] == {"swid": SWID, "espn_s2": S2}


def test_ongoing_request_preserves_null_final_season(monkeypatch):
    monkeypatch.setattr("config.bootstrap.require_supported_python", lambda: None)

    profile = validate_espn_league(
        _request(first_season=2025, final_season=None),
        http_get=lambda *a, **k: _Response(payload=_payload()),
        today=date(2026, 8, 18),
    )

    assert profile.available_seasons == (2025, 2026)
    assert profile.final_season is None
    assert profile.validated_through_season == 2026


def test_paired_matchups_are_h2h_evidence(monkeypatch):
    monkeypatch.setattr("config.bootstrap.require_supported_python", lambda: None)
    schedule = [{"home": {"teamId": 1}, "away": {"teamId": 2}}]

    profile = validate_espn_league(
        _request(first_season=2026),
        http_get=lambda *a, **k: _Response(
            payload=_payload(league_type=0, schedule=schedule)
        ),
    )

    assert profile.league_format == "h2h"
    assert "paired" in profile.format_evidence


@pytest.mark.parametrize(
    "status, code, needle",
    [
        (401, BootstrapErrorCode.AUTH_EXPIRED, "refresh both"),
        (403, BootstrapErrorCode.ACCESS_DENIED, "signed-in account"),
    ],
)
def test_auth_failures_are_actionable_and_secret_safe(
    monkeypatch, status, code, needle
):
    monkeypatch.setattr("config.bootstrap.require_supported_python", lambda: None)

    with pytest.raises(BootstrapValidationError) as exc:
        validate_espn_league(
            _request(), http_get=lambda *a, **k: _Response(status_code=status)
        )

    message = str(exc.value)
    assert exc.value.code == code
    assert needle in message.lower()
    for secret in (S2, SWID, LEAGUE_ID):
        assert secret not in message


def test_partially_missing_history_stops_before_writes(monkeypatch):
    monkeypatch.setattr("config.bootstrap.require_supported_python", lambda: None)

    def get(url, **kwargs):
        return _Response(
            status_code=404 if "/2025/" in url else 200,
            payload=_payload(),
        )

    with pytest.raises(BootstrapValidationError) as exc:
        validate_espn_league(_request(), http_get=get)
    assert exc.value.code == BootstrapErrorCode.HISTORY_UNAVAILABLE
    assert "2025" in str(exc.value)


def test_unknown_format_fails_closed(monkeypatch):
    monkeypatch.setattr("config.bootstrap.require_supported_python", lambda: None)

    with pytest.raises(BootstrapValidationError) as exc:
        validate_espn_league(
            _request(first_season=2026),
            http_get=lambda *a, **k: _Response(payload=_payload(league_type=3)),
        )
    assert exc.value.code == BootstrapErrorCode.UNSUPPORTED_FORMAT
    assert "nothing was written" in str(exc.value).lower()


def test_network_failure_is_classified_without_echoing_request(monkeypatch):
    monkeypatch.setattr("config.bootstrap.require_supported_python", lambda: None)

    def fail(*args, **kwargs):
        raise requests.Timeout("synthetic timeout")

    with pytest.raises(BootstrapValidationError) as exc:
        validate_espn_league(_request(), http_get=fail)
    assert exc.value.code == BootstrapErrorCode.NETWORK
    assert LEAGUE_ID not in str(exc.value)


@pytest.mark.parametrize(
    "response",
    [
        _Response(status_code=500),
        _Response(payload=ValueError("not JSON")),
        _Response(payload=[]),
    ],
)
def test_unexpected_espn_responses_share_one_safe_category(monkeypatch, response):
    monkeypatch.setattr("config.bootstrap.require_supported_python", lambda: None)

    with pytest.raises(BootstrapValidationError) as exc:
        validate_espn_league(
            _request(first_season=2026),
            http_get=lambda *a, **k: response,
        )

    assert exc.value.code == BootstrapErrorCode.UPSTREAM
    for secret in (S2, SWID, LEAGUE_ID):
        assert secret not in str(exc.value)


def test_cli_is_a_thin_shell_over_validation_then_writer(monkeypatch, capsys):
    request = _request(first_season=2026)
    profile = type(
        "Profile",
        (),
        {
            "league_name": "Synthetic League",
            "team_count": 10,
            "league_format": "points",
            "format_evidence": "synthetic evidence",
            "first_season": 2026,
            "final_season": 2026,
            "validated_through_season": 2026,
        },
    )()
    monkeypatch.setattr(setup_cli, "require_supported_python", lambda: None)
    monkeypatch.setattr(setup_cli, "collect_request", lambda: request)
    monkeypatch.setattr(setup_cli, "validate_espn_league", lambda value: profile)
    writes = []
    monkeypatch.setattr(
        setup_cli,
        "write_validated_configuration",
        lambda supplied_request, supplied_profile: writes.append(
            (supplied_request, supplied_profile)
        )
        or type("Result", (), {"changed": True})(),
    )
    monkeypatch.setattr(setup_cli, "prompt_create_almanac", lambda: False)

    assert setup_cli.main([]) == 0
    output = capsys.readouterr().out
    assert writes == [(request, profile)]
    assert "Validated successfully" in output
    assert "Local setup saved" in output
    assert "almanac was not started" in output
    for secret in (S2, SWID, LEAGUE_ID):
        assert secret not in output


def test_cli_stops_on_python_version_before_asking_for_secrets(monkeypatch, capsys):
    prompted = []

    def stop():
        raise BootstrapValidationError(
            BootstrapErrorCode.UNSUPPORTED_PYTHON, "use Python 3.13"
        )

    monkeypatch.setattr(setup_cli, "require_supported_python", stop)
    monkeypatch.setattr(
        setup_cli,
        "collect_request",
        lambda: prompted.append(True),
    )

    assert setup_cli.main([]) == 2
    assert prompted == []
    assert "unsupported_python" in capsys.readouterr().err
