"""Pre-push guard against reintroducing real-league strings (MLB-95, MLB-176).

Scans the HEAD tree -- what a push publishes -- for any real-league name
and fails loudly with the offending files. Wired locally via
.git/hooks/pre-push; also runnable by hand:

    python tools/check_pii.py              # the sweep
    python tools/check_pii.py --review     # every one-word-name hit, in full
    python tools/check_pii.py --census     # what it will search for, counted
    python tools/check_pii.py --strict     # refuse to run degraded

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
why this cannot be a gitleaks rule. On a clone with neither source (every
clone but the maintainer's) this script is a silent no-op.

If the warehouse is unreachable the sweep still runs against the map and
says out loud that it is degraded. `--strict` turns that into a failure,
which is what a release-cut sweep wants.

How matching works
------------------
FULL NAMES are matched CASE-INSENSITIVELY. That is the other half of
MLB-176: the anonymization pass replaced the Title-Case occurrence of a
name and left the lowercase or all-caps twin standing in the same
sentence, and a case-sensitive grep structurally could not see it. A
guard that reports clean while the thing it guards against is present is
worse than no guard.

Three match modes, because one rule does not fit a full name, a bare
given name, and a four-letter abbrev:

  PHRASE  anything with a space in it -- full names, team names -- plus
          the map's slugs and phone numbers. Case-insensitive, and
          bounded at both ends so "Ana Vale" stops matching inside
          "Joana Vale". This is the class that BLOCKS a push.
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

  * PHRASE hits FAIL. A full name is unambiguous.
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
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP = os.path.join(REPO, "archives", "anonymization", "name_map.csv")

# Wall-clock ceiling on any git call. Generous for a ~10MB tree, and the
# point is not speed -- it is that this runs inside a pre-push hook, so
# every wait it can do must be a bounded one.
TIMEOUT = 120

PHRASE, WORD, ABBREV = "phrase", "word", "abbrev"
# Sorted in report order: what blocks the push comes first.
FATAL, REVIEW, NOTICE = "1-fatal", "2-review", "3-notice"

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


class Token:
    """One string to search for, and how to search for it."""

    __slots__ = ("text", "mode", "source", "needles")

    def __init__(self, text, mode, source):
        self.text = text
        self.mode = mode
        self.source = source
        # Every mode is a plain substring search, differing only in which
        # spellings it accepts and what it demands of the neighbouring
        # characters. Deliberately not regex: the word-boundary patterns
        # need a lookbehind, a leading lookbehind defeats the literal-prefix
        # optimization, and 28 unanchored IGNORECASE scans over a 13MB tree
        # turned this hook into a two-minute wait. str.find is C.
        if mode == PHRASE:
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


def _git(*args):
    return subprocess.run(["git", "-C", REPO, *args],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def _maintainer_words():
    """Words of the maintainer's own name, from git config. Their name is
    the byline, not a leak."""
    return {w.casefold() for w in _git("config", "user.name").split() if w}


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
    raw = subprocess.run(["git", "-C", REPO, "ls-tree", "-r", "-z",
                          "--name-only", "HEAD"],
                         capture_output=True, timeout=TIMEOUT).stdout
    paths = [p for p in raw.split(b"\0") if p]
    if stats is not None:
        stats["tracked"] = len(paths)
        stats["scanned"] = stats["skipped_binary"] = stats["bytes"] = 0
    if not paths:
        return

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

    # Responses come back in request order, and a resolved header is
    # "<sha> <type> <size>" -- it does NOT echo the path back, so the
    # path has to come from the request list we sent.
    pos = 0
    for path in paths:
        nl = blob.find(b"\n", pos)
        if nl < 0:
            break
        header = blob[pos:nl]
        pos = nl + 1
        if header.endswith(b"missing"):
            continue                               # header only, no body
        size = int(header.split()[-1])
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
    for mode in (PHRASE, WORD, ABBREV):
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
                    help="fail if the warehouse list cannot be computed")
    ap.add_argument("--no-warehouse", action="store_true",
                    help="skip the warehouse and sweep the map only")
    ap.add_argument("--review", action="store_true",
                    help="list every one-word-name hit instead of counting")
    ap.add_argument("--fail-on-abbrev", action="store_true",
                    help="treat franchise-abbrev hits as failures too")
    ap.add_argument("--fail-on-review", action="store_true",
                    help="treat one-word-name hits as failures too")
    args = ap.parse_args(argv)

    tokens, census = build_tokens(use_warehouse=not args.no_warehouse)

    if args.census:
        _print_census(census, tokens, args.show_tokens)
        return 0

    if census["warehouse_error"] and not args.no_warehouse:
        print(f"PII GUARD: warehouse list unavailable "
              f"({census['warehouse_error']}) -- sweeping the map only, "
              f"which cannot see an unmapped owner.", file=sys.stderr)
        if args.strict:
            return 1
    if not tokens:
        return 0                                   # no sources: silent no-op

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
