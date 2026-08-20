"""The guided shell supplies privacy-safe, offline, just-in-time help."""

from __future__ import annotations

from pathlib import Path

import tools.setup_league as setup_cli


REHEARSAL = setup_cli.REPO_ROOT / "docs" / "v2.0-clean-machine-rehearsal.md"


def test_default_yes_opens_the_bundled_guide_as_a_local_file(capsys):
    opened = []

    result = setup_cli.offer_illustrated_guide(
        input_fn=lambda prompt: "",
        opener=lambda url: opened.append(url) or True,
    )

    assert result is True
    assert len(opened) == 1
    assert opened[0].startswith("file:")
    assert "espn-cookie-guide.html" in opened[0]
    output = capsys.readouterr().out.lower()
    assert "never asks for your espn password" in output
    assert "hidden prompts" in output
    assert "remain local" in output


def test_declining_guide_continues_with_an_exact_local_path(capsys):
    opened = []

    result = setup_cli.offer_illustrated_guide(
        input_fn=lambda prompt: "n",
        opener=lambda url: opened.append(url) or True,
    )

    assert result is False
    assert opened == []
    assert "docs\\espn-cookie-guide.html" in capsys.readouterr().out


def test_browser_refusal_prints_the_resolved_path_without_blocking(tmp_path, capsys):
    guide = tmp_path / "Folder With Spaces" / "guide.html"
    guide.parent.mkdir()
    guide.write_text("synthetic", encoding="utf-8")

    result = setup_cli.offer_illustrated_guide(
        input_fn=lambda prompt: "y",
        opener=lambda url: False,
        guide_path=guide,
    )

    assert result is False
    assert str(guide.resolve()) in capsys.readouterr().out


def test_guide_is_fully_offline_illustrated_and_privacy_safe():
    guide = Path(setup_cli.ESPN_GUIDE)
    text = guide.read_text(encoding="utf-8")
    lowered = text.lower()

    assert text.count("<svg") >= 2
    assert "leagueid=" in lowered
    assert "espn_s2" in text
    assert "SWID" in text
    assert "application" in lowered
    assert "cookies" in lowered
    assert "hidden" in lowered
    assert "not sent to kyle" in lowered
    assert "never asks for" in lowered
    assert "password" in lowered and "two-factor" in lowered
    assert "do not send your real league id" in lowered
    assert "rotate_espn_credentials.cmd" in lowered
    assert "<script" not in lowered
    assert "src=\"http" not in lowered
    assert "href=\"http" not in lowered


def test_request_prompts_explain_defaults_years_and_hidden_cookie_input(
    monkeypatch, capsys
):
    prompts = []
    answers = iter(["", "24681357", "2015", ""])

    def answer(prompt):
        prompts.append(prompt)
        return next(answers)

    secret_prompts = []
    secrets = iter(["synthetic-s2", "{SYNTHETIC-SWID}"])

    def hidden(prompt):
        secret_prompts.append(prompt)
        return next(secrets)

    monkeypatch.setattr(setup_cli, "offer_illustrated_guide", lambda: True)
    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr(setup_cli, "getpass", hidden)

    request = setup_cli.collect_request()

    assert request.platform == "espn"
    assert request.first_season == 2015
    assert request.final_season is None
    rendered = " ".join(prompts).lower()
    assert "press enter for espn" in rendered
    assert "number after leagueid=" in rendered
    assert "first season this espn league existed" in rendered
    assert "for example 2015" in rendered
    assert "press enter if ongoing" in rendered
    hidden_rendered = " ".join(secret_prompts)
    assert "espn_s2" in hidden_rendered
    assert "including { and }" in hidden_rendered
    assert hidden_rendered.lower().count("nothing will appear") == 2
    output = capsys.readouterr().out.lower()
    assert "next two entries are hidden" in output
    assert "nothing will appear on screen" in output


def test_clean_machine_gate_is_zip_first_and_requires_no_manual_config():
    lowered = REHEARSAL.read_text(encoding="utf-8").lower()

    assert "actual candidate zip" in lowered
    assert "start_almanac.cmd" in lowered
    assert "made no manual configuration edits" in lowered
    assert "signed-out/private browser window" in lowered
    assert "zip sha-256" in lowered
    assert "private pii inventory" in lowered
    assert "do not tag, release, or post" in lowered


def test_release_and_outreach_gates_preserve_settled_scope():
    lowered = REHEARSAL.read_text(encoding="utf-8").lower()

    assert "espn-first and windows-first" in lowered
    assert "cbs guided onboarding" in lowered and "not included" in lowered
    assert "packaged sample mode is deferred" in lowered
    assert "automated browser-cookie acquisition is not included" in lowered
    assert "pending google branding review does not by itself block" in lowered
    assert "a non-kyle completion is not required before reddit" in lowered
    assert "gates the later linkedin post" in lowered
