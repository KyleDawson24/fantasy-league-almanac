"""The release-bundle builder (MLB-209).

The credential this project distributes never enters git. It is injected
into a temporary tree by `tools/build_release_bundle.py` and shipped in a
ZIP. That makes the builder the single place where a live credential and
this codebase meet, so the properties worth testing are the ones that
keep the meeting brief:

  - it builds from a GIT REF, so a dirty working tree cannot ship;
  - it refuses to run if the source already contains a credential, which
    is the alarm for "one reached git after all";
  - it copies exactly two fields and refuses token material;
  - the finished tree and ZIP each carry exactly one credential, in the
    one file entitled to it;
  - it prints no value, ever;
  - the temporary tree is gone afterwards -- including after a failure,
    which is when a credential-bearing directory would otherwise be left
    lying around.

Fully synthetic. The credentials below are made up, and the git ref is a
throwaway repository built in tmp_path -- Kyle's real client JSON and the
real repository are never touched.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / 'tools'))

import build_release_bundle as brb          # noqa: E402  (path set above)


# Built from parts rather than written out, so this file contains no
# credential-shaped literal for a scanner to find.
_PREFIX = 'GOCSPX' + '-'


def synthetic_secret(tag='SYNTHETIC'):
    return _PREFIX + (tag + '0' * 28)[:28]


SYNTHETIC_ID = '000000000000-synthetic.apps.googleusercontent.com'
PUBLIC_FRONT_DOOR = (
    'START_ALMANAC.cmd',
    'ROTATE_ESPN_CREDENTIALS.cmd',
    'tools/windows_launcher.py',
    'docs/espn-cookie-guide.html',
    'QUICKSTART.md',
)


def client_json(tmp_path, **overrides):
    fields = {
        'client_id': SYNTHETIC_ID,
        'client_secret': synthetic_secret(),
        'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
        'token_uri': 'https://oauth2.googleapis.com/token',
        'project_id': 'synthetic-project',
        'redirect_uris': ['http://localhost'],
    }
    fields.update(overrides)
    path = tmp_path / 'client.json'
    path.write_text(json.dumps({'installed': fields}), encoding='utf-8')
    return path


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """A throwaway git repo carrying a descriptor with the real
    placeholder shape, so `git archive` and the injector are exercised
    for real rather than mocked."""
    root = tmp_path / 'repo'
    (root / 'output').mkdir(parents=True)

    descriptor = REPO_ROOT / 'output' / 'public_oauth_client.py'
    (root / 'output' / 'public_oauth_client.py').write_text(
        descriptor.read_text(encoding='utf-8'), encoding='utf-8')
    (root / 'README.md').write_text('synthetic\n', encoding='utf-8')
    for relative in PUBLIC_FRONT_DOOR:
        source = REPO_ROOT / relative
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())

    for args in (('init', '-q'), ('add', '-A'),
                 ('-c', 'user.email=t@t', '-c', 'user.name=t',
                  'commit', '-qm', 'synthetic')):
        subprocess.run(['git', *args], cwd=root, check=True,
                       capture_output=True)

    monkeypatch.setattr(brb, 'REPO_ROOT', root)
    return root


# ---------------------------------------------------------------------------
# Reading the external client JSON
# ---------------------------------------------------------------------------

def test_only_the_two_needed_fields_are_read(tmp_path):
    """`project_id` and `redirect_uris` are in the source file and must
    not travel: this flow uses neither, and project_id would publish
    which Cloud project the identity came from."""
    fields = brb.read_client_fields(client_json(tmp_path))

    assert set(fields) == {'client_id', 'client_secret'}


def test_a_cached_token_is_refused_rather_than_bundled(tmp_path):
    """THE dangerous confusion. A token cache also has client_id and
    client_secret in it; bundling one would ship a real person's granted
    access to their own Drive to every downloader."""
    path = client_json(tmp_path, refresh_token='1//synthetic-refresh')

    with pytest.raises(brb.BuildError, match='token material'):
        brb.read_client_fields(path)


def test_a_web_client_is_refused(tmp_path):
    path = tmp_path / 'web.json'
    path.write_text(json.dumps({'web': {'client_id': SYNTHETIC_ID}}),
                    encoding='utf-8')

    with pytest.raises(brb.BuildError, match='web client'):
        brb.read_client_fields(path)


@pytest.mark.parametrize('payload', ['{}', '[]', 'not json', '{"installed": 3}'])
def test_unrecognised_descriptor_shapes_are_refused(tmp_path, payload):
    path = tmp_path / 'odd.json'
    path.write_text(payload, encoding='utf-8')

    with pytest.raises(brb.BuildError):
        brb.read_client_fields(path)


@pytest.mark.parametrize('field', ['client_id', 'client_secret'])
def test_a_blank_required_field_is_refused(tmp_path, field):
    with pytest.raises(brb.BuildError, match=field):
        brb.read_client_fields(client_json(tmp_path, **{field: '   '}))


def test_a_missing_client_json_is_named_not_traced(tmp_path):
    with pytest.raises(brb.BuildError, match='not found'):
        brb.read_client_fields(tmp_path / 'nope.json')


# ---------------------------------------------------------------------------
# The build
# ---------------------------------------------------------------------------

def test_a_build_produces_one_credential_in_one_expected_place(
        fake_repo, tmp_path, capsys):
    out = tmp_path / 'dist'
    zip_path = brb.build(client_json(tmp_path), '9.9.9', ref='HEAD',
                         out_dir=out)

    assert zip_path.exists()
    assert brb.zip_census(zip_path) == {
        'fantasy-league-almanac-9.9.9/output/public_oauth_client.py': 1}


def test_a_build_writes_a_verifiable_public_checksum_asset(
        fake_repo, tmp_path):
    zip_path = brb.build(client_json(tmp_path), '9.9.9',
                         out_dir=tmp_path / 'dist')
    checksum_path = zip_path.with_suffix('.zip.sha256')
    expected = hashlib.sha256(zip_path.read_bytes()).hexdigest()

    assert checksum_path.read_bytes() == (
        f'{expected}  {zip_path.name}\n'.encode('ascii'))


def test_a_checksum_failure_leaves_no_upload_ready_zip(tmp_path, monkeypatch):
    zip_path = tmp_path / 'candidate.zip'
    zip_path.write_bytes(b'synthetic archive')

    def _refuse(*args, **kwargs):
        raise OSError('synthetic checksum write failure')

    monkeypatch.setattr(Path, 'write_text', _refuse)

    with pytest.raises(brb.BuildError, match='checksum sidecar'):
        brb.write_checksum(zip_path, '0' * 64)

    assert not zip_path.exists()
    assert not zip_path.with_suffix('.zip.sha256').exists()


def test_the_bundled_descriptor_is_the_working_one(fake_repo, tmp_path):
    """Not just present -- actually wired into BUNDLED_PUBLIC_CLIENT and
    passing the shipped validator."""
    zip_path = brb.build(client_json(tmp_path), '9.9.9',
                         out_dir=tmp_path / 'dist')

    member = 'fantasy-league-almanac-9.9.9/output/public_oauth_client.py'
    with zipfile.ZipFile(zip_path) as archive:
        text = archive.read(member).decode('utf-8')

    assert f"'client_secret': '{synthetic_secret()}'" in text
    assert f"'client_id': '{SYNTHETIC_ID}'" in text
    assert "'client_secret': ''" not in text


def test_ref_built_bundle_carries_the_complete_guided_front_door(
        fake_repo, tmp_path):
    zip_path = brb.build(client_json(tmp_path), '9.9.9',
                         out_dir=tmp_path / 'dist')
    prefix = 'fantasy-league-almanac-9.9.9/'

    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
        launcher = archive.read(prefix + 'START_ALMANAC.cmd').decode('utf-8')
        guide = archive.read(
            prefix + 'docs/espn-cookie-guide.html').decode('utf-8')

    for relative in PUBLIC_FRONT_DOOR:
        assert prefix + relative in names
    assert 'tools\\windows_launcher.py' in launcher
    assert guide.count('<svg') >= 2
    assert '<script' not in guide.lower()


def test_the_build_reads_the_ref_and_not_the_working_tree(
        fake_repo, tmp_path):
    """A dirty checkout is the normal state of a maintainer's machine.
    Shipping from it is how a debugging edit reaches a stranger."""
    stray = fake_repo / 'output' / 'public_oauth_client.py'
    stray.write_text(stray.read_text(encoding='utf-8')
                     + '\nUNCOMMITTED = "must not ship"\n', encoding='utf-8')

    zip_path = brb.build(client_json(tmp_path), '9.9.9',
                         out_dir=tmp_path / 'dist')

    with zipfile.ZipFile(zip_path) as archive:
        text = archive.read(
            'fantasy-league-almanac-9.9.9/output/public_oauth_client.py'
        ).decode('utf-8')

    assert 'UNCOMMITTED' not in text


def test_a_credential_already_in_the_source_stops_the_build(
        fake_repo, tmp_path):
    """THE ALARM. If the exported ref carries a credential-shaped literal,
    one has reached git history -- where it cannot be removed after the
    fact and where partner scanning will find it. The build must refuse
    rather than quietly produce a bundle."""
    leaked = fake_repo / 'output' / 'public_oauth_client.py'
    leaked.write_text(
        leaked.read_text(encoding='utf-8').replace(
            "'client_secret': ''", f"'client_secret': '{synthetic_secret()}'"),
        encoding='utf-8')
    subprocess.run(['git', 'add', '-A'], cwd=fake_repo, check=True,
                   capture_output=True)
    subprocess.run(['git', '-c', 'user.email=t@t', '-c', 'user.name=t',
                    'commit', '-qm', 'leak'], cwd=fake_repo, check=True,
                   capture_output=True)

    with pytest.raises(brb.BuildError, match='reached git history'):
        brb.build(client_json(tmp_path), '9.9.9', out_dir=tmp_path / 'dist')


def test_compiled_artifacts_never_enter_the_bundle(fake_repo, tmp_path):
    """THE REGRESSION THIS EXISTS FOR, and it shipped silently once.

    Validating the injected descriptor imports it, and Python wrote a
    `.pyc` beside it inside the release tree. A `.pyc` embeds the
    module's string constants, so the archive carried a SECOND copy of
    the credential -- invisible to a census that filtered by file
    extension, and it made the build nondeterministic as a side effect,
    which is the only reason it was noticed at all."""
    zip_path = brb.build(client_json(tmp_path), '9.9.9',
                         out_dir=tmp_path / 'dist')

    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()

    assert not [n for n in names if '__pycache__' in n or n.endswith('.pyc')]


def test_the_census_reads_bytes_rather_than_trusting_extensions(tmp_path):
    """The census is a proof about what is present, so it cannot decide
    in advance what is worth looking at. A credential inside a file with
    an unremarkable extension must still be found."""
    (tmp_path / 'nested').mkdir()
    (tmp_path / 'nested' / 'artifact.bin').write_bytes(
        b'\x00\x01compiled\x00' + synthetic_secret().encode() + b'\x00\xff')

    assert brb.census(tmp_path) == {'nested/artifact.bin': 1}


def test_the_build_is_deterministic(fake_repo, tmp_path):
    """Same ref, same credential, same version -- byte-identical archive,
    so a rebuild can be diffed against what was published."""
    first = brb.build(client_json(tmp_path), '9.9.9', out_dir=tmp_path / 'a')
    second = brb.build(client_json(tmp_path), '9.9.9', out_dir=tmp_path / 'b')

    assert first.read_bytes() == second.read_bytes()
    assert first.with_suffix('.zip.sha256').read_bytes() == (
        second.with_suffix('.zip.sha256').read_bytes())


# ---------------------------------------------------------------------------
# What must never escape
# ---------------------------------------------------------------------------

def test_no_value_is_ever_printed(fake_repo, tmp_path, capsys):
    brb.build(client_json(tmp_path), '9.9.9', out_dir=tmp_path / 'dist')

    printed = capsys.readouterr()
    for stream in (printed.out, printed.err):
        assert synthetic_secret() not in stream
        assert SYNTHETIC_ID not in stream
    assert 'length 35' in printed.out          # the redacted form instead


def test_no_error_message_carries_a_value(tmp_path):
    """Build failures get pasted into issues and chat logs."""
    path = client_json(tmp_path, refresh_token='1//synthetic')

    with pytest.raises(brb.BuildError) as exc:
        brb.read_client_fields(path)

    assert synthetic_secret() not in str(exc.value)


def test_the_temporary_tree_is_removed_after_a_successful_build(
        fake_repo, tmp_path, monkeypatch):
    seen = []
    real_mkdtemp = brb.tempfile.mkdtemp

    def _record(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        seen.append(Path(path))
        return path

    monkeypatch.setattr(brb.tempfile, 'mkdtemp', _record)
    brb.build(client_json(tmp_path), '9.9.9', out_dir=tmp_path / 'dist')

    assert seen and not [p for p in seen if p.exists()]


def test_the_temporary_tree_is_removed_after_a_FAILED_build(
        fake_repo, tmp_path, monkeypatch):
    """The case that matters: the tree holds a live credential from the
    moment injection runs, so a crash between then and cleanup is exactly
    when one would be left on disk."""
    seen = []
    real_mkdtemp = brb.tempfile.mkdtemp

    def _record(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        seen.append(Path(path))
        return path

    monkeypatch.setattr(brb.tempfile, 'mkdtemp', _record)

    def _boom(tree):
        raise brb.BuildError('synthetic failure after injection')

    monkeypatch.setattr(brb, 'validate_injected', _boom)

    with pytest.raises(brb.BuildError, match='synthetic failure'):
        brb.build(client_json(tmp_path), '9.9.9', out_dir=tmp_path / 'dist')

    assert seen and not [p for p in seen if p.exists()], (
        'a credential-bearing temporary tree survived a failed build'
    )


# ---------------------------------------------------------------------------
# The repository itself
# ---------------------------------------------------------------------------

def test_the_output_directory_is_gitignored():
    """The ZIP carries the credential. If `dist/` were trackable, one
    `git add -A` would put it in history -- the exact outcome this whole
    arrangement exists to prevent."""
    result = subprocess.run(['git', 'check-ignore', '-q', 'dist/x.zip'],
                            cwd=REPO_ROOT, capture_output=True)

    assert result.returncode == 0, 'dist/ is not gitignored'
