"""Public GitHub intake stays structured and privacy-safe (MLB-252).

These are intentionally contract tests rather than a prose linter. A broken
form can disappear from GitHub's chooser without touching application code,
and a weakened warning can teach a stranger to publish the exact artifacts
the local-first design is meant to keep private.
"""

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = REPO_ROOT / ".github" / "ISSUE_TEMPLATE"
CHOOSER_URL = (
    "https://github.com/KyleDawson24/fantasy-league-almanac/"
    "issues/new/choose"
)

FORM_PATHS = {
    "bug": TEMPLATE_DIR / "01-bug-report.yml",
    "coverage": TEMPLATE_DIR / "02-coverage-request.yml",
    "feedback": TEMPLATE_DIR / "03-product-feedback.yml",
}


def _load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _field(form, field_id):
    return next(item for item in form["body"] if item.get("id") == field_id)


def _flat(value):
    return " ".join(str(value).lower().split())


def test_the_chooser_has_exactly_the_three_public_forms_and_config():
    assert {path.name for path in TEMPLATE_DIR.glob("*.yml")} == {
        "01-bug-report.yml",
        "02-coverage-request.yml",
        "03-product-feedback.yml",
        "config.yml",
    }


def test_every_issue_form_has_the_required_github_shape():
    forms = [_load(path) for path in FORM_PATHS.values()]

    assert len({form["name"] for form in forms}) == len(forms)
    for form in forms:
        assert len(form["name"]) > 3
        assert form["description"]
        assert isinstance(form["body"], list) and form["body"]

        fields = [item for item in form["body"] if "id" in item]
        ids = [item["id"] for item in fields]
        assert len(ids) == len(set(ids))
        assert not {"contact", "email", "league_id", "league-id"} & set(ids)
        assert all(item["type"] != "upload" for item in form["body"])


def test_forms_use_stable_titles_and_existing_standard_labels():
    expected = {
        "bug": ("[Bug] ", ["bug"]),
        "coverage": ("[Coverage request] ", ["enhancement"]),
        "feedback": ("[Feedback] ", ["enhancement"]),
    }

    for kind, path in FORM_PATHS.items():
        form = _load(path)
        assert (form["title"], form["labels"]) == expected[kind]


def test_every_form_requires_an_explicit_privacy_acknowledgement():
    warning_terms = (
        "credential",
        "league id",
        "member",
        "private",
    )

    for path in FORM_PATHS.values():
        form = _load(path)
        privacy = _field(form, "privacy")

        assert privacy["type"] == "checkboxes"
        options = privacy["attributes"]["options"]
        assert options
        assert all(option.get("required") is True for option in options)

        whole_form = _flat(form)
        for term in warning_terms:
            assert term in whole_form, f"{path.name} lost warning {term!r}"


def test_bug_form_collects_the_minimum_sanitized_diagnostic_context():
    bug = _load(FORM_PATHS["bug"])
    required_ids = {
        "release_version",
        "operating_system",
        "python_version",
        "fantasy_platform",
        "scoring_format",
        "history_span",
        "draft_type",
        "failed_step",
        "expected_result",
        "actual_result",
    }

    for field_id in required_ids:
        assert _field(bug, field_id)["validations"]["required"] is True

    error_tail = _field(bug, "sanitized_error_tail")
    assert error_tail["type"] == "textarea"
    assert error_tail["validations"]["required"] is False
    assert "10-20" in error_tail["attributes"]["description"]
    assert "full log" in error_tail["attributes"]["description"].lower()


def test_coverage_dimensions_are_stable_for_the_future_counter():
    coverage = _load(FORM_PATHS["coverage"])
    expected_options = {
        "fantasy_platform": [
            "ESPN",
            "CBS Sports",
            "Yahoo Fantasy",
            "Fantrax",
            "Ottoneu",
            "Sleeper",
            "Other / not listed",
        ],
        "scoring_format": [
            "Head-to-head points",
            "Season-total points (no head-to-head)",
            "Head-to-head categories",
            "Rotisserie / season-long categories",
            "Best ball",
            "Other / not sure",
        ],
        "draft_type": [
            "Auction / salary cap",
            "Snake / linear",
            "No draft / imported rosters",
            "Other / not sure",
        ],
        "history_length": [
            "1 season",
            "2-4 seasons",
            "5-9 seasons",
            "10-19 seasons",
            "20+ seasons",
        ],
    }

    for field_id, options in expected_options.items():
        field = _field(coverage, field_id)
        assert field["type"] == "dropdown"
        assert field["attributes"]["options"] == options
        assert field["validations"]["required"] is True

    intro = _flat(coverage["body"][0]["attributes"]["value"])
    assert "not telemetry" in intro
    assert "not fantasy-baseball market share" in intro
    assert "does not promise a delivery date" in intro


def test_chooser_disables_blank_public_issues_and_routes_private_support():
    config = _load(TEMPLATE_DIR / "config.yml")

    assert config["blank_issues_enabled"] is False
    assert len(config["contact_links"]) == 1
    private = config["contact_links"][0]
    assert private["name"] == "Private support by email"
    assert private["url"] == (
        "https://github.com/KyleDawson24/fantasy-league-almanac#contact"
    )
    assert "sanitized" in private["about"].lower()
    assert "never send credentials" in private["about"].lower()


def test_live_docs_link_directly_to_the_issue_chooser():
    for relative in ("README.md", "QUICKSTART.md"):
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert CHOOSER_URL in text


def test_reporting_policy_preserves_redaction_and_internal_triage_rules():
    policy = _flat(
        (REPO_ROOT / "docs" / "reporting-an-issue.md").read_text(
            encoding="utf-8"
        )
    )

    for term in (
        ".env",
        "espn cookies",
        "google oauth tokens",
        "workbook urls",
        ".duckdb",
        ".parquet",
        "full logs",
        "[redacted]",
        "internal planning issue",
        "never copied into the internal board",
        "not telemetry",
        "not a representative survey",
    ):
        assert term in policy
