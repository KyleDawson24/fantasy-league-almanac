"""Pre-push guard against reintroducing real-league strings (MLB-95, MLB-176,
MLB-203, MLB-234).

Scans the HEAD tree -- what a push publishes -- for any real-league name
and fails loudly with the offending files. Wired locally via
.git/hooks/pre-push; also runnable by hand:

    python tools/check_pii.py                  # the sweep, and the gate
    python tools/check_pii.py --review         # recorded decisions, in full
    python tools/check_pii.py --census         # what it will search for, counted
    python tools/check_pii.py --record-dispositions
    python tools/check_pii.py --allow-unreviewed  # census only; does not gate
    python tools/check_pii.py --allow-degraded # accept a partial sweep, loudly

The default IS the gate: it exits non-zero on a fatal hit and on any
occurrence nobody has reviewed. tools/hooks/pre-push runs exactly that,
with no flags, and is tracked so the rule cannot live on one machine.

Where the real-side list comes from
-----------------------------------
Three sources, and the order matters:

1. COMPUTED, from the warehouse. Read-only queries, run at sweep time.
   This is the primary source and the whole point of the MLB-176 rebuild:
   the warehouse holds every identity the leagues have ever had, so one
   nobody remembered to anonymize is still guarded. The previous version
   searched only a hand-maintained map, which meant an unmapped identity
   was invisible to every sweep ever run -- and one was. Compute, never
   transcribe.

   MLB-234 widened this from owner names alone to every identity family
   the warehouse can produce, because the same failure had simply moved
   up a level: the map covered ESPN franchise ABBREVS but almost none of
   the ESPN franchise NAMES, so no sweep had ever looked for a team name.
   Owners, franchise names, franchise labels, division names and team ids
   now all come from their own authoritative table. Adding a league adds
   its identities automatically; nothing here is a list to maintain.

2. THE REGISTRY, for capability identifiers. config/leagues.yml names the
   .env variable holding each platform league id (`league_id_env`), so the
   ids themselves stay out of the repo. Reading them back through the
   registry is what lets the guard search for the one string that would
   hand a stranger the league.

3. The MAP (archives/anonymization/name_map.csv), for the real-side
   strings the warehouse no longer holds: retired identities, historical
   spellings, owner-id slugs, the old phone numbers.

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

Five match modes, because one rule does not fit a full name, a bare given
name, an arbitrary team label, and a numeric id:

  FAMILY  any multi-part identity -- full names, team names, the map's
          slugs. Matched as the whole FAMILY of spellings it belongs to
          rather than the one that happened to be stored: the parts may
          be separated by any short run of non-alphanumerics (space,
          hyphen, underscore, dot, "%20", or nothing at all), an optional
          middle initial is allowed between a given name and a surname,
          and the whole thing is case-insensitive. "Jonas McAvery" and
          jonas-mcavery and Jonas_McAvery and "Jonas Q. McAvery" are one
          identity, and a guard that saw only the first was reporting
          clean on the other three.

          Cheap literal prefilter on the longest part, regex only on a
          hit -- the pattern never runs on a file that cannot match.
  PHRASE  a multi-word string too short to be a safe family. Bounded at
          both ends so "Ana Vale" stops matching inside "Joana Vale".
  WORD    one-word names: given names, surnames, initials ("Q.Z.").
          Bounded, and matched only in name-shaped casing -- the stored
          spelling, Title, or UPPER. Never a blanket lowercase, or a
          one-word name fires on every contraction built from it
          ("Shan" on "shan't").
  LABEL   a franchise's short label. Bounded and exactly cased, and
          DELIBERATELY SHAPE-AGNOSTIC -- see below.
  IDENT   a platform identifier: a league id, a team id. Entropy-gated,
          because a bare digit grep is not a threat model -- see below.

A LABEL IS WHATEVER THE LEAGUE SAYS IT IS (MLB-234). The predecessor
admitted a label only if it was four uppercase letters, which is the
pioneer ESPN league's house style and nothing more. Every label that
broke that mould fell somewhere worse: a two-character emoji label was
dropped as "too short" and never searched at all, and an all-digits team
name landed in the one-word class, where a map-sourced hit is FATAL --
so a label was punished for the crime of not looking like the first
league's labels. Team names are user data and can be anything: digits,
emoji, punctuation, a single glyph. What makes a string a label here is
the COLUMN IT CAME FROM, not the characters in it.

IDENTIFIERS ARE ENTROPY-GATED, NOT GREPPED (MLB-234). "Search for the
team ids" sounds obvious and is unusable: measured against this tree,
the 34 one- and two-digit team ids occur 32,800 times across 290 of 328
files, because they are also every week number, every seed value and
every array index in the repository. The population of standalone digit
runs at HEAD, by length, is what decides the boundary:

    length   distinct values present at HEAD   share of the space
      1                  10                     100%
      2                 100                     100%
      3                 629                      63%
      4                 603                       6%
      6                 506                    0.05%
      8                  58                  0.00006%
     10                  58                 0.0000006%

Below eight characters an identifier is not distinctive enough to match
on its own: a randomly chosen 4-digit id is already in the tree 6% of the
time, and a 2-digit one always is. At eight and above, coincidence is
below one in a million, so a bare occurrence IS the identifier.

So: an identifier of >= 8 characters is matched bare and BLOCKS -- that
is the class a real league id falls in, and the one that hands a stranger
the league. A shorter identifier is matched ONLY inside an
identity-shaped context: `team_id = 7`, `"leagueId": 7`, `/teams/7`,
`LEAGUE_ID=7`. That still catches a committed .env, a config file or a
captured API payload, which is what a low-entropy id exposure actually
looks like, while a week number written in prose is left alone. The key
vocabulary is structural (`league_id`, `team_id`, ...) plus the env
variable names the registry itself declares, so a new league's credential
variable is covered without editing anything here.

Severity is not uniform, and that is deliberate. A one-word name cannot
be adjudicated by machine in a repo whose subject matter is the names of
baseball players: the first full sweep returned 110 of them, and nearly
all were MLB players in a seed or an ordinary word in a sentence. So:

  * FAMILY and PHRASE hits FAIL. A full name is unambiguous.
  * DIVISION names and LEAGUE IDS fail on the same footing.
  * WORD hits from the warehouse are REVIEW -- counted on every push,
    listed in full by `--review`, never blocking. They are the class
    that found a real owner misspelled past the map, so they are not
    dropped; they just do not get to cry wolf 110 times.
  * WORD hits from the map (slugs, phone numbers) FAIL. Those are
    deliberate, unambiguous identifiers.
  * FRANCHISE names and labels are ALLOWED, by the maintainer's ruling
    of 2026-08-10: a fantasy team name is deliberate public portfolio
    content, not a privacy leak. The ruling is applied at the CATEGORY,
    which is the only place it can be applied honestly -- they are
    derived, searched and counted in the census, but an ordinary new one
    is not privacy debt and does not earn a per-site row. Filing 197
    identical retentions was a chore impersonating a control: nobody
    re-reads two hundred rows that all say the same sentence, and the
    handful of real judgements drown among them. The ruling covers
    franchise names and labels ONLY; owner identities, league ids,
    division names and team ids keep their stronger handling.
  * A MAP-SOURCED franchise NAME still FAILS. Somebody put that exact
    string on the replace-list; a later general ruling about team names
    does not repeal a specific decision to remove one, and a dedup that
    silently kept the softer of two classifications would.

Two exemptions, both computed rather than listed:

  * The maintainer's own name, read from `git config user.name`. It is
    the byline on every commit, the README, and the exposures file --
    guarding against it would fail every push forever.
  * Tokens shorter than 3 characters, which cannot be matched without
    firing on ordinary prose. They are counted in the census rather than
    dropped silently, because a silent drop is how the last blind spot
    got its start. A LABEL is exempt from this floor when it is not
    alphanumeric: a two-glyph emoji cannot fire on prose.

Sentinels are excluded, and computed rather than listed: the holding-pen
franchise id and its label come from the dbt vars that define them, so
the placeholder team is never mistaken for somebody's identity.

Dispositions: how a hit stops being news
----------------------------------------
A NOTICE that says "already dispositioned" about every hit of its class
is not a disposition, it is a blanket amnesty (MLB-234). The predecessor
printed all 90 franchise-abbrev occurrences under one banner citing a
ruling taken on 90 sites that were never enumerated, so a brand-new
occurrence arriving tomorrow would be greeted by the same reassuring
sentence. A green exit cannot mean both "reviewed" and "never reviewed".

So every reviewable hit is now matched against a LEDGER, one row per
OCCURRENCE:

    tools/pii_dispositions.csv    path,category,digest,disposition,reason,recorded

Per occurrence, not per token-in-a-file, and the difference is a hole
rather than a nicety. The matcher used to stop at the first match of a
token in a file, so a decision recorded about that match answered for
every later one: a file carrying a one-word owner name as an MLB
player's name is recorded as a collision, somebody adds a genuine
reference to that owner further down the same file, and the new one
inherits a judgement passed on a different sentence. The digest
therefore covers the identity, the category AND the normalized text
around the match -- with an ordinal only where surroundings are truly
identical. Reflowing a paragraph keeps a decision; rewriting the
sentence invalidates it and asks again, which is the safe direction.

The ledger is COMMITTED -- it is the durable record, and a count in a
handoff document is not one. It carries no real-side value: the identity
is represented by `digest`, an HMAC of the token under a secret that
lives beside the map at archives/anonymization/pii_salt.txt and is
gitignored with it. A plain hash would not do. An attacker who knows the
category and suspects a four-letter label can hash all 456,976 of them
and read the ledger straight off, so publishing an unsalted digest of a
short token publishes the token. Under an HMAC with a 32-byte secret the
row is inert.

That secret is REQUIRED, not optional: a sweep that cannot open it
cannot tell a reviewed hit from an unreviewed one, so it refuses to
vouch rather than reporting every disposition as missing. Losing it
costs a re-review, not a leak.

Two dispositions, and the difference is the point:

  retain     the occurrence is a real identity, and it is meant to be
             there. Portfolio sample output, a documented decision.
  collision  the match is not the identity at all -- an MLB club code
             that happens to spell a franchise label, a stat abbreviation,
             a date-format token, a synthetic fixture value.

Anything with no row is UNREVIEWED, says so by name, and BLOCKS. That is
the default and the hook runs the default. It was previously a warning
that exited 0 while a separate `--fail-on-unreviewed` did the blocking --
which meant the gate was whichever command somebody happened to type, and
the one the hook typed was the lenient one. `--allow-unreviewed` still
exists for taking a census mid-work, and is exactly what the hook does not
run. `--record-dispositions` writes the rows for the occurrences present
now.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import os
import re
import secrets
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP = os.path.join(REPO, "archives", "anonymization", "name_map.csv")
# The disposition ledger is COMMITTED; the secret that makes its digests
# meaningless to a reader is not, and lives beside the map.
LEDGER = os.path.join(REPO, "tools", "pii_dispositions.csv")
SALT = os.path.join(REPO, "archives", "anonymization", "pii_salt.txt")
# The map's missing category column, kept beside it and gitignored with it.
# Provenance cannot be inferred from four characters -- see _map_categories.
MAP_CATEGORIES = os.path.join(REPO, "archives", "anonymization",
                              "name_map_categories.csv")
REGISTRY = os.path.join(REPO, "config", "leagues.yml")
DBT_PROJECT = os.path.join(REPO, "dbt_league", "dbt_project.yml")

# Wall-clock ceiling on any git call. Generous for a ~10MB tree, and the
# point is not speed -- it is that this runs inside a pre-push hook, so
# every wait it can do must be a bounded one.
TIMEOUT = 120

PHRASE, WORD, LABEL, FAMILY, IDENT = "phrase", "word", "label", "family", "ident"
MODES = (FAMILY, PHRASE, WORD, LABEL, IDENT)

# What KIND of identity a token is. The category is the token's provenance
# -- which authoritative column produced it -- and it, not the token's
# shape, decides how severely a hit is treated.
OWNER, FRANCHISE, DIVISION, LEAGUE_ID, TEAM_ID = (
    "owner", "franchise", "division", "league-id", "team-id")

# Sorted in report order: what blocks the push comes first.
#
# ALLOWED is a whole category ruled on ONCE, and it is the difference
# between a policy and a chore. The maintainer's ruling of 2026-08-10 is
# that a fantasy team name is deliberate public portfolio content -- so
# franchise identities are derived, counted and visible in the census, but
# an ordinary new one is not privacy debt and does not need a per-site row
# to say so. Making the whole category answer for itself with 197 recorded
# retentions was a filing system pretending to be a decision: nobody
# re-reads two hundred rows that all say the same sentence, and burying the
# handful of real judgements among them costs more than it protects.
FATAL, REVIEW, ALLOWED = "1-fatal", "2-review", "3-allowed"

RETAIN, COLLISION = "retain", "collision"
DISPOSITIONS = (RETAIN, COLLISION)

# How much text on either side of a match identifies WHICH occurrence it is.
# Wide enough that two different sentences using the same token do not
# collapse to the same fingerprint, narrow enough that an edit elsewhere in
# the paragraph does not invalidate the decision.
_CONTEXT_WINDOW = 48

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

# The entropy boundary for identifiers, justified by the measured digit-run
# population in the module docstring. At and above this length a bare
# occurrence is the identifier; below it, only an identity-shaped context
# counts. Stated as a constant so the tests can name it rather than
# hard-code 8 in six places.
IDENT_BARE_MIN = 8

# The structural vocabulary that makes a number an identifier rather than a
# quantity. Schema and API words, not identities -- safe to write down.
# The registry's own credential variable names are appended at build time,
# so a new league's `.env` key is covered without editing this.
_IDENT_KEYWORDS = (
    r"(?:league|team|franchise|owner)[ _-]?id",
    r"leagueId", r"teamId", r"franchiseId", r"ownerId",
)
_IDENT_PATH_SEGMENTS = r"(?:leagues?|teams?|franchises?|owners?)"
# What can follow a key and still be one identifier: the value, and nothing
# adjacent that would make it a different one.
_IDENT_VALUE = r"[0-9A-Za-z][0-9A-Za-z_-]{0,63}"

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

# Every franchise identity the warehouse can produce, in one shape:
# (category, value). int_franchise_registry is the platform-general
# OBSERVED seam -- what each league's own history makes available -- and
# dim_franchise / dim_franchise_season carry the curated continuity
# overrides on top of it. A franchise that was renamed mid-season appears
# under both its spellings because the season grain holds the older one.
FRANCHISE_SQL = """
select distinct 'franchise' as category, value from (
    select observed_name      as value from int_franchise_registry
    union all select observed_abbrev    from int_franchise_registry
    union all select canonical_name     from dim_franchise
    union all select canonical_abbrev   from dim_franchise
    union all select canonical_name     from dim_franchise_season
    union all select canonical_abbrev   from dim_franchise_season
)
where value is not null and trim(value) <> ''
"""

# Division names, flattened from the schedule-settings payload. Trimmed at
# staging already (one real division name carries a trailing space), so the
# spelling here is the one a consumer would ever write down.
DIVISION_SQL = """
select distinct division_name as value
from stg_divisions
where division_name is not null and trim(division_name) <> ''
"""

# Team ids as the platform mints them. Low-entropy by nature -- see the
# entropy discussion above -- so these are searched contextually.
TEAM_ID_SQL = """
select distinct cast(franchise_id as varchar) as value
from int_franchise_registry
where franchise_id is not null
"""


class Failure(Exception):
    """Something the caller must look at rather than work around."""


def _bounded(haystack, start, end):
    """Is haystack[start:end] a standalone token rather than a fragment?

    Both ends must be free of an alphanumeric neighbour. Spelled out
    rather than left to a regex \\b because \\b does the wrong thing on a
    token ENDING in punctuation -- "Q.Z." followed by a space has no word
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


def ident_context_pattern(extra_keys=()):
    """The one regex that says "this number is being used as an id".

    Compiled once per sweep and run once per file, yielding the VALUES
    that appear in an identity-shaped position. The alternative -- running
    a per-token pattern over every file -- is the same work multiplied by
    the number of ids, which at 35 ids and 328 files is a hook nobody
    waits for.

    `extra_keys` carries the registry's declared env variable names, so
    `LEAGUE_ID=...` in a committed .env is caught by the same pass.
    """
    keys = list(_IDENT_KEYWORDS) + [re.escape(k) for k in sorted(extra_keys)]
    return re.compile(
        # The closing quote after the KEY is what makes a JSON payload work:
        # `"teamId": 6` puts a quote between the key and its colon, and a
        # pattern that went straight from \b to [=:] saw none of them.
        r"(?:\b(?:" + "|".join(keys) + r")\b[\"']?\s*[=:]\s*[\"']?"
        r"(?P<kv>" + _IDENT_VALUE + r")"
        r"|[/](?:" + _IDENT_PATH_SEGMENTS + r")[/]"
        r"(?P<seg>" + _IDENT_VALUE + r"))",
        re.IGNORECASE)


def ident_contexts(text, pattern):
    """{value: [(start, end), ...]} for every identity-shaped value here.

    EVERY position, not the first: a config file that sets the same id in
    two places is two sites, and a decision recorded about one of them is
    not a decision about the other.
    """
    found = {}
    for m in pattern.finditer(text):
        value = m.group("kv") or m.group("seg")
        if value is None:
            continue
        span = m.span("kv") if m.group("kv") is not None else m.span("seg")
        found.setdefault(value, []).append(span)
        if value.casefold() != value:
            found.setdefault(value.casefold(), []).append(span)
    return found


class Token:
    """One string to search for, and how to search for it."""

    __slots__ = ("text", "mode", "source", "category", "needles", "pattern",
                 "anchor", "bare")

    def __init__(self, text, mode, source, category=OWNER):
        self.text = text
        self.mode = mode
        self.source = source
        self.category = category
        self.pattern = None
        self.anchor = None
        self.needles = []
        self.bare = False
        # Every mode is a plain substring search, differing only in which
        # spellings it accepts and what it demands of the neighbouring
        # characters. Deliberately not regex: the word-boundary patterns
        # need a lookbehind, a leading lookbehind defeats the literal-prefix
        # optimization, and 28 unanchored IGNORECASE scans over a 13MB tree
        # turned this hook into a two-minute wait. str.find is C.
        if mode == FAMILY:
            parts = family_parts(text)
            # Cheap literal prefilter first, regex only on a hit. The
            # comment above about 28 unanchored IGNORECASE scans costing
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
        elif mode == LABEL:
            # Exactly as stored, exactly as cased. A label is a display
            # string with a definite spelling, and case is most of what
            # keeps a three-letter one off every sentence that shares its
            # letters. No shape is required of it -- see the module
            # docstring: digits, emoji and punctuation are all real labels.
            self.needles = [text]
        elif mode == IDENT:
            # Entropy gate. Long enough to be distinctive on its own, or
            # it only counts inside an identity-shaped context.
            self.bare = len(text) >= IDENT_BARE_MIN
            self.needles = [text.casefold()]
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

    def find_all(self, text, low, contexts=None):
        """EVERY (start, end, matched-text) in this blob, in order.

        Enumerated rather than sampled, and that is a correctness property
        rather than a thoroughness one. This used to return the first match
        per token per file, which made a disposition a decision about a
        TOKEN-IN-A-FILE and not about a site. The failure had a shape: a
        file carries a one-word owner name as an MLB player's name and is
        recorded, correctly, as a collision. Somebody later adds a genuine
        reference to that owner further down the same file. The first match
        still wins, the same ledger row still answers, and the new leak
        inherits a decision taken about a different sentence.

        Word-boundary modes reject a match whose neighbour is
        alphanumeric. That is spelled out rather than left to a regex \\b
        because \\b does the wrong thing on a token ENDING in
        punctuation -- "Q.Z." followed by a space has no word boundary
        after it, and an owner's initials are exactly the case that has
        to work.
        """
        if self.mode == FAMILY:
            if low.find(self.anchor) < 0:
                return []                          # prefilter: no anchor
            return [(m.start(), m.end(), m.group(0))
                    for m in self.pattern.finditer(text)
                    if _bounded(text, m.start(), m.end())]

        if self.mode == IDENT:
            if not self.bare:
                # Low-entropy: an identity-shaped context is the only
                # evidence that this run of digits is an id at all.
                spans = (contexts or {}).get(self.text)
                if spans is None:
                    spans = (contexts or {}).get(self.text.casefold())
                return [(s, e, self.text) for s, e in (spans or ())]
            needle = self.needles[0]
            found, i = [], low.find(self.needles[0])
            while i >= 0:
                if _bounded(low, i, i + len(needle)):
                    found.append((i, i + len(needle), text[i:i + len(needle)]))
                i = low.find(needle, i + 1)
            return found

        haystack = low if self.mode == PHRASE else text
        found = []
        for needle in self.needles:
            n = len(needle)
            i = haystack.find(needle)
            while i >= 0:
                if _bounded(haystack, i, i + n):
                    found.append((i, i + n, needle))
                i = haystack.find(needle, i + 1)
        found.sort()
        return found

    def find(self, text, low, contexts=None):
        """The first occurrence, or None. Kept for callers that only ask
        whether this token appears here at all."""
        found = self.find_all(text, low, contexts)
        return found[0] if found else None


def severity(token):
    """How loud a hit on this token is, and why.

    Provenance first, shape second. The predecessor decided severity from
    the characters in a string, which is how an all-digits team name
    became a FATAL one-word hit and a four-letter one became a blanket
    notice.
    """
    if token.category == DIVISION:
        return FATAL
    if token.category == LEAGUE_ID:
        return FATAL
    if token.category == TEAM_ID:
        # Only ever matched in an identity-shaped context, but a low-entropy
        # id can still land in one by coincidence (`team_id = 7` in a
        # doctest). Reviewable, with a recorded decision -- not a block.
        return FATAL if token.bare else REVIEW
    if token.category == FRANCHISE:
        # The maintainer's ruling, 2026-08-10: team names and labels are
        # deliberate public content, so the whole category is ALLOWED --
        # derived and counted, but not privacy debt and not a per-site
        # chore. The one exception is specific rather than general: a
        # MAP-sourced NAME is a prior decision to remove that exact string,
        # and a later ruling about team names in general does not repeal a
        # particular decision to delete one.
        if token.source == "map" and token.mode in (FAMILY, PHRASE):
            return FATAL
        return ALLOWED
    if token.mode in (FAMILY, PHRASE):
        return FATAL
    if token.mode == WORD:
        return REVIEW if token.source == "warehouse" else FATAL
    return REVIEW


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


def _warehouse():
    """The output layer's query function, imported lazily.

    A clone without snowflake-connector installed, or without credentials,
    must degrade rather than crash -- this runs inside a git hook.
    """
    sys.path.insert(0, os.path.join(REPO, "output"))
    from db import init, query_snowflake
    init()
    return query_snowflake


def _computed_names():
    """Owner names straight from the warehouse. Returns (names, error)."""
    try:
        rows = _warehouse()(OWNER_SQL)
    except Exception as exc:                       # noqa: BLE001 -- degrade
        return set(), f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"
    return {(r["name"] or "").strip() for r in rows if (r["name"] or "").strip()}, None


def _computed_league_inventory():
    """Franchise names/labels, division names and team ids, from their own
    authoritative tables. Returns ({category: {values}}, error).

    One connection, three queries, no transcription: this is what makes a
    league that was never anonymized still guarded, and what makes adding
    a league to the registry enough to cover its identities.
    """
    found = {FRANCHISE: set(), DIVISION: set(), TEAM_ID: set()}
    try:
        query = _warehouse()
        for category, sql in ((FRANCHISE, FRANCHISE_SQL),
                              (DIVISION, DIVISION_SQL),
                              (TEAM_ID, TEAM_ID_SQL)):
            for row in query(sql):
                value = str(row["value"] or "").strip()
                if value:
                    found[category].add(value)
    except Exception as exc:                       # noqa: BLE001 -- degrade
        return found, f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"
    return found, None


def _registry_identifiers():
    """League ids from the registry, and the env var names that hold them.

    Returns (ids, key_names, error). The ids are the capability strings --
    the one value that hands a stranger the league -- and they live in
    .env precisely so they are not in the repo, so reading them back
    through the registry is the only way to search for them.

    The key NAMES are not secret and are useful on their own: they extend
    the identity-context vocabulary, so `CBS_LEAGUE=...` in a committed
    .env is an identity-shaped context even though nothing here knows what
    a CBS league id looks like.
    """
    ids, keys = set(), set()
    try:
        sys.path.insert(0, REPO)
        from config.league_registry import load_registry
        registry = load_registry(REGISTRY)
    except Exception as exc:                       # noqa: BLE001 -- degrade
        return ids, keys, f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"

    unresolved = []
    for key, league in sorted(registry["leagues"].items()):
        if league.league_id_env:
            keys.add(league.league_id_env)
        keys.update(league.credential_env)
        try:
            value = str(league.resolve_league_id()).strip()
        except Exception:                          # noqa: BLE001 -- degrade
            unresolved.append(key)
            continue
        if value:
            ids.add(value)
    error = None
    if unresolved:
        error = (f"league id unresolved for {', '.join(unresolved)} -- the "
                 f".env variable naming it is unset, so the one string that "
                 f"would hand a stranger the league is not being searched for")
    return ids, keys, error


def _sentinels():
    """Placeholder values that are not anybody's identity.

    Read from the dbt vars that define them rather than written down here:
    the holding pen's id and label are configuration, and a guard that
    hard-coded them would start flagging the pen the day somebody changed
    the placeholder.
    """
    found = set()
    try:
        with open(DBT_PROJECT, "r", encoding="utf-8") as f:
            body = f.read()
    except OSError:
        return found
    for name in ("holding_pen_franchise_id", "holding_pen_label"):
        m = re.search(rf"^\s*{name}\s*:\s*(.+?)\s*$", body, re.MULTILINE)
        if m:
            found.add(m.group(1).strip().strip("'\""))
    return found


def _map_rows():
    """Real-side strings from the private map, or None if it is absent."""
    if not os.path.exists(MAP):
        return None
    with open(MAP, newline="", encoding="utf-8") as f:
        return [r["real"].strip() for r in csv.DictReader(f)
                if r.get("real") and r["real"].strip()]


def _is_short_single_part(text):
    """Short enough and simple enough that its category is not self-evident.

    NOT a classifier -- a question. Four characters carry no provenance:
    the same shape covers a franchise abbrev, an owner's initials and a
    stat code, and guessing between them is wrong in both directions. A
    guess toward franchise downgrades a map-listed owner identifier from
    fatal to allowed; a guess toward owner promotes a retired franchise
    abbrev to fatal and blocks the push on a collision with an MLB club
    code. Rows this shape must be RESOLVED, not inferred.
    """
    return len(text) <= 4 and not re.search(r"\s", text) and text == text.strip()


def _map_categories():
    """Explicit categories for map rows, from the private sidecar.

    The map is two columns -- real, fake -- and has never carried what KIND
    of identity each row is. For most rows that costs nothing: a multi-part
    name is a name whichever league it belongs to. It costs a great deal for
    the short single-part rows, which is why they are resolved here instead
    of guessed (see _is_short_single_part).

    The sidecar lives beside the map and is gitignored with it, because a
    file pairing a real-side string with its category is exactly as
    publishable as the map itself. Rows the derived warehouse inventory
    already corroborates need no entry; the sidecar is for what the
    warehouse can no longer confirm, which is the map's whole job --
    retired identities it is the last record of.
    """
    if not os.path.exists(MAP_CATEGORIES):
        return {}
    with open(MAP_CATEGORIES, newline="", encoding="utf-8") as f:
        # Comment-aware, because this file is hand-maintained and the
        # reasoning for a row is the most useful thing in it. Without this
        # the first `#` line becomes the header and every row parses into
        # a column nobody asked for -- silently, and the guard then reports
        # every row unresolved while the answers sit right there.
        rows = csv.DictReader(
            line for line in f if not line.lstrip().startswith("#"))
        return {r["real"].strip(): r["category"].strip()
                for r in rows if r.get("real") and r.get("category")}


def classify(text, source, category, sentinels=(), too_short=None):
    """One inventory entry -> a Token, or None if it cannot be searched.

    `category` is what the source says this is. It picks the match
    mechanics for the categories that have their own (labels, ids) and
    otherwise falls through to the shape-based name classification that
    has always been here.
    """
    if text in sentinels:
        return None

    if category in (LEAGUE_ID, TEAM_ID):
        return Token(text, IDENT, source, category)

    if category == FRANCHISE and source == "warehouse":
        # The warehouse tells us franchise names and labels in one column
        # family, so shape decides which mechanics to use -- but NOT the
        # severity, which the category already fixed. A multi-part name is
        # a family; anything else is a label, whatever it is made of.
        parts = family_parts(text)
        if parts is not None:
            return Token(text, FAMILY, source, category)
        if len(text.split()) > 1:
            return Token(text, PHRASE, source, category)
        if len(text) >= 2 or not text.isalnum():
            # The 3-character floor that applies to one-word NAMES is about
            # firing on prose, and a label is not exposed to that risk in
            # the same way: it is matched exactly as cased and bounded at
            # both ends, where a name is matched in three casings. Two
            # characters is where the predecessor's abbrev class started
            # too, so this keeps every real label searched. A label that is
            # not alphanumeric -- an emoji -- cannot fire on prose at all,
            # so it is admitted at any length rather than dropped unsearched.
            return Token(text, LABEL, source, category)
        if too_short is not None:
            too_short.append(text)
        return None

    if category == FRANCHISE and _is_short_single_part(text):
        return Token(text, LABEL, source, category)

    parts = family_parts(text)
    if parts is not None:
        # Full names, team names AND the map's slugs all land here: a
        # multi-part identity is matched as its whole delimiter and
        # middle-initial family, not just the one spelling that
        # happened to be stored. Supersedes the old PHRASE-only match, so
        # `jonas-mcavery` in a slug and `Jonas Q. McAvery` in prose are
        # both seen.
        return Token(text, FAMILY, source, category)
    if len(text.split()) > 1:
        return Token(text, PHRASE, source, category)
    if len(text) >= 3:
        return Token(text, WORD, source, category)
    if too_short is not None:
        too_short.append(text)
    return None


# Which of two classifications of the same string wins. Never the softer
# one: the warehouse's general franchise ruling must not quietly downgrade
# a string the map explicitly listed for removal.
_STRICTNESS = {FATAL: 0, REVIEW: 1, ALLOWED: 2}


def build_tokens(use_warehouse=True):
    """The search list, classified into match modes.

    Returns (tokens, census) where census explains every decision -- what
    each source contributed, what was exempted and why. The census is the
    part that makes this auditable; a guard nobody can interrogate is how
    we got here.
    """
    census = {"computed": 0, "map": 0, "warehouse_error": None,
              "exempt_maintainer": 0, "too_short": [], "modes": {},
              "categories": {}, "league_error": None, "registry_error": None,
              "sentinels": 0, "map_unresolved": []}

    mine = _maintainer_words()
    sentinels = _sentinels()
    census["sentinels"] = len(sentinels)

    names, err = (_computed_names() if use_warehouse else (set(), "skipped"))
    census["warehouse_error"] = err
    census["computed"] = len(names)

    league, league_err = (_computed_league_inventory() if use_warehouse
                          else ({FRANCHISE: set(), DIVISION: set(),
                                 TEAM_ID: set()}, "skipped"))
    census["league_error"] = league_err

    ids, ident_keys, registry_err = _registry_identifiers()
    census["registry_error"] = registry_err
    census["ident_keys"] = ident_keys

    entries = [(n, "warehouse", OWNER) for n in sorted(names)]
    for category in (FRANCHISE, DIVISION, TEAM_ID):
        entries += [(v, "warehouse", category) for v in sorted(league[category])]
    entries += [(v, "registry", LEAGUE_ID) for v in sorted(ids)]

    mapped = _map_rows()
    census["map"] = 0 if mapped is None else len(mapped)
    census["map_present"] = mapped is not None
    # The map has no category column, so each row's category is settled in
    # this order and never by shape:
    #
    #   1. the private sidecar, if it names this row;
    #   2. corroboration -- the warehouse's derived franchise inventory
    #      still holds this exact string, so it is a franchise identity;
    #   3. owner, the stronger class, for everything else.
    #
    # A row that is SHORT AND SINGLE-PART and survives to (3) is not
    # classified at all: it is collected as unresolved and the sweep
    # refuses to vouch. That is the correction to the version that guessed
    # "four characters means franchise", which downgraded any map-listed
    # owner initial from fatal to allowed -- silently, and in the one
    # direction a guard must never be wrong.
    explicit = _map_categories()
    franchise_values = {v.casefold() for v in league[FRANCHISE]}
    unresolved = []
    for text in (mapped or []):
        category = explicit.get(text)
        if category not in (OWNER, FRANCHISE, DIVISION, LEAGUE_ID, TEAM_ID):
            if text.casefold() in franchise_values:
                category = FRANCHISE
            elif _is_short_single_part(text):
                unresolved.append(text)
                continue
            else:
                category = OWNER
        entries.append((text, "map", category))
    census["map_unresolved"] = unresolved

    by_key = {}
    for text, source, category in entries:
        if any(w in mine for w in text.casefold().split()):
            census["exempt_maintainer"] += 1
            continue
        token = classify(text, source, category, sentinels, census["too_short"])
        if token is None:
            continue
        # Keyed by mode as well as spelling: a franchise label and the
        # given name it was derived from casefold to the same thing but
        # are searched differently, and collapsing them would silently
        # drop one of the two. On a genuine collision the STRICTER
        # classification wins -- see _STRICTNESS.
        key = (text.casefold(), token.mode)
        current = by_key.get(key)
        if current is None or (_STRICTNESS[severity(token)]
                               < _STRICTNESS[severity(current)]):
            by_key[key] = token

    tokens = list(by_key.values())
    for t in tokens:
        census["modes"][t.mode] = census["modes"].get(t.mode, 0) + 1
        census["categories"][t.category] = census["categories"].get(t.category, 0) + 1
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


# --------------------------------------------------------------------------
# The disposition ledger
# --------------------------------------------------------------------------

def _salt(create=False):
    """The HMAC secret, or None when it is not on this machine.

    Kept beside the map, gitignored with it. See the module docstring for
    why an unsalted digest of a four-letter label is the label.
    """
    if os.path.exists(SALT):
        with open(SALT, "r", encoding="utf-8") as f:
            value = f.read().strip()
        return value or None
    if not create:
        return None
    os.makedirs(os.path.dirname(SALT), exist_ok=True)
    value = secrets.token_hex(32)
    with open(SALT, "w", encoding="utf-8") as f:
        f.write(value + "\n")
    return value


def normalize_context(text, start, end):
    """The neighbourhood of a match, reduced to what identifies it.

    Whitespace collapsed and the whole thing casefolded, so re-wrapping a
    paragraph or re-indenting a block does not by itself invalidate a
    decision. Anything more than that -- a sentence rewritten, a value
    changed -- does invalidate it, and SHOULD: the reviewer agreed to a
    specific occurrence in specific words, and once the words move far
    enough the decision is about a site that no longer exists. Re-review
    is the safe direction.
    """
    left = text[max(0, start - _CONTEXT_WINDOW):start]
    right = text[end:end + _CONTEXT_WINDOW]
    return " ".join(f"{left}\x00{text[start:end]}\x00{right}".split()).casefold()


def digest(salt, category, text, context="", ordinal=0):
    """The ledger's stand-in for one OCCURRENCE. Inert without the salt.

    Keyed by the identity AND where it sits, because the ledger's claim is
    "somebody looked at this site" and a token-level key cannot make that
    claim about the second use in the same file.

    `ordinal` separates occurrences whose surroundings are genuinely
    identical -- a repeated table row, a generated block. It is the last
    resort rather than the key, because an ordinal alone renumbers every
    later decision the moment a line is inserted above it.
    """
    material = f"{category}\x00{text.casefold()}\x00{context}\x00{ordinal}"
    return hmac.new(salt.encode("utf-8"), material.encode("utf-8"),
                    hashlib.sha256).hexdigest()[:16]


def load_ledger():
    """{(path, category, digest): row} for every recorded disposition."""
    if not os.path.exists(LEDGER):
        return {}
    with open(LEDGER, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return {(r["path"], r["category"], r["digest"]): r
            for r in rows if r.get("path") and r.get("digest")}


def write_ledger(rows):
    """Rewrite the ledger, sorted so a diff reads as a review."""
    with open(LEDGER, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["path", "category", "digest", "disposition",
                           "reason", "recorded"])
        writer.writeheader()
        for row in sorted(rows, key=lambda r: (r["path"], r["category"],
                                               r["digest"])):
            writer.writerow(row)


def sweep(tokens, stats=None, salt=None, ledger=None, ident_keys=()):
    """Every (severity, path, line, token, context, disposition, digest).

    One entry per OCCURRENCE -- not per token, and no longer per
    token-in-a-file. `disposition` is the ledger row covering that exact
    occurrence, or None, which is what lets a new hit be told from a
    reviewed one. There is no class-wide amnesty here and no per-file one
    either: a hit is dispositioned because somebody recorded THIS site
    (MLB-234).

    ALLOWED hits are ruled on by category and never consult the ledger --
    they are counted, not filed.
    """
    hits = []
    ledger = ledger or {}
    pattern = ident_context_pattern(ident_keys)
    wants_context = any(t.mode == IDENT and not t.bare for t in tokens)
    for path, text in head_blobs(stats):
        low = text.lower()
        contexts = ident_contexts(text, pattern) if wants_context else {}
        for token in tokens:
            sev = severity(token)
            # Identical surroundings inside one file need an ordinal to be
            # told apart; counted per (token, normalized context) so the
            # ordinal is 0 for every occurrence that is already distinct.
            seen = {}
            for start, end, _matched in token.find_all(text, low, contexts):
                line_no = text.count("\n", 0, start) + 1
                shown = text[max(0, start - 40):end + 40].replace("\n", " ")
                fingerprint = row = None
                if sev != ALLOWED and salt is not None:
                    norm = normalize_context(text, start, end)
                    ordinal = seen.get(norm, 0)
                    seen[norm] = ordinal + 1
                    fingerprint = digest(salt, token.category, token.text,
                                         norm, ordinal)
                    row = ledger.get((path, token.category, fingerprint))
                hits.append((sev, path, line_no, token, shown.strip(), row,
                             fingerprint))
    hits.sort(key=lambda h: (h[0], h[1], h[2]))
    return hits


def _print_census(census, tokens, show_tokens):
    print("PII GUARD -- what this sweep searches for")
    print(f"  computed from warehouse : {census['computed']} owner-name "
          f"spellings")
    if census["warehouse_error"]:
        print(f"      UNAVAILABLE: {census['warehouse_error']}")
    if census["league_error"]:
        print(f"      LEAGUE INVENTORY UNAVAILABLE: {census['league_error']}")
    if census["registry_error"]:
        print(f"      REGISTRY: {census['registry_error']}")
    print(f"  from the private map    : {census['map']}"
          f"{'' if census['map_present'] else ' (map not on this machine)'}")
    print(f"  searchable after dedup  : {len(tokens)}")
    for mode in MODES:
        print(f"      {mode:7}: {census['modes'].get(mode, 0)}")
    print("  by category:")
    for category in (OWNER, FRANCHISE, DIVISION, LEAGUE_ID, TEAM_ID):
        print(f"      {category:10}: {census['categories'].get(category, 0)}")
    print(f"  exempt (maintainer)     : {census['exempt_maintainer']}")
    print(f"  excluded (sentinels)    : {census['sentinels']}")
    if census["map_unresolved"]:
        print(f"  UNRESOLVED map rows     : {len(census['map_unresolved'])} "
              f"-- category not self-evident; name them in {MAP_CATEGORIES}")
    print(f"  dropped (under 3 chars) : {len(census['too_short'])}"
          f"{' -- ' + ', '.join(census['too_short']) if show_tokens and census['too_short'] else ''}")
    if show_tokens:
        for t in sorted(tokens, key=lambda t: (t.mode, t.text.casefold())):
            print(f"      {t.mode:7} {t.category:10} {t.source:9} {t.text}")


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
                    help="list every reviewable hit instead of counting")
    ap.add_argument("--unreviewed", action="store_true",
                    help="list every hit with no recorded disposition")
    ap.add_argument("--record-dispositions", action="store_true",
                    help="write a ledger row for every undispositioned hit "
                         "and exit; review the diff before committing it")
    ap.add_argument("--disposition", default=RETAIN, choices=DISPOSITIONS,
                    help="with --record-dispositions, the class to record")
    ap.add_argument("--reason", default="",
                    help="with --record-dispositions, the recorded reason")
    ap.add_argument("--recorded", default="",
                    help="with --record-dispositions, the date to stamp")
    ap.add_argument("--fail-on-review", action="store_true",
                    help="treat every reviewable hit as a failure, recorded "
                         "or not -- the strictest reading there is")
    ap.add_argument("--fail-on-unreviewed", action="store_true",
                    help="accepted and ignored: failing on an unreviewed hit "
                         "is now the default")
    ap.add_argument("--allow-unreviewed", action="store_true",
                    help="DIAGNOSTIC ONLY: report unreviewed hits and exit 0. "
                         "For taking a census while you work; never what the "
                         "pre-push hook runs")
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
    salt = _salt(create=args.record_dispositions)
    degraded = []
    if census["warehouse_error"] and not args.no_warehouse:
        degraded.append(
            f"the warehouse owner list could not be computed "
            f"({census['warehouse_error']}), so an owner who was never "
            f"added to the map is invisible to this sweep")
    if census["league_error"] and not args.no_warehouse:
        degraded.append(
            f"the warehouse league inventory could not be computed "
            f"({census['league_error']}), so franchise names and labels, "
            f"division names and team ids are not searched")
    if census["registry_error"]:
        degraded.append(
            f"the league registry did not yield every league id "
            f"({census['registry_error']})")
    if not census["map_present"]:
        degraded.append(
            f"the private map is not on this machine ({MAP}), so retired "
            f"identities, historical spellings and owner-id slugs are not "
            f"searched")
    if salt is None:
        degraded.append(
            f"the disposition secret is not on this machine ({SALT}), so a "
            f"reviewed hit cannot be told from an unreviewed one -- which "
            f"is the whole question the ledger exists to answer")
    if census["map_unresolved"]:
        degraded.append(
            f"{len(census['map_unresolved'])} map row(s) are too short for "
            f"their category to be self-evident and the warehouse no longer "
            f"holds them, so nothing can say whether each is a retired "
            f"franchise label or an owner's initials -- and the two are "
            f"handled oppositely. Name them in {MAP_CATEGORIES} "
            f"(real,category) rather than let this be guessed: "
            f"{', '.join(census['map_unresolved'])}")
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

    ledger = load_ledger()
    stats = {}
    try:
        hits = sweep(tokens, stats, salt, ledger, census.get("ident_keys", ()))
    except (Failure, subprocess.TimeoutExpired) as exc:
        print(f"PII GUARD: {exc}", file=sys.stderr)
        return 1

    if args.record_dispositions:
        rows = dict(ledger)
        added = 0
        for sev, path, _line, token, _ctx, row, fingerprint in hits:
            if row is not None or sev in (FATAL, ALLOWED) or fingerprint is None:
                continue
            key = (path, token.category, fingerprint)
            if key in rows:
                continue
            rows[key] = {"path": path, "category": token.category,
                         "digest": key[2], "disposition": args.disposition,
                         "reason": args.reason, "recorded": args.recorded}
            added += 1
        write_ledger(rows.values())
        print(f"PII GUARD: recorded {added} new disposition(s) as "
              f"'{args.disposition}' in {LEDGER}. Review the diff.",
              file=sys.stderr)
        return 0

    print(f"PII GUARD: scanned {stats.get('scanned', 0)} of "
          f"{stats.get('tracked', 0)} files tracked at HEAD "
          f"({stats.get('bytes', 0) / 1e6:.1f}MB, "
          f"{stats.get('skipped_binary', 0)} binary skipped) for "
          f"{len(tokens)} strings. Not a filesystem walk -- the file list "
          f"is git ls-tree, so untracked and ignored trees are out by "
          f"construction.", file=sys.stderr)

    fatal = [h for h in hits if h[0] == FATAL]
    review = [h for h in hits if h[0] == REVIEW]
    allowed = [h for h in hits if h[0] == ALLOWED]
    unreviewed = [h for h in review if h[5] is None]
    dispositioned = [h for h in review if h[5] is not None]

    if allowed:
        by_cat = {}
        for h in allowed:
            by_cat[h[3].category] = by_cat.get(h[3].category, 0) + 1
        summary = ", ".join(f"{v} {k}" for k, v in sorted(by_cat.items()))
        print(f"PII GUARD: {len(allowed)} occurrence(s) allowed by category "
              f"rule ({summary}) across {len({h[1] for h in allowed})} "
              f"file(s). Derived and searched, ruled on once -- not filed "
              f"per site.", file=sys.stderr)

    if dispositioned:
        by_class = {}
        for h in dispositioned:
            key = h[5].get("disposition", "?")
            by_class[key] = by_class.get(key, 0) + 1
        summary = ", ".join(f"{v} {k}" for k, v in sorted(by_class.items()))
        print(f"PII GUARD: {len(dispositioned)} occurrence(s) carry a "
              f"recorded disposition ({summary}). Each was reviewed at the "
              f"exact site it appears in -- not covered by a class-wide "
              f"ruling, and not by a decision about the same word elsewhere "
              f"in the same file.", file=sys.stderr)

    if unreviewed:
        names = sorted({h[3].category for h in unreviewed})
        print(f"PII GUARD UNREVIEWED: {len(unreviewed)} occurrence(s) across "
              f"{len({h[1] for h in unreviewed})} file(s) have NO recorded "
              f"disposition [{', '.join(names)}]. These are new: no decision "
              f"has ever been taken about them. `--record-dispositions` "
              f"records them.", file=sys.stderr)
        for _sev, path, line, token, context, _row, _fp in unreviewed:
            print(f"  {path}:{line}: [{token.category}/{token.mode}]",
                  file=sys.stderr)
            print(f"      ...{context}...", file=sys.stderr)

    if review and args.review:
        for _sev, path, line, token, context, row, _fp in dispositioned:
            print(f"  {path}:{line}: [{token.category}/{token.mode}] "
                  f"{row.get('disposition')} -- {row.get('reason')}",
                  file=sys.stderr)

    if fatal:
        print("PII GUARD: real-league strings found in the tree about to be "
              "pushed:", file=sys.stderr)
        for _sev, path, line, token, context, _row, _fp in fatal:
            print(f"  {path}:{line}: {token.text}  [{token.category}/"
                  f"{token.mode}, {token.source}]", file=sys.stderr)
            print(f"      ...{context}...", file=sys.stderr)
        print("Fix (or extend archives/anonymization/) before pushing. "
              "Bypass only if you are CERTAIN: git push --no-verify",
              file=sys.stderr)
        return 1

    # UNREVIEWED BLOCKS BY DEFAULT. It did not, and the gap between the
    # release-cut command and the hook was the whole exposure: the hook ran
    # the default, the default printed a warning and exited 0, so a new
    # unreviewed occurrence was pushable and only the person reading the
    # push output would ever know. A warning that does not stop anything is
    # a warning nobody reads twice. `--allow-unreviewed` is the diagnostic
    # way to take a census mid-work; it is deliberately not what the hook
    # runs.
    if unreviewed and not args.allow_unreviewed:
        print("\n  These have never been reviewed, so this push is blocked. "
              "Record a decision with --record-dispositions (and commit the "
              "ledger), or re-run with --allow-unreviewed for a non-blocking "
              "census while you work.", file=sys.stderr)
        return 1
    if unreviewed:
        print("  --allow-unreviewed: not blocking on the above.",
              file=sys.stderr)
    return 1 if (review and args.fail_on_review) else 0


if __name__ == "__main__":
    sys.exit(main())
