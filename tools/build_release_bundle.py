#!/usr/bin/env python3
"""Build the distributable Fantasy League Almanac release ZIP (MLB-209).

WHY THIS EXISTS. The published tool needs Google OAuth client credentials
to work at all -- Google's token endpoint requires a client secret for the
Desktop client this project ships (measured live; see
`output/public_oauth_client.py`). Google's installed-app guidance accepts
that a native app cannot keep those confidential, so shipping them in a
distributed artifact is legitimate.

A PUBLIC GIT REPOSITORY IS NOT A DISTRIBUTED ARTIFACT. GitHub's partner
secret scanning runs on public repositories out of band from any scanner
configuration the repository can carry, and out of band from a
push-protection bypass. A supported credential in public history is
reported to Google directly, and Google then decides whether it stays
valid. Nothing committed can be un-committed afterwards either -- history
is the problem, not the tip.

So the credential lives in exactly two places: the maintainer's own
machine, and the release archive this script builds. Never in git.

WHAT MAKES IT SAFE TO RUN:

  - it builds from a GIT REF (default HEAD), never from the working
    tree, so an experiment left in a file cannot be shipped by accident;
  - it verifies the archived source carries ZERO credential-shaped
    literals BEFORE injecting anything -- if that check ever fails, a
    credential has reached git and the build stops;
  - it reads exactly two fields from the external client JSON and copies
    nothing else, refusing token material and unrecognised shapes;
  - it verifies the FINISHED tree and the FINISHED ZIP each carry exactly
    one credential, in the one file that is supposed to have it;
  - it never prints, logs, or echoes either value -- only names, lengths
    and verdicts;
  - the temporary tree is removed in a `finally`, so a failure part-way
    through does not leave a credential-bearing directory behind.

Usage (from the repo root):

    .\\.venv\\Scripts\\python.exe tools\\build_release_bundle.py \\
        --client-json C:\\Users\\<you>\\.gcp\\<public-client>.json \\
        --version 1.9.0

Add `--ref v1.9.0` to build from a tag rather than HEAD. The output ZIP
is written to `dist/`, which is gitignored.
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / 'output'))

import public_oauth_client as poc          # noqa: E402  (path set above)

DESCRIPTOR_REL = 'output/public_oauth_client.py'

# The two lines the injector replaces. Matched exactly, and each must
# appear exactly once -- a descriptor whose shape drifted is a build
# failure, not something to paper over with a looser pattern.
PLACEHOLDERS = {
    'client_id': "        'client_id': '',\n",
    'client_secret': "        'client_secret': '',\n",
}

# Credential-shaped literals, for the before/after census. Deliberately
# UNANCHORED and not tied to `poc.GOOGLE_SECRET_SHAPE`, which is anchored
# for validating a whole field: here the job is to find one anywhere in a
# file, including inside a longer line.
SECRET_LITERAL = re.compile(r'GOCSPX-[A-Za-z0-9_-]{28}')

# Build artifacts that must never enter the bundle. `__pycache__` is the
# one that bit: validating the injected descriptor imports it, Python
# writes a .pyc beside it, and a .pyc embeds the module's string
# constants -- so the archive shipped a SECOND copy of the credential,
# in a file an earlier suffix-filtered census could not see. Excluded
# here, and the census below no longer trusts suffixes either.
EXCLUDED_PARTS = {'__pycache__'}
EXCLUDED_SUFFIXES = {'.pyc', '.pyo'}

FIELDS = ('client_id', 'client_secret')


class BuildError(RuntimeError):
    """Anything that should stop the build. Messages never carry values."""


def _git(*args, cwd=None):
    # Resolved at CALL time, not bound as a default: a default argument
    # would freeze REPO_ROOT at import and make this function permanently
    # point at whichever checkout the module was first loaded from.
    result = subprocess.run(['git', *args], cwd=cwd or REPO_ROOT,
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise BuildError(f"git {' '.join(args)} failed: "
                         f"{result.stderr.strip()}")
    return result.stdout.strip()


def export_ref(ref, destination):
    """Extract `ref` into `destination`. The working tree is not consulted.

    Via `git archive` rather than a copy, so what lands is exactly what
    the commit contains -- no untracked files, no local edits, no
    gitignored state such as token caches.
    """
    sha = _git('rev-parse', f'{ref}^{{commit}}')
    with tempfile.TemporaryDirectory() as staging:
        tar_path = Path(staging) / 'source.tar'
        _git('archive', '--format=tar', f'--output={tar_path}', sha)
        with tarfile.open(tar_path) as tar:
            tar.extractall(destination, filter='data')
    return sha


def _count_in(blob):
    """Credential-shaped literals in raw bytes.

    BYTES, NOT TEXT, AND NO SUFFIX FILTER. An earlier version scanned an
    allowlist of text-ish extensions and so declared a bundle clean while
    a `.pyc` inside it carried the secret in its string constants. A
    census that decides what to look at cannot be a proof about what is
    there, so this one looks at everything.
    """
    return len(SECRET_LITERAL.findall(blob.decode('utf-8', errors='ignore')))


def census(root):
    """Every file under `root` holding a credential-shaped literal, with
    counts. Returns {relative_posix_path: count}; never any value."""
    found = {}
    for path in Path(root).rglob('*'):
        if not path.is_file():
            continue
        try:
            count = _count_in(path.read_bytes())
        except OSError:
            continue
        if count:
            found[path.relative_to(root).as_posix()] = count
    return found


def read_client_fields(client_json_path):
    """Read ONLY client_id and client_secret from the external JSON.

    Everything else in a downloaded Desktop client -- `project_id`,
    `auth_provider_x509_cert_url`, `redirect_uris` -- is left behind:
    this flow uses none of it, and `project_id` would publish which Cloud
    project the identity came from for no benefit.
    """
    path = Path(client_json_path)
    if not path.is_file():
        raise BuildError(f'client JSON not found at {path}')

    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except ValueError as exc:
        raise BuildError(f'client JSON is not valid JSON: {exc}') from None

    if not isinstance(payload, dict) or poc.INSTALLED_KEY not in payload:
        kind = ('a web client' if isinstance(payload, dict)
                and 'web' in payload else 'an unrecognised shape')
        raise BuildError(f'client JSON is {kind}; an installed (Desktop app) '
                         f'client is required')

    fields = payload[poc.INSTALLED_KEY]
    if not isinstance(fields, dict):
        raise BuildError("the 'installed' section is not a JSON object")

    carried = sorted(f for f in poc.TOKEN_FIELDS if f in fields)
    if carried:
        raise BuildError(
            f"the client JSON carries token material ({', '.join(carried)}); "
            f"this is a cached grant, not a client descriptor, and must "
            f"never be bundled")

    extracted = {}
    for name in FIELDS:
        value = fields.get(name)
        if not isinstance(value, str) or not value.strip():
            raise BuildError(f'client JSON has no usable installed.{name}')
        extracted[name] = value.strip()
    return extracted


def inject(tree, fields):
    """Write the two values into the descriptor inside `tree`."""
    descriptor = Path(tree) / DESCRIPTOR_REL
    if not descriptor.is_file():
        raise BuildError(f'{DESCRIPTOR_REL} is missing from the exported ref')

    text = descriptor.read_text(encoding='utf-8')
    for name, placeholder in PLACEHOLDERS.items():
        if text.count(placeholder) != 1:
            raise BuildError(
                f'expected exactly one {name} placeholder in '
                f'{DESCRIPTOR_REL}, found {text.count(placeholder)} -- the '
                f'descriptor shape changed and the injector needs updating')
        text = text.replace(placeholder,
                            f"        '{name}': '{fields[name]}',\n")
    descriptor.write_text(text, encoding='utf-8')


def validate_injected(tree):
    """Import the injected descriptor in a SUBPROCESS and ask the shipped
    validator whether it is usable.

    A subprocess because this process already imported the repo's own
    copy; re-importing a second one under the same name would be a
    coin-flip about which module object answers. The child prints a
    verdict, never a value.
    """
    script = (
        'import sys; sys.path.insert(0, sys.argv[1]);'
        'import public_oauth_client as p;'
        'print(p.describe_problem(p.BUNDLED_PUBLIC_CLIENT) or "OK")'
    )
    # -B so importing the injected module writes no .pyc into the release
    # tree. A .pyc embeds string constants, so one would be a second copy
    # of the credential inside the bundle.
    result = subprocess.run(
        [sys.executable, '-B', '-c', script, str(Path(tree) / 'output')],
        capture_output=True, text=True)
    if result.returncode != 0:
        raise BuildError(f'the injected descriptor could not be imported: '
                         f'{result.stderr.strip()}')
    verdict = result.stdout.strip()
    if verdict != 'OK':
        raise BuildError(f'the injected descriptor is not usable: {verdict}')


def write_zip(tree, zip_path, prefix, commit_epoch):
    """Zip `tree` deterministically: sorted members, one fixed timestamp.

    Same ref plus same credential plus same version gives a byte-identical
    archive, so a rebuild can be compared against a published one.
    """
    stamp = commit_epoch
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(
        p for p in Path(tree).rglob('*')
        if p.is_file()
        and not EXCLUDED_PARTS & set(p.parts)
        and p.suffix.lower() not in EXCLUDED_SUFFIXES
    )

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            arcname = f'{prefix}/{path.relative_to(tree).as_posix()}'
            info = zipfile.ZipInfo(arcname, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    return zip_path


def zip_census(zip_path):
    """Which ZIP members hold a credential-shaped literal, and how many.

    Every member, whatever its extension -- see `_count_in`.
    """
    found = {}
    with zipfile.ZipFile(zip_path) as archive:
        for name in archive.namelist():
            count = _count_in(archive.read(name))
            if count:
                found[name] = count
    return found


def write_checksum(zip_path, digest):
    """Write the standard SHA-256 sidecar shipped beside the ZIP.

    A checksum printed only to the maintainer's terminal cannot be verified
    by a stranger after download. The sidecar is a second release asset and
    carries only the digest plus the public artifact name.
    """
    checksum_path = zip_path.with_suffix(zip_path.suffix + '.sha256')
    try:
        checksum_path.write_text(
            f'{digest}  {zip_path.name}\n', encoding='ascii', newline='\n')
    except OSError as exc:
        # A release bundle without its promised public checksum is not a
        # complete build. Do not leave the ZIP looking upload-ready.
        zip_path.unlink(missing_ok=True)
        checksum_path.unlink(missing_ok=True)
        raise BuildError(
            f'could not write checksum sidecar: {exc}') from None
    return checksum_path


def build(client_json, version, ref='HEAD', out_dir=None):
    out_dir = Path(out_dir or REPO_ROOT / 'dist')
    prefix = f'fantasy-league-almanac-{version}'
    zip_path = out_dir / f'{prefix}.zip'

    # Read and validate the credential BEFORE exporting anything, so a
    # bad client JSON costs nothing and creates no temporary tree.
    fields = read_client_fields(client_json)
    print(f'client JSON            : {Path(client_json).name}')
    for name in FIELDS:
        print(f'{name:<22} : length {len(fields[name])}')
    print(f"client_id suffix ok    : "
          f"{fields['client_id'].endswith(poc.CLIENT_ID_SUFFIX)}")
    print(f"client_secret shape ok : "
          f"{bool(poc.GOOGLE_SECRET_SHAPE.match(fields['client_secret']))}")
    print('values printed         : False')
    print()

    tree = Path(tempfile.mkdtemp(prefix='fla-release-'))
    try:
        sha = export_ref(ref, tree)
        print(f'ref                    : {ref}')
        print(f'commit                 : {sha}')

        # PROOF 1: the source this was built from carries no credential.
        before = census(tree)
        if before:
            raise BuildError(
                f'the exported source already contains credential-shaped '
                f'literals in {sorted(before)} -- a credential has reached '
                f'git history and this build must not continue')
        print('credentials in source  : 0  (verified, not assumed)')

        inject(tree, fields)
        validate_injected(tree)
        print('injected descriptor    : validates')

        # PROOF 2: exactly one credential, in the one file entitled to it.
        after = census(tree)
        if after != {DESCRIPTOR_REL: 1}:
            raise BuildError(
                f'after injection the tree carries {after}, expected exactly '
                f'{{{DESCRIPTOR_REL!r}: 1}}')
        print(f'credentials in tree    : 1  ({DESCRIPTOR_REL})')

        epoch = _git('show', '-s', '--format=%cd',
                     '--date=format:%Y %m %d %H %M %S', sha).split()
        write_zip(tree, zip_path, prefix,
                  tuple(int(part) for part in epoch))

        in_zip = zip_census(zip_path)
        expected = {f'{prefix}/{DESCRIPTOR_REL}': 1}
        if in_zip != expected:
            zip_path.unlink(missing_ok=True)
            raise BuildError(f'the finished ZIP carries {sorted(in_zip)}, '
                             f'expected {sorted(expected)}')
        print(f'credentials in ZIP     : 1  ({prefix}/{DESCRIPTOR_REL})')
    finally:
        # Always, including on every raise above: the tree holds a live
        # credential from the moment `inject` runs.
        shutil.rmtree(tree, ignore_errors=True)

    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    checksum_path = write_checksum(zip_path, digest)
    print()
    print(f'bundle                 : {zip_path}')
    print(f'size                   : {zip_path.stat().st_size:,} bytes')
    print(f'sha256                 : {digest}')
    print(f'checksum asset         : {checksum_path}')
    print(f'temp tree removed      : {not tree.exists()}')
    return zip_path


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Build the release ZIP with the public OAuth identity '
                    'injected. Never writes a credential into the repo.')
    parser.add_argument('--client-json', required=True,
                        help='Path to the public Desktop OAuth client JSON, '
                             'outside the repo.')
    parser.add_argument('--version', required=True,
                        help='Release version, e.g. 1.9.0. Names the ZIP and '
                             'its top-level directory.')
    parser.add_argument('--ref', default='HEAD',
                        help='Git ref to build from. Default HEAD. The '
                             'working tree is never used.')
    parser.add_argument('--out-dir', default=None,
                        help='Where to write the ZIP. Default dist/, which '
                             'is gitignored.')
    args = parser.parse_args(argv)

    try:
        build(args.client_json, args.version, ref=args.ref,
              out_dir=args.out_dir)
    except BuildError as exc:
        print(f'[release] {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
