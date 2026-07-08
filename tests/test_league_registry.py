"""Unit tests for config/league_registry.py (MLB-57).

Pure-function scope per the Phase 7 convention: no Snowflake, no network.
Two test families:

  1. Loader mechanics against synthetic registries written to tmp_path --
     defaults, unknown keys, malformed entries, credential checks.
  2. The committed config/leagues.yml itself -- the ESPN league must be
     entry #1 (the default) and the CBS museum league entry #2, because
     the extract/output edges and the RAW league_key stamp all hang off
     those exact keys.
"""

import textwrap

import pytest

from config.league_registry import (
    League,
    LeagueRegistryError,
    default_league_key,
    get_league,
    league_keys,
    load_registry,
)


# ---------------------------------------------------------------------------
# Synthetic-registry helpers
# ---------------------------------------------------------------------------
MINIMAL_YML = textwrap.dedent("""\
    default_league: espn-test
    leagues:
      espn-test:
        platform: espn
        display_name: "Test League"
        league_id_env: TEST_LEAGUE_ID
        credential_env: [TEST_S2, TEST_SWID]
        first_season: 2025
        final_season: null
        sinks:
          bbcode: true
      cbs-test:
        platform: cbs
        league_id: "public-slug"
        credential_env: [TEST_CBS_TOKEN]
""")


def write_registry(tmp_path, content=MINIMAL_YML):
    path = tmp_path / "leagues.yml"
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Loader mechanics
# ---------------------------------------------------------------------------
def test_default_league_resolution(tmp_path):
    path = write_registry(tmp_path)
    league = get_league(path=path)
    assert league.key == "espn-test"
    assert league.platform == "espn"
    assert default_league_key(path=path) == "espn-test"


def test_explicit_key_resolution(tmp_path):
    path = write_registry(tmp_path)
    league = get_league("cbs-test", path=path)
    assert league.platform == "cbs"
    # display_name falls back to the key when the entry doesn't set one.
    assert league.display_name == "cbs-test"


def test_unknown_key_lists_known_leagues(tmp_path):
    path = write_registry(tmp_path)
    with pytest.raises(LeagueRegistryError) as exc:
        get_league("yahoo-nope", path=path)
    message = str(exc.value)
    assert "yahoo-nope" in message
    assert "cbs-test" in message and "espn-test" in message


def test_league_keys_sorted(tmp_path):
    path = write_registry(tmp_path)
    assert league_keys(path=path) == ["cbs-test", "espn-test"]


def test_missing_file_error_names_path(tmp_path):
    ghost = tmp_path / "nope" / "leagues.yml"
    with pytest.raises(LeagueRegistryError) as exc:
        load_registry(path=ghost)
    assert "leagues.yml" in str(exc.value)


def test_malformed_yaml_rejected(tmp_path):
    path = write_registry(tmp_path, "leagues: [unclosed")
    with pytest.raises(LeagueRegistryError) as exc:
        load_registry(path=path)
    assert "not valid YAML" in str(exc.value)


def test_default_pointing_nowhere_rejected(tmp_path):
    content = MINIMAL_YML.replace("default_league: espn-test",
                                  "default_league: missing-league")
    path = write_registry(tmp_path, content)
    with pytest.raises(LeagueRegistryError) as exc:
        load_registry(path=path)
    assert "missing-league" in str(exc.value)


def test_unknown_platform_rejected(tmp_path):
    content = MINIMAL_YML.replace("platform: cbs", "platform: sleeper")
    path = write_registry(tmp_path, content)
    with pytest.raises(LeagueRegistryError) as exc:
        load_registry(path=path)
    assert "sleeper" in str(exc.value)


def test_credential_env_must_be_string_list(tmp_path):
    content = MINIMAL_YML.replace(
        "credential_env: [TEST_CBS_TOKEN]", "credential_env: TEST_CBS_TOKEN")
    path = write_registry(tmp_path, content)
    with pytest.raises(LeagueRegistryError) as exc:
        load_registry(path=path)
    assert "credential_env" in str(exc.value)


# ---------------------------------------------------------------------------
# Credential + league-id resolution (env-dependent, so monkeypatched)
# ---------------------------------------------------------------------------
def test_missing_credentials_reported(tmp_path, monkeypatch):
    path = write_registry(tmp_path)
    monkeypatch.setenv("TEST_S2", "abc")
    monkeypatch.delenv("TEST_SWID", raising=False)
    league = get_league(path=path)
    assert league.missing_credentials() == ["TEST_SWID"]
    with pytest.raises(LeagueRegistryError) as exc:
        league.require_credentials()
    assert "TEST_SWID" in str(exc.value)


def test_all_credentials_present_passes(tmp_path, monkeypatch):
    path = write_registry(tmp_path)
    monkeypatch.setenv("TEST_S2", "abc")
    monkeypatch.setenv("TEST_SWID", "{guid}")
    league = get_league(path=path)
    assert league.missing_credentials() == []
    league.require_credentials()  # must not raise


def test_league_id_env_resolution(tmp_path, monkeypatch):
    path = write_registry(tmp_path)
    monkeypatch.setenv("TEST_LEAGUE_ID", "123456")
    assert get_league(path=path).resolve_league_id() == "123456"

    monkeypatch.delenv("TEST_LEAGUE_ID")
    with pytest.raises(LeagueRegistryError) as exc:
        get_league(path=path).resolve_league_id()
    assert "TEST_LEAGUE_ID" in str(exc.value)


def test_league_id_literal_wins(tmp_path):
    path = write_registry(tmp_path)
    assert get_league("cbs-test", path=path).resolve_league_id() == "public-slug"


# ---------------------------------------------------------------------------
# The committed registry: the contract the pipeline actually runs on
# ---------------------------------------------------------------------------
def test_committed_registry_parses():
    registry = load_registry()
    assert registry["default_league"] == "espn-main"
    assert set(registry["leagues"]) == {"espn-main", "cbs-bsb"}


def test_committed_espn_entry_is_byte_neutral_default():
    """The ESPN league is registry entry #1 with zero behavior change:
    default target, same .env variables the extract has always read."""
    league = get_league()
    assert league.key == "espn-main"
    assert league.platform == "espn"
    assert league.league_id_env == "LEAGUE_ID"
    assert set(league.credential_env) == {"ESPN_S2", "SWID", "LEAGUE_ID"}


def test_committed_cbs_entry_is_museum_league():
    league = get_league("cbs-bsb")
    assert league.platform == "cbs"
    assert league.league_id_env == "CBS_LEAGUE"
    assert league.first_season == 2001
    assert league.sinks.get("bbcode") is False


def test_league_dataclass_is_immutable():
    league = get_league()
    with pytest.raises(Exception):
        league.key = "other"
