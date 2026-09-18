"""Fetch private corpus inputs; never uploads or contacts the public repository."""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

from dotenv import load_dotenv
from corpus_fixture import DEFAULT_FREEZE_ID, REPO, asset_name, digest, fixture_root, verify

REPO_SLUG = 'KyleDawson24/fantasy-league-almanac-dev'


def fetch(freeze_id, local=None):
    if REPO_SLUG != 'KyleDawson24/fantasy-league-almanac-dev':
        raise ValueError('Only the designated private repository is allowed')
    root = fixture_root(freeze_id)
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='fetch-', dir=root) as temporary:
        stage = Path(temporary)
        if local:
            archive = local.resolve()
        else:
            load_dotenv(REPO / '.env')
            env = dict(os.environ)
            if env.get('CORPUS_FIXTURE_GH_TOKEN'):
                env['GH_TOKEN'] = env['CORPUS_FIXTURE_GH_TOKEN']
            elif subprocess.run(['gh', 'auth', 'status'], env=env, capture_output=True).returncode:
                raise ValueError('Authenticate gh or set CORPUS_FIXTURE_GH_TOKEN in .env')
            private = subprocess.run(['gh', 'api', f'repos/{REPO_SLUG}', '--jq', '.private'],
                                     env=env, capture_output=True, text=True, check=True).stdout.strip()
            if private != 'true':
                raise ValueError('Fixture repository is not private')
            subprocess.run(['gh', 'release', 'download', f'corpus-fixture-{freeze_id}',
                '--repo', REPO_SLUG, '--dir', str(stage), '--pattern', asset_name(freeze_id),
                '--pattern', asset_name(freeze_id)+'.sha256', '--clobber'], env=env, check=True)
            archive = stage / asset_name(freeze_id)
        sidecar = archive.with_suffix('.tar.sha256')
        checksum, filename = sidecar.read_text(encoding='ascii').strip().split()
        if not re.fullmatch('[0-9a-f]{64}', checksum) or filename != asset_name(freeze_id) or digest(archive) != checksum:
            raise ValueError('Archive checksum or asset name mismatch')
        with tarfile.open(archive) as tar:
            for member in tar.getmembers():
                if not member.name.startswith('bundle/') or not (member.isfile() or member.isdir()):
                    raise ValueError('Unexpected archive member')
            tar.extractall(stage, filter='data')
        verify(freeze_id, stage)
        if (root / 'bundle').exists():
            verify(freeze_id)
            if digest(root / 'bundle/BUNDLE_MANIFEST.json') != digest(stage / 'bundle/BUNDLE_MANIFEST.json'):
                raise ValueError('Refusing to replace an existing freeze with different inputs')
        else:
            os.replace(stage / 'bundle', root / 'bundle')
        target = root / asset_name(freeze_id)
        if archive != target.resolve():
            shutil.copyfile(archive, target)
            shutil.copyfile(sidecar, target.with_suffix('.tar.sha256'))
    verify(freeze_id)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze-id', default=DEFAULT_FREEZE_ID)
    parser.add_argument('--from-local', type=Path)
    args = parser.parse_args()
    fetch(args.freeze_id, args.from_local)
