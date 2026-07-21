"""Pre-push guard against reintroducing real-league strings (MLB-95).

The anonymization map lives OUTSIDE the repo (archives/anonymization/
name_map.csv, gitignored) -- publishing the real-side strings in any
tracked config would defeat the point, which is why this cannot be a
gitleaks rule. On machines without the map (every clone but the
maintainer's) this script is a silent no-op.

Scans the HEAD tree (what a push publishes) for any real-side string
from the map and fails loudly with the offending files. Wired locally
via .git/hooks/pre-push; also runnable by hand:

    python tools/check_pii.py
"""
import csv
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP = os.path.join(REPO, "archives", "anonymization", "name_map.csv")


def main() -> int:
    if not os.path.exists(MAP):
        return 0  # clone without the private map: nothing to check against

    with open(MAP, newline="", encoding="utf-8") as f:
        reals = [r["real"] for r in csv.DictReader(f)
                 if r.get("real") and len(r["real"]) >= 4
                 # abbrev rows (FULT/MATT) are 4-char uppercase tokens that
                 # collide with ordinary words as fixed substrings -- skip
                 and not (r["real"].isupper() and len(r["real"]) <= 4)]
    if not reals:
        return 0

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="utf-8") as tf:
        tf.write("\n".join(reals))
        patterns = tf.name
    try:
        proc = subprocess.run(
            ["git", "-C", REPO, "grep", "-I", "-l", "-F", "-f", patterns,
             "HEAD"],
            capture_output=True, text=True, encoding="utf-8",
        )
    finally:
        os.unlink(patterns)

    if proc.returncode == 0 and proc.stdout.strip():
        print("PII GUARD: real-league strings found in the tree about to be "
              "pushed:", file=sys.stderr)
        print(proc.stdout, file=sys.stderr)
        print("Fix (or extend archives/anonymization/) before pushing. "
              "Bypass only if you are CERTAIN: git push --no-verify",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
