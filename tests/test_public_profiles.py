import json
import stat

from hub import profiles


def test_profile_seed_initializes_empty_private_directory(tmp_path, monkeypatch):
    config = tmp_path / 'config'
    monkeypatch.setattr(profiles, 'CONFIG', config)
    assert profiles.seed_profiles() == []
    directory = config / 'projects'
    assert directory.is_dir()
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert list(directory.iterdir()) == []


def test_profile_seed_preserves_existing_commands(tmp_path, monkeypatch):
    config = tmp_path / 'config'
    directory = config / 'projects'
    directory.mkdir(parents=True)
    path = directory / 'reviewed.json'
    original = json.dumps({'repo': '/private/example', 'groups': {'unit': []}})
    path.write_text(original)
    path.chmod(0o600)
    monkeypatch.setattr(profiles, 'CONFIG', config)
    assert profiles.seed_profiles() == [{'profile': 'reviewed', 'state': 'preserved'}]
    assert path.read_text() == original
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
