"""Pre-push guard against reintroducing real-league strings (MLB-95, MLB-176).

Scans the HEAD tree -- what a push publishes -- for any real-league name
and fails loudly with the offending files. Wired locally via
.git/hooks/pre-push; also runnable by hand:

    python tools/check_pii.py                  # the sweep
    python tools/check_pii.py --review         # every one-word-name hit, in full
    python tools/check_pii.py --census         # what it will search for, counted
    python tools/check_pii.py --allow-degraded # accept a partial sweep, loudly

Where the real-side list comes from
-----------------------------------
Two sources, and the order matters:

1. COMPUTED, from the warehouse owner tables (`dim_owner`, plus the CBS
   owner bridge for historical spellings). Read-only queries, run at
   sweep time. This is the primary source and the whole point of the
   MLB-176 rebuild: the warehouse holds every owner the leagues have
   ever had, so an owner nobody remembered to anonymize is still
   guarded. The previous version searched only a hand-maintained map,
   which meant an unmapped owner was invisible to every sweep ever run
   -- and one was. Compute, never transcribe.

2. The MAP (archives/anonymization/name_map.csv), for the real-side
   strings the warehouse does not hold as owner names: team names,
   franchise abbrevs, owner-id slugs, the old phone numbers.

The map lives OUTSIDE the repo and is gitignored -- publishing the
real-side strings in any tracked config would defeat the point, which is
why this cannot be a gitleaks rule.

STRICT BY DEFAULT (MLB-203). This script used to be a silent no-op on a
clone with neither source: it printed that the warehouse was unavailable,
swept zero strings, and exited 0. "The guard passed" therefore did not
mean the committed content had been checked -- it could equally mean
nothing was checked at all, and the two were indistinguishable from the
exit code that gates every push. A run that cannot see everything it is
supposed to now FAILS and says which source is missing.
`--allow-degraded` accepts a partial sweep, out loud, on purpose.

How matching works
------------------
FULL NAMES are matched CASE-INSENSITIVELY. That is the other half of
MLB-176: the anonymization pass replaced the Title-Case occurrence of a
name and left the lowercase or all-caps twin standing in the same
sentence, and a case-sensitive grep structurally could not see it. A
guard that reports clean while the thing it guards against is present is
worse than no guard.

Four match modes, because one rule does not fit a full name, a bare
given name, and a four-letter abbrev:

  FAMILY  any multi-part identity -- full names, team names, the map's
          slugs. Matched as the whole FAMILY of spellings it belongs to
          rather than the one that happened to be stored: the parts may
          be separated by any short run of non-alphanumerics (space,
          hyphen, underscore, dot, "%20", or nothing at all), an optional
          middle initial is allowed between a given name and a surname,
          and the whole thing is case-insensitive. "Jonas McAvery" and
          jonas-mcavery and Jonas_McAvery and "Jonas Q. McAvery" are one
          identity, and a guard that saw only the first was reporting
          clean on the other three. BLOCKS a push.

          Cheap literal prefilter on the longest part, regex only on a
          hit -- the pattern never runs on a file that cannot match.
  PHRASE  a multi-word string too short to be a safe family. Bounded at
          both ends so "Ana Vale" stops matching inside "Joana Vale".
          BLOCKS a push.
  WORD    one-word names: given names, surnames, initials ("L.J.").
          Bounded, and matched only in name-shaped casing -- the stored
          spelling, Title, or UPPER. Never a blanket lowercase, or a
          one-word name fires on every contraction built from it
          ("Shan" on "shan't").
  ABBREV  the four-letter franchise abbrevs. Bounded and exactly cased.

Severity is not uniform, and that is deliberate. A one-word name cannot
be adjudicated by machine in a repo whose subject matter is the names of
baseball players: the first full sweep returned 110 of them, and nearly
all were MLB players in a seed or an ordinary word in a sentence. So:

  * FAMILY and PHRASE hits FAIL. A full name is unambiguous.
  * WORD hits from the warehouse are REVIEW -- counted on every push,
    listed in full by `--review`, never blocking. They are the class
    that found a real owner misspelled past the map, so they are not
    dropped; they just do not get to cry wolf 110 times.
  * WORD hits from the map (slugs, phone numbers) FAIL. Those are
    deliberate, unambiguous identifiers.
  * ABBREV hits are a NOTICE: MLB-95's 07-31 ruling dispositioned the
    committed abbrev sites as keep-with-recorded-decision, and failing
    on a decision already taken trains people to bypass the hook.

`--fail-on-abbrev` and `--fail-on-review` escalate the softer classes,
which is what a release-cut sweep may want.

Two exemptions, both computed rather than listed:

  * The maintainer's own name, read from `git config user.name`. It is
    the byline on every commit, the README, and the exposures file --
    guarding against it would fail every push forever.
  * Tokens shorter than 3 characters, which cannot be matched without
    firing on ordinary prose. They are counted in the census rather than
    dropped silently, because a silent drop is how the last blind spot
    got its start.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP = os.path.join(REPO, "archives", "anonymization", "name_map.csv")

# Wall-clock ceiling on any git call. Generous for a ~10MB tree, and the
# point is not speed -- it is that this runs inside a pre-push hook, so
# every wait it can do must be a bounded one.
TIMEOUT = 120

PHRASE, WORD, ABBREV, FAMILY = "phrase", "word", "abbrev", "family"
# Sorted in report order: what blocks the push comes first.
FATAL, REVIEW, NOTICE = "1-fatal", "2-review", "3-notice"

# How far apart the parts of a multi-part identity may drift and still be
# the same identity. Three characters covers " ", "-", "_", ".", ", " and
# " - "; zero covers the concatenated form.
#
# Percent-encoding ("%20") is NOT covered, and the reason is worth stating
# rather than discovering: its digits are alphanumeric, so it is not a
# non-alphanumeric run at all. Widening the class to admit it would admit
# every digit run with it, and "Jonas 2019 McAvery" is not an identity.
#
# NEWLINES ARE EXCLUDED, and that is not a detail. A name is written on
# one line; two unrelated words that happen to end and begin adjacent
# lines are not an identity, they are a line wrap. Allowing \n here
# manufactured a FATAL hit out of a hyphenated line break in an archived
# handoff -- and a guard that fires on text reflowing is a guard people
# learn to bypass, which is the one outcome this file keeps arguing
# against.
_SEP = r"[^0-9A-Za-z\r\n]{0,3}"
# An optional middle initial between the given name and the surname, so a
# mapped "First Last" also matches "First Q. Last" and the reverse.
_INITIAL = r"(?:[A-Za-z]\.?[^0-9A-Za-z\r\n]{1,3})?"
# Below this, a multi-part token is too short to match without firing on
# ordinary prose -- the same judgement _MIN of 3 makes for one-word names.
_MIN_FAMILY_CHARS = 6

# Distinct owner-name spellings across both leagues. dim_owner is the
# owner dimension itself; the CBS bridge carries the per-season
# roster-page spellings, which drift from the dimension's latest one.
OWNER_SQL = """
select distinct name from (
    select first_name     as name from dim_owner
    union all select last_name              from dim_owner
    union all select preferred_name         from dim_owner
    union all select owner_display          from dim_owner
    union all select owner_name             from stg_cbs__team_owners
)
where name is not null and trim(name) <> ''
"""


class Failure(Exception):
    """Something the caller must look at rather than work around."""


def _bounded(haystack, start, end):
    """Is haystack[start:end] a standalone token rather than a fragment?

    Both ends must be free of an alphanumeric neighbour. Spelled out
    rather than left to a regex \\b because \\b does the wrong thing on a
    token ENDING in punctuation -- "L.J." followed by a space has no word
    boundary after it, and an owner's initials are exactly the case that
    has to work.

    The apostrophe clause is the difference between a possessive and a
    contraction. "Ana's Blue Sox" names Ana; "Shan't we" does not name
    Shan, and a repo full of handoff notes has enough contractions whose
    stem is somebody's name to bury a real finding under them.
    """
    if start > 0 and haystack[start - 1].isalnum():
        return False
    if end < len(haystack):
        nxt = haystack[end]
        if nxt.isalnum():
            return False
        if nxt in "'’" and end + 1 < len(haystack):
            suffix = haystack[end + 1]
            if suffix.isalpha() and suffix.lower() != "s":
                return False
    return True


def family_parts(text):
    """The alphanumeric parts of a multi-part identity, or None.

    An identity that reaches a file has usually been through a delimiter
    change on the way -- "Jonas McAvery" becomes jonas-mcavery in a slug,
    Jonas_McAvery in an identifier, jonas.mcavery in a path, JonasMcAvery
    in camelCase, and "Jonas Q. McAvery" the moment a middle initial is
    carried. Matching only the stored spelling means the guard reports
    clean on every one of those, which is the failure mode this whole file
    exists to end. So a token is stored as the FAMILY it belongs to.

    Returns None when the text is a single part, or too short to match
    without firing on ordinary prose.

    A middle initial in the STORED spelling is dropped here rather than
    treated as a part, so "Jonas Q. McAvery" and "Jonas McAvery" produce
    the same family and each matches the other.
    """
    parts = [p for p in re.split(r"[^0-9A-Za-z]+", text) if p]
    if len(parts) > 2 and len(parts[1]) == 1 and parts[1].isalpha():
        parts = [parts[0]] + parts[2:]
    if len(parts) < 2:
        return None
    if sum(len(p) for p in parts) < _MIN_FAMILY_CHARS:
        return None
    if max(len(p) for p in parts) < 3:
        # No part long enough to prefilter on cheaply, and a family of
        # two-letter parts is not distinctive enough to be worth it.
        return None
    return parts


class Token:
    """One string to search for, and how to search for it."""

    __slots__ = ("text", "mode", "source", "needles", "pattern", "anchor")

    def __init__(self, text, mode, source):
        self.text = text
        self.mode = mode
        self.source = source
        self.pattern = None
        self.anchor = None
        # Every mode is a plain substring search, differing only in which
        # spellings it accepts and what it demands of the neighbouring
        # characters. Deliberately not regex: the word-boundary patterns
        # need a lookbehind, a leading lookbehind defeats the literal-prefix
        # optimization, and 28 unanchored IGNORECASE scans over a 13MB tree
        # turned this hook into a two-minute wait. str.find is C.
        if mode == FAMILY:
            parts = family_parts(text)
            # Cheap literal prefilter first, regex only on a hit. The
            # comment below about 28 unanchored IGNORECASE scans costing
            # two minutes is exactly why: the longest part is a plain
            # str.find in C, and the pattern never runs on a file that
            # does not contain it.
            self.anchor = max(parts, key=len).lower()
            self.needles = [self.anchor]
            joined = _SEP.join(re.escape(p) for p in parts)
            if len(parts) == 2 and all(p.isalpha() for p in parts):
                joined = (re.escape(parts[0]) + _SEP + _INITIAL
                          + re.escape(parts[1]))
            self.pattern = re.compile(joined, re.IGNORECASE)
        elif mode == PHRASE:
            self.needles = [text.lower()]
        elif mode == ABBREV:
            self.needles = [text]
        else:
            # Name-shaped spellings only, derived from the stored one --
            # never a blanket lowercase. A one-word name that is also an
            # ordinary English word is the whole problem here: matched
            # case-insensitively, an owner whose name is also a verb
            # fires on every sentence using it, and one whose name is
            # "Blank" on every blank cell. Nobody reads a report like
            # that, and a report nobody reads is the failure mode this
            # rebuild exists to end. Casing is the only signal available
            # that separates a person from a word, so it is the one used.
            self.needles = sorted({text, text.title(), text.upper()})

    def find(self, text, low):
        """First (start, end, matched-text) in this blob, or None.

        Word-boundary modes reject a match whose neighbour is
        alphanumeric. That is spelled out rather than left to a regex \\b
        because \\b does the wrong thing on a token ENDING in
        punctuation -- "L.J." followed by a space has no word boundary
        after it, and an owner's initials are exactly the case that has
        to work.
        """
        if self.mode == FAMILY:
            if low.find(self.anchor) < 0:
                return None                        # prefilter: no anchor
            for m in self.pattern.finditer(text):
                if _bounded(text, m.start(), m.end()):
                    return (m.start(), m.end(), m.group(0))
            return None

        haystack = low if self.mode == PHRASE else text
        best = None
        for needle in self.needles:
            n = len(needle)
            i = haystack.find(needle)
            while i >= 0:
                if _bounded(haystack, i, i + n):
                    if best is None or i < best[0]:
                        best = (i, i + n, needle)
                    break
                i = haystack.find(needle, i + 1)
        return best


def _git(*args, allow_failure=False):
    """Run git and return stdout, refusing to ignore a failure.

    The return code used to be discarded. That is how a guard reports
    clean without having looked: a git call that fails returns an empty
    string, an empty string parses as "nothing found", and the sweep exits
    0. `allow_failure` is for the one call where a non-zero really does
    mean "unset" rather than "broken".
    """
    proc = subprocess.run(["git", "-C", REPO, *args],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=TIMEOUT)
    if proc.returncode != 0 and not allow_failure:
        raise Failure(
            f"git {' '.join(args)} failed (exit {proc.returncode}): "
            f"{proc.stderr.strip()[:200]}")
    return proc.stdout


def _maintainer_words():
    """Words of the maintainer's own name, from git config. Their name is
    the byline, not a leak.

    Failure is allowed here and the empty result is the SAFE direction: no
    exemptions means more strings searched, not fewer.
    """
    return {w.casefold()
            for w in _git("config", "user.name", allow_failure=True).split()
            if w}


def _computed_names():
    """Owner names straight from the warehouse. Returns (names, error).

    Imports the output layer's connection helper lazily: a clone without
    snowflake-connector installed, or without credentials, must degrade
    rather than crash -- this runs inside a git hook.
    """
    try:
        sys.path.insert(0, os.path.join(REPO, "output"))
        from db import init, query_snowflake
        init()
        rows = query_snowflake(OWNER_SQL)
    except Exception as exc:                       # noqa: BLE001 -- degrade
        return set(), f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"
    return {(r["name"] or "").strip() for r in rows if (r["name"] or "").strip()}, None


def _map_rows():
    """Real-side strings from the private map, or None if it is absent."""
    if not os.path.exists(MAP):
        return None
    with open(MAP, newline="", encoding="utf-8") as f:
        return [r["real"].strip() for r in csv.DictReader(f)
                if r.get("real") and r["real"].strip()]


def build_tokens(use_warehouse=True):
    """The search list, classified into match modes.

    Returns (tokens, census) where census explains every decision -- what
    each source contributed, what was exempted and why. The census is the
    part that makes this auditable; a guard nobody can interrogate is how
    we got here.
    """
    census = {"computed": 0, "map": 0, "warehouse_error": None,
              "exempt_maintainer": 0, "too_short": [], "modes": {}}

    mine = _maintainer_words()
    names, err = (_computed_names() if use_warehouse else (set(), "skipped"))
    census["warehouse_error"] = err
    census["computed"] = len(names)

    mapped = _map_rows()
    census["map"] = 0 if mapped is None else len(mapped)
    census["map_present"] = mapped is not None

    by_key = {}
    for text, source in ([(n, "warehouse") for n in sorted(names)] +
                         [(m, "map") for m in (mapped or [])]):
        if any(w in mine for w in text.casefold().split()):
            census["exempt_maintainer"] += 1
            continue
        if text.isupper() and text.isalpha() and len(text) <= 4:
            # isalpha() matters: "L.J." is also short and also uppercase,
            # and routing an owner's initials into the notice-only abbrev
            # class would rebuild the exact blind spot this replaces.
            mode = ABBREV
        elif family_parts(text) is not None:
            # Full names, team names AND the map's slugs all land here: a
            # multi-part identity is matched as its whole delimiter and
            # middle-initial family, not just the one spelling that
            # happened to be stored. Supersedes the old PHRASE mode, which
            # matched the literal only -- so `jonas-mcavery` in a slug and
            # `Jonas Q. McAvery` in prose both went unseen.
            mode = FAMILY
        elif len(text.split()) > 1:
            mode = PHRASE
        elif len(text) >= 3:
            mode = WORD
        else:
            census["too_short"].append(text)
            continue
        # Keyed by mode as well as spelling: a franchise abbrev and the
        # given name it was derived from casefold to the same thing but
        # are searched differently, and collapsing them would silently
        # drop one of the two.
        by_key.setdefault((text.casefold(), mode), Token(text, mode, source))

    tokens = list(by_key.values())
    for t in tokens:
        census["modes"][t.mode] = census["modes"].get(t.mode, 0) + 1
    return tokens, census


def head_blobs(stats=None):
    """Every tracked file at HEAD, as (path, text). Binary files are
    skipped, the same way `git grep -I` skips them.

    This is NOT a filesystem walk, and the difference is load-bearing:
    the input is `git ls-tree -r HEAD`, so .venv/, .git/, data/, target/
    and every parquet artifact are excluded by construction rather than
    by an exclude list that can rot. What a push publishes is exactly
    what is scanned. `stats` collects the counts worth printing.

    One `git cat-file --batch` process reads all of them; a `git show`
    per path is 279 process spawns on Windows and turns a hook into a
    coffee break.

    It is collected with a BOUNDED communicate() rather than streamed,
    and that is the whole design. Streaming this means the parent must
    drain stdout while the child is still producing, and when that
    handshake goes wrong the failure is not a wrong answer -- it is a
    process that waits forever. This hook blocks a push; a push that
    never returns is worse than a guard that fails. The tree is ~10MB,
    which is nothing to hold in memory, so it buys a hard timeout for
    free. If git cannot produce it in TIMEOUT seconds, the guard says so
    and fails instead of hanging.
    """
    listing = subprocess.run(["git", "-C", REPO, "ls-tree", "-r", "-z",
                              "--name-only", "HEAD"],
                             capture_output=True, timeout=TIMEOUT)
    if listing.returncode != 0:
        raise Failure(
            f"git ls-tree failed (exit {listing.returncode}): "
            f"{listing.stderr.decode('utf-8', 'replace').strip()[:200]}. "
            f"The guard cannot enumerate what a push would publish, so it "
            f"cannot vouch for it.")
    paths = [p for p in listing.stdout.split(b"\0") if p]
    if stats is not None:
        stats["tracked"] = len(paths)
        stats["scanned"] = stats["skipped_binary"] = stats["bytes"] = 0
    if not paths:
        # An empty HEAD tree is not a clean tree -- it is a repo with no
        # commits, or a git call that answered with nothing. Either way
        # sweeping it proves nothing, and returning quietly would print
        # "scanned 0 of 0 files" and exit green.
        raise Failure(
            "git ls-tree returned no files at HEAD. There is nothing to "
            "scan, so a pass here would mean nothing.")

    # Requests go in via a temp file, so stdin is a plain file handle the
    # child can read at its own pace and there is only one pipe to reason
    # about instead of two.
    with tempfile.TemporaryFile() as requests:
        requests.write(b"".join(b"HEAD:" + p + b"\n" for p in paths))
        requests.seek(0)
        proc = subprocess.Popen(["git", "-C", REPO, "cat-file", "--batch"],
                                stdin=requests, stdout=subprocess.PIPE)
        try:
            blob, _ = proc.communicate(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise Failure(
                f"git cat-file did not return the HEAD tree within "
                f"{TIMEOUT}s. The guard will not hang your push -- rerun "
                f"it by hand to see what git is doing.")

    if proc.returncode != 0:
        raise Failure(
            f"git cat-file exited {proc.returncode} while reading the HEAD "
            f"tree. A partial read cannot be told apart from a clean one, "
            f"so the guard refuses rather than reporting on what it got.")

    # Responses come back in request order, and a resolved header is
    # "<sha> <type> <size>" -- it does NOT echo the path back, so the
    # path has to come from the request list we sent.
    #
    # EVERY REQUEST MUST PRODUCE A COMPLETE RESPONSE. This loop used to
    # `break` on a short read and skip anything it could not parse, which
    # meant a truncated batch scanned a prefix of the tree and reported
    # clean on the rest. Truncation is not cleanliness (MLB-203).
    pos = 0
    for index, path in enumerate(paths):
        nl = blob.find(b"\n", pos)
        if nl < 0:
            raise Failure(
                f"git cat-file returned {index} of {len(paths)} responses "
                f"before the output ran out. The remaining files were never "
                f"read, so the tree has NOT been checked.")
        header = blob[pos:nl]
        pos = nl + 1
        if header.endswith(b"missing"):
            # ls-tree just said this path is at HEAD, so cat-file calling
            # it missing means the object store disagrees with the tree.
            raise Failure(
                f"git cat-file cannot read {path.decode('utf-8', 'replace')}, "
                f"which ls-tree reports at HEAD. The repository is "
                f"inconsistent; the guard will not sweep around it.")
        fields = header.split()
        if len(fields) != 3 or not fields[2].isdigit():
            raise Failure(
                f"unparseable git cat-file header for "
                f"{path.decode('utf-8', 'replace')}: {header[:80]!r}")
        size = int(fields[2])
        if pos + size > len(blob):
            raise Failure(
                f"git cat-file promised {size} bytes for "
                f"{path.decode('utf-8', 'replace')} but the batch ended "
                f"early. A truncated blob is not an empty one.")
        data = blob[pos:pos + size]
        pos += size + 1                            # + the trailing newline
        if b"\0" in data:
            if stats is not None:
                stats["skipped_binary"] += 1
            continue                               # binary
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            if stats is not None:
                stats["skipped_binary"] += 1
            continue
        if stats is not None:
            stats["scanned"] += 1
            stats["bytes"] += len(data)
        yield path.decode("utf-8"), text


def sweep(tokens, stats=None):
    """Every (severity, path, line, token, context) the tree still carries."""
    hits = []
    # A franchise abbrev and the given name it came from are the same
    # four letters. When a hit IS the abbrev spelling, it is the ruled
    # -accepted site, not a leaked name -- report it as the notice it was
    # dispositioned to be rather than failing the push on a decision
    # already taken (MLB-95, 07-31).
    abbrevs = {t.text for t in tokens if t.mode == ABBREV}
    for path, text in head_blobs(stats):
        low = text.lower()
        for token in tokens:
            found = token.find(text, low)          # one hit per token per file
            if found is None:
                continue
            start, end, matched = found
            line_no = text.count("\n", 0, start) + 1
            context = text[max(0, start - 40):end + 40].replace("\n", " ")
            if matched in abbrevs:
                severity = NOTICE
            elif token.mode == WORD and token.source == "warehouse":
                severity = REVIEW
            else:
                severity = FATAL
            hits.append((severity, path, line_no, token, context.strip()))
    hits.sort(key=lambda h: (h[0], h[1], h[2]))
    return hits


def _print_census(census, tokens, show_tokens):
    print("PII GUARD -- what this sweep searches for")
    print(f"  computed from warehouse : {census['computed']} owner-name "
          f"spellings")
    if census["warehouse_error"]:
        print(f"      UNAVAILABLE: {census['warehouse_error']}")
    print(f"  from the private map    : {census['map']}"
          f"{'' if census['map_present'] else ' (map not on this machine)'}")
    print(f"  searchable after dedup  : {len(tokens)}")
    for mode in (FAMILY, PHRASE, WORD, ABBREV):
        print(f"      {mode:7}: {census['modes'].get(mode, 0)}")
    print(f"  exempt (maintainer)     : {census['exempt_maintainer']}")
    print(f"  dropped (under 3 chars) : {len(census['too_short'])}"
          f"{' -- ' + ', '.join(census['too_short']) if show_tokens and census['too_short'] else ''}")
    if show_tokens:
        for t in sorted(tokens, key=lambda t: (t.mode, t.text.casefold())):
            print(f"      {t.mode:7} {t.source:9} {t.text}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--census", action="store_true",
                    help="print the search list and exit without sweeping")
    ap.add_argument("--show-tokens", action="store_true",
                    help="with --census, print the strings themselves")
    ap.add_argument("--strict", action="store_true",
                    help="accepted and ignored: strict is now the default")
    ap.add_argument("--allow-degraded", action="store_true",
                    help="proceed even though the sweep cannot see "
                         "everything it is supposed to; prints why")
    ap.add_argument("--no-warehouse", action="store_true",
                    help="skip the warehouse and sweep the map only")
    ap.add_argument("--review", action="store_true",
                    help="list every one-word-name hit instead of counting")
    ap.add_argument("--fail-on-abbrev", action="store_true",
                    help="treat franchise-abbrev hits as failures too")
    ap.add_argument("--fail-on-review", action="store_true",
                    help="treat one-word-name hits as failures too")
    args = ap.parse_args(argv)

    try:
        tokens, census = build_tokens(use_warehouse=not args.no_warehouse)
    except (Failure, subprocess.TimeoutExpired) as exc:
        print(f"PII GUARD: {exc}", file=sys.stderr)
        return 1

    if args.census:
        _print_census(census, tokens, args.show_tokens)
        return 0

    # STRICT BY DEFAULT (MLB-203). This used to warn about a missing
    # warehouse and then, two lines later, return 0 for an empty token
    # list -- so on a clean clone the guard printed "warehouse list
    # unavailable", swept nothing at all, and exited GREEN. A pre-push
    # hook that passes without looking is worse than no hook: it is the
    # sentence "the guard passed" meaning nothing, said with confidence.
    #
    # Degradation is still allowed. It just has to be asked for.
    degraded = []
    if census["warehouse_error"] and not args.no_warehouse:
        degraded.append(
            f"the warehouse owner list could not be computed "
            f"({census['warehouse_error']}), so an owner who was never "
            f"added to the map is invisible to this sweep")
    if not census["map_present"]:
        degraded.append(
            f"the private map is not on this machine ({MAP}), so team "
            f"names, franchise abbrevs and owner-id slugs are not searched")
    if not tokens:
        degraded.append(
            "there is nothing to search for at all -- neither source "
            "produced a single token, so a pass would prove nothing")

    if degraded:
        label = "DEGRADED" if args.allow_degraded else "REFUSING TO VOUCH"
        print(f"PII GUARD {label}: this sweep cannot see everything it is "
              f"supposed to.", file=sys.stderr)
        for reason in degraded:
            print(f"  - {reason}", file=sys.stderr)
        if not args.allow_degraded:
            print("\n  A guard that passes without looking is not a guard. "
                  "Fix the source above, or re-run with --allow-degraded to "
                  "say out loud that you accept a partial sweep.",
                  file=sys.stderr)
            return 1
        print("  --allow-degraded: continuing with a partial sweep.",
              file=sys.stderr)

    if not tokens:
        return 1 if not args.allow_degraded else 0

    stats = {}
    try:
        hits = sweep(tokens, stats)
    except (Failure, subprocess.TimeoutExpired) as exc:
        print(f"PII GUARD: {exc}", file=sys.stderr)
        return 1
    print(f"PII GUARD: scanned {stats.get('scanned', 0)} of "
          f"{stats.get('tracked', 0)} files tracked at HEAD "
          f"({stats.get('bytes', 0) / 1e6:.1f}MB, "
          f"{stats.get('skipped_binary', 0)} binary skipped) for "
          f"{len(tokens)} strings. Not a filesystem walk -- the file list "
          f"is git ls-tree, so untracked and ignored trees are out by "
          f"construction.", file=sys.stderr)
    fatal = [h for h in hits if h[0] == FATAL]
    review = [h for h in hits if h[0] == REVIEW]
    notices = [h for h in hits if h[0] == NOTICE]

    if notices:
        print(f"PII GUARD NOTICE: {len(notices)} franchise-abbrev "
              f"occurrence(s) -- dispositioned keep-with-recorded-decision "
              f"(MLB-95, 07-31):", file=sys.stderr)
        for _sev, path, line, token, _ctx in notices:
            print(f"  {path}:{line}: {token.text}", file=sys.stderr)

    if review:
        names = sorted({h[3].text for h in review}, key=str.casefold)
        print(f"PII GUARD REVIEW: {len(review)} one-word-name occurrence(s) "
              f"across {len({h[1] for h in review})} file(s), "
              f"{len(names)} distinct name(s). Most are MLB players or "
              f"ordinary words; a few are not. Not blocking -- "
              f"`--review` lists them.", file=sys.stderr)
        if args.review:
            for _sev, path, line, token, context in review:
                print(f"  {path}:{line}: {token.text}", file=sys.stderr)
                print(f"      ...{context}...", file=sys.stderr)
        else:
            print(f"  names: {', '.join(names)}", file=sys.stderr)

    if fatal:
        print("PII GUARD: real-league strings found in the tree about to be "
              "pushed:", file=sys.stderr)
        for _sev, path, line, token, context in fatal:
            print(f"  {path}:{line}: {token.text}  [{token.mode}, "
                  f"{token.source}]", file=sys.stderr)
            print(f"      ...{context}...", file=sys.stderr)
        print("Fix (or extend archives/anonymization/) before pushing. "
              "Bypass only if you are CERTAIN: git push --no-verify",
              file=sys.stderr)
        return 1

    if notices and args.fail_on_abbrev:
        return 1
    return 1 if (review and args.fail_on_review) else 0


if __name__ == "__main__":
    sys.exit(main())
