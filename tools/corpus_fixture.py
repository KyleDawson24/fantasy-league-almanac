"""Build byte-diff inputs from a private, immutable RAW/config snapshot (MLB-295)."""
import argparse
import csv
import io
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone

REPO = Path(__file__).resolve().parents[1]
LIVE_DB = (REPO / 'data/duckdb/ESPN_FANTASY.duckdb').resolve()
DEFAULT_FREEZE_ID = 'wk22-2026'


def fixture_root(freeze_id):
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', freeze_id):
        raise ValueError('Invalid freeze id')
    return REPO / 'data/fixtures' / f'corpus-{freeze_id}'


def asset_name(freeze_id):
    return f'corpus-fixture-{freeze_id}.tar'


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str)+'\n', encoding='utf-8')


def public_columns(filename):
    """Read the committed template, never the private working copy."""
    result = subprocess.run(['git', 'show', f'HEAD:dbt_league/league_config/{filename}'],
                            cwd=REPO, capture_output=True, check=True)
    columns = next(csv.reader(io.StringIO(result.stdout.decode('utf-8-sig'))))
    if not columns or len(columns) != len(set(columns)):
        raise ValueError(f'Invalid public schema: {filename}')
    return columns


def project_config(source_dir, destination):
    """Validate every used cell before writing any projected CSV; never log values."""
    prepared = []
    for source in sorted(source_dir.glob('*.csv')):
        columns = public_columns(source.name)
        before = digest(source)
        with source.open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.DictReader(stream)
            headers = reader.fieldnames or []
            if len(headers) != len(set(headers)) or not set(columns) <= set(headers):
                raise ValueError(f'Missing or duplicate declared columns: {source.name}')
            rows = list(reader)
        if any(None in row or any(row[c] is None for c in columns) for row in rows):
            raise ValueError(f'Malformed CSV row: {source.name} (values withheld)')
        output = io.StringIO(newline='')
        writer = csv.DictWriter(output, fieldnames=columns, lineterminator='\n', extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
        data = output.getvalue().encode('utf-8')
        projected = list(csv.DictReader(io.StringIO(data.decode('utf-8'), newline='')))
        equal = {c: [row[c] for row in rows] == [row[c] for row in projected] for c in columns}
        if len(rows) != len(projected) or not all(equal.values()) or digest(source) != before:
            raise ValueError(f'Projection validation failed: {source.name} (values withheld)')
        prepared.append((source.name, data, dict(source_sha256=before,
            declared_columns=columns, dropped_columns=[c for c in headers if c not in columns],
            projected_sha256=hashlib.sha256(data).hexdigest(), source_row_count=len(rows),
            projected_row_count=len(projected), per_column_equal=equal)))
    if len(prepared) != 14:
        raise ValueError(f'Expected 14 projected configuration files; got {len(prepared)}')
    for name, data, _ in prepared:
        (destination / name).write_bytes(data)
    return {name: record for name, _, record in prepared}


def column_gate(bundle, write_report=True):
    """Inventory physical columns; RAW declarations come from the dump's schema manifest.

    This is a column-name gate, not inspection of keys/values inside JSON payloads.
    """
    import duckdb
    manifest = json.loads((bundle / 'raw/_manifest.json').read_text(encoding='utf-8'))
    declared_raw = {t['table']: [c['name'] for c in t['columns']] for t in manifest['tables']}
    inventory, failures = [], []
    hints = ('email', 'e_mail', 'phone', 'address', 'postal', 'zip', 'mobile', 'telephone', 'contact', 'fax')
    with duckdb.connect() as connection:
        for path in sorted((bundle / 'raw').glob('*.parquet')):
            columns = [r[0] for r in connection.execute('describe select * from read_parquet(?)', [str(path)]).fetchall()]
            declared = declared_raw.get(path.stem, [])
            inventory.append(dict(file=path.relative_to(bundle).as_posix(), columns=columns,
                                  declared_columns=declared, schema_source='raw/_manifest.json'))
        for path in sorted((bundle / 'league_config').glob('*.csv')):
            with path.open(encoding='utf-8-sig', newline='') as stream:
                columns = next(csv.reader(stream))
            inventory.append(dict(file=path.relative_to(bundle).as_posix(), columns=columns,
                declared_columns=public_columns(path.name), schema_source=f'HEAD:dbt_league/league_config/{path.name}'))
    for item in inventory:
        for column in item['columns']:
            if any(hint in column.lower() for hint in hints):
                failures.append(dict(file=item['file'], column=column, reason='contact-like column name'))
            if column not in item['declared_columns']:
                failures.append(dict(file=item['file'], column=column, reason='undeclared column'))
        for column in set(item['declared_columns']) - set(item['columns']):
            failures.append(dict(file=item['file'], column=column, reason='missing declared column'))
    raw_count = sum(i['file'].startswith('raw/') for i in inventory)
    csv_count = len(inventory)-raw_count
    if (raw_count, csv_count) != (30, 14):
        failures.append(dict(file='inventory', column='', reason='expected 30 RAW files and 14 CSVs'))
    report = dict(raw_files=raw_count, csv_files=csv_count,
        column_occurrences=sum(len(i['columns']) for i in inventory),
        unique_column_names=len({c for i in inventory for c in i['columns']}),
        result='STOP' if failures else 'PASS', failures=failures, inventory=inventory,
        scope='Physical column names only; JSON payload contents are not inspected')
    if write_report:
        write_json(bundle / 'COLUMN_INVENTORY.json', report)
    print(f'Column gate: {report["result"]}; {raw_count} RAW + {csv_count} CSV files; {report["column_occurrences"]} columns', flush=True)
    if failures:
        for failure in failures:
            print(f'{failure["file"]}: {failure["column"]}: {failure["reason"]}', flush=True)
        raise ValueError('Column gate STOP; see COLUMN_INVENTORY.json (names only)')
    return report


def package(freeze_id, metadata):
    root = fixture_root(freeze_id)
    bundle = root / 'bundle'
    metadata['files'] = {p.relative_to(bundle).as_posix(): {'sha256': digest(p), 'bytes': p.stat().st_size}
                        for p in sorted(bundle.rglob('*')) if p.is_file() and p.name != 'BUNDLE_MANIFEST.json'}
    write_json(bundle / 'BUNDLE_MANIFEST.json', metadata)
    archive = root / asset_name(freeze_id)
    with tarfile.open(archive, 'w') as tar:
        for path in sorted(bundle.rglob('*')):
            info = tar.gettarinfo(str(path), arcname=path.relative_to(root).as_posix())
            info.mtime = info.uid = info.gid = 0
            info.uname = info.gname = ''
            if path.is_file():
                with path.open('rb') as stream:
                    tar.addfile(info, stream)
            else:
                tar.addfile(info)
    archive.with_suffix('.tar.sha256').write_text(f'{digest(archive)}  {archive.name}\n', encoding='ascii')
    print(f'Packaged {archive.stat().st_size} bytes', flush=True)


def reproject(freeze_id, league_config):
    """Apply the approved projection amendment to the unuploaded first freeze."""
    root = fixture_root(freeze_id)
    metadata = verify(freeze_id, allow_unprojected=True)
    if metadata.get('projections'):
        raise ValueError('Projection amendment already applied')
    for source in league_config.glob('*.csv'):
        if digest(source) != metadata['files'][f'league_config/{source.name}']['sha256']:
            raise ValueError(f'Private source changed since freeze: {source.name}')
    # Quarantine the obsolete archive before changing its unpacked contents.
    retired = root / 'superseded-unprojected-do-not-upload'
    retired.mkdir(exist_ok=False)
    for path in [root / asset_name(freeze_id), root / (asset_name(freeze_id)+'.sha256')]:
        if not path.resolve().is_relative_to(root.resolve()) or not retired.resolve().is_relative_to(root.resolve()):
            raise ValueError('Unsafe archive relocation')
        os.replace(path, retired / path.name)
    shutil.copyfile(root / 'bundle/BUNDLE_MANIFEST.json', retired / 'BUNDLE_MANIFEST.json')
    projections = project_config(league_config, root / 'bundle/league_config')
    write_json(root / 'bundle/PROJECTION_STAMPS.json', projections)
    metadata['projections'] = projections
    metadata['projection_amendment_utc'] = datetime.now(timezone.utc).isoformat()
    column_gate(root / 'bundle')
    package(freeze_id, metadata)


def freeze(freeze_id, raw_dir, league_config):
    import duckdb
    root = fixture_root(freeze_id)
    bundle = root / 'bundle'
    if bundle.exists():
        raise ValueError('Freeze already exists; never overwrite frozen inputs')
    manifest = json.loads((raw_dir / '_manifest.json').read_text(encoding='utf-8'))
    manifest['tables'] = [t for t in manifest['tables'] if '_BAK_' not in t['table']]
    files = sorted(p for p in raw_dir.glob('*.parquet') if '_BAK_' not in p.name)
    seeds = sorted(league_config.glob('*.csv'))
    if len(files) != 30 or len(seeds) != 14:
        raise ValueError(f'Expected 30 RAW tables and 14 CSVs; got {len(files)}, {len(seeds)}')
    if {p.stem for p in files} != {t['table'] for t in manifest['tables']}:
        raise ValueError('RAW manifest and files disagree')
    with duckdb.connect() as con:
        probes = [con.execute(sql, [str(raw_dir / name)]).fetchall() for name, sql in [
            ('BOX_SCORES.parquet', 'select max(matchup_period),max(scoring_period),count(*) from read_parquet(?) where season_year=2026'),
            ('CBS_STANDINGS.parquet', 'select max(period) from read_parquet(?) where season_year=2026'),
            ('CBS_ROSTERS.parquet', 'select max(roster_date) from read_parquet(?) where season_year=2026'),
            ('MLB_GAMELOGS.parquet', 'select max(game_date),count(*) from read_parquet(?)')]]
    if freeze_id == DEFAULT_FREEZE_ID and str(probes) != "[[(22, 166, 166)], [(24,)], [(datetime.date(2026, 9, 6),)], [(datetime.date(2026, 9, 6), 1824248)]]":
        raise ValueError(f'Freeze horizon changed: {probes}')
    (bundle / 'raw').mkdir(parents=True)
    (bundle / 'league_config').mkdir()
    for path in files:
        shutil.copyfile(path, bundle / 'raw' / path.name)
    projections = project_config(league_config, bundle / 'league_config')
    write_json(bundle / 'PROJECTION_STAMPS.json', projections)
    write_json(bundle / 'raw/_manifest.json', manifest)
    column_gate(bundle)
    inventory = {p.relative_to(bundle).as_posix(): {'sha256': digest(p), 'bytes': p.stat().st_size}
                 for p in sorted(bundle.rglob('*')) if p.is_file()}
    write_json(bundle / 'BUNDLE_MANIFEST.json', dict(freeze_id=freeze_id,
        created_utc=datetime.now(timezone.utc).isoformat(),
        source=dict(raw_dir=str(raw_dir), dumped='2026-09-13 16:27-16:30 EDT from Snowflake ESPN_FANTASY.RAW', probes=probes), files=inventory, projections=projections))
    archive = root / asset_name(freeze_id)
    with tarfile.open(archive, 'w') as tar:
        for path in sorted(bundle.rglob('*')):
            info = tar.gettarinfo(str(path), arcname=path.relative_to(root).as_posix())
            info.mtime = info.uid = info.gid = 0
            info.uname = info.gname = ''
            if path.is_file():
                with path.open('rb') as stream:
                    tar.addfile(info, stream)
            else:
                tar.addfile(info)
    archive.with_suffix('.tar.sha256').write_text(f'{digest(archive)}  {archive.name}\n', encoding='ascii')
    print(f'Frozen {len(files)} tables, {len(seeds)} CSVs; {archive.stat().st_size} bytes', flush=True)


def verify(freeze_id, root=None, allow_unprojected=False):
    root = root or fixture_root(freeze_id)
    bundle = root / 'bundle'
    manifest = json.loads((bundle / 'BUNDLE_MANIFEST.json').read_text(encoding='utf-8'))
    if manifest['freeze_id'] != freeze_id:
        raise ValueError('Manifest freeze id mismatch')
    actual = {p.relative_to(bundle).as_posix() for p in bundle.rglob('*') if p.is_file()}
    if actual != set(manifest['files']) | {'BUNDLE_MANIFEST.json'}:
        raise ValueError('Missing or extra fixture files')
    for name, spec in manifest['files'].items():
        path = (bundle / name).resolve()
        if not path.is_relative_to(bundle.resolve()) or path.is_symlink():
            raise ValueError('Unsafe manifest path')
        if path.stat().st_size != spec['bytes'] or digest(path) != spec['sha256']:
            raise ValueError(f'Fixture checksum mismatch: {name}')
    if not manifest.get('projections') and not allow_unprojected:
        raise ValueError('Unprojected fixture is superseded; do not use or upload it')
    if manifest.get('projections'):
        projections = json.loads((bundle / 'PROJECTION_STAMPS.json').read_text(encoding='utf-8'))
        if projections != manifest['projections'] or len(projections) != 14:
            raise ValueError('Projection stamp mismatch')
        for name, record in projections.items():
            if (record['source_row_count'] != record['projected_row_count']
                    or not all(record['per_column_equal'].values())
                    or record['projected_sha256'] != digest(bundle / 'league_config' / name)):
                raise ValueError(f'Invalid projection proof: {name}')
        actual_inventory = column_gate(bundle, write_report=False)
        if actual_inventory != json.loads((bundle / 'COLUMN_INVENTORY.json').read_text(encoding='utf-8')):
            raise ValueError('Column inventory changed')
    archive = root / asset_name(freeze_id)
    if archive.exists():
        expected = archive.with_suffix('.tar.sha256').read_text(encoding='ascii').split()[0]
        if digest(archive) != expected:
            raise ValueError('Archive checksum mismatch')
    print(f'verified {len(manifest["files"])} fixture files', flush=True)
    return manifest


def stamp(freeze_id, manifest):
    paths = []
    for folder in ('models', 'macros', 'seeds'):
        paths.extend(p for p in (REPO / 'dbt_league' / folder).rglob('*') if p.is_file())
    paths.extend(REPO / p for p in ('dbt_league/dbt_project.yml', 'dbt_league/packages.yml',
        'dbt_league/package-lock.yml', 'dbt_league/profiles/profiles.yml', 'tools/load_parquet_to_duckdb.py'))
    h = hashlib.sha256()
    for path in sorted(paths):
        h.update(path.relative_to(REPO).as_posix().encode()+b'\0'+path.read_bytes()+b'\0')
    inventory = '\n'.join(f'{name} {spec["sha256"]}' for name, spec in sorted(manifest['files'].items()))
    return dict(freeze_id=freeze_id, logic_sha256=h.hexdigest(),
        tar_sha256=(fixture_root(freeze_id)/(asset_name(freeze_id)+'.sha256')).read_text().split()[0],
        bundle_files_sha256=hashlib.sha256(inventory.encode()).hexdigest(),
        versions={name: importlib.metadata.version(name) for name in ('dbt-core', 'dbt-duckdb', 'duckdb')})


def remove_cache(path, root):
    path = path.resolve()
    if path.parent != root.resolve() or path.name not in ('cache', 'cache.building'):
        raise ValueError('Refusing unsafe cache removal')
    if path.exists():
        shutil.rmtree(path)


def build(freeze_id):
    root = fixture_root(freeze_id)
    lock = root / 'cache.lock'
    deadline = time.monotonic() + 3600
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError('Fixture cache is locked')
            time.sleep(10)
    try:
        desired = stamp(freeze_id, verify(freeze_id))
        cache = root / 'cache'
        if (cache / 'STAMP.json').exists() and json.loads((cache / 'STAMP.json').read_text()) == desired:
            print('cache fresh', flush=True)
            return
        building = root / 'cache.building'
        remove_cache(building, root)
        building.mkdir()
        db = (building / 'ESPN_FANTASY.duckdb').resolve()
        if db == LIVE_DB:
            raise ValueError('Refusing live warehouse')
        env = dict(os.environ, DBT_DUCKDB_PATH=str(db),
            DBT_LEAGUE_CONFIG=f'../data/fixtures/corpus-{freeze_id}/bundle/league_config',
            DBT_DUCKDB_MEMORY_LIMIT='6GB', DBT_DUCKDB_TEMP_LIMIT='6GB',
            DBT_BIN='.venv/Scripts/dbt.exe', TARGET_PATH='target/corpus', SWEEP_CMD='run')
        common = ['--project-dir', 'dbt_league', '--profiles-dir', 'dbt_league/profiles', '--target-path', 'target/corpus', '--threads', '1']
        commands = [[sys.executable, 'tools/load_parquet_to_duckdb.py', '--db', str(db), '--parquet-dir', str(root / 'bundle/raw')],
            [str(REPO / '.venv/Scripts/dbt.exe'), 'seed', *common, '--full-refresh'], ['bash', 'tools/duckdb_run.sh']]
        for command in commands:
            subprocess.run(command, cwd=REPO, env=env, check=True)
        write_json(building / 'STAMP.json', desired)
        temp = (building / 'tmp').resolve()
        if temp.is_relative_to(building.resolve()) and temp.exists():
            shutil.rmtree(temp)
        remove_cache(cache, root)
        os.replace(building, cache)
    finally:
        lock.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['freeze', 'verify', 'build', 'reproject'])
    parser.add_argument('--freeze-id', default=DEFAULT_FREEZE_ID)
    parser.add_argument('--raw-dir', type=Path, default=REPO / 'data/parquet/raw')
    parser.add_argument('--league-config', type=Path, default=REPO / 'dbt_league/league_config')
    args = parser.parse_args()
    if args.command == 'freeze':
        freeze(args.freeze_id, args.raw_dir, args.league_config)
    elif args.command == 'reproject':
        reproject(args.freeze_id, args.league_config)
    else:
        globals()[args.command](args.freeze_id)


if __name__ == '__main__':
    main()
