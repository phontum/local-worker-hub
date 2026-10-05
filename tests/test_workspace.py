import os
import subprocess
from pathlib import Path

import pytest

from hub import workspace
from hub.scoped import ScopeError

GIT = ['git', '-c', 'user.name=t', '-c', 'user.email=t@t']


def git(repo, *args):
    return subprocess.run(GIT + ['-C', str(repo), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture(autouse=True)
def isolated_root(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'ROOT', tmp_path / 'workspaces')


@pytest.fixture
def origin(tmp_path):
    repo = tmp_path / 'origin'
    repo.mkdir()
    git(repo, 'init', '-q')
    (repo / 'a.py').write_text('x = 1\n')
    (repo / 'b.py').write_text('y = 2\n')
    (repo / '.gitignore').write_text('ignored.txt\nnode_modules/\n')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'init')
    return repo


def test_copy_includes_dirty_and_untracked_files_but_not_ignored_or_secret_ones(origin):
    (origin / 'a.py').write_text('x = 10  # uncommitted\n')
    (origin / 'new.py').write_text('z = 3\n')
    (origin / 'ignored.txt').write_text('skip\n')
    (origin / '.env').write_text('TOKEN=1\n')
    (origin / 'node_modules').mkdir()
    (origin / 'node_modules' / 'dep.js').write_text('1\n')
    record = workspace.create(origin, 'job1', ['a.py'])
    work = Path(record['path'])
    assert (work / 'a.py').read_text() == 'x = 10  # uncommitted\n' and (work / 'new.py').exists()
    assert not (work / 'ignored.txt').exists() and not (work / '.env').exists()
    assert (work / 'node_modules').is_symlink()
    assert 'node_modules' not in git(work, 'ls-files') and 'a.py' in git(work, 'ls-files')
    assert record['base'] == {'a.py': workspace.sha(origin / 'a.py')}
    assert (origin / '.git').is_dir() and not (origin / '.git' / 'worktrees').exists()


def test_patch_apply_roundtrip_leaves_the_origin_untouched_until_apply(origin):
    workspace.create(origin, 'job2', ['a.py', 'new_file.py'])
    work = Path(workspace.read_record('job2')['path'])
    (work / 'a.py').write_text('x = 2\n')
    (work / 'new_file.py').write_text('fresh = True\n')
    (work / 'b.py').write_text('tampered outside scope\n')
    diff = workspace.patch('job2')
    assert '-x = 1' in diff and '+x = 2' in diff and 'new_file.py' in diff and 'b.py' not in diff
    assert (origin / 'a.py').read_text() == 'x = 1\n' and not (origin / 'new_file.py').exists()
    result = workspace.apply('job2')
    assert sorted(result['applied']) == ['a.py', 'new_file.py'] and result['state'] == 'applied'
    assert (origin / 'a.py').read_text() == 'x = 2\n' and (origin / 'new_file.py').read_text() == 'fresh = True\n'
    assert (origin / 'b.py').read_text() == 'y = 2\n'
    assert not Path(workspace.read_record('job2')['path']).exists()
    with pytest.raises(workspace.WorkspaceError):
        workspace.apply('job2')


def test_apply_refuses_when_the_user_changed_an_authorized_file_meanwhile(origin):
    workspace.create(origin, 'job3', ['a.py', 'b.py'])
    work = Path(workspace.read_record('job3')['path'])
    (work / 'a.py').write_text('x = 2\n')
    (work / 'b.py').write_text('y = 3\n')
    (origin / 'a.py').write_text('x = 99  # user edit\n')
    result = workspace.apply('job3')
    assert result == {'applied': [], 'conflicts': ['a.py'], 'stale_dependencies': [], 'state': 'ready'}
    assert (origin / 'a.py').read_text() == 'x = 99  # user edit\n' and (origin / 'b.py').read_text() == 'y = 2\n'


def test_apply_refuses_a_file_created_in_the_user_tree_meanwhile(origin):
    workspace.create(origin, 'job4', ['fresh.py'])
    work = Path(workspace.read_record('job4')['path'])
    (work / 'fresh.py').write_text('a = 1\n')
    (origin / 'fresh.py').write_text('user wrote this first\n')
    assert workspace.apply('job4')['conflicts'] == ['fresh.py']


def test_apply_never_writes_through_a_symlink_or_into_secret_files(origin, tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    (origin / 'linked').symlink_to(outside, target_is_directory=True)
    workspace.create(origin, 'job5', ['linked/evil.py'])
    work = Path(workspace.read_record('job5')['path'])
    (work / 'linked').mkdir(exist_ok=True)
    (work / 'linked' / 'evil.py').write_text('bad\n')
    with pytest.raises(ScopeError):
        workspace.apply('job5')
    assert not (outside / 'evil.py').exists()


def test_discard_and_ttl_purge_remove_the_tree_but_keep_the_record(origin):
    workspace.create(origin, 'job6', ['a.py'])
    assert workspace.discard('job6') == {'state': 'discarded'}
    assert not Path(workspace.read_record('job6')['path']).exists()
    workspace.create(origin, 'job7', ['a.py'])
    assert workspace.purge_old(now=workspace.read_record('job7')['created'] + 8 * 24 * 3600) == ['job7']
    assert workspace.read_record('job7')['state'] == 'expired'
    with pytest.raises(workspace.WorkspaceError):
        workspace.apply('job7')


def test_non_git_directories_are_copied_with_exclusions(tmp_path):
    plain = tmp_path / 'plain'
    (plain / 'src').mkdir(parents=True)
    (plain / 'src' / 'm.py').write_text('1\n')
    (plain / '.venv').mkdir()
    (plain / '.venv' / 'lib.py').write_text('2\n')
    (plain / 'credentials.json').write_text('{}\n')
    work = Path(workspace.create(plain, 'job8', ['src/m.py'])['path'])
    assert (work / 'src' / 'm.py').exists() and not (work / 'credentials.json').exists()
    assert (work / '.venv').is_symlink() and not os.path.exists(work / '.git' / 'worktrees')


def test_oversized_repositories_are_refused_with_a_clear_error(origin, monkeypatch):
    monkeypatch.setattr(workspace, 'MAX_TOTAL', 5)
    with pytest.raises(workspace.WorkspaceError, match='larger than'):
        workspace.create(origin, 'job9', ['a.py'])
    assert not (workspace.ROOT / 'job9').exists()
