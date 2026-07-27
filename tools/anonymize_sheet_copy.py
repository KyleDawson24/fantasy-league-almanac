#!/usr/bin/env python3
"""Anonymize a THROWAWAY Google Sheet copy in place, for screenshots (MLB-100).

The portfolio needs screenshots of the almanac that show the anonymized
owner identities -- the ones a stranger cloning this repo would render --
not the real league. Anonymization is a repo property, not a run mode:
the anonymized names live only in the COMMITTED copies of five seeds,
while Kyle's working tree carries the real data (skip-worktree, MLB-95).
So every render to date is real-name.

The cheap way to get anonymized screenshots is not to swap seeds and
rebuild the warehouse (that recipe exists as the fallback, and it mutates
shared state). It is to copy the dev workbook in Drive and rewrite the
COPY. Nothing in the repo or the warehouse is touched; the worst case is
deleting a bad copy and making another.

    python tools/anonymize_sheet_copy.py --target <id-or-url> [--target ...]
    python tools/anonymize_sheet_copy.py --target <id> --dry-run

WHERE THE MAPPING COMES FROM
    Derived at runtime, never hardcoded -- this file has to be safe to
    commit to a public repo. For each of the five seeds, the real side is
    the file on disk and the anonymized side is `git show HEAD:<path>`
    (the committed twin). Joining those two on a stable key gives
    real -> anon for every name form the sheets can render.

    The join key is not uniform, which is the one subtlety here:
      * ESPN owners carry a braced SWID GUID that survived anonymization,
        so owner_id joins them directly.
      * CBS owners carry a name-DERIVED slug, so the anonymizer had to
        rewrite the id too -- owner_id is not stable for them. They are
        bridged through cbs_team_owners.csv, whose (league_key,
        franchise_id) key IS stable: that file pairs real owner_id to
        anon owner_id, and the bridge is cross-checked against row
        alignment before it is trusted (see _build_owner_crosswalk).
      * The by-year file joins on (league_key, season_year, franchise_id)
        and the franchise file on (league_key, franchise_id); both keys
        are untouched by anonymization.

    A join that does not reconcile raises. A partial mapping would
    produce a sheet that looks anonymized and is not, which is the one
    outcome worth failing loudly to avoid.

    A handful of team names only ever existed in the live platform API
    (ESPN team names are never in a seed), so their anonymized twins can't
    be derived from the seeds. Those come from the private scrub map
    (archives/anonymization/name_map.csv, gitignored) when it is present --
    the same file check_pii.py reads, and a silent no-op on any clone that
    lacks it. Seed-derived pairs win on any real-form the two share, so
    owner names stay identical to what a stranger's clone would render.

REPLACEMENT
    Longest-first, word-boundary, case-preserving, applied to cell VALUES
    and FORMULAS (so HYPERLINK display text is covered) on every tab, plus
    the tab titles themselves -- those show in the tab bar of any
    screenshot. Surname tokens are replaced even inside team names, per
    Kyle's standing call: privacy over charm.

    The substitution is a SINGLE pass over one combined alternation. That
    matters: a two-pass scheme could re-replace an anonymized name if it
    happened to be a real name elsewhere in the league, and would make the
    result depend on ordering.

PLAYER NAMES ARE NEVER TOUCHED
    These sheets interleave owner identities with real MLB player names,
    and the two collide: "Matt" is an owner first name AND the first name
    of Matt Carpenter, Matt Olson, and a dozen others. A naive owner-token
    replacement rewrites every one of those players (v1 turned "Matt
    Carpenter" into "CODE Carpenter"). The residual audit never caught it
    because over-writing a player is not a leftover owner string.

    So every real player name in the target is collected first -- the
    display text and search query of every baseball-reference HYPERLINK,
    which is how the almanac renders players -- and any mapping pair whose
    real form falls on a word boundary inside a player name is DROPPED. An
    owner token that coincides with a player (a bare first name, mostly) is
    simply not scrubbed; the owner is still hidden by their full name, the
    "Last, First" form, and the team name, none of which collide with a
    single player token. As belt-and-suspenders, a cell carrying a
    baseball-reference link is never written at all. The count of dropped
    tokens is printed, never silent -- privacy leans on what remains, so
    the run says what it declined to touch and why.

    The deliberately-kept team whose name is "14-30-8-24-5-15-13-20"
    (abbrev MATT) is left intact: its abbrev coincides with an owner's
    first name, but the abbrev is a kept identity (its seed twin equals
    itself) and only the owner-first-name rule -- now dropped as a player
    collision -- ever rewrote it.

DOUBLE SAFETY LATCH (in this order, both before any write)
    (a) Resolve the real dev/prod spreadsheet ids from the league registry
        + .env and HARD-REFUSE if the target matches one. This also
        refuses when NO ids resolve at all -- an unloaded .env would
        otherwise make the check vacuously pass, which is the failure mode
        that actually bites.
    (b) Require a throwaway marker ("ANON" or "DEMO") in the target's
        title. If absent, rename the (already-verified-safe) copy to
        prepend the ANON banner and carry on -- the copy has already
        proven it is not a real sheet.

ACCEPTANCE
    A residual audit re-reads every cell and formula of every tab and
    rescans for every real token that was actually applied, printing
    per-tab counts. It also confirms zero baseball-reference player cells
    were altered. Zero on both is the pass condition; anything else exits
    non-zero.

Auth: the same cached user token as every other sheets writer
(output/.sheets_oauth_token.json); no new consent flow, no Drive scope.
"""

import argparse
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import gspread
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from config.league_registry import load_registry  # noqa: E402

_OAUTH_SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
_TOKEN_PATH = REPO_ROOT / 'output' / '.sheets_oauth_token.json'

# The five skip-worktree seeds (MLB-95). Disk = real, HEAD = anonymized.
_SEED_DIR = 'dbt_league/seeds'
SEED_FRANCHISES = f'{_SEED_DIR}/cbs_franchises.csv'
SEED_TEAM_OWNERS = f'{_SEED_DIR}/cbs_team_owners.csv'
SEED_ALIAS = f'{_SEED_DIR}/owner_alias.csv'
SEED_NICKNAMES = f'{_SEED_DIR}/owner_nicknames.csv'
SEED_BY_YEAR = f'{_SEED_DIR}/team_owner_by_year.csv'

ALL_SEEDS = (
    SEED_FRANCHISES, SEED_TEAM_OWNERS, SEED_ALIAS, SEED_NICKNAMES, SEED_BY_YEAR,
)

# The private MLB-95 scrub map: real -> fake for strings the seeds don't
# carry (ESPN team names live only in the live API). Gitignored, maintainer-
# only; absent on clones, where its contribution is simply empty. Same file
# tools/check_pii.py reads.
_PRIVATE_MAP = REPO_ROOT / 'archives' / 'anonymization' / 'name_map.csv'

# Legacy single-league sheet variables, plus whatever the registry's
# per-league `sinks` name. Both feed latch (a).
_LEGACY_SHEET_ENV = ('SHEETS_OUTPUT_ID', 'SHEETS_DEV_ID', 'SHEETS_PROD_ID')

# ASCII double-hyphen, matching the almanac's house dash style.
ANON_TITLE_PREFIX = 'ANON COPY -- SCREENSHOTS ONLY -- '
# Any of these words in the title is proof the sheet is a throwaway and
# not a real book. "DEMO" earns its place because the screenshot copies
# are named "... DEMO COPY"; latch (a) is the hard id-based guard, this is
# the human-readable second line.
ANON_TITLE_MARKERS = ('ANON', 'DEMO')

# Contact columns exist on the real side of owner_nicknames and were
# DROPPED from the committed twin, so there is no anonymized counterpart
# to map to. They are redacted to neutral placeholders instead. Beyond the
# name-form brief, but a phone number in a screenshot is the worst leak
# available and the cost of covering it is three lines.
_REDACTIONS = {'email': 'redacted@example.com', 'phone_number': '000-000-0000'}

# A token this short cannot be matched safely even on word boundaries.
# Nothing in the current seeds trips it; if that ever changes the skip is
# printed, never silent.
_MIN_TOKEN_LEN = 3


# --------------------------------------------------------------------------
# Seed pair loading
# --------------------------------------------------------------------------

def _git_show(path):
    """The COMMITTED bytes of a path -- the anonymized twin.

    Deliberately `git show HEAD:<path>` and not a working-tree read: for
    these five seeds the working copy is real league data and only the
    committed copy is anonymized (the recurring false alarm in CLAUDE.md,
    inverted -- here we want the committed side on purpose).
    """
    proc = subprocess.run(
        ['git', 'show', f'HEAD:{path}'],
        cwd=str(REPO_ROOT), capture_output=True, text=True, encoding='utf-8',
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git show HEAD:{path} failed -- cannot read the anonymized twin. "
            f"{proc.stderr.strip()}"
        )
    return proc.stdout


def _parse_csv(text):
    import csv
    import io
    return list(csv.DictReader(io.StringIO(text)))


def _load_pair(path):
    """Return (real_rows, anon_rows) for one seed, validated as pairable."""
    disk = (REPO_ROOT / path)
    if not disk.is_file():
        raise RuntimeError(f"Seed {path} not found on disk at {disk}.")
    real = _parse_csv(disk.read_text(encoding='utf-8-sig'))
    anon = _parse_csv(_git_show(path))
    if len(real) != len(anon):
        raise RuntimeError(
            f"Seed {path}: working tree has {len(real)} rows but HEAD has "
            f"{len(anon)}. The pair cannot be joined row-wise; the "
            f"anonymized twin is out of date with the real seed."
        )
    return real, anon


def _assert_stable(path, real, anon, cols):
    """Fail unless the named columns are identical index-for-index.

    These are the columns anonymization must never touch (ids, seasons,
    league keys). Verifying them is what earns the right to pair the rows
    at all -- if a supposedly stable key drifted, every mapping derived
    from this file would be silently wrong.
    """
    for i, (r, a) in enumerate(zip(real, anon)):
        for c in cols:
            if (r.get(c) or '') != (a.get(c) or ''):
                raise RuntimeError(
                    f"Seed {path} row {i + 2}: column {c!r} differs between "
                    f"the working tree and HEAD, but it is supposed to be a "
                    f"stable join key. Refusing to derive a mapping from an "
                    f"unreconciled pair."
                )


# --------------------------------------------------------------------------
# Mapping derivation
# --------------------------------------------------------------------------

def _build_owner_crosswalk(verbose=True):
    """real owner_id -> anon owner_id, for every owner in the league.

    Two populations, two mechanisms:
      * Braced SWID GUIDs (ESPN) survived anonymization untouched, so they
        map to themselves.
      * CBS slugs are name-derived and were rewritten, so they come from
        cbs_team_owners.csv, where (league_key, franchise_id) is stable and
        pairs the two id spaces directly.

    The bridge is then cross-checked against plain row alignment in
    owner_nicknames. Two independent derivations agreeing is what makes
    this trustworthy rather than a guess; a disagreement raises.
    """
    tow_real, tow_anon = _load_pair(SEED_TEAM_OWNERS)
    _assert_stable(SEED_TEAM_OWNERS, tow_real, tow_anon, ['league_key', 'franchise_id'])

    crosswalk = {}
    for r, a in zip(tow_real, tow_anon):
        rid, aid = (r.get('owner_id') or '').strip(), (a.get('owner_id') or '').strip()
        if not rid:
            continue
        if rid in crosswalk and crosswalk[rid] != aid:
            raise RuntimeError(
                f"{SEED_TEAM_OWNERS}: real owner_id {rid[:8]}... maps to two "
                f"different anonymized ids. The bridge is ambiguous."
            )
        crosswalk[rid] = aid

    nick_real, nick_anon = _load_pair(SEED_NICKNAMES)
    stable = disagreements = bridged = unbridged = 0
    for r, a in zip(nick_real, nick_anon):
        rid, aid = (r.get('owner_id') or '').strip(), (a.get('owner_id') or '').strip()
        if rid == aid:
            crosswalk.setdefault(rid, aid)
            stable += 1
        elif rid in crosswalk:
            # Row alignment says rid->aid; the bridge already has an
            # opinion. They must agree.
            if crosswalk[rid] != aid:
                disagreements += 1
            bridged += 1
        else:
            unbridged += 1

    if disagreements:
        raise RuntimeError(
            f"{SEED_NICKNAMES}: {disagreements} owner id(s) pair differently "
            f"by row order than by the {SEED_TEAM_OWNERS} bridge. The two "
            f"seeds disagree about who is who; refusing to guess."
        )
    if unbridged:
        raise RuntimeError(
            f"{SEED_NICKNAMES}: {unbridged} owner(s) have a rewritten "
            f"owner_id that {SEED_TEAM_OWNERS} cannot bridge. The mapping "
            f"would be incomplete."
        )
    if verbose:
        print(f"  owner crosswalk: {stable} stable-id (ESPN GUID) + "
              f"{bridged} bridged (CBS slug) = {len(crosswalk)} owners")
    return crosswalk


def build_mapping(verbose=True):
    """Derive real -> anon for every name form the sheets can render.

    Returns (pairs, report) where pairs is a list of (real, anon) and
    report carries the counts and every judgement call made, so the run
    log shows what was decided rather than just what was replaced.
    """
    crosswalk = _build_owner_crosswalk(verbose=verbose)
    pairs = []          # (real, anon)
    report = {'sources': Counter(), 'ambiguous': [], 'skipped_short': []}

    def add(real_value, anon_value, source):
        real_value = (real_value or '').strip()
        anon_value = (anon_value or '').strip()
        # Identity pairs carry no secret and MUST NOT enter the audit set:
        # a value that is legitimately unchanged would otherwise be
        # reported as a residual forever, making zero unreachable.
        if not real_value or not anon_value or real_value == anon_value:
            return
        if len(real_value) < _MIN_TOKEN_LEN:
            report['skipped_short'].append((real_value, source))
            return
        pairs.append((real_value, anon_value))
        report['sources'][source] += 1

    # owner_nicknames: the primary name table. first/last are single-token
    # columns and become token-level replacements (this is what reaches a
    # surname buried inside a team name); preferred_name is a whole form.
    nick_real, nick_anon = _load_pair(SEED_NICKNAMES)
    for r, a in zip(nick_real, nick_anon):
        add(r.get('first_name'), a.get('first_name'), 'nicknames.first_name')
        add(r.get('last_name'), a.get('last_name'), 'nicknames.last_name')
        add(r.get('preferred_name'), a.get('preferred_name'), 'nicknames.preferred_name')
        # Composite forms the sheets build but no single column holds.
        first, last = (r.get('first_name') or '').strip(), (r.get('last_name') or '').strip()
        afirst, alast = (a.get('first_name') or '').strip(), (a.get('last_name') or '').strip()
        if first and last and afirst and alast:
            add(f'{first} {last}', f'{afirst} {alast}', 'nicknames.full_name')
            add(f'{last}, {first}', f'{alast}, {afirst}', 'nicknames.last_first')
            add(f'{first[0]}. {last}', f'{afirst[0]}. {alast}', 'nicknames.initial_last')
        # Contact columns have no anonymized counterpart (dropped from the
        # committed twin) -- redact rather than map.
        for col, placeholder in _REDACTIONS.items():
            raw = (r.get(col) or '').strip()
            if raw:
                add(raw, placeholder, f'nicknames.{col}')
                digits = re.sub(r'\D', '', raw)
                if col == 'phone_number' and len(digits) >= 7 and digits != raw:
                    add(digits, re.sub(r'\D', '', placeholder), 'nicknames.phone_digits')

    # owner_alias: preferred_name keyed by owner. This file carries its own
    # id space -- legacy/duplicate ids being folded into a canonical one --
    # so most of its owner_ids are absent from the crosswalk by design.
    # Verify only the ones the crosswalk actually knows about; demanding
    # full membership would reject a correct pairing.
    alias_real, alias_anon = _load_pair(SEED_ALIAS)
    _assert_stable(SEED_ALIAS, alias_real, alias_anon, ['league_key'])
    for r, a in zip(alias_real, alias_anon):
        for col in ('owner_id', 'canonical_owner_id'):
            rid, aid = (r.get(col) or '').strip(), (a.get(col) or '').strip()
            if rid in crosswalk and crosswalk[rid] != aid:
                raise RuntimeError(
                    f"{SEED_ALIAS}: {col} {rid[:8]}... pairs to a different "
                    f"anonymized id than {SEED_TEAM_OWNERS} does."
                )
            if rid and aid:
                crosswalk.setdefault(rid, aid)
        add(r.get('preferred_name'), a.get('preferred_name'), 'alias.preferred_name')

    # The CBS owner_id slugs are themselves name-derived, so an id that
    # leaked into a rendered cell would leak a name with it. Map them
    # outright. (ESPN's GUIDs are identical on both sides and drop out via
    # the identity rule.)
    for rid, aid in crosswalk.items():
        add(rid, aid, 'owner_id.slug')

    # team_owner_by_year: the rendered owner label per season. Stable key.
    year_real, year_anon = _load_pair(SEED_BY_YEAR)
    _assert_stable(SEED_BY_YEAR, year_real, year_anon,
                   ['league_key', 'season_year', 'franchise_id'])
    for r, a in zip(year_real, year_anon):
        add(r.get('owner_name'), a.get('owner_name'), 'by_year.owner_name')

    # cbs_franchises: team names (which carry surnames) and abbrevs.
    fr_real, fr_anon = _load_pair(SEED_FRANCHISES)
    _assert_stable(SEED_FRANCHISES, fr_real, fr_anon, ['league_key', 'franchise_id'])
    for r, a in zip(fr_real, fr_anon):
        add(r.get('franchise_name'), a.get('franchise_name'), 'franchises.franchise_name')
        add(r.get('abbrev'), a.get('abbrev'), 'franchises.abbrev')

    # Private map fills what the seeds cannot: ESPN team names, which only
    # ever existed in the live API. Add only real-forms the seeds did not
    # already cover, so owner names stay identical to the seed twins.
    seed_reals = {real.lower() for real, _ in pairs}
    for real_value, anon_value in _load_private_map():
        if real_value.lower() not in seed_reals:
            add(real_value, anon_value, 'private_map')

    pairs, report['ambiguous'] = _resolve(pairs)

    if verbose:
        print(f"  mapping: {len(pairs)} distinct real forms from "
              f"{sum(report['sources'].values())} source values")
        for src, n in sorted(report['sources'].items()):
            print(f"      {src:<34} {n}")
        for real_form, chosen, others in report['ambiguous']:
            print(f"  [ambiguous] {len(real_form)}-char real form shared by "
                  f"{len(others) + 1} owners; using one anonymized form "
                  f"consistently (all candidates are anonymous)")
        for value, src in report['skipped_short']:
            print(f"  [skipped] {src}: real form shorter than "
                  f"{_MIN_TOKEN_LEN} chars -- unsafe to match, NOT replaced")
    return pairs, report


def _load_private_map():
    """(real, fake) rows from the private scrub map, or [] when absent.

    Mirrors tools/check_pii.py: the map is gitignored and maintainer-only,
    so on any clone that lacks it this is a silent no-op. Rows missing
    either side, or with a real form too short to match safely, are
    skipped. No literal names live in this script -- they come from the
    file at runtime, exactly like the seed twins.
    """
    if not _PRIVATE_MAP.is_file():
        return []
    import csv
    out = []
    with open(_PRIVATE_MAP, newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            real = (row.get('real') or '').strip()
            fake = (row.get('fake') or '').strip()
            if real and fake and real != fake and len(real) >= _MIN_TOKEN_LEN:
                out.append((real, fake))
    return out


def _resolve(pairs):
    """Collapse to one anon form per real form, case-insensitively.

    A bare first name shared by two owners is genuinely ambiguous with no
    surname context (longest-first already resolves the cases that DO have
    context, because the full name is a longer form and matches first).
    Every candidate is anonymous, so any choice is privacy-equivalent --
    what matters is that it is deterministic and reported.
    """
    by_real = defaultdict(list)
    for real_value, anon_value in pairs:
        by_real[real_value.lower()].append((real_value, anon_value))

    resolved, ambiguous = {}, []
    for key, candidates in by_real.items():
        counts = Counter(anon for _, anon in candidates)
        # Most frequent wins; lexicographic breaks the tie so reruns and
        # different machines agree.
        best = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        # Keep the real form's most common surface casing for the pattern.
        surface = Counter(real for real, _ in candidates).most_common(1)[0][0]
        resolved[key] = (surface, best)
        if len(counts) > 1:
            ambiguous.append((surface, best, [a for a in counts if a != best]))
    return sorted(resolved.values(), key=lambda p: (-len(p[0]), p[0])), ambiguous


def _assert_auditable(pairs):
    """Refuse a mapping whose own output would trip the residual audit.

    If some real form is ALSO an anonymized form for someone else, then
    after replacement that string legitimately appears in the sheet and
    the audit could never reach zero -- there would be no way to tell a
    leak from a correct substitution.
    """
    reals = {r.lower() for r, _ in pairs}
    anons = {a.lower() for _, a in pairs}
    collisions = sorted(reals & anons)
    if collisions:
        raise RuntimeError(
            f"{len(collisions)} real name form(s) are also anonymized name "
            f"forms. The residual audit could not distinguish a leak from a "
            f"correct replacement, so zero residuals would be unverifiable. "
            f"Resolve the seed twins before anonymizing a sheet."
        )


# --------------------------------------------------------------------------
# Player-name protection
# --------------------------------------------------------------------------
#
# The one hazard these sheets pose: an owner's name token is also a real
# MLB player's. Replace it blindly and you rewrite the player. Players are
# rendered as baseball-reference HYPERLINKs, so the display text and search
# query of those links are a reliable roster of every player name present.
# Any mapping pair whose real form lands on a word boundary inside one of
# those names is dropped -- the owner stays hidden through their non-
# colliding forms (full name, "Last, First", team name).

_BREF_HOST = 'baseball-reference.com'
_HYPERLINK_DISPLAY = re.compile(r'HYPERLINK\(\s*"[^"]*"\s*,\s*"([^"]*)"', re.I)
_BREF_SEARCH = re.compile(r'search=([^"&]+)', re.I)


def _is_player_cell(cell):
    """A cell that renders an MLB player -- a baseball-reference link."""
    return isinstance(cell, str) and _BREF_HOST in cell.lower()


def build_player_oracle(tabs):
    """The set of real player-name strings present in the target.

    Pulled from every baseball-reference HYPERLINK: the display text (what
    the reader sees) and the search query (the same name, '+'-joined).
    Built from the target itself so it adapts to whatever players a given
    week's render happens to include.
    """
    names = set()
    for grid, _, _ in tabs.values():
        for row in grid:
            for cell in row:
                if not _is_player_cell(cell):
                    continue
                for disp in _HYPERLINK_DISPLAY.findall(cell):
                    names.add(disp.strip())
                for q in _BREF_SEARCH.findall(cell):
                    names.add(q.replace('+', ' ').strip())
    return names


def split_player_safe(pairs, player_names):
    """Partition pairs into (safe, dropped).

    Dropped = the real form occurs, on word boundaries, inside some real
    player name; replacing it would deface a player. Everything else is
    safe to apply globally.
    """
    blob = '\n'.join(player_names)
    safe, dropped = [], []
    for real_value, anon_value in pairs:
        pattern = rf'(?<![0-9A-Za-z_]){re.escape(real_value)}(?![0-9A-Za-z_])'
        if re.search(pattern, blob, re.IGNORECASE):
            dropped.append((real_value, anon_value))
        else:
            safe.append((real_value, anon_value))
    return safe, dropped


# --------------------------------------------------------------------------
# Replacement engine
# --------------------------------------------------------------------------

class Replacer:
    """Longest-first, word-boundary, case-preserving, single-pass.

    Single pass is the load-bearing property: one combined alternation
    means every character of the input is consumed at most once, so an
    anonymized name written into the text can never be re-matched by a
    later rule.
    """

    def __init__(self, pairs):
        self.pairs = pairs
        self._by_key = {real.lower(): anon for real, anon in pairs}
        # Sorted longest-first so "First Last" wins over a bare "First".
        ordered = sorted(pairs, key=lambda p: (-len(p[0]), p[0]))
        alternation = '|'.join(re.escape(real) for real, _ in ordered)
        # Lookarounds rather than \b: these forms can start or end with a
        # non-word character (initials like "J.", possessive-adjacent
        # surnames), where \b would silently fail to anchor.
        self._pattern = re.compile(
            rf'(?<![0-9A-Za-z_])(?:{alternation})(?![0-9A-Za-z_])',
            re.IGNORECASE,
        ) if ordered else None

    def sub(self, text):
        """Return (new_text, n_replacements)."""
        if self._pattern is None or not text:
            return text, 0
        count = [0]

        def _repl(match):
            found = match.group(0)
            anon = self._by_key.get(found.lower())
            if anon is None:  # pragma: no cover - alternation guarantees a hit
                return found
            count[0] += 1
            return _match_case(found, anon)

        return self._pattern.sub(_repl, text), count[0]

    def find(self, text):
        """Real forms still present -- the audit's view."""
        if self._pattern is None or not text:
            return []
        return self._pattern.findall(text)


def _match_case(found, replacement):
    """Carry the matched text's casing onto the replacement where it is
    meaningful. A canonical anonymized form like "McAvery" has internal
    capitals worth preserving, so title-case matches keep the replacement
    as authored rather than being re-cased."""
    letters = [c for c in found if c.isalpha()]
    if len(letters) > 1 and all(c.isupper() for c in letters):
        return replacement.upper()
    if letters and all(c.islower() for c in letters):
        return replacement.lower()
    return replacement


# --------------------------------------------------------------------------
# Safety latches
# --------------------------------------------------------------------------

def _protected_sheet_ids():
    """Every real dev/prod spreadsheet id, from the registry + .env.

    Returns {id: label}. The registry names the .env variables rather than
    holding ids (the repo is public), so this walks every league's `sinks`
    plus the legacy single-league variables.
    """
    names = set(_LEGACY_SHEET_ENV)
    try:
        registry = load_registry()
        for key, league in registry['leagues'].items():
            for sink, value in (league.sinks or {}).items():
                if sink.endswith('_env') and isinstance(value, str):
                    names.add(value)
    except Exception as exc:
        raise RuntimeError(
            f"Could not load the league registry to resolve protected sheet "
            f"ids: {exc}. Refusing to run without latch (a)."
        )
    protected = {}
    for name in sorted(names):
        value = (os.getenv(name) or '').strip()
        if value:
            protected[value] = name
    return protected


def latch_a_not_a_real_sheet(target_id, protected):
    """HARD REFUSE if the target is a real dev or production sheet."""
    if not protected:
        raise SystemExit(
            "REFUSING: no real sheet ids resolved from the registry + .env, "
            "so the 'is this a real sheet?' check would pass vacuously. That "
            "is exactly the case where this tool could overwrite production. "
            "Check that the .env at the repo root is present and readable."
        )
    if target_id in protected:
        raise SystemExit(
            f"REFUSING: target {target_id} is the REAL sheet named by "
            f"{protected[target_id]}. This tool only ever rewrites throwaway "
            f"copies. Make a Drive copy and pass that id instead."
        )


def latch_b_anon_title(spreadsheet, dry_run):
    """Require a throwaway marker (ANON/DEMO) in the title; rename the
    verified-safe copy to add one if absent."""
    title = spreadsheet.title
    present = next((m for m in ANON_TITLE_MARKERS if m in title.upper()), None)
    if present:
        print(f"  latch (b): title already carries {present!r} -- {title!r}")
        return title
    new_title = f'{ANON_TITLE_PREFIX}{title}'
    if dry_run:
        print(f"  latch (b): DRY RUN -- would rename to {new_title!r}")
        return title
    _sheets_call('rename spreadsheet', lambda: spreadsheet.update_title(new_title))
    print(f"  latch (b): title lacked any of {ANON_TITLE_MARKERS}; "
          f"renamed to {new_title!r}")
    return new_title


# --------------------------------------------------------------------------
# Sheets plumbing (auth + the writers' backoff)
# --------------------------------------------------------------------------

def _load_env():
    if load_dotenv is None:
        return
    envf = REPO_ROOT / '.env'
    if envf.exists():
        load_dotenv(envf)


def _run_consent_flow():
    client_path = os.getenv('GOOGLE_OAUTH_CLIENT_PATH')
    if not client_path or not Path(client_path).exists():
        raise RuntimeError(
            'GOOGLE_OAUTH_CLIENT_PATH unset or missing; cannot open the OAuth '
            'consent flow. See the Phase 6.3.1 setup steps.'
        )
    flow = InstalledAppFlow.from_client_secrets_file(client_path, _OAUTH_SCOPES)
    return flow.run_local_server(port=0)


def _client():
    creds = None
    if _TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH), _OAUTH_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                creds = _run_consent_flow()
        else:
            creds = _run_consent_flow()
        with open(_TOKEN_PATH, 'w') as f:
            f.write(creds.to_json())
    return gspread.authorize(creds)


def _is_quota_error(exc):
    message = str(exc).lower()
    return '[429]' in message or 'quota exceeded' in message or 'rate limit' in message


def _sheets_call(label, func, attempts=3, delay_seconds=70):
    """Run a Sheets call, backing off when the API write quota resets.
    Same shape as output/almanac_write._sheets_call."""
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except gspread.exceptions.APIError as exc:
            if attempt == attempts or not _is_quota_error(exc):
                raise
            print(f"  [anon] Sheets quota hit during {label}; "
                  f"retrying in {delay_seconds}s")
            time.sleep(delay_seconds)


def _sheet_id(id_or_url):
    m = re.search(r'/spreadsheets/d/([A-Za-z0-9_-]+)', id_or_url or '')
    return m.group(1) if m else (id_or_url or '').strip()


# --------------------------------------------------------------------------
# A1 helpers
# --------------------------------------------------------------------------

def _col_letters(index):
    """1-based column index -> A1 letters."""
    letters = ''
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _quote_title(title):
    return "'" + title.replace("'", "''") + "'"


def _range_origin(range_str):
    """Start (row, col) of a returned A1 range, 1-based."""
    cell = range_str.split('!')[-1].split(':')[0]
    m = re.match(r'([A-Z]+)(\d+)', cell)
    if not m:
        return 1, 1
    col = 0
    for ch in m.group(1):
        col = col * 26 + (ord(ch) - 64)
    return int(m.group(2)), col


# --------------------------------------------------------------------------
# Read / write / audit
# --------------------------------------------------------------------------

def _read_tabs(spreadsheet, titles, chunk=8):
    """{title: (values_grid, origin_row, origin_col)} with FORMULA render.

    FORMULA is the editable view: formula cells come back as their formula
    text (so HYPERLINK display text is reachable) and everything else as
    its literal value.
    """
    out = {}
    for i in range(0, len(titles), chunk):
        batch = titles[i:i + chunk]
        resp = _sheets_call(
            f'read tabs {i + 1}-{i + len(batch)}',
            lambda b=batch: spreadsheet.values_batch_get(
                [_quote_title(t) for t in b],
                params={'valueRenderOption': 'FORMULA', 'majorDimension': 'ROWS'},
            ),
        )
        for title, vr in zip(batch, resp.get('valueRanges', [])):
            row0, col0 = _range_origin(vr.get('range', 'A1'))
            out[title] = (vr.get('values', []), row0, col0)
    return out


def _plan_edits(grid, row0, col0, replacer):
    """Return (text_edits, formula_edits, n_replacements, n_protected).

    Split by kind so each can be written with the right valueInputOption:
    formulas need USER_ENTERED to stay formulas, while literal text is
    written RAW so nothing gets reinterpreted on the way in.

    A baseball-reference player cell is never written, even if a token
    slips the danger filter -- the last line of defense for player names.
    n_protected counts cells skipped for this reason.
    """
    text_edits, formula_edits, total, protected = [], [], 0, 0
    for r, row in enumerate(grid):
        for c, cell in enumerate(row):
            if not isinstance(cell, str) or not cell:
                continue
            new, n = replacer.sub(cell)
            if not n:
                continue
            if _is_player_cell(cell):
                # Danger filter should already guarantee n == 0 here; the
                # guard makes altering a player name structurally impossible.
                protected += 1
                continue
            total += n
            a1 = f'{_col_letters(col0 + c)}{row0 + r}'
            (formula_edits if cell.startswith('=') else text_edits).append((a1, new))
    return text_edits, formula_edits, total, protected


def _write_edits(spreadsheet, title, edits, value_input_option, chunk=400):
    if not edits:
        return
    q = _quote_title(title)
    for i in range(0, len(edits), chunk):
        batch = edits[i:i + chunk]
        body = {
            'valueInputOption': value_input_option,
            'data': [{'range': f'{q}!{a1}', 'values': [[v]]} for a1, v in batch],
        }
        _sheets_call(
            f'write {len(batch)} cells to {title}',
            lambda b=body: spreadsheet.values_batch_update(b),
        )


def _rename_tabs(spreadsheet, replacer, dry_run):
    """Anonymize worksheet titles.

    Tab titles are visible in the tab bar of every screenshot, and the
    team pages are titled with full owner names. Renaming happens BEFORE
    the cell pass on purpose: Sheets rewrites formula references when a
    sheet is renamed, so doing it first means the formulas we later read
    already point at the new titles and our own edits stay consistent.
    """
    requests, renames = [], []
    worksheets = _sheets_call('list tabs', spreadsheet.worksheets)
    taken = {ws.title for ws in worksheets}
    for ws in worksheets:
        new, n = replacer.sub(ws.title)
        if not n or new == ws.title:
            continue
        candidate, suffix = new, 2
        while candidate in taken:
            candidate, suffix = f'{new} ({suffix})', suffix + 1
        taken.discard(ws.title)
        taken.add(candidate)
        renames.append((ws.title, candidate))
        requests.append({'updateSheetProperties': {
            'properties': {'sheetId': ws.id, 'title': candidate},
            'fields': 'title',
        }})
    if not requests:
        print('  tab titles: nothing to rename')
        return 0
    if dry_run:
        print(f'  tab titles: DRY RUN -- would rename {len(requests)} tab(s)')
        return len(requests)
    _sheets_call(
        f'rename {len(requests)} tabs',
        lambda: spreadsheet.batch_update({'requests': requests}),
    )
    print(f'  tab titles: renamed {len(requests)} tab(s)')
    return len(requests)


def anonymize(spreadsheet, all_pairs, dry_run):
    """Build the player oracle, drop colliding tokens, then rename tabs and
    rewrite every safe cell value and formula. Returns the (player-safe)
    replacer so the audit rescans against exactly what was applied."""
    # The oracle must be built before renaming: renaming rewrites formula
    # references, but the player display text it reads is unaffected, so an
    # up-front read is the clean source.
    titles0 = [ws.title for ws in _sheets_call('list tabs', spreadsheet.worksheets)]
    player_names = build_player_oracle(_read_tabs(spreadsheet, titles0))
    safe, dropped = split_player_safe(all_pairs, player_names)
    _assert_auditable(safe)
    replacer = Replacer(safe)

    print(f'  player-name oracle: {len(player_names)} names from baseball-'
          f'reference links')
    print(f'  mapping forms: kept {len(safe)}, dropped {len(dropped)} that '
          f'collide with a real player name')
    if dropped:
        print(f'    dropped forms left unscrubbed (they occur only inside '
              f'player names or as the kept MATT abbrev): lengths '
              f'{sorted(len(r) for r, _ in dropped)}')

    _rename_tabs(spreadsheet, replacer, dry_run)
    titles = [ws.title for ws in _sheets_call('list tabs', spreadsheet.worksheets)]
    tabs = _read_tabs(spreadsheet, titles)

    grand_total, touched, protected_total = 0, 0, 0
    for title in titles:
        grid, row0, col0 = tabs.get(title, ([], 1, 1))
        text_edits, formula_edits, n, protected = _plan_edits(grid, row0, col0, replacer)
        protected_total += protected
        cells = len(text_edits) + len(formula_edits)
        if not cells:
            continue
        touched += 1
        grand_total += n
        verb = 'would rewrite' if dry_run else 'rewrote'
        print(f'    {title:<34} {verb} {cells:>5} cells ({n} replacements'
              f'{", " + str(len(formula_edits)) + " formulas" if formula_edits else ""})')
        if not dry_run:
            _write_edits(spreadsheet, title, text_edits, 'RAW')
            _write_edits(spreadsheet, title, formula_edits, 'USER_ENTERED')
    print(f'  cells: {grand_total} replacements across {touched} tab(s); '
          f'{protected_total} baseball-reference player cell(s) left untouched')
    return replacer


def audit(spreadsheet, replacer):
    """Re-read everything and rescan against the applied (player-safe) map.

    Returns (owner_residuals, player_hits):
      * owner_residuals -- applied real tokens still present in titles or
        non-player cells. Must be zero: the owner is not hidden otherwise.
      * player_hits -- applied real tokens found INSIDE a baseball-reference
        player cell. Must be zero too: a nonzero here means a kept token
        collides with a player the oracle missed (the write guard still
        spared the cell, but it flags an oracle gap).
    """
    titles = [ws.title for ws in _sheets_call('list tabs', spreadsheet.worksheets)]
    tabs = _read_tabs(spreadsheet, titles)
    owner_residuals = player_hits = 0

    title_hits = len(replacer.find(spreadsheet.title))
    print(f'  {"[spreadsheet title]":<38} {title_hits}')
    owner_residuals += title_hits

    for title in titles:
        grid, _, _ = tabs.get(title, ([], 1, 1))
        hits = len(replacer.find(title))
        for row in grid:
            for cell in row:
                if not isinstance(cell, str) or not cell:
                    continue
                if _is_player_cell(cell):
                    player_hits += len(replacer.find(cell))
                else:
                    hits += len(replacer.find(cell))
        flag = '' if hits == 0 else '   <-- RESIDUAL'
        print(f'  {title:<38} {hits}{flag}')
        owner_residuals += hits
    return owner_residuals, player_hits


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--target', action='append', required=True,
                    help='spreadsheet id or url of a THROWAWAY copy; repeatable')
    ap.add_argument('--dry-run', action='store_true',
                    help='report what would change; write nothing')
    args = ap.parse_args()

    _load_env()

    print('Deriving the mapping from the five seed pairs '
          '(disk = real, HEAD = anonymized)'
          + (' + the private scrub map' if _PRIVATE_MAP.is_file() else '')
          + ' ...')
    pairs, _ = build_mapping()

    protected = _protected_sheet_ids()
    print(f'  latch (a): {len(protected)} real sheet id(s) resolved and protected')

    gc = _client()
    failures = []
    for raw in args.target:
        target_id = _sheet_id(raw)
        print(f'\n=== target {target_id} ===')
        latch_a_not_a_real_sheet(target_id, protected)
        print('  latch (a): not a registered dev/prod sheet -- OK')

        spreadsheet = _sheets_call('open', lambda: gc.open_by_key(target_id))
        latch_b_anon_title(spreadsheet, args.dry_run)

        # The player-safe replacer is derived per target from its own player
        # roster, so a sheet with different players drops the right tokens.
        replacer = anonymize(spreadsheet, pairs, args.dry_run)

        print('  residual audit (every cell + formula + title, per tab):')
        owner_residuals, player_hits = audit(spreadsheet, replacer)
        if owner_residuals or player_hits:
            print(f'  AUDIT FAILED: {owner_residuals} owner residual(s), '
                  f'{player_hits} token(s) inside player cells.')
            failures.append((target_id, owner_residuals, player_hits))
        else:
            print('  AUDIT PASSED: zero owner residuals, zero player cells altered.')

    print()
    if args.dry_run:
        print('DRY RUN -- nothing was written. Counts above reflect the sheet '
              'as it stands, not as it would be.')
        return 0
    if failures:
        for target_id, owner_residuals, player_hits in failures:
            print(f'FAIL {target_id}: {owner_residuals} owner residual(s), '
                  f'{player_hits} player-cell hit(s)')
        return 1
    print(f'All {len(args.target)} target(s) anonymized with zero residuals '
          f'and no player names altered.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
