#!/usr/bin/env python3
"""Anonymize a THROWAWAY Google Sheet copy in place, for screenshots (MLB-100).

The portfolio needs screenshots of the almanac that show the anonymized
owner identities -- the ones a stranger cloning this repo would render --
not the real league. Anonymization is a repo property, not a run mode:
the anonymized names live only in the published demo fixture, while
Kyle's working tree carries the real data (skip-worktree, MLB-95/114).
So every render to date is real-name.

The cheap way to get anonymized screenshots is not to swap seeds and
rebuild the warehouse (that recipe exists as the fallback, and it mutates
shared state). It is to copy the dev workbook in Drive and rewrite the
COPY. Nothing in the repo or the warehouse is touched; the worst case is
deleting a bad copy and making another.

    python tools/anonymize_sheet_copy.py --league <key> --target <id-or-url> [--target ...]
    python tools/anonymize_sheet_copy.py --league <key> --target <id> --dry-run

WHERE THE MAPPING COMES FROM
    Derived at runtime, never hardcoded -- this file has to be safe to
    commit to a public repo. The two sides live in two DIFFERENT
    directories, and that separation is the whole design (MLB-114):

      * REAL side -- `dbt_league/league_config/<f>.csv` ON DISK. Those 13
        files are skip-worktree, so the working copy is the real league
        data while the COMMITTED bytes are a blank template.
      * TWIN side -- `demo/league_config/<f>.csv` AS COMMITTED. The demo
        fixture is the anonymized twin a stranger's clone actually
        renders; it is tracked normally, so HEAD and disk agree. Reading
        HEAD anyway pins the mapping to what is published rather than to
        a locally-dirty fixture.

    This tool used to read both sides out of `dbt_league/seeds/`, where
    disk was real and HEAD was the twin. The 114 split moved the real
    side to league_config/ and the twin to demo/, so HEAD of the old path
    became a bare header row and the tool raised before it could map
    anything (MLB-202).

    ROWS ARE PAIRED BY STABLE KEY, NEVER BY POSITION. Row position is not
    a key: it survives only until someone sorts a file or the fixture
    regenerates in a different order, and a silently mis-paired mapping
    produces a sheet that looks anonymized and is not. Each file declares
    the columns anonymization provably does not touch, and the join runs
    on those:

      * cbs_franchises      (league_key, franchise_id) -- unique.
      * cbs_team_owners     (league_key, franchise_id) -- NOT unique;
                            co-owned franchises carry several rows.
      * team_owner_by_year  (league_key, season_year, franchise_id) --
                            also not unique, same reason.
      * owner_nicknames     owner_id, through the crosswalk below.
      * owner_alias         canonical_owner_id, through the crosswalk.

    Where a key names several rows, the group is resolved by identity
    evidence rather than by the order the rows happen to sit in -- see
    _build_owner_crosswalk. Nothing is ever guessed: a group that will
    not resolve raises.

    The owner id spaces differ by platform, which is the subtlety:
      * ESPN owners carry a braced SWID GUID that anonymization retains
        deliberately (identity resolution depends on it), so those ids
        pair to themselves.
      * CBS owners carry a name-DERIVED slug, so the scrub had to rewrite
        the id too. Those are bridged through cbs_team_owners' stable
        (league_key, franchise_id) key, and the co-owner groups it cannot
        separate are settled by the private scrub map's real -> fake name
        record.

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
    which is how the almanac renders players -- and any SINGLE-TOKEN
    mapping pair whose real form falls on a word boundary inside a player
    name is DROPPED. An owner token that coincides with a player (a bare
    first name, mostly) is simply not scrubbed; the owner is still hidden
    by their full name, the "Last, First" form, the team name and -- since
    MLB-323 -- the co-owner label, each mapped whole and never dropped. As
    belt-and-suspenders, a cell carrying a baseball-reference link is
    never written at all. The count of dropped tokens is printed, never
    silent, and the inventory audit catches any real form a drop leaves
    visible.

CO-OWNER LABELS ARE MAPPED WHOLE (MLB-323)
    A co-owned CBS franchise-season renders as its owners' first names,
    "A, B" in the warehouse and "A & B" on most tabs. No config column
    holds that string, so the label used to be reached one first name at a
    time -- and with the colliding name dropped, half a real label stayed
    on the page. Labels are now derived on both sides exactly as the
    warehouse builds them and paired by franchise-season, "A & B" ->
    "A' & B'" in both spellings; longest-first replaces the whole label in
    one step, before any name inside it could match.

ABBREVIATIONS ARE CASE-SENSITIVE (MLB-323)
    Team abbreviations render in capitals and are matched that way. Matched
    case-insensitively they collided with names: "CAL" was dropped because
    Cal Raleigh is a player, the CBS franchise abbreviated MATT (team name
    "14-30-8-24-5-15-13-20", kept) collapsed into the owner first name
    Matt and was dropped with it -- though its twin abbreviation is now
    CODE -- and two-letter abbreviations (BP, HH) were skipped as too
    short. Case-sensitive and word-bounded, two letters are safe and a
    player first name cannot veto an abbreviation. Pairs carry their
    league, and --league keeps the target's own: where the target league's
    twin keeps an abbreviation unchanged (CBS's HH), the other league's
    pair for the same letters does not apply.

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

THE TOOL HIDES PEOPLE, NOT CONTENT (Kyle's ruling, 2026-10-06)
    It hides owners and co-owners: their full names, and their first names
    wherever a first name stands as an owner label. It never hides MLB
    players, and it never hides fantasy team names or abbreviations --
    public content, the 2026-08-10 ruling in tools/check_pii.py,
    reaffirmed. A team name or abbreviation publishes verbatim unless the
    scrub map (or the demo fixture's twin) has a row for it, and a visible
    one is reported as information, never as a failure. The one exception:
    a team name carrying a real owner's first AND last name is an owner
    form -- a residual and a refusal; one carrying only a last name is
    reported. When an abbreviation row applies, every team name kept
    verbatim is an identity pair in the same single pass, so "I Paid BP
    Writers for Advice" and "Intentional Walk to the Bar" stay as written
    while a standalone BP or WALK still maps; the audit counts an
    abbreviation inside a kept name as excused for that reason.

NO TWIN, NO RUN (MLB-323)
    Before the first write (latch (b)'s rename), check_twins refuses if an
    OWNER identity the book renders has no twin: an owner full name in the
    inventory, or a team name (config, roster or the target's team pages,
    read by the renderer's own A1 "{team} ({abbrev})" header) carrying an
    owner's first and last name. Twins are reviewed rows Kyle adds; the
    tool never invents one. Owners are refused by digest, never by name.
    A team or abbreviation with no twin never refuses; it is listed.

ACCEPTANCE (MLB-323)
    The audit does NOT rescan the tokens the mapping applied. That was the
    old design, and it could only ever confirm the scrub did what it set
    out to do: a form dropped as a player collision, or never mapped at
    all, is invisible to it by construction (MLB-321 found both on the dev
    books). Instead build_form_inventory enumerates, from the REAL side
    alone, every form a reader could see -- co-owner labels in both
    spellings the reader meets, owner full and first names, team names,
    team abbreviations (matched case-sensitively), owner ids, contact
    details, the private map's own forms, and the owner labels of the
    platform roster (the shared export: members who left before the
    config knew them). The audit re-reads every cell, formula and tab
    title of the copy and searches it for all of them, printing residuals
    per class.

    A hit is a residual unless the positive player check proves it sits
    inside an MLB player's name, against the MLB spine on disk rather than
    anything the sheet says about itself (proven_players). The exclusions
    print by player, so the report says what it let through and why. A
    form is set aside only when the published twin keeps it unchanged --
    those print too. Zero residuals is the pass condition; anything else
    exits non-zero.

    PLAYERS ARE NEVER ANONYMIZED (Kyle's ruling, 2026-10-06). The tool
    hides the PEOPLE in a league; MLB players are public figures. So player
    names are matched generously (PlayerSpine): with generational suffixes
    and middle initials folded, under the renderer's short form ('M
    Trout'), with formal and short given names equated (Michael / Mike),
    and with one edit of surname drift. An owner FIRST name is excused when
    it sits inside such a whole player name anywhere in a cell -- prose
    notes and runner-up strings included (whole_player_name). Never excused:
    a first name outside a whole player name ("Chris", "Chris & Matt"),
    because that is the shape a co-owner label leaks in. The spine is the
    Stats API data under data/mlb_stats/season plus a supplement grown from
    the Stats API's per-season player lists (--grow-spine FIRST-LAST),
    which extract/mlb_load.py never loads.

    --league names the book being copied. Forms carry the league whose
    config they came from, and the inventory is that league's plus the
    private map's (which has no league column), so a first name in one
    league is not a finding in the other's team names.

AFTER THE RUN: THE COPY STILL REMEMBERS (MLB-323)
    A zero-residual audit is a claim about the cells, not the file. Drive's
    version history still holds the real render the scrub replaced (Study
    Material 08-06: 4 revisions on a scrubbed book against 1 on a fresh
    copy of it). So a clean run ends by printing three steps the operator
    takes by hand: make a Drive copy of the scrubbed copy (a copy starts
    with no revisions), share THAT copy as anyone-with-the-link reader, and
    delete the scrubbed one. A failed run says the copy is unpublishable.

Auth: the same cached user token as every other sheets writer
(output/.sheets_oauth_token.json); no new consent flow, no Drive scope --
which is why the steps above are printed rather than performed.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from itertools import product
from pathlib import Path
from typing import NamedTuple, Optional

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

# The five twinned identity files (MLB-95), re-rooted by the MLB-114
# split: real data on disk under league_config/, published twins under
# demo/. Named by basename because the two sides differ only by root.
_REAL_DIR = 'dbt_league/league_config'
_TWIN_DIR = 'demo/league_config'

SEED_FRANCHISES = 'cbs_franchises.csv'
SEED_TEAM_OWNERS = 'cbs_team_owners.csv'
SEED_ALIAS = 'owner_alias.csv'
SEED_NICKNAMES = 'owner_nicknames.csv'
SEED_BY_YEAR = 'team_owner_by_year.csv'
# Not twinned (it carries no names today), but a canonical name or abbrev
# written into it later is a form a reader would see.
LINEAGE = 'franchise_lineage.csv'

ALL_SEEDS = (
    SEED_FRANCHISES, SEED_TEAM_OWNERS, SEED_ALIAS, SEED_NICKNAMES, SEED_BY_YEAR,
)

# The columns anonymization provably does not touch, per file. These are
# the join keys; see the module docstring for why position is not one.
SEED_KEYS = {
    SEED_FRANCHISES: ('league_key', 'franchise_id'),
    SEED_TEAM_OWNERS: ('league_key', 'franchise_id'),
    SEED_ALIAS: ('league_key',),
    SEED_NICKNAMES: ('owner_id',),
    SEED_BY_YEAR: ('league_key', 'season_year', 'franchise_id'),
}

# A braced SWID GUID. ESPN owner ids are retained verbatim through
# anonymization on purpose (CLAUDE.md: name-anonymized, NOT identifier-
# anonymized), so an id of this shape pairs to itself. Nothing else is
# allowed to self-pair: a name-derived CBS slug that happened to equal
# some twin slug would otherwise map a real id to itself and, being an
# identity pair, get dropped from the mapping entirely -- a silent leak.
_STABLE_ID = re.compile(r'^\{[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}\}$')

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

# Same idea for a legacy owner slug that has no twin to become: a
# name-derived id with no counterpart is redacted, not passed through.
_ALIAS_REDACTION = 'cbs-redacted-owner'

# A token this short cannot be matched safely even on word boundaries.
# Nothing in the current seeds trips it; if that ever changes the skip is
# printed, never silent.
_MIN_TOKEN_LEN = 3

# The universal MLB spine on disk (MLB-70): one Stats API document per
# player, each split naming the player as {id, fullName}. The positive
# player check reads it as the INDEPENDENT record of who the players are;
# the sheet is never allowed to vouch for its own cells. Read-only, local,
# no warehouse connection.
_SPINE_DIR = REPO_ROOT / 'data' / 'mlb_stats' / 'season'

# Players the spine directory lacks, grown from the same public source the
# spine and the CBS crosswalk come from: the Stats API's per-season player
# list. Beside the spine rather than in it, because extract/mlb_load.py
# loads every season/*.json into RAW; this file feeds the anonymizer only.
# Written by `--grow-spine`.
_SPINE_SUPPLEMENT = REPO_ROOT / 'data' / 'mlb_stats' / 'people_supplement.json'
_STATS_API = 'https://statsapi.mlb.com/api/v1'

# The shared almanac export (MLB-301): per league, every team the platform
# ever served, with its owner label. Platform-served owners never land in
# league_config (an ESPN member who left the league is only in the
# warehouse), so this is where the inventory finds them. Local and
# gitignored like the rest of data/.
_SHARED_EXPORTS = REPO_ROOT / 'data' / 'exports' / 'shared'


# --------------------------------------------------------------------------
# Seed pair loading
# --------------------------------------------------------------------------

def _git_show(path):
    """The COMMITTED bytes of a repo-relative path.

    Used for the TWIN side only. The demo fixture is tracked normally, so
    this equals the working copy in the healthy case; reading HEAD anyway
    pins the mapping to what is actually published.
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


def _load_pair(name):
    """Return (real_rows, twin_rows) for one identity file.

    Real = the maintainer's working copy under league_config/ (skip-
    worktree; blank at HEAD). Twin = the committed demo fixture. Row
    COUNTS are not required to match -- the join is by key -- but a twin
    that came back empty means the roots are wrong again, which is worth
    saying in those words rather than as a confusing key-miss later.
    """
    disk = REPO_ROOT / _REAL_DIR / name
    if not disk.is_file():
        raise RuntimeError(
            f"{_REAL_DIR}/{name} not found on disk at {disk}. The real side "
            f"of the mapping is the maintainer's working copy; a clone that "
            f"only has the blank template cannot anonymize a sheet."
        )
    real = _parse_csv(disk.read_text(encoding='utf-8-sig'))
    twin = _parse_csv(_git_show(f'{_TWIN_DIR}/{name}'))
    if real and not twin:
        raise RuntimeError(
            f"{_TWIN_DIR}/{name} is empty at HEAD but {_REAL_DIR}/{name} has "
            f"{len(real)} rows. The published twin is missing, so no mapping "
            f"can be derived -- check that the demo fixture is committed."
        )
    return real, twin


def _ref(value):
    """A short, stable, NON-reversible handle for a real identifier.

    Diagnostics in this file are read by a human and may be pasted into a
    ticket, so they must never carry a real identifier -- and a CBS owner
    id is a name-derived slug, which means printing any slice of one
    prints part of a surname. A digest keeps the useful property (the same
    owner reads the same across two messages) and drops the leak.
    """
    import hashlib
    return hashlib.sha1((value or '').encode('utf-8')).hexdigest()[:8]


def _key_of(row, cols):
    return tuple((row.get(c) or '').strip() for c in cols)


def _group_by_key(rows, cols):
    grouped = defaultdict(list)
    for row in rows:
        grouped[_key_of(row, cols)].append(row)
    return grouped


def _pair_by_key(name, real, twin, resolver=None):
    """Align real rows to twin rows on the file's stable key.

    Returns a list of (real_row, twin_row). Groups of one pair directly.
    Larger groups -- co-owned franchises, mostly -- are handed to
    `resolver`, which pairs them on identity evidence; without one, or if
    it cannot separate them, this raises rather than falling back to row
    order. That fallback is the bug this function exists to remove.
    """
    cols = SEED_KEYS[name]
    gr, gt = _group_by_key(real, cols), _group_by_key(twin, cols)

    missing = sorted(set(gr) - set(gt))
    if missing:
        raise RuntimeError(
            f"{_REAL_DIR}/{name}: {len(missing)} key(s) present in the real "
            f"file have no row in {_TWIN_DIR}/{name} (first: "
            f"{'/'.join(missing[0])}). The twin is out of date with the real "
            f"file; refusing to derive a partial mapping."
        )

    paired = []
    for key, r_rows in gr.items():
        t_rows = gt[key]
        if len(r_rows) != len(t_rows):
            raise RuntimeError(
                f"{name}: key {'/'.join(key)} has {len(r_rows)} real row(s) "
                f"but {len(t_rows)} twin row(s). The pair cannot be aligned."
            )
        if len(r_rows) == 1:
            paired.append((r_rows[0], t_rows[0]))
            continue
        if resolver is None:
            raise RuntimeError(
                f"{name}: key {'/'.join(key)} names {len(r_rows)} rows and "
                f"this file has no way to tell them apart. Row order is not "
                f"a key; refusing to guess which row is whose."
            )
        paired.extend(resolver(name, key, r_rows, t_rows))
    return paired


def _assert_stable(name, paired, cols):
    """Fail unless the named columns agree within every paired row.

    The key columns agree by construction; this checks the OTHER columns
    that anonymization must not touch (seasons, league keys carried
    alongside a different key). A drift here means the twin is not a twin.
    """
    for r, a in paired:
        for c in cols:
            if (r.get(c) or '') != (a.get(c) or ''):
                raise RuntimeError(
                    f"{name}: column {c!r} differs between the real file and "
                    f"the published twin for key "
                    f"{'/'.join(_key_of(r, SEED_KEYS[name]))}, but it is "
                    f"supposed to be stable. Refusing to derive a mapping "
                    f"from an unreconciled pair."
                )


# --------------------------------------------------------------------------
# Co-owner labels, in the renderer's own format
# --------------------------------------------------------------------------
#
# A co-owned CBS franchise-season is labelled by its owners' FIRST NAMES,
# and no config column holds that string -- the warehouse builds it and the
# renderer rewrites it. Reproduced here step for step, because a label
# that is guessed rather than derived is a label the audit cannot find:
#
#   int_cbs__team_owner_season  owner_name -> owner_id: a current owner's
#       display name keeps its seeded id, anyone else is slugged from the
#       name, then owner_alias folds variants onto a canonical id.
#   dim_owner                   first_name = the first space-separated
#       token of that owner's most recent owner_name.
#   dim_team_owner (CBS branch) two or more owners -> first names joined
#       with ', ', ordered by first name.
#   output/ (cbs_almanac_sheets, records_book_data) rewrite ', ' to ' & '
#       for display; surfaces that read owner_display directly keep ', '.
#
# So both spellings reach a reader, and both are forms.

CO_OWNER_JOIN = ', '
CO_OWNER_RENDER = ' & '


def _read_real(name):
    """Rows of the maintainer's real league_config file (disk); [] when
    the file does not exist."""
    path = REPO_ROOT / _REAL_DIR / name
    return _parse_csv(path.read_text(encoding='utf-8-sig')) if path.is_file() else []


def _read_twin(name):
    """Rows of the published twin (HEAD of demo/league_config)."""
    return _parse_csv(_git_show(f'{_TWIN_DIR}/{name}'))


def _slug_owner_name(name):
    """The id int_cbs__team_owner_season mints for a name it cannot match
    to a current owner (build_continuity_sheet._slug's rule)."""
    return 'cbs-' + re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')


def resolve_owner_years(load):
    """[(league_key, season_year, franchise_id, owner_id, owner_name)] for
    every named row of team_owner_by_year, resolved to canonical owner ids
    the way int_cbs__team_owner_season does it. `load` reads one config
    file by name -- the real side or the twin side."""
    nick = {(r.get('owner_id') or '').strip(): r for r in load(SEED_NICKNAMES)}
    current = {}
    for r in load(SEED_TEAM_OWNERS):
        oid = (r.get('owner_id') or '').strip()
        n = nick.get(oid) or {}
        display = (n.get('preferred_name') or '').strip() or _owner_name(n)
        if oid and display:
            current.setdefault(display.lower(), oid)
    alias = {(r.get('owner_id') or '').strip(): (r.get('canonical_owner_id') or '').strip()
             for r in load(SEED_ALIAS)}
    out = []
    for r in load(SEED_BY_YEAR):
        name = (r.get('owner_name') or '').strip()
        if not name:
            continue
        raw = current.get(name.lower()) or _slug_owner_name(name)
        out.append(((r.get('league_key') or '').strip(),
                     (r.get('season_year') or '').strip(),
                     (r.get('franchise_id') or '').strip(),
                     alias.get(raw) or raw, name))
    return out


def _platform_league(platform):
    """The registry's one league on `platform`, or None when there is not
    exactly one. A form that cannot be placed in a league stays unscoped,
    which is the strict side: unscoped forms are audited on every target."""
    keys = [k for k, lg in load_registry()['leagues'].items()
            if getattr(lg, 'platform', None) == platform]
    return keys[0] if len(keys) == 1 else None


def owner_leagues(load):
    """owner_id -> league_key for every owner the config can place.

    owner_nicknames carries no league column. A CBS owner is placed by the
    franchise rows that name it; an ESPN owner is a retained member GUID,
    which only the ESPN platform mints.
    """
    out = {}
    for r in load(SEED_TEAM_OWNERS):
        oid, lg = (r.get('owner_id') or '').strip(), (r.get('league_key') or '').strip()
        if oid and lg:
            out.setdefault(oid, lg)
    for lg, _, _, oid, _ in resolve_owner_years(load):
        if lg:
            out.setdefault(oid, lg)
    for r in load(SEED_ALIAS):
        lg = (r.get('league_key') or '').strip()
        for col in ('owner_id', 'canonical_owner_id'):
            oid = (r.get(col) or '').strip()
            if oid and lg:
                out.setdefault(oid, lg)
    espn = _platform_league('espn')
    for r in load(SEED_NICKNAMES):
        oid = (r.get('owner_id') or '').strip()
        if espn and _STABLE_ID.match(oid):
            out.setdefault(oid, espn)
    return out


def co_owner_labels(load):
    """{(league_key, season_year, franchise_id): [label, ...]} for every
    co-owned franchise-season, in the warehouse's ', ' form.

    Usually one label per key. dim_owner takes the first name from an
    owner's most recent owner_name; if that season carries two spellings
    with different first tokens the warehouse's pick is arbitrary, so every
    candidate label is returned rather than guessing which one renders.
    """
    resolved = resolve_owner_years(load)
    latest = {}
    for _, season, _, oid, name in resolved:
        year = int(season) if season.isdigit() else -1
        prior = latest.get(oid)
        if prior is None or year > prior[0]:
            latest[oid] = (year, {name})
        elif year == prior[0]:
            prior[1].add(name)
    first = {oid: sorted({n.split(' ')[0] for n in names})
             for oid, (_, names) in latest.items()}

    owners = defaultdict(set)
    for league, season, fid, oid, _ in resolved:
        owners[(league, season, fid)].add(oid)

    # Python's str ordering is codepoint order, which is the binary
    # collation both warehouse engines use for the LISTAGG ORDER BY.
    return {key: sorted({CO_OWNER_JOIN.join(sorted(combo))
                         for combo in product(*(first[o] for o in oids))})
            for key, oids in owners.items() if len(oids) > 1}


def co_owner_label_pairs(real_load=None, twin_load=None):
    """[(real label, twin label, league_key)] in BOTH spellings a reader
    meets -- the warehouse's ', ' and the renderer's ' & '.

    Paired by (league, season, franchise), never by position or by
    re-pairing the first names one at a time: the twin's label is what
    the twin's own config renders for the same franchise-season, ordered
    by the twin's first names. A key co-owned on one side and not the
    other raises; the tool does not invent a label.
    """
    real = co_owner_labels(real_load or _read_real)
    twin = co_owner_labels(twin_load or _read_twin)
    missing = sorted(set(real) - set(twin))
    if missing:
        raise RuntimeError(
            f"{len(missing)} co-owned franchise-season(s) in {_REAL_DIR} are "
            f"not co-owned in the published twin (first: {'/'.join(missing[0])}). "
            f"Their labels have no twin to become; refusing to guess one."
        )
    out = []
    for key in sorted(real):
        anon = twin[key][0]
        for label in real[key]:
            out.append((label, anon, key[0]))
            out.append((label.replace(CO_OWNER_JOIN, CO_OWNER_RENDER),
                        anon.replace(CO_OWNER_JOIN, CO_OWNER_RENDER), key[0]))
    return out


# --------------------------------------------------------------------------
# Mapping derivation
# --------------------------------------------------------------------------

def _owner_name(row):
    """The 'First Last' form a nickname row carries, for the name bridge."""
    first = (row.get('first_name') or '').strip()
    last = (row.get('last_name') or '').strip()
    return f'{first} {last}'.strip()


def _build_owner_crosswalk(verbose=True):
    """real owner_id -> twin owner_id, for every owner in the league.

    Four sources, applied in descending order of authority. Each one must
    agree with whatever the earlier ones already established; a
    contradiction raises rather than picking a winner.

      (a) Retained GUIDs. ESPN owner ids survive anonymization verbatim by
          design, so they pair to themselves. Only ids of that exact shape
          are allowed to self-pair -- see _STABLE_ID.
      (b) Unambiguous franchise keys. A (league_key, franchise_id) group
          holding one row per side pairs those two rows outright.
      (c) The private scrub map. Co-owned franchises put several owners
          under one key, and the map's real -> fake name record is the
          authority on which is which. Maintainer-only and gitignored,
          exactly like the real seeds this tool already requires.
      (d) Elimination. If a group has one real and one twin id left
          unclaimed after (a)-(c), that pairing is forced -- determined by
          what is left over, not by where the rows sit.

    Anything still unresolved raises. A missing owner would leave a real
    name unmapped, and an unmapped real name is the leak this whole file
    exists to prevent.
    """
    tow_real, tow_twin = _load_pair(SEED_TEAM_OWNERS)
    nick_real, nick_twin = _load_pair(SEED_NICKNAMES)

    twin_ids = {(r.get('owner_id') or '').strip() for r in nick_twin}
    twin_ids |= {(r.get('owner_id') or '').strip() for r in tow_twin}

    crosswalk, sources = {}, Counter()

    def record(rid, aid, source):
        if not rid or not aid:
            return
        prior = crosswalk.get(rid)
        if prior is not None:
            if prior != aid:
                raise RuntimeError(
                    f"owner {_ref(rid)} pairs to two different twin ids "
                    f"depending on the source ({source} disagrees with what "
                    f"was already established). The seeds disagree about who "
                    f"is who; refusing to guess."
                )
            return
        crosswalk[rid] = aid
        sources[source] += 1

    # (a) Retained GUIDs pair to themselves.
    for row in list(nick_real) + list(tow_real):
        rid = (row.get('owner_id') or '').strip()
        if not _STABLE_ID.match(rid):
            continue
        if rid not in twin_ids:
            raise RuntimeError(
                f"owner {_ref(rid)} is a retained GUID on the real side "
                f"but appears nowhere on the twin side. The published fixture "
                f"is out of date with the real league config."
            )
        record(rid, rid, 'retained GUID')

    # (b) Franchise keys that name exactly one row per side.
    cols = SEED_KEYS[SEED_TEAM_OWNERS]
    gr, gt = _group_by_key(tow_real, cols), _group_by_key(tow_twin, cols)
    missing = sorted(set(gr) - set(gt))
    if missing:
        raise RuntimeError(
            f"{SEED_TEAM_OWNERS}: {len(missing)} franchise key(s) have no row "
            f"in the published twin (first: {'/'.join(missing[0])})."
        )
    shared = []
    for key, r_rows in gr.items():
        t_rows = gt[key]
        if len(r_rows) != len(t_rows):
            raise RuntimeError(
                f"{SEED_TEAM_OWNERS}: franchise {'/'.join(key)} has "
                f"{len(r_rows)} real owner row(s) but {len(t_rows)} twin "
                f"row(s); the group cannot be aligned."
            )
        if len(r_rows) == 1:
            record((r_rows[0].get('owner_id') or '').strip(),
                   (t_rows[0].get('owner_id') or '').strip(), 'franchise key')
        else:
            shared.append((key, r_rows, t_rows))

    # (c)/(d) Co-owner groups: name evidence first, then elimination.
    private = {real.lower(): fake for real, fake in _load_private_map()}
    real_name_by_id = {(r.get('owner_id') or '').strip(): _owner_name(r)
                       for r in nick_real}
    twin_id_by_name = {}
    for r in nick_twin:
        twin_id_by_name.setdefault(_owner_name(r).lower(),
                                   (r.get('owner_id') or '').strip())

    def _bridge(rid):
        """Twin owner id for a real id, via real name -> fake name."""
        fake = private.get((real_name_by_id.get(rid) or '').strip().lower())
        return twin_id_by_name.get((fake or '').strip().lower())

    unresolved = []
    for key, r_rows, t_rows in shared:
        t_ids = [(x.get('owner_id') or '').strip() for x in t_rows]
        claimed, pending = set(), []
        for r in r_rows:
            rid = (r.get('owner_id') or '').strip()
            if rid in crosswalk:
                claimed.add(crosswalk[rid])
                continue
            aid = _bridge(rid)
            if aid and aid in t_ids and aid not in claimed:
                record(rid, aid, 'private map')
                claimed.add(aid)
            else:
                pending.append(rid)
        left = [a for a in t_ids if a not in claimed]
        if len(pending) == 1 and len(left) == 1:
            record(pending[0], left[0], 'elimination')
        elif pending:
            unresolved.append((key, len(pending)))

    # Owners who appear in nicknames but never in cbs_team_owners still
    # need a pairing -- the alias file reaches them by canonical id.
    for r in nick_real:
        rid = (r.get('owner_id') or '').strip()
        if not rid or rid in crosswalk:
            continue
        aid = _bridge(rid)
        if aid:
            record(rid, aid, 'private map')

    if unresolved:
        detail = ', '.join(f"{'/'.join(k)} ({n})" for k, n in unresolved[:4])
        raise RuntimeError(
            f"{len(unresolved)} co-owned franchise group(s) could not be "
            f"separated by name evidence or elimination: {detail}. The "
            f"private scrub map (archives/anonymization/name_map.csv) is what "
            f"resolves these; without it the pairing would be a guess, and a "
            f"mis-paired owner is a mapping this tool must not produce."
        )

    unmapped = sorted(
        rid for rid in real_name_by_id
        if rid and rid not in crosswalk
    )
    if unmapped:
        raise RuntimeError(
            f"{len(unmapped)} owner(s) in {_REAL_DIR}/{SEED_NICKNAMES} have no "
            f"twin. Their real names would pass through a screenshot "
            f"unreplaced; refusing to run with an incomplete mapping."
        )

    if verbose:
        summary = ' + '.join(f'{n} {src}' for src, n in sorted(sources.items()))
        print(f"  owner crosswalk: {summary} = {len(crosswalk)} owners")
    return crosswalk


def build_mapping(verbose=True, league=None):
    """Derive real -> anon for every name form the sheets can render.

    Returns (pairs, report) where pairs is a list of Pair(real, anon,
    exact) and report carries the counts and every judgement call made, so
    the run log shows what was decided rather than just what was replaced.

    `league` scopes the result to one book: a pair derived from another
    league's config is left out, and a form the target league's twin KEEPS
    unchanged wins over a pair for the same string from elsewhere -- the
    private map's ESPN "HH" row must not rename the CBS team whose twin
    abbreviation is also HH. None keeps every league (the mapping's shape
    before MLB-323).
    """
    crosswalk = _build_owner_crosswalk(verbose=verbose)
    leagues = owner_leagues(_read_real)
    tagged = []         # (real, anon, source, league, exact)
    report = {'sources': Counter(), 'ambiguous': [], 'skipped_short': [],
              'unpaired_alias': [], 'kept': [], 'kept_over_pair': []}

    def add(real_value, anon_value, source, league=None, exact=False):
        real_value = (real_value or '').strip()
        anon_value = (anon_value or '').strip()
        # Identity pairs carry no secret and MUST NOT enter the audit set:
        # a value that is legitimately unchanged would otherwise be
        # reported as a residual forever, making zero unreachable. They are
        # recorded, with the league whose twin keeps them, so the form
        # inventory can say what it set aside and why.
        if real_value and real_value == anon_value:
            report['kept'].append((real_value, league, source))
            return
        if not real_value or not anon_value:
            return
        # An abbreviation is matched case-sensitively and on word
        # boundaries, which makes two characters (HH, BP) safe; a name is
        # matched case-insensitively and needs three.
        if len(real_value) < (_MIN_FORM_LEN if exact else _MIN_TOKEN_LEN):
            report['skipped_short'].append((real_value, source))
            return
        tagged.append((real_value, anon_value, source, league, exact))

    # Real owner-name form -> twin form. This is what separates co-owner
    # rows in the by-year file further down, where the stable key alone
    # names several rows.
    #
    # Seeded from the private scrub map because the by-year file reaches
    # back further than the nickname table does: most of its distinct name
    # forms belong to owners who left the league years ago and hold no
    # nickname row at all. The scrub map is the only record that covers
    # them. Nickname-derived pairs are written over the top, since those
    # are derived from the published twin itself.
    owner_name_map = {real.lower(): anon for real, anon in _load_private_map()}

    def note_name(real_value, anon_value):
        real_value = (real_value or '').strip()
        anon_value = (anon_value or '').strip()
        if real_value and anon_value:
            owner_name_map[real_value.lower()] = anon_value

    # owner_nicknames: the primary name table. first/last are single-token
    # columns and become token-level replacements (this is what reaches a
    # surname buried inside a team name); preferred_name is a whole form.
    #
    # Paired through the crosswalk, not by row order: for ESPN owners the
    # id is stable and joins directly, and for CBS owners the crosswalk is
    # what carries the rewritten slug across.
    nick_real, nick_twin = _load_pair(SEED_NICKNAMES)
    twin_nick_by_id = {(x.get('owner_id') or '').strip(): x for x in nick_twin}
    nick_pairs = []
    for r in nick_real:
        rid = (r.get('owner_id') or '').strip()
        aid = crosswalk.get(rid)
        a = twin_nick_by_id.get(aid) if aid else None
        if a is None:
            raise RuntimeError(
                f"{SEED_NICKNAMES}: owner {_ref(rid)} has no row in the "
                f"published twin. The mapping would be incomplete."
            )
        nick_pairs.append((r, a))

    for r, a in nick_pairs:
        lg = leagues.get((r.get('owner_id') or '').strip())
        add(r.get('first_name'), a.get('first_name'), 'nicknames.first_name', lg)
        add(r.get('last_name'), a.get('last_name'), 'nicknames.last_name', lg)
        add(r.get('preferred_name'), a.get('preferred_name'), 'nicknames.preferred_name', lg)
        # Composite forms the sheets build but no single column holds.
        first, last = (r.get('first_name') or '').strip(), (r.get('last_name') or '').strip()
        afirst, alast = (a.get('first_name') or '').strip(), (a.get('last_name') or '').strip()
        if first and last and afirst and alast:
            add(f'{first} {last}', f'{afirst} {alast}', 'nicknames.full_name', lg)
            add(f'{last}, {first}', f'{alast}, {afirst}', 'nicknames.last_first', lg)
            add(f'{first[0]}. {last}', f'{afirst[0]}. {alast}', 'nicknames.initial_last', lg)
            note_name(f'{first} {last}', f'{afirst} {alast}')
            note_name(f'{last}, {first}', f'{alast}, {afirst}')
        note_name(r.get('preferred_name'), a.get('preferred_name'))
        # Contact columns have no anonymized counterpart (dropped from the
        # committed twin) -- redact rather than map.
        for col, placeholder in _REDACTIONS.items():
            raw = (r.get(col) or '').strip()
            if raw:
                add(raw, placeholder, f'nicknames.{col}', lg)
                digits = re.sub(r'\D', '', raw)
                if col == 'phone_number' and len(digits) >= 7 and digits != raw:
                    add(digits, re.sub(r'\D', '', placeholder), 'nicknames.phone_digits', lg)

    # owner_alias: legacy/duplicate owner ids folded into a canonical one.
    # Its own owner_id column is a name-DERIVED variant slug that the scrub
    # rewrote, so it is not a key. The canonical id IS reachable through the
    # crosswalk, and one alias row per canonical owner makes that a join.
    alias_real, alias_twin = _load_pair(SEED_ALIAS)
    twin_alias_by_canon = {(x.get('canonical_owner_id') or '').strip(): x
                           for x in alias_twin}
    alias_paired, alias_pending = [], []
    for r in alias_real:
        canon = (r.get('canonical_owner_id') or '').strip()
        a = twin_alias_by_canon.get(crosswalk.get(canon, ''))
        if a is None:
            alias_pending.append(r)
        else:
            alias_paired.append((r, a))

    # A canonical id the crosswalk never saw belongs to a legacy owner who
    # holds no franchise row and no nickname row -- there is nothing left
    # to key on. If exactly one twin row is also unclaimed, the pairing is
    # forced by elimination, which is evidence and not row order.
    claimed = {id(a) for _, a in alias_paired}
    alias_left = [x for x in alias_twin if id(x) not in claimed]
    if len(alias_pending) == 1 and len(alias_left) == 1:
        alias_paired.append((alias_pending.pop(), alias_left.pop()))

    for r, a in alias_paired:
        _assert_stable(SEED_ALIAS, [(r, a)], ['league_key'])
        rid, aid = (r.get('owner_id') or '').strip(), (a.get('owner_id') or '').strip()
        if rid and aid:
            if crosswalk.get(rid, aid) != aid:
                raise RuntimeError(
                    f"{SEED_ALIAS}: variant id {_ref(rid)} pairs to a "
                    f"different twin id than {SEED_TEAM_OWNERS} does."
                )
            crosswalk.setdefault(rid, aid)
        add(r.get('preferred_name'), a.get('preferred_name'), 'alias.preferred_name',
            (r.get('league_key') or '').strip() or None)

    # Anything still unpaired carries a real, name-derived slug with no
    # twin to become. Redact it to a neutral placeholder rather than let
    # it render: the same call _REDACTIONS makes for the contact columns,
    # for the same reason -- no counterpart is not a licence to pass it
    # through. Reported, never silent.
    for r in alias_pending:
        for col in ('owner_id', 'canonical_owner_id'):
            add(r.get(col), _ALIAS_REDACTION, f'alias.{col}.redacted',
                (r.get('league_key') or '').strip() or None)
        report['unpaired_alias'].append((r.get('canonical_owner_id') or '').strip())

    # The CBS owner_id slugs are themselves name-derived, so an id that
    # leaked into a rendered cell would leak a name with it. Map them
    # outright. (ESPN's GUIDs are identical on both sides and drop out via
    # the identity rule.)
    for rid, aid in crosswalk.items():
        add(rid, aid, 'owner_id.slug', leagues.get(rid))

    # team_owner_by_year: the rendered owner label per season. The stable
    # key names several rows for a co-owned franchise-season, so those
    # groups are separated by the owner-name mapping built above.
    def _resolve_by_year(name, key, r_rows, t_rows):
        """Separate co-owner rows in one franchise-season by name evidence.

        Three passes, strongest evidence first. A scrubbed name is matched
        through the mapping; a name the scrub deliberately KEPT (a handful
        of single-token labels that are nobody's personal name) appears
        verbatim on both sides and pairs to itself; a lone survivor on each
        side is settled by elimination. Row order is never consulted.
        """
        remaining, pending, out = list(t_rows), list(r_rows), []

        def _claim(match_for):
            for r in list(pending):
                want = match_for(r)
                if not want:
                    continue
                hit = next((t for t in remaining
                            if (t.get('owner_name') or '').strip().lower() == want),
                           None)
                if hit is not None:
                    remaining.remove(hit)
                    pending.remove(r)
                    out.append((r, hit))

        def _own(r):
            return (r.get('owner_name') or '').strip().lower()

        _claim(lambda r: (owner_name_map.get(_own(r)) or '').strip().lower())
        _claim(_own)

        if len(pending) == 1 and len(remaining) == 1:
            out.append((pending[0], remaining[0]))
        elif pending:
            raise RuntimeError(
                f"{name}: franchise-season {'/'.join(key)} carries "
                f"{len(pending)} co-owner row(s) that the owner-name mapping "
                f"cannot separate. Refusing to pair them by row order."
            )
        return out

    year_real, year_twin = _load_pair(SEED_BY_YEAR)
    for r, a in _pair_by_key(SEED_BY_YEAR, year_real, year_twin, _resolve_by_year):
        add(r.get('owner_name'), a.get('owner_name'), 'by_year.owner_name',
            (r.get('league_key') or '').strip() or None)

    # Co-owner labels as WHOLE forms (MLB-323). The label is a string no
    # config column holds, so it used to be reached only one first name at
    # a time -- and a first name that collides with a player is dropped,
    # which left "<real> & <twin>" half-scrubbed in every CBS records tab.
    # Mapped whole, the label is longer than any name inside it, so the
    # longest-first single pass replaces it in one step and no first name
    # ever has to be mapped alone.
    for real_label, anon_label, lg in co_owner_label_pairs():
        add(real_label, anon_label, 'co_owner.label', lg)

    # cbs_franchises: team names (which carry surnames) and abbrevs.
    fr_real, fr_twin = _load_pair(SEED_FRANCHISES)
    for r, a in _pair_by_key(SEED_FRANCHISES, fr_real, fr_twin):
        lg = (r.get('league_key') or '').strip() or None
        add(r.get('franchise_name'), a.get('franchise_name'), 'franchises.franchise_name', lg)
        add(r.get('abbrev'), a.get('abbrev'), 'franchises.abbrev', lg, exact=True)

    # Private map fills what the seeds cannot: ESPN team names, which only
    # ever existed in the live API. Add only real-forms the seeds did not
    # already cover, so owner names stay identical to the seed twins.
    # Every private-map row goes in; scope_pairs lets a seed-derived pair
    # for the same string win, once it knows which seeds are in scope.
    for real_value, anon_value in _read_private_map():
        if real_value != anon_value:
            add(real_value, anon_value, PRIVATE_SOURCE,
                exact=_classify_private(real_value) == CLS_ABBREV)

    pairs, report['kept_over_pair'], report['sources'] = scope_pairs(
        tagged, report['kept'], league)
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
            print(f"  [skipped] {src}: real form too short to match safely "
                  f"({len(value)} chars) -- NOT replaced")
        for value, src in report['kept_over_pair']:
            print(f"  [kept] {src}: a {len(value)}-char form the target "
                  f"league's twin keeps unchanged -- not replaced")
        for canon in report['unpaired_alias']:
            print(f"  [unpaired] {SEED_ALIAS}: canonical owner {_ref(canon)} "
                  f"has no twin row; its slugs are REDACTED, not mapped")
    return pairs, report


PRIVATE_SOURCE = 'private_map'


def scope_pairs(tagged, kept, league):
    """(pairs, kept_over_pair, sources) for one target league.

    tagged: (real, anon, source, league, exact) as build_mapping collects
    them; kept: (value, league, source) identity pairs. A pair from another
    league's config is out of scope. Within scope, a real form the target
    league's twin keeps UNCHANGED beats any pair for the same string from
    elsewhere, and a seed-derived pair beats the private map's -- the twin
    is what a stranger's clone renders for this book. league=None keeps
    everything.
    """
    def in_scope(lg):
        return league is None or lg is None or lg == league

    def key(real_value, exact):
        return real_value if exact else real_value.lower()

    kept_here = {value for value, lg, _ in kept if in_scope(lg)}
    kept_fold = {value.lower() for value in kept_here}
    seeded = {key(r, ex) for r, _, src, lg, ex in tagged
              if src != PRIVATE_SOURCE and in_scope(lg)}
    pairs, kept_over_pair, sources = [], [], Counter()
    for real_value, anon_value, source, lg, exact in tagged:
        if not in_scope(lg):
            continue
        if source == PRIVATE_SOURCE and key(real_value, exact) in seeded:
            continue
        if (real_value in kept_here) if exact else (real_value.lower() in kept_fold):
            kept_over_pair.append((real_value, source))
            continue
        pairs.append(Pair(real_value, anon_value, exact))
        sources[source] += 1
    return pairs, kept_over_pair, sources


def _read_private_map():
    """Every (real, fake) row of the private scrub map with both sides set,
    short ones included, or [] when the map is absent (a clone)."""
    if not _PRIVATE_MAP.is_file():
        return []
    import csv
    out = []
    with open(_PRIVATE_MAP, newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            real = (row.get('real') or '').strip()
            fake = (row.get('fake') or '').strip()
            if real and fake:
                out.append((real, fake))
    return out


def _load_private_map():
    """(real, fake) rows from the private scrub map, or [] when absent.

    Mirrors tools/check_pii.py: the map is gitignored and maintainer-only,
    so on any clone that lacks it this is a silent no-op. Rows missing
    either side, or with a real form too short to match safely, are
    skipped. No literal names live in this script -- they come from the
    file at runtime, exactly like the seed twins.
    """
    return [(real, fake) for real, fake in _read_private_map()
            if real != fake and len(real) >= _MIN_TOKEN_LEN]


class Pair(NamedTuple):
    """real -> anon. `exact` pairs (team abbreviations) match case-
    sensitively: "MATT" the team is not "Matt" the owner or Matt Olson."""
    real: str
    anon: str
    exact: bool = False


def _as_pair(p):
    return p if isinstance(p, Pair) else Pair(*p)


def _resolve(pairs):
    """Collapse to one anon form per real form -- case-insensitively for
    names, exactly for abbreviations, which never collapse into a name.

    A bare first name shared by two owners is genuinely ambiguous with no
    surname context (longest-first already resolves the cases that DO have
    context, because the full name is a longer form and matches first).
    Every candidate is anonymous, so any choice is privacy-equivalent --
    what matters is that it is deterministic and reported.
    """
    by_real = defaultdict(list)
    for p in map(_as_pair, pairs):
        by_real[(p.exact, p.real if p.exact else p.real.lower())].append(p)

    resolved, ambiguous = {}, []
    for (exact, _), candidates in by_real.items():
        counts = Counter(p.anon for p in candidates)
        # Most frequent wins; lexicographic breaks the tie so reruns and
        # different machines agree.
        best = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        # Keep the real form's most common surface casing for the pattern.
        surface = Counter(p.real for p in candidates).most_common(1)[0][0]
        resolved[(exact, surface.lower() if not exact else surface)] = Pair(surface, best, exact)
        if len(counts) > 1:
            ambiguous.append((surface, best, [a for a in counts if a != best]))
    return sorted(resolved.values(), key=lambda p: (-len(p.real), p.real, not p.exact)), ambiguous


def _assert_auditable(pairs):
    """Refuse a mapping whose own output would trip the residual audit.

    If some real form is ALSO an anonymized form for someone else, then
    after replacement that string legitimately appears in the sheet and
    the audit could never reach zero -- there would be no way to tell a
    leak from a correct substitution.
    """
    reals = {p.real.lower() for p in map(_as_pair, pairs)}
    anons = {p.anon.lower() for p in map(_as_pair, pairs)}
    collisions = sorted(reals & anons)
    if collisions:
        raise RuntimeError(
            f"{len(collisions)} real name form(s) are also anonymized name "
            f"forms. The residual audit could not distinguish a leak from a "
            f"correct replacement, so zero residuals would be unverifiable. "
            f"Resolve the seed twins before anonymizing a sheet."
        )


# --------------------------------------------------------------------------
# The form inventory: what COULD be wrong (MLB-323)
# --------------------------------------------------------------------------
#
# The mapping says what to CHANGE. The audit asks a different question --
# is any real identity still VISIBLE -- and a rescan of the forms the
# mapping applied cannot answer it: a form dropped as a player collision,
# or never mapped at all, is invisible to that rescan by construction.
# MLB-321's simulation found both on the dev books.
#
# So the inventory is built from the REAL side alone, independently of the
# mapping, and holds every form a reader could see. A form is set aside
# only when the published twin itself keeps it unchanged (it is public in
# the demo fixture), and every set-aside is printed with its reason.

CLS_CO_OWNER = 'co-owner label'
CLS_OWNER_FULL = 'owner full name'
CLS_OWNER_FIRST = 'owner first name'
CLS_TEAM_OWNER = 'team name w/ owner'
CLS_OWNER_ID = 'owner id'
CLS_CONTACT = 'contact detail'
CLS_SCRUB_MAP = 'scrub-map form'
CLS_TEAM = 'team name'
CLS_ABBREV = 'team abbreviation'
CLS_SCRUB_TEAM = 'scrub-map team form'

# Report order, and the class a hit is filed under when one string is
# several kinds of form at once.
FORM_CLASSES = (CLS_CO_OWNER, CLS_OWNER_FULL, CLS_OWNER_FIRST, CLS_TEAM_OWNER,
                CLS_OWNER_ID, CLS_CONTACT, CLS_SCRUB_MAP,
                CLS_TEAM, CLS_ABBREV, CLS_SCRUB_TEAM)

# The tool hides PEOPLE (Kyle's ruling, 2026-10-06). Fantasy team names and
# abbreviations are public content -- the 2026-08-10 ruling in
# tools/check_pii.py, reaffirmed -- so a visible one is reported as
# information and never fails a run. The exception is CLS_TEAM_OWNER: a
# team name carrying a real owner's first AND last name is an owner form.
INFO_CLASSES = frozenset({CLS_TEAM, CLS_ABBREV, CLS_SCRUB_TEAM})

# Abbreviations render in capitals and nowhere else. Matched case-
# sensitively, "CAL" is a team and "Cal" is Cal Raleigh; every other class
# is matched case-insensitively.
EXACT_CASE_CLASSES = frozenset({CLS_ABBREV})

# The shortest form the audit searches for. Two characters are safe on
# word boundaries; a one-character form cannot be searched and is reported.
_MIN_FORM_LEN = 2

# A scrub-map row's real side carries no category column; its shape does.
_ABBREV_SHAPE = re.compile(r'^[A-Z0-9#]{2,6}$')


class Form(NamedTuple):
    value: str
    cls: str
    league: Optional[str]      # None: every league (the private map has no league column)
    source: str


def _classify_private(real):
    if real.startswith('cbs-'):
        return CLS_OWNER_ID
    if real.isdigit():
        return CLS_CONTACT
    if _ABBREV_SHAPE.match(real):
        return CLS_ABBREV
    return CLS_SCRUB_MAP


def _form_key(cls, value):
    return value if cls in EXACT_CASE_CLASSES else value.lower()


def _words(text):
    return (text or '').replace('’', "'").casefold()


def _has_word(text, word):
    """`word` in `text` on word boundaries, both already _words()-folded.
    "Fulton" is in "Fulton's Folly"; "Andy" is not in "Andys"."""
    return bool(word) and re.search(
        rf'(?<![0-9a-z_]){re.escape(word)}(?![0-9a-z_])', text) is not None


def owner_names(full_names):
    """{(first, last)} from owner full names of two or more tokens, folded."""
    out = set()
    for name in full_names:
        tokens = _words(name).replace(',', ' ').split()
        if len(tokens) >= 2:
            out.add((tokens[0], tokens[-1]))
    return out


def scan_team_names(team_names, owners):
    """(with_full_name, with_last_name): team names carrying some real
    owner's first AND last name -- an owner form, which stays a residual
    and refuses without a twin -- and those carrying only a last name,
    which are reported and never refused."""
    full, last_only = [], []
    for team in sorted(set(t for t in team_names if t)):
        t = _words(team)
        holders = [(f, l) for f, l in owners if _has_word(t, l)]
        if any(_has_word(t, f) for f, _ in holders):
            full.append(team)
        elif holders:
            last_only.append(team)
    return full, last_only


def platform_roster(root=_SHARED_EXPORTS):
    """(rows, sources) from the latest shared export of every league.

    rows: dicts with league_key, team_name, team_abbrev, owner_display.
    sources: (league_key, snapshot, identity-as-of) per export read, so the
    report can say how fresh the roster is. Both empty when no export is on
    disk, which the report states rather than hides.
    """
    rows, sources = [], []
    if not root.is_dir():
        return rows, sources
    for league_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        pointer = league_dir / 'LATEST'
        if not pointer.is_file():
            continue
        snap = league_dir / pointer.read_text(encoding='utf-8').strip()
        try:
            manifest = json.loads((snap / 'manifest.json').read_text(encoding='utf-8'))
            teams = json.loads((snap / 'teams.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        league = manifest.get('league_key')
        for t in teams.get('rows') or []:
            rows.append({'league_key': league,
                         'team_name': (t.get('team_name') or '').strip(),
                         'team_abbrev': (t.get('team_abbrev') or '').strip(),
                         'owner_display': (t.get('owner_display') or '').strip()})
        sources.append((league, snap.name, manifest.get('club_identity_as_of')))
    return rows, sources


def build_form_inventory(league, kept, roster=(), load=_read_real,
                         private=_read_private_map):
    """(forms, notes): every real form a reader of a `league` book could see.

    Sources, all on the real side: the five twinned config files plus
    franchise_lineage, the private scrub map, the co-owner labels as the
    warehouse and renderer build them, and the owner labels of the
    platform roster (owners who left the league before the config knew
    them). `kept` is build_mapping's report['kept']: (value, league,
    source) identity pairs, which the published twin carries unchanged.
    `load` reads one real config file by name and `private` returns the
    scrub map's rows; both default to the maintainer's disk.

    A form tagged with another league is out of scope; an untagged one
    (the private map has no league column) is always in scope. Within
    scope a form is set aside only when the target league's twin keeps it
    -- and notes['kept'] lists it.
    """
    forms = []

    def add(value, cls, lg, source):
        value = (value or '').strip()
        if value:
            forms.append(Form(value, cls, lg or None, source))

    leagues = owner_leagues(load)

    for r in load(SEED_NICKNAMES):
        oid = (r.get('owner_id') or '').strip()
        lg = leagues.get(oid)
        first = (r.get('first_name') or '').strip()
        last = (r.get('last_name') or '').strip()
        add(first, CLS_OWNER_FIRST, lg, 'nicknames.first_name')
        add(r.get('preferred_name'), CLS_OWNER_FULL, lg, 'nicknames.preferred_name')
        if first and last:
            add(f'{first} {last}', CLS_OWNER_FULL, lg, 'nicknames.full_name')
            add(f'{last}, {first}', CLS_OWNER_FULL, lg, 'nicknames.last_first')
            add(f'{first[0]}. {last}', CLS_OWNER_FULL, lg, 'nicknames.initial_last')
        if oid and not _STABLE_ID.match(oid):
            add(oid, CLS_OWNER_ID, lg, 'nicknames.owner_id')
        for col in _REDACTIONS:
            raw = (r.get(col) or '').strip()
            add(raw, CLS_CONTACT, lg, f'nicknames.{col}')
            digits = re.sub(r'\D', '', raw)
            if col == 'phone_number' and len(digits) >= 7 and digits != raw:
                add(digits, CLS_CONTACT, lg, 'nicknames.phone_digits')

    for lg, _, _, oid, name in resolve_owner_years(load):
        if ' ' in name:
            add(name, CLS_OWNER_FULL, lg, 'by_year.owner_name')
        add(name.split(' ')[0], CLS_OWNER_FIRST, lg, 'by_year.first_name')
        if not _STABLE_ID.match(oid):
            add(oid, CLS_OWNER_ID, lg, 'by_year.owner_id')

    for (lg, _, _), labels in co_owner_labels(load).items():
        for label in labels:
            add(label, CLS_CO_OWNER, lg, 'co_owner.warehouse')
            add(label.replace(CO_OWNER_JOIN, CO_OWNER_RENDER), CLS_CO_OWNER, lg,
                'co_owner.rendered')

    for r in load(SEED_ALIAS):
        lg = (r.get('league_key') or '').strip()
        add(r.get('preferred_name'), CLS_OWNER_FULL, lg, 'alias.preferred_name')
        for col in ('owner_id', 'canonical_owner_id'):
            oid = (r.get(col) or '').strip()
            if not _STABLE_ID.match(oid):
                add(oid, CLS_OWNER_ID, lg, f'alias.{col}')

    for r in load(SEED_TEAM_OWNERS):
        oid = (r.get('owner_id') or '').strip()
        if not _STABLE_ID.match(oid):
            add(oid, CLS_OWNER_ID, r.get('league_key'), 'team_owners.owner_id')

    for r in load(SEED_FRANCHISES):
        add(r.get('franchise_name'), CLS_TEAM, r.get('league_key'), 'franchises.franchise_name')
        add(r.get('abbrev'), CLS_ABBREV, r.get('league_key'), 'franchises.abbrev')
    for r in load(LINEAGE):
        add(r.get('canonical_name'), CLS_TEAM, r.get('league_key'), 'lineage.canonical_name')
        add(r.get('canonical_abbrev'), CLS_ABBREV, r.get('league_key'), 'lineage.canonical_abbrev')

    for row in roster:
        add(row.get('owner_display'), CLS_OWNER_FULL, row.get('league_key'),
            'roster.owner_display')
        add(row.get('team_name'), CLS_TEAM, row.get('league_key'), 'roster.team_name')
        add(row.get('team_abbrev'), CLS_ABBREV, row.get('league_key'), 'roster.team_abbrev')

    # Who the owners are, as (first, last): what classifies the private
    # map's unlabelled rows and drives the owner-name-in-team-name scan.
    # Initial and "Last, First" forms are left out (they re-spell the same
    # people), and so are identities the twin keeps (Kyle's own name).
    kept_values = {v.lower() for v, _, _ in kept}
    named = [f for f in forms if f.cls == CLS_OWNER_FULL
             and f.source not in ('nicknames.initial_last', 'nicknames.last_first')]
    owners = {o for o in owner_names(f.value for f in named)
              if f'{o[0]} {o[1]}' not in kept_values}
    known_owners = {f.value.lower() for f in named}
    team_league = {}
    for f in forms:
        if f.cls == CLS_TEAM:
            team_league.setdefault(f.value, f.league)
    teams_folded = [_words(t) for t in team_league]

    # The private map has no category column. A row is an owner form when
    # it is a known owner name or carries one owner's first and last name;
    # team content when it is, or is part of, a known team name; anything
    # else stays a residual class -- the strict default.
    for real, _ in private():
        cls = _classify_private(real)
        if cls == CLS_SCRUB_MAP:
            w = _words(real)
            if real.lower() in known_owners or any(
                    _has_word(w, f) and _has_word(w, l) for f, l in owners):
                cls = CLS_OWNER_FULL
            elif any(_has_word(t, w) for t in teams_folded):
                cls = CLS_SCRUB_TEAM
                team_league.setdefault(real, None)
        add(real, cls, None, 'scrub_map')

    with_owner, with_last = scan_team_names(team_league, owners)
    for team in with_owner:
        add(team, CLS_TEAM_OWNER, team_league.get(team), 'team name scan')

    def in_scope(lg):
        return league is None or lg is None or lg == league

    kept_keys = {}
    for value, lg, source in kept:
        if in_scope(lg):
            kept_keys.setdefault(value, source)
            kept_keys.setdefault(value.lower(), source)

    notes = {'kept': defaultdict(set), 'too_short': Counter(),
             'out_of_scope': 0, 'forms': Counter(), 'owners': owners,
             'team_scan': {'with_owner': [t for t in with_owner
                                          if in_scope(team_league.get(t))],
                           'with_last': [t for t in with_last
                                         if in_scope(team_league.get(t))]}}
    audited, seen = [], set()
    for f in forms:
        if not in_scope(f.league):
            notes['out_of_scope'] += 1
            continue
        key = (f.cls, _form_key(f.cls, f.value))
        if key in seen:
            continue
        seen.add(key)
        if _form_key(f.cls, f.value) in kept_keys:
            notes['kept'][f.cls].add(f.value)
            continue
        if len(f.value) < _MIN_FORM_LEN:
            notes['too_short'][f.cls] += 1
            continue
        audited.append(f)
        notes['forms'][f.cls] += 1
    return audited, notes


class FormMatcher:
    """One word-bounded alternation over every audited form, longest first,
    abbreviations case-sensitive and everything else case-insensitive."""

    def __init__(self, forms):
        self._exact, self._loose = {}, {}
        # One string can be several kinds of form at once (a team name that
        # carries an owner's name); the hit is filed under the earliest
        # class in FORM_CLASSES, so an owner form outranks team content.
        for f in sorted(forms, key=lambda f: FORM_CLASSES.index(f.cls)
                        if f.cls in FORM_CLASSES else len(FORM_CLASSES)):
            if f.cls in EXACT_CASE_CLASSES:
                self._exact.setdefault(f.value, f)
            else:
                self._loose.setdefault(f.value.lower(), f)
        alts = [(v, True) for v in self._exact] + \
               [(f.value, False) for f in self._loose.values()]
        # Longest first; at equal length the case-sensitive alternative
        # goes first, so "MATT" the abbreviation outranks "Matt" the name.
        alts.sort(key=lambda a: (-len(a[0]), not a[1], a[0]))
        body = '|'.join(f'(?-i:{re.escape(v)})' if exact else re.escape(v)
                        for v, exact in alts)
        self._pattern = re.compile(
            rf'(?<![0-9A-Za-z_])(?:{body})(?![0-9A-Za-z_])', re.IGNORECASE,
        ) if alts else None

    def find(self, text):
        """[(start, end, Form)] for every audited form present."""
        if self._pattern is None or not text:
            return []
        out = []
        for m in self._pattern.finditer(text):
            found = m.group(0)
            form = self._exact.get(found) or self._loose.get(found.lower())
            out.append((m.start(), m.end(),
                        form or Form(found, CLS_SCRUB_MAP, None, 'unkeyed')))
        return out


# The positive player check knows exactly two cell shapes, both written by
# the renderer and nothing else:
#
#   a player link -- output/almanac_render._bref_link: bref's name search
#       keyed on the official name, the visible label as display text;
#   a contributor list -- the record "Details" cells
#       (cbs_almanac_sheets._contributor_detail, records_book_logic,
#       almanac_render): ', '.join(f'{name}: {value}'), values formatted
#       numbers like 1,127 or 807.7.
_BREF_CELL = re.compile(
    r'^=HYPERLINK\("https://www\.baseball-reference\.com/search/search\.fcgi\?'
    r'search=([^"]*)",\s*"((?:[^"]|"")*)"\)$'
)
_CONTRIB_VALUE = r'-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
_CONTRIB_ITEM = re.compile(rf'(?:^|, )([^,:]+?): {_CONTRIB_VALUE}(?=, |$)')
_CONTRIB_CELL = re.compile(
    rf'^[^,:]+?: {_CONTRIB_VALUE}(?:, [^,:]+?: {_CONTRIB_VALUE})*$')


def _fold_name(value):
    """Accent-, period-, case- and spacing-blind spelling of a person's name.
    Periods go because the renderer strips them from the search key."""
    value = unicodedata.normalize('NFKD', value)
    value = ''.join(c for c in value if not unicodedata.combining(c))
    return ' '.join(value.replace('.', '').casefold().split())


# Kyle's ruling (2026-10-06): MLB players are public figures and are never
# anonymized, so their names are matched GENEROUSLY -- the way a platform,
# a renderer or a historian might spell the same person. What is never
# excused is a first name that is not inside a whole player name: that is
# exactly the shape a co-owner label leaks in ("Chris", "Chris & Matt").
_NAME_SUFFIXES = frozenset({'jr', 'sr', 'ii', 'iii', 'iv', 'v'})
_NAME_TOKEN = re.compile(r"[^\W\d_](?:[^\W\d_]|['’.\-])*")


def _name_variants(name):
    """The spellings one player goes by: as written, without a generational
    suffix (Luis Robert / Luis Robert Jr.), without middle initials (Chris
    Young / Chris B. Young), and both. Only two-or-more-token variants: a
    single token is never a whole name."""
    tokens = name.replace('’', "'").split()
    out = set()
    for drop_suffix in (False, True):
        for drop_initials in (False, True):
            t = list(tokens)
            if drop_suffix and len(t) > 2 and t[-1].replace('.', '').lower() in _NAME_SUFFIXES:
                t = t[:-1]
            if drop_initials and len(t) > 2:
                t = [t[0]] + [x for x in t[1:-1] if len(x.replace('.', '')) > 1] + [t[-1]]
            if len(t) >= 2:
                out.add(' '.join(t))
    return out


def _name_key(name):
    return _fold_name(name.replace('-', ' ').replace("'", '').replace('’', ''))


# Given names a platform writes formally and the Stats API informally, or the
# other way round (CBS "Michael Burrows" is the Stats API's "Mike Burrows").
# Applied only with the surname matching exactly, so a match is still one
# player's whole name.
_GIVEN_NAME_FORMS = (
    'michael mike', 'matthew matt', 'christopher chris', 'james jim jimmy jamie',
    'robert rob bob bobby robbie', 'daniel dan danny', 'david dave',
    'thomas tom tommy', 'william will bill billy willie', 'jacob jake',
    'joshua josh', 'alexander alex', 'nicholas nick', 'zachary zach zack',
    'andrew andy drew', 'anthony tony', 'joseph joe joey', 'benjamin ben',
    'samuel sam', 'steven stephen steve', 'gregory greg', 'edward ed eddie',
    'richard rich rick ricky', 'timothy tim', 'kenneth ken kenny',
    'patrick pat', 'peter pete', 'philip phillip phil', 'charles charlie',
    'jeffrey jeff', 'jonathan jon', 'nathan nathaniel nate', 'douglas doug',
    'gerald jerry', 'lawrence larry', 'raymond ray', 'ronald ron',
    'donald don', 'frederick fred freddy', 'mitchell mitch', 'cameron cam',
)
_GIVEN_NAME = {form: group.split()[0] for group in _GIVEN_NAME_FORMS for form in group.split()}


def _given_name_key(key):
    first, _, rest = key.partition(' ')
    return f'{_GIVEN_NAME.get(first, first)} {rest}' if rest else key


def _within_one_edit(a, b):
    """Levenshtein distance <= 1, without building the matrix."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = j = edits = 0
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        if len(a) == len(b):
            i += 1
        j += 1
    return edits + (len(b) - j) + (len(a) - i) <= 1


class PlayerSpine:
    """The independent record of who the players are, indexed for generous
    WHOLE-name matching: every variant of every name (_name_variants), the
    renderer's board short form of each ('M Trout', _draft_initial_text),
    and, for spelling drift, the same surname under a formal or short form
    of the given name (_GIVEN_NAME_FORMS) or the same first name with a
    surname one edit away (five letters or longer). Built from Stats API
    fullNames, never from anything the sheet says about itself."""

    def __init__(self, names=()):
        self.names = frozenset(n for n in names if n)
        self._full, self._short, self._given = set(), set(), set()
        self._surnames = defaultdict(set)
        for name in self.names:
            for variant in _name_variants(name):
                key = _name_key(variant)
                first, _, rest = key.partition(' ')
                if not rest:
                    continue
                self._full.add(key)
                self._given.add(_given_name_key(key))
                self._surnames[first].add(rest)
                self._short.add(_name_key(_draft_initial_text(variant)))

    def __len__(self):
        return len(self.names)

    def __contains__(self, name):
        return self.matches(name)

    def matches(self, name):
        """True when `name` is some spine player's whole name, in any of
        the spellings above."""
        for variant in _name_variants(name.replace('+', ' ')):
            key = _name_key(variant)
            if key in self._full or key in self._short or _given_name_key(key) in self._given:
                return True
            first, _, rest = key.partition(' ')
            if len(rest) >= 5 and any(_within_one_edit(rest, known)
                                      for known in self._surnames.get(first, ())
                                      if abs(len(known) - len(rest)) <= 1):
                return True
        return False


def as_spine(spine):
    """A PlayerSpine from whatever the caller holds (a spine, or names)."""
    return spine if isinstance(spine, PlayerSpine) else PlayerSpine(spine or ())


def whole_player_name(text, start, end, spine):
    """The whole MLB player name in `text` that covers [start, end), or None.

    Tries every run of two to five consecutive name tokens around the hit
    -- separated by spaces only (or '+', a URL's space), so "Chris & Matt"
    and "Matt, Chris" never form a run -- longest first, and asks the spine
    whether the run is a player's whole name. Any cell shape qualifies:
    prose notes, runner-up strings, labels, URLs.
    """
    tokens = list(_NAME_TOKEN.finditer(text))
    k = next((i for i, t in enumerate(tokens) if t.start() <= start < t.end()), None)
    if k is None:
        return None
    for i in range(k, max(-1, k - 4), -1):
        for j in range(min(len(tokens) - 1, i + 4), max(i, k) - 1, -1):
            if j == i or tokens[i].start() > start or tokens[j].end() < end:
                continue
            if any(not re.fullmatch(r'[ +]+', text[tokens[m].end():tokens[m + 1].start()])
                   for m in range(i, j)):
                continue
            run = text[tokens[i].start():tokens[j].end()]
            if spine.matches(run):
                return run
    return None


def load_player_spine(root=_SPINE_DIR, supplement=_SPINE_SUPPLEMENT):
    """The MLB spine (Stats API fullNames) plus the grown supplement, as a
    PlayerSpine; empty when neither is on disk -- in which case nothing can
    be proven and every hit counts, which is the safe direction to fail."""
    names = set()
    if Path(root).is_dir():
        for path in Path(root).glob('*.json'):
            try:
                doc = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            for block in (doc.get('payload') or {}).get('stats') or []:
                for split in block.get('splits') or []:
                    name = ((split.get('player') or {}).get('fullName') or '').strip()
                    if name:
                        names.add(name)
    if supplement and Path(supplement).is_file():
        try:
            doc = json.loads(Path(supplement).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            doc = {}
        names.update((p.get('fullName') or '').strip() for p in doc.get('people') or [])
    return PlayerSpine(names)


def _fetch_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'espn-league-manager/anonymizer-spine'})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read())


def grow_player_spine(first_season, last_season, fetch=_fetch_json,
                      path=_SPINE_SUPPLEMENT, root=_SPINE_DIR, pause=0.3):
    """Write every MLB player of the given seasons that the spine directory
    lacks to the supplement, from the Stats API's per-season player list
    (/sports/1/players?season=YYYY -- the source extract/mlb_crosswalk.py
    indexes). Public, read-only, no token; touches no sheet. Returns the
    number of people written."""
    have = load_player_spine(root, supplement=None)
    people, seen = [], set()
    for season in range(first_season, last_season + 1):
        for p in fetch(f'{_STATS_API}/sports/1/players?season={season}').get('people') or []:
            name = (p.get('fullName') or '').strip()
            if name and p.get('id') not in seen and _name_key(name) not in have._full:
                seen.add(p.get('id'))
                people.append({'id': p.get('id'), 'fullName': name})
        if pause:
            time.sleep(pause)
    Path(path).write_text(json.dumps({
        'source': f'{_STATS_API}/sports/1/players?season=<{first_season}..{last_season}>',
        'fetched_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'note': 'players absent from data/mlb_stats/season; read by '
                'tools/anonymize_sheet_copy.py only, never loaded to RAW',
        'people': sorted(people, key=lambda p: p['fullName']),
    }, indent=1, ensure_ascii=False), encoding='utf-8')
    return len(people)


def _draft_initial_text(name):
    """output/almanac_render.draft_initial_text, mirrored rather than
    imported (the renderer drags its warehouse modules in with it). A
    test pins the two together; if they ever drift, labels stop proving and
    the audit fails closed rather than open."""
    name = (name or '').strip()
    if not name:
        return ''
    parts = name.split()
    first, rest = parts[0], ' '.join(parts[1:])
    core = first.replace('.', '')
    if not rest:
        return first
    if core.isupper() and 2 <= len(core) <= 3:
        return f'{core} {rest}'
    return f'{(core[:1] or first[:1]).upper()} {rest}'


_POINTS_LABEL = re.compile(r'^(.*) \(-?[\d,]+(?:\.\d+)?\)$')


def _is_player_label(display, name):
    """True when `display` is one of the visible labels the renderer derives
    from a player's official name: the name itself or its board short form
    ('M Trout'), either one keeper-marked ' (K)' (almanac_render.
    _draft_player_label / _draft_initial_label), or the name with a points
    total, 'Chris Sale (872)' (cbs_almanac_sheets' draft top-3 cells)."""
    short = _draft_initial_text(name)
    labels = {_fold_name(v) for v in (name, f'{name} (K)', short, f'{short} (K)') if v}
    if _fold_name(display) in labels:
        return True
    m = _POINTS_LABEL.match(display)
    return bool(m) and _fold_name(m.group(1)) == _fold_name(name)


def proven_players(cell, spine):
    """[(start, end, player)] spans of `cell` PROVEN to be MLB player names,
    or [] when the cell proves nothing.

    The sheet never vouches for itself: the MLB spine is the independent
    record of who the players are (the demo gate's name_gate_v2 rule,
    MLB-319). A proof is all-or-nothing per cell --

      * player link: the whole cell is the renderer's baseball-reference
        link, the name it searches for is on the spine, and its visible
        text is that same name or one of the renderer's fixed labels for
        it (_is_player_label) -- a nickname label fails. The span is the
        whole cell, since the search key repeats the name.
      * contributor list: the whole cell is the renderer's list shape and
        EVERY name in it is on the spine. One unproven name and the cell
        proves nothing at all. The spans are the names, so a hit counts as
        excluded only if it sits inside one.
    """
    spine = as_spine(spine)
    if not isinstance(cell, str) or not spine:
        return []
    m = _BREF_CELL.match(cell)
    if m:
        key = urllib.parse.unquote_plus(m.group(1))
        display = m.group(2).replace('""', '"')
        if spine.matches(key) and _is_player_label(display, key):
            return [(0, len(cell), key)]
        return []
    if _CONTRIB_CELL.match(cell):
        spans = [(it.start(1), it.end(1), it.group(1))
                 for it in _CONTRIB_ITEM.finditer(cell)]
        if spans and all(spine.matches(name) for _, _, name in spans):
            return spans
    return []


def audit_tabs(tabs, forms, spine, spreadsheet_title=None, kept_names=()):
    """Search every cell, formula and tab title for every audited form.

    tabs: {title: (grid, origin_row, origin_col)} as _read_tabs returns.
    A team name or abbreviation (INFO_CLASSES) is public content: counted
    as information, never a residual -- and an abbreviation inside the
    exact span of a team name kept verbatim (`kept_names`) is counted as
    excused for that reason. Any other hit is a residual unless it is
    positively identified as an MLB player:

      * 'player cell'  -- it lies inside a span proven_players proves (a
        renderer player link or contributor list); any class.
      * 'player name'  -- it is an owner FIRST name and sits inside a whole
        player name anywhere in the cell (whole_player_name). Only first
        names: a full owner name or a co-owner label is never excused by
        resembling a player.

    Excusals are counted by (reason, class) and by player, so the report
    shows exactly what was let through and why.
    """
    kept = sorted({n for n in kept_names if n}, key=len, reverse=True)
    # A kept name is recognised as a span, not searched as a form: matched
    # as a form it would swallow the abbreviation inside it, and the
    # excusal the ruling asks to be counted would never be seen.
    matcher = FormMatcher([f for f in forms if not (
        f.cls in (CLS_TEAM, CLS_SCRUB_TEAM) and f.value in set(kept))])
    spine = as_spine(spine)
    kept_re = re.compile('(?<![0-9A-Za-z_])(?:' + '|'.join(map(re.escape, kept))
                         + ')(?![0-9A-Za-z_])') if kept else None
    result = {'residuals': Counter(), 'cells': [], 'excused': Counter(),
              'excluded': defaultdict(Counter), 'excluded_cells': 0,
              'info': Counter(), 'kept_names_visible': 0}

    def judge(where, text, hits, cell=None):
        team_spans = [m.span() for m in kept_re.finditer(text)] if kept_re else []
        result['kept_names_visible'] += len(team_spans)
        if not hits:
            return False
        spans = proven_players(cell, spine) if cell is not None else []
        excused = False
        for start, end, form in hits:
            if form.cls in INFO_CLASSES:
                if form.cls == CLS_ABBREV and any(s <= start and end <= e
                                                  for s, e in team_spans):
                    result['excused'][('kept team name', form.cls)] += 1
                else:
                    result['info'][form.cls] += 1
                continue
            reason = 'player cell'
            player = next((p for s, e, p in spans if s <= start and end <= e), None)
            if player is None and form.cls == CLS_OWNER_FIRST and spine:
                reason = 'player name'
                player = whole_player_name(text, start, end, spine)
            if player is None:
                result['residuals'][form.cls] += 1
                result['cells'].append((where, form))
            else:
                result['excused'][(reason, form.cls)] += 1
                result['excluded'][form.cls][player] += 1
                excused = True
        return excused

    if spreadsheet_title:
        judge('[spreadsheet title]', spreadsheet_title, matcher.find(spreadsheet_title))
    for title, (grid, row0, col0) in tabs.items():
        judge(f'{title} [tab title]', title, matcher.find(title))
        for r, row in enumerate(grid):
            for c, cell in enumerate(row):
                if cell is None or cell == '' or isinstance(cell, bool):
                    continue
                text = cell if isinstance(cell, str) else str(cell)
                where = f'{title}!{_col_letters(col0 + c)}{row0 + r}'
                result['excluded_cells'] += judge(where, text, matcher.find(text), cell)
    return result


def print_audit(result, notes, spine, roster_sources, show=25):
    """The audit report. Real forms are never printed -- a residual is named
    by its class, its length and a non-reversible digest, plus the cell to
    open. Excluded player names ARE printed: they are public MLB names, and
    the report has to show what the positive check let through."""
    total = sum(result['residuals'].values())
    owner_classes = [c for c in FORM_CLASSES if c not in INFO_CLASSES]
    n_forms = sum(notes['forms'][c] for c in owner_classes)
    print(f'  residual audit: {n_forms} real owner form(s), searched in every '
          f'cell, formula and tab title')
    print(f'    {"class":<20} {"forms":>6} {"residual hits":>14}')
    for cls in owner_classes:
        if notes['forms'][cls] or result['residuals'][cls]:
            flag = '   <-- RESIDUAL' if result['residuals'][cls] else ''
            print(f'    {cls:<20} {notes["forms"][cls]:>6} '
                  f'{result["residuals"][cls]:>14}{flag}')
    print(f'    {"TOTAL":<20} {n_forms:>6} {total:>14}')
    info = result.get('info', {})
    print('  information only (team names and abbreviations are public content, '
          'never a failure): ' + ', '.join(
              f'{cls} {notes["forms"][cls]} form(s) / {info.get(cls, 0)} visible'
              for cls in FORM_CLASSES if cls in INFO_CLASSES))
    scan = notes.get('team_scan') or {}
    print(f'  owner-name-in-team-name scan: {len(scan.get("with_owner", []))} team '
          f"name(s) carry an owner's first AND last name (owner forms, "
          f'residual class); {len(scan.get("with_last", []))} carry only a last '
          f'name (reported): '
          + (', '.join(repr(t) for t in scan.get('with_last', [])) or 'none'))
    for where, form in result['cells'][:show]:
        print(f'      {where}: {form.cls}, len {len(form.value)}, '
              f'#{_ref(form.value)} ({form.source})')
    if len(result['cells']) > show:
        print(f'      ... and {len(result["cells"]) - show} more')

    excused = result.get('excused', {})
    print(f'  excused: {sum(excused.values())} hit(s) -- MLB players on the spine '
          f'({len(spine)} names): a renderer player link or contributor list '
          f'(any class), or a whole player name anywhere in a cell (owner first '
          f'names only); and abbreviations inside a team name kept verbatim')
    for (reason, cls), n in sorted(excused.items()):
        print(f'    {reason:<15} {cls:<20} {n:>6}')
    print(f'  team names kept verbatim, occurrences: {result.get("kept_names_visible", 0)}')
    for cls in FORM_CLASSES:
        players = result['excluded'].get(cls)
        if players:
            listed = ', '.join(f'{p!r} x{n}' for p, n in players.most_common(8))
            more = f', +{len(players) - 8} more' if len(players) > 8 else ''
            print(f'    {cls}: {listed}{more}')
    if not spine:
        print('    WARNING: no MLB spine on disk -- nothing can be proven, so '
              'every hit above counts as a residual')

    for cls in FORM_CLASSES:
        if notes['kept'].get(cls):
            print(f'  set aside, kept unchanged by the published twin: {cls}: '
                  f'{", ".join(repr(v) for v in sorted(notes["kept"][cls]))}')
    for cls, n in notes['too_short'].items():
        print(f'  NOT searchable (shorter than {_MIN_FORM_LEN} chars): {cls} x{n}')
    if roster_sources:
        for lg, snap, as_of in roster_sources:
            print(f'  platform roster: {lg} shared export {snap} '
                  f'(club identity as of {as_of})')
    else:
        print('  WARNING: no shared export on disk -- owners the platform served '
              'but the config never held (members who left) are NOT in the '
              'inventory')
    return total


# --------------------------------------------------------------------------
# No twin, no run (MLB-323)
# --------------------------------------------------------------------------
#
# A twin is a reviewed row Kyle adds -- to the demo fixture or to the
# private map. The tool never invents one, so a real team or owner the
# mapping has no twin for cannot be anonymized, and the honest move is to
# stop BEFORE the copy is touched and name what is missing. (MLB-321: an
# ESPN team renamed mid-season had no row, and the copy would have
# published it unchanged.)
#
# Only an OWNER identity stops a run (Kyle's ruling, 2026-10-06): the tool
# hides people, and fantasy team names and abbreviations are public content
# (the 2026-08-10 ruling in tools/check_pii.py, reaffirmed). A team without
# a twin is listed and published verbatim. The one team-shaped refusal is a
# team name carrying a real owner's first AND last name, which is an owner
# form; a team name carrying only a last name is reported.

# The renderer's team-page header (almanac_logic.build_team_history_tabs):
# A1 is "{team_name} ({team_abbrev})", or the bare name when the team has
# no abbreviation, and A2 starts with this.
_TEAM_PAGE_A2 = 'Optimal Lineups'
_TEAM_PAGE_A1 = re.compile(r'^(.*\S) \(([^()]+)\)$')


def team_pages(tabs):
    """[(tab title, team name, abbreviation or '')] for every team page."""
    out = []
    for title, (grid, row0, col0) in tabs.items():
        if (row0, col0) != (1, 1) or len(grid) < 2 or not grid[0] or not grid[1]:
            continue
        a1, a2 = grid[0][0], grid[1][0]
        if not (isinstance(a1, str) and isinstance(a2, str)
                and a2.startswith(_TEAM_PAGE_A2)):
            continue
        m = _TEAM_PAGE_A1.match(a1.strip())
        name, abbrev = (m.group(1), m.group(2)) if m else (a1.strip(), '')
        out.append((title, name, abbrev))
    return out


def check_twins(tabs, forms, pairs, kept, league, owners=()):
    """Refuse -- before a single write -- when the book renders an OWNER
    identity with no twin: an owner full name, or a team name carrying an
    owner's first and last name (from the inventory's scan, or a team page
    the scan finds now). Owners are never named, only class, length and
    digest. Team names and abbreviations never refuse: one without a twin
    is listed and published verbatim.

    Returns the team names kept verbatim -- every known team name no pair
    rewrites and that is not an owner form -- for the replacer to protect
    and the audit to recognise.
    """
    pairs = [_as_pair(p) for p in pairs]
    in_scope = [v for v, lg, _ in kept if league is None or lg is None or lg == league]
    exact = {p.real for p in pairs if p.exact} | {p.anon for p in pairs} | set(in_scope)
    loose = ({p.real.lower() for p in pairs if not p.exact}
             | {p.anon.lower() for p in pairs} | {v.lower() for v in in_scope})
    rewritten = {p.real.lower() for p in pairs if not p.exact}

    pages = team_pages(tabs)
    page_owner, _ = scan_team_names([n for _, n, _ in pages], owners)
    owner_teams = {f.value for f in forms if f.cls == CLS_TEAM_OWNER} | set(page_owner)
    bad = [f for f in forms if f.cls in (CLS_OWNER_FULL, CLS_TEAM_OWNER)
           and f.value.lower() not in loose]
    bad += [Form(n, CLS_TEAM_OWNER, league, 'team page') for n in page_owner
            if n.lower() not in loose and all(n != f.value for f in bad)]

    candidates = {n for _, n, _ in pages} | {f.value for f in forms if f.cls == CLS_TEAM}
    keep = sorted(n for n in candidates
                  if len(n) >= _MIN_TOKEN_LEN and n.lower() not in rewritten
                  and n not in owner_teams)
    if keep:
        print(f'  team names kept verbatim (public content; protected from '
              f'abbreviation rewrites inside them): {", ".join(repr(n) for n in keep)}')
    no_twin = sorted({a for _, _, a in pages if a and a not in exact})
    if no_twin:
        print(f'  team abbreviations with no twin, published verbatim: '
              f'{", ".join(repr(a) for a in no_twin)}')
    if not bad:
        print('  twins: every owner identity the book renders has a twin')
        return keep
    lines = [f'    {f.cls}, len {len(f.value)}, #{_ref(f.value)} '
             f'({f.source}, {f.league or "any league"}) has no twin' for f in bad]
    raise SystemExit(
        'REFUSING before any write: the book renders owner identities with no '
        'twin row. Twins are reviewed rows -- add them to '
        'archives/anonymization/name_map.csv (real,fake) or the demo fixture; '
        'this tool never invents one.\n' + '\n'.join(lines))


def simulate_scrub(tabs, replacer):
    """What the live pass would leave behind, computed offline: tab titles
    and every non-player cell rewritten by `replacer`, duplicate titles
    suffixed as _rename_tabs does. Lets the audit be measured -- and tested
    -- without touching a sheet."""
    out, taken = {}, set(tabs)
    for title, (grid, row0, col0) in tabs.items():
        new_title, n = replacer.sub(title)
        candidate = title
        if n and new_title != title:
            candidate, suffix = new_title, 2
            while candidate in taken:
                candidate, suffix = f'{new_title} ({suffix})', suffix + 1
            taken.discard(title)
            taken.add(candidate)
        out[candidate] = ([[replacer.sub(cell)[0]
                            if isinstance(cell, str) and cell and not _is_player_cell(cell)
                            else cell for cell in row] for row in grid], row0, col0)
    return out


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

    Dropped = a SINGLE-TOKEN real form (a bare first or last name) that
    occurs, on word boundaries, inside some real player name; replacing it
    would deface a player. A multi-token form -- a full name, a co-owner
    label, a team name -- is never dropped: player cells are never written
    at all, and dropping one would leave a whole owner identity visible.
    Whatever a drop leaves visible, the inventory audit catches.

    An abbreviation pair collides only case-sensitively, the way it is
    matched: "CAL" is not inside "Cal Raleigh", so a player's first name
    can no longer veto a team abbreviation.
    """
    blob = '\n'.join(player_names)
    safe, dropped = [], []
    for item in pairs:
        p = _as_pair(item)
        pattern = rf'(?<![0-9A-Za-z_]){re.escape(p.real)}(?![0-9A-Za-z_])'
        flags = 0 if p.exact else re.IGNORECASE
        if len(p.real.split()) == 1 and re.search(pattern, blob, flags):
            dropped.append(item)
        else:
            safe.append(item)
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

    `protect` holds team names kept verbatim (MLB-323). Each is an identity
    pair, matched exactly and returned unchanged; being longer than any
    abbreviation inside it, it is consumed first, so "I Paid BP Writers
    for Advice" stays as written while a standalone "BP" still maps.
    """

    def __init__(self, pairs, protect=()):
        self.pairs = [_as_pair(p) for p in pairs]
        self._exact = {p.real: p.anon for p in self.pairs if p.exact}
        self._by_key = {p.real.lower(): p.anon for p in self.pairs if not p.exact}
        self._protect = {name for name in protect if name}
        # Sorted longest-first so "First Last" wins over a bare "First"; at
        # equal length a kept name goes first, then a case-sensitive
        # abbreviation, so "MATT" the team outranks "Matt" the name.
        entries = ([(name, 0) for name in self._protect]
                   + [(p.real, 1 if p.exact else 2) for p in self.pairs])
        ordered = sorted(entries, key=lambda e: (-len(e[0]), e[1], e[0]))
        alternation = '|'.join(re.escape(text) if kind == 2 else f'(?-i:{re.escape(text)})'
                               for text, kind in ordered)
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
            if found in self._protect:
                return found
            if found in self._exact:
                count[0] += 1
                return self._exact[found]
            anon = self._by_key.get(found.lower())
            if anon is None:  # pragma: no cover - alternation guarantees a hit
                return found
            count[0] += 1
            return _match_case(found, anon)

        return self._pattern.sub(_repl, text), count[0]

    def find(self, text):
        """The APPLIED real forms present in `text`. Not the audit: the audit
        searches for the whole inventory (audit_tabs), which is the point of
        MLB-323."""
        if self._pattern is None or not text:
            return []
        return [f for f in self._pattern.findall(text) if f not in self._protect]


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


def anonymize(spreadsheet, all_pairs, dry_run, tabs=None, protect=()):
    """Build the player oracle, drop colliding tokens, then rename tabs and
    rewrite every safe cell value and formula. Returns the player-safe
    replacer. `tabs` is the pre-write read, when the caller already has it."""
    # The oracle must be built before renaming: renaming rewrites formula
    # references, but the player display text it reads is unaffected, so an
    # up-front read is the clean source.
    if tabs is None:
        titles0 = [ws.title for ws in _sheets_call('list tabs', spreadsheet.worksheets)]
        tabs = _read_tabs(spreadsheet, titles0)
    player_names = build_player_oracle(tabs)
    safe, dropped = split_player_safe(all_pairs, player_names)
    _assert_auditable(safe)
    replacer = Replacer(safe, protect=protect)

    print(f'  player-name oracle: {len(player_names)} names from baseball-'
          f'reference links')
    print(f'  mapping forms: kept {len(safe)}, dropped {len(dropped)} that '
          f'collide with a real player name')
    if dropped:
        print(f'    dropped single names left unscrubbed (each occurs inside a '
              f'player name; the audit reports any that stay visible '
              f'elsewhere): lengths {sorted(len(_as_pair(p).real) for p in dropped)}')

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


def audit(spreadsheet, forms, notes, spine, roster_sources, kept_names=()):
    """Re-read the whole copy and search it for every inventoried real form
    -- not for the forms the mapping applied. Returns the residual count;
    zero is the pass condition."""
    titles = [ws.title for ws in _sheets_call('list tabs', spreadsheet.worksheets)]
    tabs = _read_tabs(spreadsheet, titles)
    result = audit_tabs(tabs, forms, spine, spreadsheet.title, kept_names)
    return print_audit(result, notes, spine, roster_sources)


# --------------------------------------------------------------------------
# What is left for the operator's hands
# --------------------------------------------------------------------------

def publish_checklist(target_ids):
    """The three steps between a clean audit and a publishable book.

    A scrub in place removes the real render from the cells, not from the
    file: Drive's version history still holds it (Study Material 08-06:
    4 revisions on a scrubbed book against 1 on a fresh copy of it). A copy
    starts with no history. This tool cannot take the steps itself -- its
    token is Sheets-scope only, by design -- so it prints them.
    """
    lines = ['REMAINING STEPS (by hand; this tool holds a Sheets-only token and '
             'cannot reach Drive):',
             '  The scrubbed copy still remembers the real render in its version '
             'history (File > Version history). A Drive copy starts with none.']
    for target_id in target_ids:
        url = f'https://docs.google.com/spreadsheets/d/{target_id}/edit'
        lines += [
            f'  {url}',
            '    1. Make a Drive copy of THIS scrubbed copy (File > Make a copy). '
            'The new copy has no revisions.',
            '    2. Share THAT new copy -- not this one -- as anyone-with-the-link, '
            'Viewer.',
            '    3. Delete THIS scrubbed copy (and empty it from Drive trash): it '
            'is the one whose history still holds the real names.',
        ]
    return lines


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        # Player and team names are not all cp1252; a console that cannot
        # print one must not crash the run halfway through a scrub.
        sys.stdout.reconfigure(errors='backslashreplace')
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--target', action='append',
                    help='spreadsheet id or url of a THROWAWAY copy; repeatable')
    ap.add_argument('--league', choices=sorted(load_registry()['leagues']),
                    help="the league whose book the target(s) copy; the audit's "
                         "form inventory is that league's")
    ap.add_argument('--player-spine', default=str(_SPINE_DIR),
                    help='MLB Stats API season documents (the positive player '
                         'check); default: %(default)s')
    ap.add_argument('--grow-spine', metavar='FIRST-LAST',
                    help='instead of anonymizing: fetch the MLB player lists of '
                         'seasons FIRST..LAST from the public Stats API and write '
                         f'the players the spine lacks to {_SPINE_SUPPLEMENT.name}')
    ap.add_argument('--dry-run', action='store_true',
                    help='report what would change; write nothing')
    args = ap.parse_args()

    if args.grow_spine:
        first, _, last = args.grow_spine.partition('-')
        n = grow_player_spine(int(first), int(last or first))
        print(f'spine supplement: {n} player(s) the spine lacked -> {_SPINE_SUPPLEMENT}')
        return 0
    if not args.target or not args.league:
        ap.error('--target and --league are required (unless --grow-spine)')

    _load_env()

    print('Deriving the mapping from the five seed pairs '
          '(disk = real, HEAD = anonymized)'
          + (' + the private scrub map' if _PRIVATE_MAP.is_file() else '')
          + ' ...')
    pairs, report = build_mapping(league=args.league)
    roster, roster_sources = platform_roster()
    forms, notes = build_form_inventory(args.league, report['kept'], roster)
    spine = load_player_spine(args.player_spine)
    print(f'  form inventory ({args.league}): {len(forms)} real forms to audit; '
          f'MLB spine: {len(spine)} names')

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
        tabs = _read_tabs(spreadsheet, [ws.title for ws in
                                        _sheets_call('list tabs', spreadsheet.worksheets)])
        # Before latch (b): its rename is this run's first write.
        kept_names = check_twins(tabs, forms, pairs, report['kept'], args.league,
                                 notes['owners'])
        latch_b_anon_title(spreadsheet, args.dry_run)

        # The player-safe replacer is derived per target from its own player
        # roster, so a sheet with different players drops the right tokens.
        anonymize(spreadsheet, pairs, args.dry_run, tabs=tabs, protect=kept_names)

        residuals = audit(spreadsheet, forms, notes, spine, roster_sources, kept_names)
        if residuals:
            print(f'  AUDIT FAILED: {residuals} real form(s) still visible.')
            failures.append((target_id, residuals))
        else:
            print('  AUDIT PASSED: zero real forms visible outside proven '
                  'player cells.')

    print()
    if args.dry_run:
        print('DRY RUN -- nothing was written. Counts above reflect the sheet '
              'as it stands, not as it would be.')
        return 0
    if failures:
        for target_id, residuals in failures:
            print(f'FAIL {target_id}: {residuals} residual(s)')
        print('Nothing here is publishable. Fix the residuals and re-run on a '
              'FRESH copy of the dev book -- a failed copy\'s version history '
              'holds the real render; delete it.')
        return 1
    print(f'All {len(args.target)} target(s) anonymized with zero residuals.')
    print()
    print('\n'.join(publish_checklist([_sheet_id(t) for t in args.target])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
