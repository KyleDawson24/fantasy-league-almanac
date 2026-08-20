"""Regenerate the inventory counts in the three docs that quote them (MLB-153).

Governed documents:

    README.md                 the transform-layer shape sentence
    SETUP.md                  the run-book counts and the pytest expectations
    dbt_league/README.md      the DAG diagram, layer table and test counts

Every CURRENT PROJECT-INVENTORY count this script governs in those files
-- model totals, per-layer counts, materialization splits,
seed/source/exposure counts, dbt test counts, the pytest collection
numbers -- is DERIVED here and written back. Nothing governed is
transcribed. The counts drifted in the first place because they were kept
by hand: the ticket that asked for this script said "72 models" while the
tree held 74.

That scope is deliberately narrow. The same documents also carry
historical measurements, release dates, memory figures and other numbers
that are intentionally frozen or measured elsewhere, and this script does
not touch them. The anchor tables below are the exact list of what it
owns; a number not anchored there is somebody else's to keep true.

The root README was the last one still drifting. It claimed to be
"regenerated from the parsed manifest at each release cut" while nothing
regenerated it, so it sat at 78 models and 573 tests against a tree
holding 92 and 702 -- a sentence that documented its own freshness and
was wrong about it.

Run it as a release-cut step. It is deliberately
NOT a CI test, a pytest case, or a pre-commit hook: docs are allowed to
lag mid-cycle, and a check that goes red between releases is a check
people learn to ignore.

    python tools/refresh_doc_inventory.py            # rewrite in place
    python tools/refresh_doc_inventory.py --check    # report drift only
    python tools/refresh_doc_inventory.py --no-parse # reuse target/manifest.json

Where the numbers come from
---------------------------
*dbt counts* come from `dbt parse` -- rerun by default so the manifest
describes the tree you are standing in rather than whatever the last
build left behind. `parse` is tier 1: offline, no credentials, writes
only to the gitignored target/.

*pytest counts* come from `--collect-only`, restricted to test files that
`git ls-files` reports as TRACKED. This is load-bearing, not incidental:
the documented numbers are fresh-clone numbers, and the maintainer's
checkout carries untracked WIP test files (CLAUDE.md). Collecting the
whole directory would write 252 where a fresh clone sees 250.

How the rewrite works
---------------------
Each number is anchored by a regex with a single `n` group, so the prose
stays in the docs and only the digits are owned here. Every anchor must
match EXACTLY ONCE in its file; zero matches or two matches is a hard
error naming the anchor. That is the whole anti-rot mechanism -- an
anchor whose sentence got reworded fails loudly at the next release
instead of silently updating nothing.

Known limitation: a count that grows a digit can nudge the column
alignment of the ASCII DAG diagram in dbt_league/README.md. The numbers
are rewritten correctly; the art may want a manual space.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_DIR = os.path.join(REPO, "dbt_league")
MANIFEST = os.path.join(PROJECT_DIR, "target", "manifest.json")
SEED_REFERENCE_DIR = os.path.join(PROJECT_DIR, "seeds")
STAT_SEED = os.path.join(SEED_REFERENCE_DIR, "stat_classification.csv")

LAYERS = {
    "staging": "staging",
    "intermediate": "intermediate",
    "marts/core": "core",
    "marts/reporting": "reporting",
}


class Failure(Exception):
    """Something the caller must look at rather than work around."""


# --------------------------------------------------------------------------
# fact gathering
# --------------------------------------------------------------------------

def run_dbt_parse() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "dbt.cli.main", "parse",
         "--project-dir", PROJECT_DIR],
        cwd=REPO, capture_output=True, text=True, encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise Failure("dbt parse failed:\n" + (proc.stdout or "")
                      + (proc.stderr or ""))


def collect_pytest(tracked: list[str], warehouse: bool) -> int:
    """Collected-test count for the tracked files, as a fresh clone sees it."""
    cmd = [sys.executable, "-m", "pytest", *tracked, "--collect-only", "-q"]
    if warehouse:
        cmd += ["-m", "warehouse"]
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise Failure("pytest collection failed:\n" + (proc.stdout or "")
                      + (proc.stderr or ""))
    # "250/267 tests collected (17 deselected) in 1.32s", or without any
    # deselection, "267 tests collected in 1.32s".
    for line in reversed((proc.stdout or "").splitlines()):
        m = re.search(r"^(\d+)/\d+ tests? collected", line.strip())
        if m:
            return int(m.group(1))
        m = re.search(r"^(\d+) tests? collected", line.strip())
        if m:
            return int(m.group(1))
    raise Failure("could not read a collection count out of pytest:\n"
                  + (proc.stdout or ""))


def tracked_test_files() -> list[str]:
    proc = subprocess.run(["git", "-C", REPO, "ls-files", "--", "tests/"],
                          capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise Failure("git ls-files failed:\n" + (proc.stderr or ""))
    files = [p for p in proc.stdout.splitlines()
             if os.path.basename(p).startswith("test_") and p.endswith(".py")]
    if not files:
        raise Failure("no tracked test files found under tests/")
    return sorted(files)


def seed_root_of(node) -> str:
    """'reference' for the tracked vocabulary root, 'league_config' otherwise.

    seed-paths is ["seeds", env_var('DBT_LEAGUE_CONFIG', 'league_config')],
    so the SECOND root moves -- the demo fixture points it at
    demo/league_config, and the test harness points it at absolute temp
    directories. The reference root never moves, so it is the one worth
    testing for, and everything outside it is league config by
    construction. Comparing resolved paths rather than the raw string
    keeps that true whether the override arrived relative or absolute.
    """
    resolved = os.path.normcase(os.path.abspath(
        os.path.join(PROJECT_DIR, node["original_file_path"])))
    reference = os.path.normcase(os.path.abspath(SEED_REFERENCE_DIR)) + os.sep
    return "reference" if resolved.startswith(reference) else "league_config"


def gather() -> dict[str, int]:
    with open(MANIFEST, encoding="utf-8") as f:
        manifest = json.load(f)

    nodes = manifest["nodes"].values()
    models = [n for n in nodes if n["resource_type"] == "model"]
    tests = [n for n in nodes if n["resource_type"] == "test"]
    seeds = [n for n in nodes if n["resource_type"] == "seed"]

    def layer_of(node) -> str:
        return os.path.dirname(node["path"]).replace("\\", "/")

    by_layer: dict[str, list] = {v: [] for v in LAYERS.values()}
    for n in models:
        key = LAYERS.get(layer_of(n))
        if key is None:
            raise Failure(
                f"model {n['name']} sits in an undocumented layer "
                f"'{layer_of(n)}' -- the README's DAG describes "
                f"{sorted(LAYERS)}. Update the prose, then this script."
            )
        by_layer[key].append(n)

    def mat(layer: str, materialization: str) -> int:
        return sum(1 for n in by_layer[layer]
                   if n["config"]["materialized"] == materialization)

    def mat_total(materialization: str) -> int:
        """Project-wide materialization count, for the root README."""
        return sum(1 for n in models
                   if n["config"]["materialized"] == materialization)

    core = by_layer["core"]
    with open(STAT_SEED, newline="", encoding="utf-8-sig") as f:
        stat_rows = sum(1 for _ in csv.DictReader(f))

    tracked = tracked_test_files()

    facts = {
        "models_total": len(models),
        "models_staging": len(by_layer["staging"]),
        "models_intermediate": len(by_layer["intermediate"]),
        "models_core": len(core),
        "models_reporting": len(by_layer["reporting"]),
        "core_dims": sum(1 for n in core if n["name"].startswith("dim_")),
        "core_facts": sum(1 for n in core if n["name"].startswith("fct_")),
        "staging_views": mat("staging", "view"),
        "intermediate_views": mat("intermediate", "view"),
        "core_views": mat("core", "view"),
        "core_incremental": mat("core", "incremental"),
        "reporting_views": mat("reporting", "view"),
        "models_views": mat_total("view"),
        "models_tables": mat_total("table"),
        "models_incremental": mat_total("incremental"),
        "seeds": len(seeds),
        "seeds_reference": sum(1 for n in seeds
                               if seed_root_of(n) == "reference"),
        "seeds_league_config": sum(1 for n in seeds
                                   if seed_root_of(n) == "league_config"),
        "sources": len(manifest["sources"]),
        "exposures": len(manifest["exposures"]),
        "tests_total": len(tests),
        "tests_generic": sum(1 for t in tests if t.get("test_metadata")),
        "tests_singular": sum(1 for t in tests if not t.get("test_metadata")),
        "stat_rows": stat_rows,
        "pytest_pure": collect_pytest(tracked, warehouse=False),
        "pytest_warehouse": collect_pytest(tracked, warehouse=True),
    }

    covered = sum(len(v) for v in by_layer.values())
    if covered != facts["models_total"]:  # pragma: no cover - guard only
        raise Failure(f"layer counts sum to {covered}, not "
                      f"{facts['models_total']}")
    if facts["core_dims"] + facts["core_facts"] != facts["models_core"]:
        raise Failure("marts/core holds a model that is neither dim_ nor "
                      "fct_; the README's '8 dims + 11 facts' sentence no "
                      "longer describes the layer.")
    materialized = (facts["models_views"] + facts["models_tables"]
                    + facts["models_incremental"])
    if materialized != facts["models_total"]:
        raise Failure(
            f"the materialization split covers {materialized} of "
            f"{facts['models_total']} models, so the root README's "
            f"'(N views, N tables, N incremental)' no longer adds up. A "
            f"fourth materialization (ephemeral, materialized_view) has "
            f"appeared -- widen the sentence, then this script."
        )
    if facts["seeds_reference"] + facts["seeds_league_config"] != facts["seeds"]:
        raise Failure(  # pragma: no cover - the split is exhaustive by
            f"seed roots sum to "  # construction; guard against a future third
            f"{facts['seeds_reference'] + facts['seeds_league_config']}, "
            f"not {facts['seeds']}"
        )
    return facts


# --------------------------------------------------------------------------
# the anchors
# --------------------------------------------------------------------------
# (fact key, regex). Exactly one `n` group; must match exactly once.

README_RULES = [
    ("sources",             r"RAW\.\* sources \((?P<n>\d+)\)"),
    ("seeds",               r"RAW\.\* sources \(\d+\)\s+seeds \((?P<n>\d+)\)"),
    ("models_staging",      r"staging/\s+\((?P<n>\d+)\) stg_\*"),
    ("models_intermediate", r"intermediate/\s+\((?P<n>\d+)\) int_\*"),
    ("models_core",         r"marts/core/\s+\((?P<n>\d+)\) dim_\*"),
    ("core_dims",           r"contract layer: (?P<n>\d+) dims"),
    ("core_facts",          r"\+ (?P<n>\d+) facts \(daily"),
    ("models_reporting",    r"marts/reporting/\s+\((?P<n>\d+)\) mart_\*"),
    ("exposures",           r"(?m)^exposures \((?P<n>\d+)\)"),
    ("models_total",        r"(?m)^(?P<n>\d+) models in all\."),
    ("staging_views",       r"\((?P<n>\d+) of \d+ pin `view` themselves\)"),
    ("models_staging",      r"\(\d+ of (?P<n>\d+) pin `view` themselves\)"),
    ("models_intermediate", r"\(all (?P<n>\d+) pin their own;"),
    ("intermediate_views",  r"pin their own; (?P<n>\d+) are views\)"),
    ("core_incremental",    r"\((?P<n>\d+) weekly facts override to incremental"),
    ("core_views",          r"incremental; (?P<n>\d+) thin dims/facts to view\)"),
    ("reporting_views",     r"\((?P<n>\d+) of \d+ override to view\)"),
    ("models_reporting",    r"\(\d+ of (?P<n>\d+) override to view\)"),
    ("stat_rows",           r"stat_classification\.csv` \((?P<n>\d+) rows\)"),
    ("tests_total",         r"(?m)^(?P<n>\d+) dbt data tests \("),
    ("tests_generic",       r"dbt data tests \((?P<n>\d+) generic"),
    ("tests_singular",      r"generic \+ (?P<n>\d+) singular\)"),
    ("tests_generic",       r"\*\*Generic tests\*\* \((?P<n>\d+)\)"),
    ("tests_singular",      r"\*\*Singular tests\*\* \((?P<n>\d+),"),
    ("seeds",               r"# load the (?P<n>\d+) seed CSVs"),
]

# The root README's one inventory sentence. It advertises itself as
# regenerated at each cut, so it has to actually be.
ROOT_README_RULES = [
    ("models_total",       r"\*\*(?P<n>\d+) dbt models\*\*"),
    ("models_views",       r"\*\*\d+ dbt models\*\* \((?P<n>\d+) views,"),
    ("models_tables",      r"\*\*\d+ dbt models\*\* \(\d+ views, (?P<n>\d+) tables,"),
    ("models_incremental", r"\d+ views, \d+ tables, (?P<n>\d+) incremental\)"),
    ("seeds",              r"\*\*(?P<n>\d+) seeds\*\*"),
    ("tests_total",        r"\*\*(?P<n>\d+) data tests\*\*"),
    ("sources",            r"\*\*(?P<n>\d+) sources\*\*"),
    ("exposures",          r"\*\*(?P<n>\d+) declared exposures\*\*"),
    # Collection counts, and the prose says so. These replaced a frozen
    # "749 passed, 3 skipped" from one CI run that stopped being current
    # the moment anything was added -- and read as a pass guarantee the
    # generator was never in a position to make, since it derives these
    # from `--collect-only`.
    ("pytest_pure",        r"collects \*\*(?P<n>\d+)\*\* tracked pure tests"),
    ("pytest_warehouse",   r"with \*\*(?P<n>\d+)\*\* warehouse-marked goldens"),
]

SETUP_RULES = [
    ("seeds",            r"# Load the (?P<n>\d+) seed CSVs"),
    # The breakdown, derived rather than written once: 5 + 13 outlived two
    # seed additions and still summed to 18 under a headline of 20.
    ("seeds_reference",     r"seed CSVs -- (?P<n>\d+) reference"),
    ("seeds_league_config", r"# \+ (?P<n>\d+) from league_config"),
    ("models_total",     r"# Build (?P<n>\d+) models \+ run"),
    ("tests_total",      r"models \+ run (?P<n>\d+) tests"),
    ("pytest_pure",      r"collection at this cut: \*\*(?P<n>\d+)\*\* pure tests"),
    ("pytest_warehouse", r"pure tests, with \*\*(?P<n>\d+)\*\*"),
    ("pytest_warehouse", r"This collects \*\*(?P<n>\d+) tests\*\*"),
    ("pytest_warehouse", r"How many of the (?P<n>\d+) actually run"),
]

TARGETS = [
    ("README.md", ROOT_README_RULES),
    ("SETUP.md", SETUP_RULES),
    (os.path.join("dbt_league", "README.md"), README_RULES),
]


def apply_rules(text: str, rules, facts: dict[str, int], where: str):
    """Rewrite every anchor. Returns (new_text, [(key, old, new), ...])."""
    changes = []
    for key, pattern in rules:
        matches = list(re.finditer(pattern, text))
        if len(matches) != 1:
            raise Failure(
                f"{where}: anchor for '{key}' matched {len(matches)} times, "
                f"expected exactly 1.\n  pattern: {pattern}\n"
                f"  The prose it anchors to was probably reworded. Fix the "
                f"pattern in tools/refresh_doc_inventory.py."
            )
        m = matches[0]
        old, new = m.group("n"), str(facts[key])
        if old != new:
            changes.append((key, old, new))
            start, end = m.span("n")
            text = text[:start] + new + text[end:]
    return text, changes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="report drift and exit 1; write nothing")
    ap.add_argument("--no-parse", action="store_true",
                    help="reuse the existing target/manifest.json")
    args = ap.parse_args()

    try:
        if not args.no_parse:
            run_dbt_parse()
        elif not os.path.exists(MANIFEST):
            raise Failure(f"--no-parse but no manifest at {MANIFEST}")
        facts = gather()

        print(f"models   {facts['models_total']:>4}  "
              f"(staging {facts['models_staging']}, "
              f"intermediate {facts['models_intermediate']}, "
              f"core {facts['models_core']}, "
              f"reporting {facts['models_reporting']})")
        print(f"seeds    {facts['seeds']:>4}   sources {facts['sources']}   "
              f"exposures {facts['exposures']}")
        print(f"dbt tests{facts['tests_total']:>4}  "
              f"({facts['tests_generic']} generic + "
              f"{facts['tests_singular']} singular)")
        print(f"pytest   {facts['pytest_pure']:>4}  pure, "
              f"{facts['pytest_warehouse']} warehouse "
              f"(fresh clone: tracked files only)")
        print()

        # Resolve every anchor in every file BEFORE writing any of them.
        # A broken anchor is a hard error, and aborting halfway would
        # otherwise leave one doc rewritten and the other stale.
        planned = []
        for rel, rules in TARGETS:
            path = os.path.join(REPO, rel)
            # newline="" on both sides: these files are CRLF on disk, and a
            # universal-newlines read followed by an untranslated write
            # would silently reline the whole file into one giant diff.
            with open(path, encoding="utf-8", newline="") as f:
                original = f.read()
            updated, changes = apply_rules(original, rules, facts, rel)
            planned.append((rel, path, rules, updated, changes))

        drifted = False
        for rel, path, rules, updated, changes in planned:
            label = rel.replace(os.sep, "/")
            if not changes:
                print(f"{label:<22} {len(rules):>2} anchors, up to date")
                continue
            drifted = True
            print(f"{label:<22} {len(rules):>2} anchors, "
                  f"{len(changes)} changed")
            for key, old, new in changes:
                print(f"    {key:<20} {old} -> {new}")
            if not args.check:
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(updated)

        print()
        if not drifted:
            print("Docs already match the tree. Nothing written.")
        elif args.check:
            print("Drift found (--check: nothing written).")
            return 1
        else:
            print("Docs updated. Review the diff, then commit with the cut.")
    except Failure as exc:
        print(f"\nrefresh_doc_inventory: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
