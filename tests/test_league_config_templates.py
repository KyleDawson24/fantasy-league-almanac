"""The packaging contract for league config (MLB-114).

Three directories, three jobs:

    dbt_league/seeds/         reference vocabulary -- same for every league
    dbt_league/league_config/ user config -- BLANK templates in git
    demo/league_config/       the demo fixture -- a complete fake league

What makes this testable at all is that the first two occupy the same paths
for everyone but hold different bytes: the maintainer's real league data
sits at `dbt_league/league_config/` on disk under `skip-worktree`, while the
tracked content at that path is a blank template.

SO EVERY CHECK HERE READS `git`, NEVER THE WORKING TREE. That is not a
stylistic preference. A test that opened these files with `open()` would
read real league data on the maintainer's machine, fail there and nowhere
else, and print real names into the failure output while doing it. `git
show HEAD:<path>` is what a clone actually receives, which is also the only
thing these assertions are about.

Fast and pure: no warehouse, no dbt invocation, no network.
"""
from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TEMPLATE_DIR = "dbt_league/league_config"
FIXTURE_DIR = "demo/league_config"
REFERENCE_DIR = "dbt_league/seeds"

# Bounded, like tools/check_pii.py: these run in a suite that must not hang.
TIMEOUT = 60

# owner_nicknames is the seed the MLB-95 contract is written about. Its
# public shape is these four columns and only these; a local copy may carry
# contact columns, which is exactly why neither the template nor
# dbt_project.yml may ever name them.
OWNER_NICKNAMES_PUBLIC = ["owner_id", "first_name", "last_name", "preferred_name"]
CONTACT_COLUMN_HINTS = ("email", "phone", "address", "zip", "postal")


def _git(*args):
    return subprocess.run(
        ["git", "-C", REPO, *args],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=TIMEOUT,
    ).stdout


def _tracked_csvs(directory):
    """Committed .csv paths under `directory`, from the tree -- not a walk."""
    out = _git("ls-tree", "-r", "HEAD", "--name-only", "--", directory)
    return sorted(p for p in out.splitlines() if p.endswith(".csv"))


def _committed(path):
    """The bytes a clone receives for `path`, as text."""
    return _git("show", f"HEAD:{path}")


def _rows(path):
    """(header_cells, data_lines) for a committed CSV."""
    lines = [ln for ln in _committed(path).replace("\r\n", "\n").split("\n") if ln.strip()]
    if not lines:
        return [], []
    return lines[0].split(","), lines[1:]


TEMPLATES = _tracked_csvs(TEMPLATE_DIR)
FIXTURES = _tracked_csvs(FIXTURE_DIR)


def test_templates_are_tracked():
    """A template nobody receives is not a template."""
    assert TEMPLATES, (
        f"no tracked .csv under {TEMPLATE_DIR}. Note that .gitignore blankets "
        f"*.csv -- a seed directory has to be allowlisted there or it ships as "
        f"nothing at all."
    )
    assert len(TEMPLATES) == 14, (
        f"league_config has {len(TEMPLATES)} tracked templates; documentation "
        "and the typed blank-template contract require exactly 14"
    )


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: os.path.basename(p))
def test_template_is_header_only(path):
    """Blank means blank: a header row and no data.

    dbt loads every row of a seed, and CSV has no comment syntax -- so an
    "example" row left in the file is not a comment, it is a record. In an
    override seed it would be a stray join key; in a root seed like
    matchup_schedule it would be a fabricated week in the output. Worked
    examples live in league_config/README.md, which dbt does not read.
    """
    header, data = _rows(path)
    assert header, f"{path} is empty -- a template still needs its header row"
    assert not data, (
        f"{path} carries {len(data)} data row(s). Tracked league config must be "
        f"blank: this is what a stranger clones, and it is also the file the "
        f"maintainer's real content hides behind via skip-worktree."
    )


def test_fixture_matches_template_set():
    """The fixture is a filled-in copy of the templates -- same files."""
    assert {os.path.basename(p) for p in FIXTURES} == {
        os.path.basename(p) for p in TEMPLATES
    }, (
        "demo fixture and league_config templates have drifted apart. Both "
        "directories are seed roots for the same models, so a file in one and "
        "not the other is a seed that exists in only one of the two modes."
    )


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: os.path.basename(p))
def test_fixture_header_matches_template(path):
    """Same schema in both modes, so switching roots cannot change columns."""
    name = os.path.basename(path)
    fixture_header, _ = _rows(path)
    template_header, _ = _rows(f"{TEMPLATE_DIR}/{name}")
    assert fixture_header == template_header, (
        f"{name}: fixture columns {fixture_header} != template columns "
        f"{template_header}. dbt_project.yml types these by name, so a "
        f"mismatch types one mode and not the other."
    )


def test_fixture_is_populated():
    """A fixture of blank files would pass every other test in this file."""
    populated = [p for p in FIXTURES if _rows(p)[1]]
    assert len(populated) >= 8, (
        f"only {len(populated)} of {len(FIXTURES)} fixture files carry data. "
        f"The fixture is meant to be a complete league -- if it is mostly "
        f"empty, the demo book it renders is mostly empty too."
    )


def test_fixture_owner_nicknames_all_have_preferred_name():
    """Every fixture owner must carry a preferred_name, and this is a
    disclosure control rather than a cosmetic one.

    ESPN owner names are served by the platform and land in the warehouse
    from RAW -- they do not come from a seed. dim_owner's display coalesces
    `preferred_name` FIRST, so a twin row that sets it masks the real name,
    and a twin row that leaves it blank falls through to whatever RAW holds.
    With 32 of 33 rows set, the fixture looked anonymized while one owner's
    real name would have rendered from any warehouse built on real RAW.

    This does not make the fixture safe against real RAW on its own -- the
    demo builds its own warehouse precisely because seeds cannot mask a
    source they do not feed (see tools/demo.sh). It closes the gap that
    made the fixture look like it could.
    """
    header, data = _rows(f"{FIXTURE_DIR}/owner_nicknames.csv")
    idx = header.index("preferred_name")
    blank = [row.split(",")[0] for row in data if not row.split(",")[idx].strip()]
    assert not blank, (
        f"{len(blank)} fixture owner row(s) have no preferred_name. "
        f"preferred_name is what makes the twin win over the platform-served "
        f"name, so a blank one is a real name waiting to render."
    )


def test_owner_nicknames_template_is_public_columns_only():
    """The MLB-95 contract, pinned as a test rather than a comment.

    The template IS the schema. A contact column appearing here would mean
    the committed seed had grown one.
    """
    header, _ = _rows(f"{TEMPLATE_DIR}/owner_nicknames.csv")
    assert header == OWNER_NICKNAMES_PUBLIC, (
        f"owner_nicknames template columns are {header}, expected "
        f"{OWNER_NICKNAMES_PUBLIC}"
    )


@pytest.mark.parametrize("path", TEMPLATES + FIXTURES, ids=lambda p: p)
def test_no_contact_columns_anywhere(path):
    """No tracked seed, in either mode, names a contact field."""
    header, _ = _rows(path)
    for cell in header:
        low = cell.strip().lower()
        assert not any(h in low for h in CONTACT_COLUMN_HINTS), (
            f"{path} has column '{cell}'. Identity seeds carry no contact data "
            f"in git, ever (MLB-95) -- and a column NAME is enough to publish "
            f"the shape of what is being withheld."
        )


def test_dbt_project_types_only_public_owner_columns():
    """dbt_project.yml is tracked config, and it describes seeds by name.

    This is the MLB-114 `column_types` resolution: the map matches the
    template exactly. It previously carried email/phone_number so that a
    six-column local copy would satisfy a supposed all-or-none rule -- which
    put two contact column names in public config to describe a file that
    must never be committed.
    """
    project = _committed("dbt_league/dbt_project.yml")
    block = project.split("owner_nicknames:", 1)
    assert len(block) == 2, "owner_nicknames has no column_types block"
    # Up to the next seed's block: a seed name at this indent level.
    body = block[1]
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith(":") and not line.startswith("        "):
            break                                   # next seed's config
        if ":" in stripped and not stripped.startswith("+"):
            col = stripped.split(":", 1)[0].strip().lower()
            assert not any(h in col for h in CONTACT_COLUMN_HINTS), (
                f"dbt_project.yml types '{col}' on owner_nicknames. The public "
                f"seed has four columns; typing a fifth publishes the name of "
                f"a column that only exists in a file which is never committed."
            )


def _declared_column_types():
    """{seed name: [declared column names]} from the committed dbt_project.yml.

    A small hand parser rather than a YAML load: yaml is not a test
    dependency, and the shape it walks is fixed --

        seeds:
          dbt_league:
            <seed>:
              +column_types:
                <column>: <type>

    READ FROM THE WORKING TREE, and that is a deliberate difference from
    every other reader in this file. The templates are read through git
    because they are skip-worktree and the path on disk holds real league
    data; dbt_project.yml is ordinary tracked config with no private content,
    it is not skip-worktree, and it is the copy dbt ACTUALLY APPLIES. Reading
    the committed one instead would make this pass on a stale commit and,
    worse, make it impossible to satisfy while the fix is being written.

    test_dbt_project_types_only_public_owner_columns keeps reading the
    committed bytes on purpose -- that one is about what a clone RECEIVES.
    """
    project = (pathlib.Path(REPO) / "dbt_league" / "dbt_project.yml").read_text(
        encoding="utf-8")
    body = project.split("\nseeds:", 1)
    assert len(body) == 2, "dbt_project.yml has no seeds: block"

    declared, seed, in_types = {}, None, False
    for line in body[1].splitlines():
        if line and not line.startswith(" ") and line.rstrip().endswith(":"):
            break                                    # a new top-level block
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 4 and stripped.endswith(":") and not stripped.startswith("+"):
            seed, in_types = stripped[:-1], False
            declared.setdefault(seed, [])
        elif indent == 6 and stripped == "+column_types:":
            in_types = True
        elif indent >= 8 and in_types and ":" in stripped:
            declared[seed].append(stripped.split(":", 1)[0].strip())
    return declared


def test_every_blank_template_column_has_a_declared_type():
    """MLB-235 rung 4B-2. An empty CSV has nothing to infer a type FROM.

    THE FAILURE THIS CLOSES, and it was published rather than theoretical:
    QUICKSTART says every league_config file may stay blank, then prints an
    unscoped `dbt seed && dbt run`. On the exact all-blank state an undeclared
    column arrived as whatever the loader guessed -- `league_key` landed
    INTEGER and died against the VARCHAR one from stg_box_scores, taking
    int_franchise_registry, int_cbs__team_owner_season and
    stg_cbs__mlbam_crosswalk with it. The maintainer's populated files
    inferred correctly and hid it, which is exactly why this is a test over
    the COMMITTED template rather than the working tree.
    """
    declared = _declared_column_types()
    missing = {}
    for path in TEMPLATES:
        seed = os.path.basename(path)[:-len(".csv")]
        header, _rows_ = _rows(path)
        absent = [c for c in header if c not in declared.get(seed, [])]
        if absent:
            missing[seed] = absent

    assert missing == {}, (
        f"these committed blank templates have undeclared column types: "
        f"{missing}. A fresh clone cannot infer a type from an empty CSV, so "
        f"an undeclared column makes `dbt seed && dbt run` fail on exactly "
        f"the installation QUICKSTART describes."
    )


def test_no_declared_type_names_a_column_the_public_template_lacks():
    """The other direction, and it is the privacy half.

    dbt_project.yml is tracked config. Declaring a type for a column that
    the committed template does not carry publishes the NAME of something
    that exists only in a file which is never committed -- the precise
    mistake test_dbt_project_types_only_public_owner_columns was written
    about, generalised from owner_nicknames to every league_config seed.

    It also catches the ordinary version: a type left behind after a column
    was renamed or dropped, which silently stops applying.
    """
    declared = _declared_column_types()
    template_columns = {
        os.path.basename(path)[:-len(".csv")]: _rows(path)[0]
        for path in TEMPLATES
    }

    stray = {}
    for seed, columns in declared.items():
        if seed not in template_columns:
            continue                     # reference vocabulary, not user config
        extra = [c for c in columns if c not in template_columns[seed]]
        if extra:
            stray[seed] = extra

    assert stray == {}, (
        f"dbt_project.yml types columns absent from the committed template: "
        f"{stray}. The public template header is the whole allowed schema."
    )


def test_every_league_config_seed_appears_in_the_type_map():
    """Named separately from the column check so the failure reads right: a
    seed with NO block at all is a different mistake from a seed missing one
    column, and it is the one that takes down `dbt seed` outright."""
    declared = _declared_column_types()
    seeds = [os.path.basename(p)[:-len(".csv")] for p in TEMPLATES]

    assert [s for s in seeds if s not in declared] == []


def test_seed_directories_are_allowlisted_in_gitignore():
    """`*.csv` is a blanket ignore, so each seed root needs its own negation.

    Without this the failure is silent and total: the directory simply does
    not exist on a clone, and every test above still passes because it reads
    the tree rather than the disk.
    """
    ignore = _committed(".gitignore")
    for directory in (REFERENCE_DIR, TEMPLATE_DIR, FIXTURE_DIR):
        assert f"!{directory}/*.csv" in ignore, (
            f"'{directory}' is not allowlisted in .gitignore. The blanket "
            f"'*.csv' rule would drop it from the published repo entirely."
        )


def test_reference_seeds_are_not_blank():
    """The other side of the split: reference vocabulary ships filled in.

    If these ever went blank the pipeline would still build and produce
    nothing recognisable -- no stat names, no record rules.
    """
    for path in _tracked_csvs(REFERENCE_DIR):
        _, data = _rows(path)
        assert data, (
            f"{path} is blank. seeds/ is reference vocabulary -- the same for "
            f"every league -- so it ships as real content. Anything that "
            f"differs per league belongs in {TEMPLATE_DIR}."
        )
