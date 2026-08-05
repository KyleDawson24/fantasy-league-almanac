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
    known = {cp.FAMILY, cp.PHRASE, cp.WORD, cp.ABBREV}
    assert all(t.mode in known for t in tokens)
    # Nothing is silently dropped: everything is either searchable or
    # counted in one of the census's explicit buckets.
    accounted = (len(tokens) + census["exempt_maintainer"]
                 + len(census["too_short"]))
    assert accounted == census["computed"] + census["map"]
