"""The PII guard's own guarantees (MLB-203).

This is the hook that vouches for every push through the launch window, so
the thing that matters is not that it finds leaks -- it is that "the guard
passed" MEANS something. Two ways it did not:

  IT COULD PASS WITHOUT LOOKING. On a clean clone the warehouse import
  failed, the map was absent, zero tokens were built, and the script
  printed a warning and returned 0. From the exit code that gates a push,
  "nothing was found" and "nothing was searched for" were the same answer.

  IT COULD MISS THE OBVIOUS. Identities were matched as the one spelling
  that happened to be stored, so a mapped "Jonas McAvery" was invisible as
  jonas-mcavery in a slug, Jonas_McAvery in an identifier, or "Jonas Q.
  McAvery" the moment a middle initial rode along.

Every identity used below is INVENTED. The tests build their own git repo
and their own map, so nothing here reads the real map, the warehouse, or
this repository's tree -- and a failure message cannot print a real name
because none is ever loaded.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASH_GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(BASH_GIT is None, reason="needs git")


@pytest.fixture
def cp():
    spec = importlib.util.spec_from_file_location(
        "check_pii_under_test", os.path.join(REPO, "tools", "check_pii.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _sealed_off(cp, tmp_path, monkeypatch):
    """No test may reach a real source. Autouse, and that is the point.

    Every path the guard reads is a module constant resolved from the real
    checkout, so a test that only patches REPO still reads this repository's
    registry -- which resolves the real league id out of the real .env and
    walks it straight into a test process. The isolation cannot be left to
    each test remembering: it is the same class of mistake as the guard's
    own, where whether a thing got checked depended on whether somebody
    remembered to check it.

    Defaults are INERT rather than absent: sources that answer emptily and
    without error, plus a salt and an empty ledger, so a test asserting on
    degradation is asserting about the source it deliberately removed and
    not about four it forgot to set up.
    """
    salt = tmp_path / "sealed-salt.txt"
    salt.write_text("f" * 64 + "\n", encoding="utf-8")
    monkeypatch.setattr(cp, "SALT", str(salt))
    monkeypatch.setattr(cp, "LEDGER", str(tmp_path / "sealed-ledger.csv"))
    monkeypatch.setattr(cp, "REGISTRY", str(tmp_path / "no-registry.yml"))
    monkeypatch.setattr(cp, "DBT_PROJECT", str(tmp_path / "no-dbt-project.yml"))
    monkeypatch.setattr(cp, "_registry_identifiers",
                        lambda: (set(), set(), None))
    monkeypatch.setattr(
        cp, "_computed_league_inventory",
        lambda: ({cp.FRANCHISE: set(), cp.DIVISION: set(), cp.TEAM_ID: set()},
                 None))


# Invented. Two tokens, a surname with internal capitals, nothing real.
MAPPED = "Jonas McAvery"
MAPPED_SLUG = "cbs-jonas-mcavery"


def _token(cp, text, source="map"):
    return cp.Token(text, cp.FAMILY, source)


def _matches(cp, token, text):
    return token.find(text, text.lower()) is not None


# --------------------------------------------------------------------------
# Variant generation: one identity, every spelling it wears in a repo.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("variant", [
    "Jonas McAvery",            # the stored spelling
    "jonas mcavery",            # lowercased in prose
    "JONAS MCAVERY",            # a shouted heading
    "Jonas-McAvery",            # hyphenated
    "jonas-mcavery",            # a slug
    "Jonas_McAvery",            # an identifier
    "jonas.mcavery",            # a path or an email local part
    "Jonas  McAvery",           # double-spaced
    "Jonas, McAvery",           # a "Last, First" style separator
    "JonasMcAvery",             # camelCase, no separator at all
    "Jonas Q. McAvery",         # a middle initial appears
    "Jonas Q McAvery",          # ... without its dot
    "jonas-q-mcavery",          # ... inside a slug
])
def test_family_matches_every_delimiter_and_initial_variant(cp, variant):
    """The whole family, not just the spelling that got stored."""
    token = _token(cp, MAPPED)
    assert _matches(cp, token, f"see {variant} for details"), (
        f"{variant!r} did not match the mapped identity -- this spelling "
        f"would pass the guard and reach a public repo"
    )


def test_a_stored_middle_initial_still_matches_the_plain_form(cp):
    """The rule runs both directions: dropping an initial is as common as
    adding one, and the map may hold either."""
    token = _token(cp, "Jonas Q. McAvery")
    assert _matches(cp, token, "credited to Jonas McAvery in the notes")


def test_slug_identity_matches_its_own_family(cp):
    token = _token(cp, MAPPED_SLUG)
    for variant in ("cbs-jonas-mcavery", "cbs_jonas_mcavery",
                    "CBS-Jonas-McAvery", "cbs.jonas.mcavery"):
        assert _matches(cp, token, f"owner_id={variant}"), variant


# --------------------------------------------------------------------------
# ... without inventing identities that are not there.
# --------------------------------------------------------------------------

def test_family_does_not_match_across_a_line_break(cp):
    """A name is written on one line.

    Two unrelated words ending and beginning adjacent lines are a line
    wrap, not an identity -- and this exact case produced a FATAL hit on
    an archived handoff before newlines were excluded from the separator.
    A guard that fires when text reflows is one people learn to bypass.
    """
    token = _token(cp, MAPPED)
    assert not _matches(cp, token, "... the jonas-\nmcavery split ...")
    assert not _matches(cp, token, "ending in Jonas\nMcAvery starting")


def test_family_respects_word_boundaries(cp):
    """"Ana Vale" must not fire inside "Joana Vale"."""
    token = _token(cp, "Ana Vale")
    assert not _matches(cp, token, "Joana Vale batted second")
    assert _matches(cp, token, "Ana Vale batted second")


def test_separator_run_is_bounded(cp):
    """A whole clause between two words is not a delimiter."""
    token = _token(cp, MAPPED)
    assert not _matches(cp, token, "Jonas was traded, and McAvery stayed")


def test_a_digit_run_is_not_a_separator(cp):
    """The class is non-ALPHANUMERIC on purpose.

    Admitting digits would match "Jonas 2019 McAvery", and would be the
    price of covering percent-encoding -- which is why %20 is out of scope
    rather than quietly half-supported.
    """
    token = _token(cp, MAPPED)
    assert not _matches(cp, token, "Jonas 2019 McAvery")
    assert not _matches(cp, token, "jonas%20mcavery")


def test_short_multipart_tokens_do_not_become_families(cp):
    """Two two-letter parts are not distinctive enough to search for."""
    assert cp.family_parts("Al Bo") is None
    assert cp.family_parts("a-b") is None
    assert cp.family_parts("Jonas") is None
    assert cp.family_parts(MAPPED) == ["Jonas", "McAvery"]


def test_middle_initial_is_dropped_from_the_stored_parts(cp):
    """So both spellings produce the same family."""
    assert cp.family_parts("Jonas Q. McAvery") == ["Jonas", "McAvery"]


# --------------------------------------------------------------------------
# A doctored tree must BLOCK.
# --------------------------------------------------------------------------

def _git(repo, *args):
    subprocess.run([BASH_GIT, "-C", repo, *args], check=True,
                   capture_output=True, timeout=60)


def _repo_with(tmp_path, filename, content):
    repo = tmp_path / "tree"
    repo.mkdir()
    _git(str(repo), "init", "-q")
    _git(str(repo), "config", "user.email", "nobody@example.com")
    _git(str(repo), "config", "user.name", "Nobody Atall")
    (repo / filename).write_text(content, encoding="utf-8")
    _git(str(repo), "add", "-A")
    _git(str(repo), "commit", "-q", "-m", "seed")
    return repo


def _map_at(tmp_path, *reals):
    path = tmp_path / "name_map.csv"
    path.write_text("real,fake\n" + "".join(f"{r},Fake Name\n" for r in reals),
                    encoding="utf-8")
    return path


@pytest.mark.parametrize("planted", [
    "Jonas McAvery",
    "jonas-mcavery",
    "Jonas_McAvery",
    "JONAS MCAVERY",
    "Jonas Q. McAvery",
    "JonasMcAvery",
])
def test_doctored_tree_blocks_on_any_variant(cp, tmp_path, monkeypatch, planted, capsys):
    """End to end, against a real git tree, through main()."""
    repo = _repo_with(tmp_path, "notes.md", f"# notes\n\nowner: {planted}\n")
    monkeypatch.setattr(cp, "REPO", str(repo))
    monkeypatch.setattr(cp, "MAP", str(_map_at(tmp_path, MAPPED)))

    assert cp.main(["--no-warehouse"]) == 1, (
        f"a tree carrying {planted!r} passed the guard"
    )
    assert "real-league strings found" in capsys.readouterr().err


def test_clean_tree_passes(cp, tmp_path, monkeypatch):
    repo = _repo_with(tmp_path, "notes.md", "# notes\n\nnothing to see\n")
    monkeypatch.setattr(cp, "REPO", str(repo))
    monkeypatch.setattr(cp, "MAP", str(_map_at(tmp_path, MAPPED)))
    assert cp.main(["--no-warehouse"]) == 0


# --------------------------------------------------------------------------
# Strict by default: a sweep that cannot see must not report clean.
# --------------------------------------------------------------------------

def test_clean_clone_with_no_sources_fails_loudly(cp, tmp_path, monkeypatch, capsys):
    """THE regression. This exact invocation printed a warning, swept
    nothing, and exited 0 during the review that filed the ticket."""
    repo = _repo_with(tmp_path, "notes.md", "# notes\n")
    monkeypatch.setattr(cp, "REPO", str(repo))
    monkeypatch.setattr(cp, "MAP", str(tmp_path / "does-not-exist.csv"))

    code = cp.main(["--no-warehouse"])
    err = capsys.readouterr().err
    assert code == 1, "a sweep with nothing to search for exited green"
    assert "REFUSING TO VOUCH" in err
    assert "nothing to search for at all" in err


def test_degraded_run_names_the_missing_source(cp, tmp_path, monkeypatch, capsys):
    """A refusal that does not say what is missing cannot be acted on."""
    repo = _repo_with(tmp_path, "notes.md", "# notes\n")
    monkeypatch.setattr(cp, "REPO", str(repo))
    monkeypatch.setattr(cp, "MAP", str(tmp_path / "does-not-exist.csv"))
    monkeypatch.setattr(cp, "_computed_names",
                        lambda: (set(), "ModuleNotFoundError: dotenv"))

    assert cp.main([]) == 1
    err = capsys.readouterr().err
    assert "ModuleNotFoundError: dotenv" in err
    assert "private map is not on this machine" in err


def test_allow_degraded_is_the_documented_way_through(cp, tmp_path, monkeypatch, capsys):
    repo = _repo_with(tmp_path, "notes.md", "# notes\n")
    monkeypatch.setattr(cp, "REPO", str(repo))
    monkeypatch.setattr(cp, "MAP", str(tmp_path / "does-not-exist.csv"))

    assert cp.main(["--no-warehouse", "--allow-degraded"]) == 0
    err = capsys.readouterr().err
    assert "DEGRADED" in err, "a degraded run must still say so"


def test_missing_map_alone_is_degraded_even_with_a_warehouse(cp, tmp_path, monkeypatch):
    """The map carries slugs and team names the warehouse does not hold."""
    repo = _repo_with(tmp_path, "notes.md", "# notes\n")
    monkeypatch.setattr(cp, "REPO", str(repo))
    monkeypatch.setattr(cp, "MAP", str(tmp_path / "does-not-exist.csv"))
    monkeypatch.setattr(cp, "_computed_names", lambda: ({"Jonas McAvery"}, None))

    assert cp.main([]) == 1


# --------------------------------------------------------------------------
# git subprocesses: a failed call is not an empty tree.
# --------------------------------------------------------------------------

def test_ls_tree_failure_refuses(cp, tmp_path, monkeypatch, capsys):
    """A git call that fails returns an empty string, and an empty string
    used to parse as 'nothing found'."""
    monkeypatch.setattr(cp, "REPO", str(tmp_path / "not-a-repo"))
    monkeypatch.setattr(cp, "MAP", str(_map_at(tmp_path, MAPPED)))

    assert cp.main(["--no-warehouse"]) == 1
    assert "PII GUARD" in capsys.readouterr().err


def test_empty_head_tree_refuses(cp, tmp_path, monkeypatch):
    """A repo with no files is not a clean repo; sweeping it proves nothing."""
    repo = tmp_path / "empty"
    repo.mkdir()
    _git(str(repo), "init", "-q")
    _git(str(repo), "config", "user.email", "nobody@example.com")
    _git(str(repo), "config", "user.name", "Nobody Atall")
    _git(str(repo), "commit", "-q", "--allow-empty", "-m", "empty")
    monkeypatch.setattr(cp, "REPO", str(repo))
    monkeypatch.setattr(cp, "MAP", str(_map_at(tmp_path, MAPPED)))

    assert cp.main(["--no-warehouse"]) == 1


def test_truncated_cat_file_batch_refuses(cp, tmp_path, monkeypatch):
    """Truncation is not cleanliness.

    The response parser used to `break` on a short read, so a batch that
    stopped early scanned a prefix of the tree and reported clean on the
    rest.
    """
    repo = _repo_with(tmp_path, "notes.md", "# notes\n\nplenty of content\n")
    monkeypatch.setattr(cp, "REPO", str(repo))

    real_popen = cp.subprocess.Popen

    class _TruncatingPopen:
        """Wraps the real Popen and hands back half the batch."""

        def __init__(self, *args, **kwargs):
            self._inner = real_popen(*args, **kwargs)
            self._truncate = "cat-file" in args[0]
            self.returncode = 0

        def communicate(self, *args, **kwargs):
            # Signature mirrors Popen's: subprocess.run passes `input`
            # positionally, and this stands in for both calls.
            out, err = self._inner.communicate(*args, **kwargs)
            self.returncode = self._inner.returncode
            return (out[: len(out) // 2] if self._truncate else out), err

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._inner.__exit__(*exc)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    monkeypatch.setattr(cp.subprocess, "Popen", _TruncatingPopen)
    with pytest.raises(cp.Failure):
        list(cp.head_blobs())


def test_git_helper_raises_on_a_failed_call(cp, tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "REPO", str(tmp_path / "not-a-repo"))
    with pytest.raises(cp.Failure):
        cp._git("rev-parse", "HEAD")


def test_missing_maintainer_name_is_tolerated(cp, tmp_path, monkeypatch):
    """git config failing means no exemptions, which searches MORE, not
    less -- so it is allowed to fail where nothing else is."""
    monkeypatch.setattr(cp, "REPO", str(tmp_path / "not-a-repo"))
    assert cp._maintainer_words() == set()


# --------------------------------------------------------------------------
# Every token that gets built must be classified.
# --------------------------------------------------------------------------

def test_every_token_is_classified(cp, tmp_path, monkeypatch):
    """A populated run leaves nothing in an unsearched limbo."""
    repo = _repo_with(tmp_path, "notes.md", "# notes\n")
    monkeypatch.setattr(cp, "REPO", str(repo))
    monkeypatch.setattr(cp, "MAP", str(_map_at(
        tmp_path, MAPPED, MAPPED_SLUG, "Vale", "HDJX", "Some Team Name")))

    tokens, census = cp.build_tokens(use_warehouse=False)
    assert tokens
    known = set(cp.MODES)
    assert all(t.mode in known for t in tokens)
    # Nothing is silently dropped: everything is either searchable or
    # counted in one of the census's explicit buckets. "Unresolved" is one
    # of those buckets rather than a quiet discard -- a map row whose
    # category cannot be established is refused, loudly, not guessed.
    accounted = (len(tokens) + census["exempt_maintainer"]
                 + len(census["too_short"]) + len(census["map_unresolved"]))
    assert accounted == census["computed"] + census["map"]


# ==========================================================================
# MLB-234: the categories the guard used to be blind to, and the ledger
# that replaced a class-wide amnesty.
#
# Every identity below is INVENTED, and invented ALL THE WAY DOWN -- names,
# labels, ids, owners and divisions alike. A synthetic fixture that borrows
# one real franchise abbrev to look realistic is not a synthetic fixture;
# it is the leak, committed, with a test asserting it stays.
# ==========================================================================

# A league that does not exist. Four labels in four different shapes,
# because "a label is four uppercase letters" is exactly the assumption
# MLB-234 exists to delete.
FAKE_FRANCHISE = "Quorum Valley Quails"      # a normal multi-part team name
FAKE_ABBREV = "QVQL"                          # the pioneer league's house style
FAKE_ALNUM_LABEL = "7-11-3-9"                 # digits and dashes: still a label
FAKE_EMOJI_LABEL = "\N{DUCK}\N{DUCK}"         # two glyphs: still a label
FAKE_SHORT_LABEL = "QV"                       # under the 3-char prose floor
FAKE_DIVISION = "Quorum Northern Division"
FAKE_LEAGUE_ID = "8675309421"                 # high entropy: blocks bare
FAKE_TEAM_ID = "6"                            # low entropy: context only


def _sources(cp, monkeypatch, *, owners=(), franchises=(), divisions=(),
             team_ids=(), league_ids=(), keys=()):
    """Pin every authoritative source. Nothing here touches a warehouse."""
    monkeypatch.setattr(cp, "_computed_names", lambda: (set(owners), None))
    monkeypatch.setattr(
        cp, "_computed_league_inventory",
        lambda: ({cp.FRANCHISE: set(franchises), cp.DIVISION: set(divisions),
                  cp.TEAM_ID: set(team_ids)}, None))
    monkeypatch.setattr(cp, "_registry_identifiers",
                        lambda: (set(league_ids), set(keys), None))


def _secrets_at(cp, tmp_path, monkeypatch, ledger_rows=()):
    """A salt and a ledger of our own, never the real ones."""
    salt = tmp_path / "salt.txt"
    salt.write_text("0" * 64 + "\n", encoding="utf-8")
    ledger = tmp_path / "dispositions.csv"
    monkeypatch.setattr(cp, "SALT", str(salt))
    monkeypatch.setattr(cp, "LEDGER", str(ledger))
    monkeypatch.setattr(cp, "DBT_PROJECT", str(tmp_path / "no-dbt-project.yml"))
    cp.write_ledger(ledger_rows)
    return ledger


def _sweep(cp, tokens, census=None):
    return cp.sweep(tokens, None, cp._salt(), cp.load_ledger(),
                    (census or {}).get("ident_keys", ()))


# --------------------------------------------------------------------------
# The inventory is DERIVED, per category, and deduplicated within one.
# --------------------------------------------------------------------------

def test_inventory_carries_every_category_from_its_own_source(
        cp, tmp_path, monkeypatch):
    """Each category arrives from the source that is authoritative for it."""
    monkeypatch.setattr(cp, "MAP", str(_map_at(tmp_path)))
    _secrets_at(cp, tmp_path, monkeypatch)
    _sources(cp, monkeypatch, owners=["Jonas McAvery"],
             franchises=[FAKE_FRANCHISE, FAKE_ABBREV],
             divisions=[FAKE_DIVISION], team_ids=[FAKE_TEAM_ID],
             league_ids=[FAKE_LEAGUE_ID])

    tokens, census = cp.build_tokens()
    assert census["categories"][cp.OWNER] == 1
    assert census["categories"][cp.FRANCHISE] == 2
    assert census["categories"][cp.DIVISION] == 1
    assert census["categories"][cp.TEAM_ID] == 1
    assert census["categories"][cp.LEAGUE_ID] == 1


def test_the_same_string_in_two_categories_is_not_collapsed(
        cp, tmp_path, monkeypatch):
    """A franchise label and an owner's initials can be the same letters and
    are searched differently; collapsing them drops one silently."""
    monkeypatch.setattr(cp, "MAP", str(_map_at(tmp_path)))
    _secrets_at(cp, tmp_path, monkeypatch)
    _sources(cp, monkeypatch, owners=[FAKE_ABBREV], franchises=[FAKE_ABBREV])

    tokens, _ = cp.build_tokens()
    assert {t.category for t in tokens} == {cp.OWNER, cp.FRANCHISE}


def test_one_category_deduplicates_within_itself(cp, tmp_path, monkeypatch):
    """Two seams report the same franchise; it is one search, not two."""
    monkeypatch.setattr(cp, "MAP", str(_map_at(tmp_path)))
    _secrets_at(cp, tmp_path, monkeypatch)
    _sources(cp, monkeypatch,
             franchises=[FAKE_FRANCHISE, FAKE_FRANCHISE.upper()])

    tokens, _ = cp.build_tokens()
    assert len([t for t in tokens if t.category == cp.FRANCHISE]) == 1


def test_the_holding_pen_sentinel_is_not_an_identity(cp, tmp_path, monkeypatch):
    """The placeholder team is configuration, not somebody's franchise --
    and it is read from the dbt vars rather than written down here."""
    project = tmp_path / "dbt_project.yml"
    project.write_text("vars:\n  holding_pen_franchise_id: 9999\n"
                       "  holding_pen_label: '####'\n", encoding="utf-8")
    monkeypatch.setattr(cp, "MAP", str(_map_at(tmp_path)))
    _secrets_at(cp, tmp_path, monkeypatch)
    monkeypatch.setattr(cp, "DBT_PROJECT", str(project))
    _sources(cp, monkeypatch, franchises=["####", FAKE_ABBREV],
             team_ids=["9999"])

    tokens, census = cp.build_tokens()
    assert census["sentinels"] == 2
    assert [t.text for t in tokens] == [FAKE_ABBREV]


# --------------------------------------------------------------------------
# A label is whatever the league says it is.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("label", [
    FAKE_ABBREV,            # four uppercase letters -- the old sole shape
    FAKE_ALNUM_LABEL,       # digits and dashes
    FAKE_EMOJI_LABEL,       # emoji
    FAKE_SHORT_LABEL,       # two characters
])
def test_any_label_shape_is_searched_and_found(cp, tmp_path, monkeypatch, label):
    """The predecessor searched exactly one of these four."""
    monkeypatch.setattr(cp, "MAP", str(_map_at(tmp_path)))
    _secrets_at(cp, tmp_path, monkeypatch)
    _sources(cp, monkeypatch, franchises=[label])

    tokens, _ = cp.build_tokens()
    assert [t.mode for t in tokens] == [cp.LABEL], (
        f"{label!r} was not classified as a label")
    blob = f"the team ({label}) finished third"
    assert tokens[0].find(blob, blob.lower(), {}) is not None, (
        f"{label!r} is in the inventory but does not match its own occurrence")


@pytest.mark.parametrize("label", [FAKE_ALNUM_LABEL, FAKE_EMOJI_LABEL])
def test_an_odd_label_is_never_promoted_into_a_fatal_class(
        cp, tmp_path, monkeypatch, label):
    """THE regression. A label that broke the pioneer league's shape used to
    fall through to the one-word class, where a hit FAILS a push -- so a team
    was punished for not looking like the first league's teams."""
    monkeypatch.setattr(cp, "MAP", str(_map_at(tmp_path)))
    _secrets_at(cp, tmp_path, monkeypatch)
    _sources(cp, monkeypatch, franchises=[label])

    tokens, _ = cp.build_tokens()
    assert cp.severity(tokens[0]) == cp.ALLOWED


def test_an_emoji_label_is_not_dropped_as_too_short(cp, tmp_path, monkeypatch):
    """The 3-character floor exists so a token cannot fire on prose. An
    emoji cannot fire on prose, so the floor does not apply to it -- and
    dropping it meant the label was never searched at all."""
    monkeypatch.setattr(cp, "MAP", str(_map_at(tmp_path)))
    _secrets_at(cp, tmp_path, monkeypatch)
    _sources(cp, monkeypatch, franchises=[FAKE_EMOJI_LABEL])

    tokens, census = cp.build_tokens()
    assert census["too_short"] == []
    assert len(tokens) == 1


def test_a_division_name_blocks(cp, tmp_path, monkeypatch):
    """Divisions are NOT covered by the franchise-name ruling."""
    monkeypatch.setattr(cp, "MAP", str(_map_at(tmp_path)))
    _secrets_at(cp, tmp_path, monkeypatch)
    _sources(cp, monkeypatch, divisions=[FAKE_DIVISION])

    tokens, _ = cp.build_tokens()
    assert cp.severity(tokens[0]) == cp.FATAL


# --------------------------------------------------------------------------
# Identifiers: entropy decides whether a bare occurrence counts.
# --------------------------------------------------------------------------

def test_a_high_entropy_league_id_matches_bare_and_blocks(
        cp, tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "MAP", str(_map_at(tmp_path)))
    _secrets_at(cp, tmp_path, monkeypatch)
    _sources(cp, monkeypatch, league_ids=[FAKE_LEAGUE_ID])

    tokens, _ = cp.build_tokens()
    token = tokens[0]
    assert token.bare, "a 10-character capability id must match on its own"
    assert cp.severity(token) == cp.FATAL
    blob = f"see the archive at {FAKE_LEAGUE_ID} for details"
    assert token.find(blob, blob.lower(), {}) is not None


def test_a_low_entropy_team_id_needs_an_identity_shaped_context(
        cp, tmp_path, monkeypatch):
    """Measured on the real tree, the one- and two-digit team ids occur
    32,800 times across 290 of 328 files. Matching them bare is not a
    threat model, it is a denial of service against the reader."""
    monkeypatch.setattr(cp, "MAP", str(_map_at(tmp_path)))
    _secrets_at(cp, tmp_path, monkeypatch)
    _sources(cp, monkeypatch, team_ids=[FAKE_TEAM_ID])

    tokens, census = cp.build_tokens()
    token = tokens[0]
    assert not token.bare
    pattern = cp.ident_context_pattern(census["ident_keys"])

    bare = f"the streak ran {FAKE_TEAM_ID} weeks and then broke"
    assert token.find(bare, bare.lower(),
                      cp.ident_contexts(bare, pattern)) is None, (
        "a bare number in prose was read as a team id")


@pytest.mark.parametrize("blob", [
    'payload = {"teamId": 6}',
    "team_id=6",
    "GET /teams/6/roster",
    "franchise-id: 6",
])
def test_an_identity_shaped_context_does_catch_the_low_entropy_id(
        cp, tmp_path, monkeypatch, blob):
    """The exposures a low-entropy id actually has: a committed config, a
    captured payload, a URL."""
    monkeypatch.setattr(cp, "MAP", str(_map_at(tmp_path)))
    _secrets_at(cp, tmp_path, monkeypatch)
    _sources(cp, monkeypatch, team_ids=[FAKE_TEAM_ID])

    tokens, census = cp.build_tokens()
    pattern = cp.ident_context_pattern(census["ident_keys"])
    assert tokens[0].find(blob, blob.lower(),
                          cp.ident_contexts(blob, pattern)) is not None, blob


def test_a_registry_credential_name_becomes_context_vocabulary(
        cp, tmp_path, monkeypatch):
    """A new league's env variable is covered without editing the guard."""
    monkeypatch.setattr(cp, "MAP", str(_map_at(tmp_path)))
    _secrets_at(cp, tmp_path, monkeypatch)
    _sources(cp, monkeypatch, team_ids=[FAKE_TEAM_ID], keys=["QUORUM_LEAGUE"])

    tokens, census = cp.build_tokens()
    pattern = cp.ident_context_pattern(census["ident_keys"])
    blob = "QUORUM_LEAGUE=6"
    assert tokens[0].find(blob, blob.lower(),
                          cp.ident_contexts(blob, pattern)) is not None


def test_the_entropy_boundary_is_a_named_constant(cp):
    """So the threat model is one number in one place, not eight literals."""
    assert cp.IDENT_BARE_MIN == 8


# --------------------------------------------------------------------------
# Dispositions: no class-wide amnesty, ever again.
# --------------------------------------------------------------------------

def _planted(cp, tmp_path, monkeypatch, content, **sources):
    repo = _repo_with(tmp_path, "notes.md", content)
    monkeypatch.setattr(cp, "REPO", str(repo))
    monkeypatch.setattr(cp, "MAP", str(_map_at(tmp_path)))
    _sources(cp, monkeypatch, **sources)
    return repo


def test_a_new_owner_occurrence_is_unreviewed_not_dispositioned(
        cp, tmp_path, monkeypatch):
    """THE regression MLB-234 was filed for. The predecessor greeted every
    hit of a class -- including one committed five minutes ago -- with a
    banner citing a ruling taken on sites nobody had enumerated."""
    _planted(cp, tmp_path, monkeypatch, "credited to Vance in the notes\n",
             owners=["Vance"])
    _secrets_at(cp, tmp_path, monkeypatch)

    tokens, census = cp.build_tokens()
    hits = _sweep(cp, tokens, census)
    assert len(hits) == 1
    assert hits[0][5] is None, (
        "a brand-new occurrence was reported as already dispositioned")


def _ledger_row(cp, salt, path, category, text, context, ordinal=0,
                disposition=None):
    return {"path": path, "category": category,
            "digest": cp.digest(salt, category, text, context, ordinal),
            "disposition": disposition or cp.COLLISION,
            "reason": "reviewed", "recorded": "x"}


def _fingerprint_for(cp, repo, path, category, text, salt):
    """The fingerprint the sweep will compute for the FIRST occurrence of
    `text` in `path` -- worked out the same way the sweep does, so a test
    can record a decision about one real occurrence."""
    blob = (repo / path).read_text(encoding="utf-8")
    token = [t for t in cp.build_tokens()[0]
             if t.text == text and t.category == category][0]
    start, end, _m = token.find_all(blob, blob.lower(), {})[0]
    return cp.digest(salt, category, text,
                     cp.normalize_context(blob, start, end), 0)


def test_a_recorded_disposition_covers_only_its_own_site(
        cp, tmp_path, monkeypatch):
    """A decision about one path is not a decision about the next one."""
    repo = _repo_with(tmp_path, "notes.md", "credited to Vance here\n")
    (repo / "other.md").write_text("also Vance over here\n", encoding="utf-8")
    _git(str(repo), "add", "-A")
    _git(str(repo), "commit", "-q", "-m", "two sites")
    monkeypatch.setattr(cp, "REPO", str(repo))
    monkeypatch.setattr(cp, "MAP", str(_map_at(tmp_path)))
    _sources(cp, monkeypatch, owners=["Vance"])
    _secrets_at(cp, tmp_path, monkeypatch)
    salt = cp._salt()
    fp = _fingerprint_for(cp, repo, "notes.md", cp.OWNER, "Vance", salt)
    _secrets_at(cp, tmp_path, monkeypatch, ledger_rows=[{
        "path": "notes.md", "category": cp.OWNER, "digest": fp,
        "disposition": cp.COLLISION, "reason": "reviewed", "recorded": "x"}])

    tokens, census = cp.build_tokens()
    by_path = {h[1]: h[5] for h in _sweep(cp, tokens, census)}
    assert by_path["notes.md"] is not None
    assert by_path["other.md"] is None, (
        "one path's disposition leaked onto another path's hit")


def test_a_disposition_does_not_cross_categories(cp, tmp_path, monkeypatch):
    """Reviewing a team id at a path says nothing about an owner name at
    the same path."""
    _planted(cp, tmp_path, monkeypatch,
             "team_id=6 was credited to Vance\n",
             owners=["Vance"], team_ids=[FAKE_TEAM_ID])
    _secrets_at(cp, tmp_path, monkeypatch)
    salt = cp._salt()
    tokens, census = cp.build_tokens()
    hits = _sweep(cp, tokens, census)
    tid = [h for h in hits if h[3].category == cp.TEAM_ID][0]
    _secrets_at(cp, tmp_path, monkeypatch, ledger_rows=[{
        "path": "notes.md", "category": cp.TEAM_ID, "digest": tid[6],
        "disposition": cp.COLLISION, "reason": "reviewed", "recorded": "x"}])

    tokens, census = cp.build_tokens()
    by_cat = {h[3].category: h[5] for h in _sweep(cp, tokens, census)}
    assert by_cat[cp.TEAM_ID] is not None
    assert by_cat[cp.OWNER] is None


def test_the_ledger_never_carries_the_identity_itself(cp, tmp_path, monkeypatch):
    """Path, category and reason are public. The token is not -- and an
    UNSALTED digest of a four-letter label is the label, because all
    456,976 of them can be hashed in a second."""
    _secrets_at(cp, tmp_path, monkeypatch)
    salt = cp._salt()
    d = cp.digest(salt, cp.FRANCHISE, FAKE_ABBREV)
    assert FAKE_ABBREV.casefold() not in d.casefold()
    assert d != cp.digest("0" * 63 + "1", cp.FRANCHISE, FAKE_ABBREV), (
        "the digest does not depend on the secret, so it is brute-forceable")
    assert d != cp.digest(salt, cp.OWNER, FAKE_ABBREV), (
        "the digest ignores the category, so a row would cross categories")


def test_a_sweep_without_the_secret_refuses_to_vouch(
        cp, tmp_path, monkeypatch, capsys):
    """Without it, every disposition reads as missing -- and 'everything is
    unreviewed' is indistinguishable from 'nothing was ever reviewed'."""
    _planted(cp, tmp_path, monkeypatch, "# notes\n", franchises=[FAKE_ABBREV])
    monkeypatch.setattr(cp, "SALT", str(tmp_path / "no-salt.txt"))
    monkeypatch.setattr(cp, "LEDGER", str(tmp_path / "no-ledger.csv"))
    monkeypatch.setattr(cp, "DBT_PROJECT", str(tmp_path / "none.yml"))

    assert cp.main([]) == 1
    assert "disposition secret is not on this machine" in capsys.readouterr().err


def test_the_default_invocation_blocks_on_an_unreviewed_occurrence(
        cp, tmp_path, monkeypatch):
    """THE gap: the gate is whatever the hook types, and the hook types the
    default. This used to print a warning and exit 0 while a separate
    --fail-on-unreviewed did the blocking, so a new unreviewed occurrence
    was pushable and only a reader of the push output would ever know."""
    _planted(cp, tmp_path, monkeypatch, "credited to Vance in the notes\n",
             owners=["Vance"])
    _secrets_at(cp, tmp_path, monkeypatch)

    assert cp.main([]) == 1, "the default invocation did not block"


def test_allow_unreviewed_is_diagnostic_and_says_so(
        cp, tmp_path, monkeypatch, capsys):
    """The census escape hatch still exists; it is just not the gate."""
    _planted(cp, tmp_path, monkeypatch, "credited to Vance in the notes\n",
             owners=["Vance"])
    _secrets_at(cp, tmp_path, monkeypatch)

    assert cp.main(["--allow-unreviewed"]) == 0
    err = capsys.readouterr().err
    assert "UNREVIEWED" in err
    assert "not blocking" in err


def test_the_tracked_hook_runs_the_gate_with_no_override_flags():
    """The hook is tracked so the rule cannot live on one machine, and it
    must not quietly opt out of the thing it exists to enforce."""
    hook = os.path.join(REPO, "tools", "hooks", "pre-push")
    assert os.path.exists(hook), "the pre-push hook is not tracked in the repo"
    body = open(hook, encoding="utf-8").read()
    invocation = [ln for ln in body.splitlines()
                  if "check_pii.py" in ln and not ln.lstrip().startswith("#")]
    assert invocation, "the hook does not invoke the guard"
    for flag in ("--allow-unreviewed", "--allow-degraded", "--no-warehouse"):
        assert all(flag not in ln for ln in invocation), (
            f"the hook passes {flag}, so the gate does not gate")


# --------------------------------------------------------------------------
# End to end: a planted identity in a real tree, per category.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("category,planted,expect", [
    ("divisions", FAKE_DIVISION, "fatal"),
    ("league_ids", FAKE_LEAGUE_ID, "fatal"),
    ("owners", "Vance", "unreviewed"),
    ("franchises", FAKE_FRANCHISE, "allowed"),
    ("franchises", FAKE_EMOJI_LABEL, "allowed"),
    ("franchises", FAKE_ALNUM_LABEL, "allowed"),
])
def test_adding_one_identity_to_a_source_catches_its_planted_occurrence(
        cp, tmp_path, monkeypatch, capsys, category, planted, expect):
    """Acceptance: a synthetic identity added to the SOURCE is caught at
    HEAD with no second list to edit -- and lands in the class its category
    was ruled into, not the class its characters suggest."""
    _planted(cp, tmp_path, monkeypatch, f"see {planted} for details\n",
             **{category: [planted]})
    _secrets_at(cp, tmp_path, monkeypatch)

    code = cp.main([])
    err = capsys.readouterr().err
    if expect == "fatal":
        assert code == 1, f"{category} identity did not block"
        assert "real-league strings found" in err
    elif expect == "unreviewed":
        assert code == 1, "an unreviewed occurrence did not block"
        assert "UNREVIEWED" in err
    else:
        assert code == 0, "an allowed-by-rule franchise blocked the gate"
        assert "allowed by category rule" in err
        assert "UNREVIEWED" not in err, (
            "an allowed-by-rule franchise was filed as review debt")


def test_a_missing_league_inventory_refuses_to_vouch(
        cp, tmp_path, monkeypatch, capsys):
    """Every source gets the MLB-203 treatment, not just the original two."""
    repo = _repo_with(tmp_path, "notes.md", "# notes\n")
    monkeypatch.setattr(cp, "REPO", str(repo))
    monkeypatch.setattr(cp, "MAP", str(_map_at(tmp_path, MAPPED)))
    _secrets_at(cp, tmp_path, monkeypatch)
    monkeypatch.setattr(cp, "_computed_names", lambda: ({"Jonas McAvery"}, None))
    monkeypatch.setattr(
        cp, "_computed_league_inventory",
        lambda: ({cp.FRANCHISE: set(), cp.DIVISION: set(), cp.TEAM_ID: set()},
                 "OperationalError: no route to warehouse"))
    monkeypatch.setattr(cp, "_registry_identifiers", lambda: (set(), set(), None))

    assert cp.main([]) == 1
    assert "league inventory could not be computed" in capsys.readouterr().err


def test_an_unresolvable_league_id_refuses_to_vouch(
        cp, tmp_path, monkeypatch, capsys):
    """An unset .env variable means the one string that would hand a
    stranger the league is not being searched for."""
    repo = _repo_with(tmp_path, "notes.md", "# notes\n")
    monkeypatch.setattr(cp, "REPO", str(repo))
    monkeypatch.setattr(cp, "MAP", str(_map_at(tmp_path, MAPPED)))
    _secrets_at(cp, tmp_path, monkeypatch)
    monkeypatch.setattr(cp, "_computed_names", lambda: ({"Jonas McAvery"}, None))
    monkeypatch.setattr(
        cp, "_computed_league_inventory",
        lambda: ({cp.FRANCHISE: set(), cp.DIVISION: set(), cp.TEAM_ID: set()},
                 None))
    monkeypatch.setattr(cp, "_registry_identifiers",
                        lambda: (set(), set(), "league id unresolved for x"))

    assert cp.main([]) == 1
    assert "league id unresolved" in capsys.readouterr().err


def test_a_map_sourced_franchise_name_still_blocks(cp, tmp_path, monkeypatch):
    """The franchise ruling is general; the map is specific. Somebody put
    that exact string on the replace-list, and a later general decision
    about team names does not repeal a specific decision to remove one."""
    repo = _repo_with(tmp_path, "notes.md", f"see {FAKE_FRANCHISE}\n")
    monkeypatch.setattr(cp, "REPO", str(repo))
    monkeypatch.setattr(cp, "MAP", str(_map_at(tmp_path, FAKE_FRANCHISE)))
    _secrets_at(cp, tmp_path, monkeypatch)
    _sources(cp, monkeypatch, franchises=[FAKE_FRANCHISE])

    assert cp.main([]) == 1, (
        "a franchise name on the map's replace-list was downgraded to review "
        "by the warehouse's general classification")


# ==========================================================================
# A disposition is about an OCCURRENCE, not a token in a file.
#
# The predecessor stopped at the first match of a token per file, so one
# recorded decision answered for every later use of the same word in the
# same file. That is not a smaller version of the class-wide amnesty it
# replaced -- it is the same bug at file scale.
# ==========================================================================

def test_every_occurrence_is_enumerated_not_just_the_first(cp):
    """The matcher has to see all of them before anything downstream can
    tell them apart."""
    token = cp.Token("Vance", cp.WORD, "warehouse", cp.OWNER)
    blob = "Vance opened, then Vance closed, and later Vance again"
    assert len(token.find_all(blob, blob.lower(), {})) == 3
    assert token.find(blob, blob.lower(), {})[0] == 0, (
        "find() must still answer 'the first one' for callers that only ask "
        "whether this token appears here")


def test_recording_one_occurrence_leaves_the_other_unreviewed(
        cp, tmp_path, monkeypatch):
    """Same token, same file, two different sentences: reviewing one is not
    reviewing the other."""
    _planted(cp, tmp_path, monkeypatch,
             "Vance is an outfielder in the seed data.\n"
             "The commissioner that year was Vance, who ran the draft.\n",
             owners=["Vance"])
    _secrets_at(cp, tmp_path, monkeypatch)
    salt = cp._salt()

    tokens, census = cp.build_tokens()
    hits = _sweep(cp, tokens, census)
    assert len(hits) == 2, "both occurrences must be seen"
    assert hits[0][6] != hits[1][6], (
        "two occurrences in different sentences share a fingerprint, so a "
        "decision about one silently answers for the other")

    # Record ONLY the first.
    _secrets_at(cp, tmp_path, monkeypatch, ledger_rows=[{
        "path": "notes.md", "category": cp.OWNER, "digest": hits[0][6],
        "disposition": cp.COLLISION, "reason": "ballplayer", "recorded": "x"}])
    tokens, census = cp.build_tokens()
    after = _sweep(cp, tokens, census)
    assert sum(1 for h in after if h[5] is not None) == 1
    assert sum(1 for h in after if h[5] is None) == 1, (
        "the unrecorded occurrence inherited the recorded one's decision")


def test_an_earlier_collision_cannot_mask_a_later_genuine_use(
        cp, tmp_path, monkeypatch, capsys):
    """THE failure shape the review named.

    A file mentions an MLB player whose surname is also a league member's
    name; that is a collision and is recorded as one. Later somebody adds a
    real reference to the member further down the SAME file. Under a
    token-in-file key the old row still answers and the new one is never
    seen -- a leak inheriting a decision taken about a ballplayer.
    """
    _planted(cp, tmp_path, monkeypatch,
             "Box score: Vance went 2-for-4 with a double.\n"
             "Roster note: the team is owned by Vance, reachable all season.\n",
             owners=["Vance"])
    _secrets_at(cp, tmp_path, monkeypatch)

    tokens, census = cp.build_tokens()
    hits = _sweep(cp, tokens, census)
    ballplayer = [h for h in hits if "Box score" in h[4]][0]
    _secrets_at(cp, tmp_path, monkeypatch, ledger_rows=[{
        "path": "notes.md", "category": cp.OWNER, "digest": ballplayer[6],
        "disposition": cp.COLLISION, "reason": "an MLB player's name",
        "recorded": "x"}])

    assert cp.main([]) == 1, (
        "the later genuine use inherited the earlier collision's disposition "
        "and passed the gate")
    assert "UNREVIEWED" in capsys.readouterr().err


def test_identical_contexts_are_disambiguated_by_ordinal(
        cp, tmp_path, monkeypatch):
    """Two occurrences whose surroundings are genuinely the same -- a
    repeated table row, a generated block -- still have to be two."""
    _planted(cp, tmp_path, monkeypatch,
             "| Vance | 10 |\n| Vance | 10 |\n", owners=["Vance"])
    _secrets_at(cp, tmp_path, monkeypatch)

    tokens, census = cp.build_tokens()
    hits = _sweep(cp, tokens, census)
    assert len(hits) == 2
    assert hits[0][6] != hits[1][6], (
        "identical surroundings collapsed to one fingerprint, so one row "
        "would silently cover both")


def test_reflowing_text_keeps_a_decision_but_rewriting_it_does_not(cp):
    """The deliberate boundary. Whitespace is normalized so re-wrapping a
    paragraph does not invalidate a review; changing the words does, and
    re-review is the safe direction."""
    a = "the owner was Vance and the season ended"
    b = "the owner was   Vance\nand the season ended"
    c = "the commissioner was Vance and the season ended"
    ctx = lambda s: cp.normalize_context(s, s.index("Vance"),
                                         s.index("Vance") + 5)
    assert ctx(a) == ctx(b), "re-wrapping a line invalidated a decision"
    assert ctx(a) != ctx(c), "a rewritten sentence kept its old decision"


def test_an_allowed_category_is_never_filed(cp, tmp_path, monkeypatch):
    """An allowed-by-rule occurrence carries no fingerprint at all, so it
    cannot accumulate rows and cannot be 'unreviewed'."""
    _planted(cp, tmp_path, monkeypatch,
             f"see {FAKE_FRANCHISE} and {FAKE_FRANCHISE} again\n",
             franchises=[FAKE_FRANCHISE])
    _secrets_at(cp, tmp_path, monkeypatch)

    tokens, census = cp.build_tokens()
    hits = _sweep(cp, tokens, census)
    assert len(hits) == 2
    assert all(h[0] == cp.ALLOWED for h in hits)
    assert all(h[6] is None for h in hits), (
        "an allowed occurrence was given a ledger fingerprint, which is how "
        "a category ruling turns back into a per-site chore")


# --------------------------------------------------------------------------
# The map's missing category column.
# --------------------------------------------------------------------------

def _map_categories_at(cp, tmp_path, monkeypatch, **rows):
    path = tmp_path / "name_map_categories.csv"
    path.write_text("# a comment the reader is entitled to write\nreal,category\n"
                    + "".join(f"{k},{v}\n" for k, v in rows.items()),
                    encoding="utf-8")
    monkeypatch.setattr(cp, "MAP_CATEGORIES", str(path))
    return path


def test_a_short_map_row_is_refused_rather_than_guessed(
        cp, tmp_path, monkeypatch, capsys):
    """Provenance cannot be inferred from four characters.

    The predecessor called every short single-part map row a franchise
    label, which downgraded a map-listed owner identifier from fatal to
    allowed -- silently, and in the one direction a guard must never be
    wrong. Guessing the other way is no better: it promotes a retired
    franchise abbrev to fatal and blocks every push on a collision with an
    MLB club code.
    """
    repo = _repo_with(tmp_path, "notes.md", "# notes\n")
    monkeypatch.setattr(cp, "REPO", str(repo))
    monkeypatch.setattr(cp, "MAP", str(_map_at(tmp_path, "QVQZ")))
    _secrets_at(cp, tmp_path, monkeypatch)
    _sources(cp, monkeypatch)

    assert cp.main([]) == 1
    err = capsys.readouterr().err
    assert "too short for their category to be self-evident" in err


def test_a_short_map_listed_owner_identifier_stays_owner_and_fatal(
        cp, tmp_path, monkeypatch):
    """The direction that matters. Named as an owner in the sidecar, a
    four-character map row keeps the map's own fatal handling."""
    repo = _repo_with(tmp_path, "notes.md", "signed off by QVQZ today\n")
    monkeypatch.setattr(cp, "REPO", str(repo))
    monkeypatch.setattr(cp, "MAP", str(_map_at(tmp_path, "QVQZ")))
    _secrets_at(cp, tmp_path, monkeypatch)
    _map_categories_at(cp, tmp_path, monkeypatch, QVQZ=cp.OWNER)
    _sources(cp, monkeypatch)

    tokens, _ = cp.build_tokens()
    assert [t.category for t in tokens] == [cp.OWNER]
    assert cp.severity(tokens[0]) == cp.FATAL
    assert cp.main([]) == 1, "a map-listed owner identifier did not block"


def test_a_short_map_row_named_franchise_is_allowed(
        cp, tmp_path, monkeypatch):
    """And the other direction: a retired franchise abbrev the warehouse no
    longer holds is a franchise, and franchises are ruled allowed."""
    repo = _repo_with(tmp_path, "notes.md", "the QVQZ tab is stale\n")
    monkeypatch.setattr(cp, "REPO", str(repo))
    monkeypatch.setattr(cp, "MAP", str(_map_at(tmp_path, "QVQZ")))
    _secrets_at(cp, tmp_path, monkeypatch)
    _map_categories_at(cp, tmp_path, monkeypatch, QVQZ=cp.FRANCHISE)
    _sources(cp, monkeypatch)

    tokens, _ = cp.build_tokens()
    assert cp.severity(tokens[0]) == cp.ALLOWED
    assert cp.main([]) == 0


def test_corroboration_by_the_warehouse_resolves_a_short_row_too(
        cp, tmp_path, monkeypatch):
    """No sidecar entry needed when the derived inventory still holds the
    exact string -- that IS the evidence."""
    repo = _repo_with(tmp_path, "notes.md", "the QVQZ tab\n")
    monkeypatch.setattr(cp, "REPO", str(repo))
    monkeypatch.setattr(cp, "MAP", str(_map_at(tmp_path, "QVQZ")))
    _secrets_at(cp, tmp_path, monkeypatch)
    _sources(cp, monkeypatch, franchises=["QVQZ"])

    tokens, census = cp.build_tokens()
    assert census["map_unresolved"] == []
    assert all(cp.severity(t) == cp.ALLOWED for t in tokens)
    assert cp.main([]) == 0


def test_a_multipart_map_name_needs_no_resolution(cp, tmp_path, monkeypatch):
    """Only the SHORT single-part rows are ambiguous; a full name is a name
    whichever league it belongs to, and still blocks."""
    repo = _repo_with(tmp_path, "notes.md", f"see {MAPPED} for details\n")
    monkeypatch.setattr(cp, "REPO", str(repo))
    monkeypatch.setattr(cp, "MAP", str(_map_at(tmp_path, MAPPED)))
    _secrets_at(cp, tmp_path, monkeypatch)
    _sources(cp, monkeypatch)

    tokens, census = cp.build_tokens()
    assert census["map_unresolved"] == []
    assert cp.main([]) == 1
