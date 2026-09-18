"""Frozen inputs preserve used cells and rebuild only for warehouse logic."""
import pytest

from tools import corpus_fixture as fixture


def test_projection_preserves_used_cells_and_never_edits_sources(tmp_path, monkeypatch):
    source, projected = tmp_path/'source', tmp_path/'projected'
    source.mkdir()
    projected.mkdir()
    payload = b'id,label,email\n001,"two\nlines",private@example.invalid\n002,,\n'
    for index in range(14):
        (source/f'config_{index}.csv').write_bytes(payload)
    monkeypatch.setattr(fixture, 'public_columns', lambda name: ['id', 'label'])
    records = fixture.project_config(source, projected)
    assert len(records) == 14
    for name, proof in records.items():
        assert (source/name).read_bytes() == payload
        assert (projected/name).read_bytes() == b'id,label\n001,"two\nlines"\n002,\n'
        assert proof['source_row_count'] == proof['projected_row_count'] == 2
        assert proof['per_column_equal'] == {'id': True, 'label': True}
        assert proof['dropped_columns'] == ['email']
        assert proof['projected_sha256'] == fixture.digest(projected/name)


def test_bad_projection_writes_nothing(tmp_path, monkeypatch):
    source, projected = tmp_path/'source', tmp_path/'projected'
    source.mkdir()
    projected.mkdir()
    for index in range(14):
        (source/f'config_{index}.csv').write_text('id\n1\n', encoding='utf-8')
    monkeypatch.setattr(fixture, 'public_columns', lambda name: ['id', 'required'])
    with pytest.raises(ValueError, match='Missing or duplicate declared columns'):
        fixture.project_config(source, projected)
    assert not list(projected.iterdir())


def test_stamp_ignores_live_inputs_but_tracks_logic_and_versions(tmp_path, monkeypatch):
    monkeypatch.setattr(fixture, 'REPO', tmp_path)
    version = ['1']
    monkeypatch.setattr(fixture.importlib.metadata, 'version', lambda name: version[0])
    for relative in ['dbt_league/models/model.sql', 'dbt_league/macros/macro.sql',
                     'dbt_league/seeds/reference.csv', 'dbt_league/dbt_project.yml',
                     'dbt_league/packages.yml', 'dbt_league/package-lock.yml',
                     'dbt_league/profiles/profiles.yml', 'tools/load_parquet_to_duckdb.py',
                     'dbt_league/league_config/private.csv', 'output/renderer.py']:
        path = tmp_path/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('initial', encoding='utf-8')
    root = fixture.fixture_root('test')
    root.mkdir(parents=True)
    (root/(fixture.asset_name('test')+'.sha256')).write_text('a'*64+'  archive.tar\n')
    manifest = {'files': {'raw/source.parquet': {'sha256': 'b'*64}}}
    original = fixture.stamp('test', manifest)
    (tmp_path/'dbt_league/league_config/private.csv').write_text('new live config')
    (tmp_path/'output/renderer.py').write_text('new renderer')
    assert fixture.stamp('test', manifest) == original
    (tmp_path/'dbt_league/models/model.sql').write_text('new logic')
    changed = fixture.stamp('test', manifest)
    assert changed['logic_sha256'] != original['logic_sha256']
    version[0] = '2'
    assert fixture.stamp('test', manifest)['versions'] != changed['versions']


def test_cache_removal_refuses_other_paths(tmp_path):
    other = tmp_path/'live'
    other.mkdir()
    with pytest.raises(ValueError, match='unsafe cache removal'):
        fixture.remove_cache(other, tmp_path)
    assert other.exists()
