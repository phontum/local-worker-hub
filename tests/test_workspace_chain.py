import subprocess
import sys
from pathlib import Path

import pytest

from hub.skills.coding.editing import workspace
from hub.models import Check

GIT = ['git', '-c', 'user.name=t', '-c', 'user.email=t@t']

def git(repo, *args):
    return subprocess.run(GIT + ['-C', str(repo), *args], check=True, capture_output=True, text=True).stdout

@pytest.fixture(autouse=True)
def isolated_root(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'ROOT', tmp_path / 'workspaces')

@pytest.fixture
def origin(tmp_path):
    repo = tmp_path / 'origin'
    (repo / 'src').mkdir(parents=True)
    (repo / 'src' / 'a.py').write_text('a = 1\n')
    (repo / 'src' / 'b.py').write_text('b = 1\n')
    (repo / 'src' / 'c.py').write_text('c = 1\n')
    git(repo, 'init', '-q')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'init')
    return repo

def tree(ident):
    return Path(workspace.read_record(ident)['path'])

def test_a_chained_job_starts_from_the_earlier_jobs_changes_and_shares_one_tree(origin):
    workspace.create(origin, 'j1', ['src/a.py'])
    (tree('j1') / 'src' / 'a.py').write_text('a = 2\n')
    record = workspace.chain(origin, 'j2', 'j1', ['src/b.py'])
    assert tree('j2') == tree('j1') and (tree('j2') / 'src' / 'a.py').read_text() == 'a = 2\n'  # sees the first job's edit
    assert record['chain'] == ['j1', 'j2'] and record['allowed'] == ['src/a.py', 'src/b.py'] and record['job_allowed'] == ['src/b.py']
    assert workspace.read_record('j1')['state'] == 'chained' and workspace.read_record('j1')['chained_into'] == 'j2'

def test_job_diff_is_this_jobs_changes_and_patch_is_cumulative(origin):
    workspace.create(origin, 'j1', ['src/a.py'])
    (tree('j1') / 'src' / 'a.py').write_text('a = 2\n')
    workspace.chain(origin, 'j2', 'j1', ['src/b.py'])
    (tree('j2') / 'src' / 'b.py').write_text('b = 2\n')
    cumulative = workspace.patch('j2')
    own = workspace.job_patch('j2')
    assert '+a = 2' in cumulative and '+b = 2' in cumulative
    assert '+b = 2' in own and 'a = 2' not in own and 'src/a.py' not in own

def test_one_apply_of_the_last_job_writes_the_whole_chain_and_the_earlier_jobs_cannot_be_applied_alone(origin):
    workspace.create(origin, 'j1', ['src/a.py'])
    (tree('j1') / 'src' / 'a.py').write_text('a = 2\n')
    workspace.chain(origin, 'j2', 'j1', ['src/b.py'])
    (tree('j2') / 'src' / 'b.py').write_text('b = 2\n')
    workspace.chain(origin, 'j3', 'j2', ['src/c.py'])
    (tree('j3') / 'src' / 'c.py').write_text('c = 2\n')
    with pytest.raises(workspace.WorkspaceError, match='apply that job'):
        workspace.apply('j1')
    result = workspace.apply('j3')
    assert sorted(result['applied']) == ['src/a.py', 'src/b.py', 'src/c.py'] and result['state'] == 'applied'
    assert [(origin / 'src' / f).read_text() for f in ('a.py', 'b.py', 'c.py')] == ['a = 2\n', 'b = 2\n', 'c = 2\n']
    assert workspace.read_record('j1')['state'] == 'applied' and workspace.read_record('j2')['applied_via'] == 'j3'

def test_conflicts_in_any_file_of_the_chain_refuse_the_whole_apply(origin):
    workspace.create(origin, 'j1', ['src/a.py'])
    (tree('j1') / 'src' / 'a.py').write_text('a = 2\n')
    workspace.chain(origin, 'j2', 'j1', ['src/b.py'])
    (tree('j2') / 'src' / 'b.py').write_text('b = 2\n')
    (origin / 'src' / 'a.py').write_text('a = 99  # user edit\n')  # a file only the first job touched
    result = workspace.apply('j2')
    assert result['applied'] == [] and result['conflicts'] == ['src/a.py'] and (origin / 'src' / 'b.py').read_text() == 'b = 1\n'

def test_a_file_the_user_changed_before_the_chain_reached_it_is_still_a_conflict(origin):
    workspace.create(origin, 'j1', ['src/a.py'])
    (tree('j1') / 'src' / 'a.py').write_text('a = 2\n')
    workspace.chain(origin, 'j2', 'j1', ['src/b.py'])
    (origin / 'src' / 'b.py').write_text('b = 99  # user edit before the second job ran its edit\n')
    (tree('j2') / 'src' / 'b.py').write_text('b = 2\n')
    assert workspace.apply('j2')['conflicts'] == ['src/b.py']

def test_revert_restores_every_file_of_the_chain_from_the_earliest_snapshot(origin):
    workspace.create(origin, 'j1', ['src/a.py'])
    (tree('j1') / 'src' / 'a.py').write_text('a = 2\n')
    workspace.chain(origin, 'j2', 'j1', ['src/b.py', 'src/new.py'])
    (tree('j2') / 'src' / 'b.py').write_text('b = 2\n')
    (tree('j2') / 'src' / 'new.py').write_text('n = 1\n')
    workspace.apply('j2')
    result = workspace.revert('j2')
    assert sorted(result['reverted']) == ['src/a.py', 'src/b.py', 'src/new.py'] and result['state'] == 'reverted'
    assert (origin / 'src' / 'a.py').read_text() == 'a = 1\n' and (origin / 'src' / 'b.py').read_text() == 'b = 1\n' and not (origin / 'src' / 'new.py').exists()

def test_discarding_the_last_job_discards_the_chain(origin):
    workspace.create(origin, 'j1', ['src/a.py'])
    workspace.chain(origin, 'j2', 'j1', ['src/b.py'])
    assert workspace.discard('j2') == {'state': 'discarded'}
    assert workspace.read_record('j1')['state'] == 'discarded' and not tree('j1').exists()
    with pytest.raises(workspace.WorkspaceError, match='discarded'):
        workspace.chain(origin, 'j3', 'j1', ['src/c.py'])

def test_chaining_needs_a_ready_workspace_of_the_same_repository(origin, tmp_path):
    workspace.create(origin, 'j1', ['src/a.py'])
    other = tmp_path / 'other'
    other.mkdir()
    (other / 'x.py').write_text('x = 1\n')
    git(other, 'init', '-q')
    with pytest.raises(workspace.WorkspaceError, match='same repository'):
        workspace.chain(other, 'j2', 'j1', ['x.py'])
    workspace.apply('j1')  # nothing changed, but the job is now applied
    with pytest.raises(workspace.WorkspaceError, match='applied'):
        workspace.chain(origin, 'j3', 'j1', ['src/b.py'])

def test_revalidation_runs_the_checks_of_every_job_in_the_chain_on_the_combined_result(origin):
    first = Check(name='a-is-2', argv=[sys.executable, '-c', "import pathlib,sys; sys.exit(0 if 'a = 2' in pathlib.Path('src/a.py').read_text() else 1)"]).model_dump()
    second = Check(name='b-is-2', argv=[sys.executable, '-c', "import pathlib,sys; sys.exit(0 if 'b = 2' in pathlib.Path('src/b.py').read_text() else 1)"]).model_dump()
    workspace.create(origin, 'j1', ['src/a.py'], checks=[first])
    (tree('j1') / 'src' / 'a.py').write_text('a = 2\n')
    workspace.chain(origin, 'j2', 'j1', ['src/b.py'], checks=[second])
    (tree('j2') / 'src' / 'b.py').write_text('b = 2\n')
    result = workspace.apply('j2', revalidate_first=True, run_checks=True)
    assert result['applied'] == ['src/a.py', 'src/b.py'] and result['post_checks_ok'] is True and [c['name'] for c in result['post_checks']] == ['a-is-2', 'b-is-2']
