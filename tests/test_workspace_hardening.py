import json
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hub import calls, pipelines, textedit, workspace
from hub.models import Check, JobRequest
from hub.report import final_report
from hub.scoped import ScopedFiles, ScopeError
from hub.service import create_app
from hub.settings import initialize

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
    (repo / 'src' / 'a.py').write_text('x = 1\n')
    (repo / 'src' / 'old.py').write_text('legacy = True\n')
    (repo / 'theme.py').write_text('BLUE = 1\n')
    git(repo, 'init', '-q')
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', 'init')
    return repo

def work_of(ident):
    return Path(workspace.read_record(ident)['path'])

def pass_check():
    return Check(name='pass', argv=[sys.executable, '-c', 'print(1)']).model_dump()

# DELETE blocks ----------------------------------------------------------------------------------------------------------

def test_a_delete_block_parses_and_must_be_terminated():
    parsed = textedit.parse('FILE: src/old.py\n<<<<<<< DELETE\n>>>>>>> DELETE\nEND OF EDITS')
    assert not parsed.problems and parsed.edits[0].delete and parsed.edits[0].path == 'src/old.py'
    assert textedit.parse('FILE: src/old.py\n<<<<<<< DELETE\n').problems
    assert textedit.parse('FILE: a\n<<<<<<< DELETE\n>>>>>>> REPLACE\n').problems

def test_deleting_needs_a_declaration_an_existing_file_and_no_other_edit(origin):
    files = ScopedFiles(JobRequest(role='editor', repo=str(origin), task='t', allowed_paths=['src/old.py', 'src/a.py'], delete_paths=['src/old.py'], idempotency_key='del1'))
    snaps = {p: textedit.snapshot(files, p) for p in ('src/old.py', 'src/a.py')}
    assert 'not declared' in textedit.plan([textedit.Edit('src/a.py', None, None, delete=True)], snaps, deletable=frozenset(['src/old.py'])).errors[0]
    assert 'both edited and deleted' in textedit.plan([textedit.Edit('src/old.py', 'legacy', 'x'), textedit.Edit('src/old.py', None, None, delete=True)], snaps, deletable=frozenset(['src/old.py'])).errors[0]
    ok = textedit.plan([textedit.Edit('src/old.py', None, None, delete=True)], snaps, deletable=frozenset(['src/old.py']))
    assert not ok.errors and ok.contents['src/old.py'] is textedit.DELETED
    changed, errors = textedit.commit(files, ok.contents, snaps)
    assert changed == ['src/old.py'] and not (origin / 'src' / 'old.py').exists()

def test_a_failed_second_write_restores_a_file_that_was_deleted_first(origin, monkeypatch):
    files = ScopedFiles(JobRequest(role='editor', repo=str(origin), task='t', allowed_paths=['src/old.py', 'src/a.py'], delete_paths=['src/old.py'], idempotency_key='del2'))
    snaps = {p: textedit.snapshot(files, p) for p in ('src/old.py', 'src/a.py')}
    planned = textedit.plan([textedit.Edit('src/old.py', None, None, delete=True), textedit.Edit('src/a.py', 'x = 1', 'x = 2')], snaps, deletable=frozenset(['src/old.py']))
    original = files._replace_observed
    def failing(path, content, expected):
        if path == 'src/a.py':
            raise OSError('disk full')
        return original(path, content, expected)
    monkeypatch.setattr(files, '_replace_observed', failing)
    changed, errors = textedit.commit(files, planned.contents, snaps)
    assert changed == [] and 'restored' in errors[0] and (origin / 'src' / 'old.py').read_text() == 'legacy = True\n'

def test_scoped_delete_checks_the_declaration_and_freshness(origin):
    undeclared = ScopedFiles(JobRequest(role='editor', repo=str(origin), task='t', allowed_paths=['src/old.py'], idempotency_key='del3'))
    with pytest.raises(ScopeError, match='not declared'):
        undeclared.delete_file('src/old.py', 'x')
    files = ScopedFiles(JobRequest(role='editor', repo=str(origin), task='t', allowed_paths=['src/old.py'], delete_paths=['src/old.py'], idempotency_key='del4'))
    with pytest.raises(ScopeError, match='changed since'):
        files.delete_file('src/old.py', 'wronghash')
    assert (origin / 'src' / 'old.py').exists()
    with pytest.raises(ValueError, match='subset'):
        JobRequest(role='editor', repo=str(origin), task='t', allowed_paths=['src/a.py'], delete_paths=['src/old.py'], idempotency_key='del5')

@pytest.mark.asyncio
async def test_run_edit_deletes_only_what_was_declared(tmp_path, origin, monkeypatch):
    async def call(client, d, label, name, step, body, event):
        assert 'Deletable: src/old.py' in body['messages'][0]['content']
        return {'message': {'content': 'FILE: src/old.py\n<<<<<<< DELETE\n>>>>>>> DELETE\nEND OF EDITS'}, 'done_reason': 'stop', 'eval_count': 20}
    monkeypatch.setattr(calls, 'call', call)
    directory = tmp_path / 'job'
    directory.mkdir()
    (directory / 'workspace').mkdir()
    (directory / 'request.json').write_text(JobRequest(role='editor', repo=str(origin), task='Remove the legacy module', allowed_paths=['src/old.py'], delete_paths=['src/old.py'], idempotency_key='del6').model_dump_json())
    await pipelines.run_edit(directory, 'edit', 'Task:\nRemove the legacy module', 'edit')
    report = final_report(json.loads((directory / 'edit.session.json').read_text()))
    assert report['status'] == 'COMPLETE' and not (origin / 'src' / 'old.py').exists()

# Workspace apply, removals, dependencies, revalidation, checks, revert ---------------------------------------------------

def test_removals_are_applied_only_when_declared_and_accepted(origin):
    workspace.create(origin, 'rm1', ['src/old.py', 'src/a.py'], delete_paths=['src/old.py'])
    (work_of('rm1') / 'src' / 'old.py').unlink()
    (work_of('rm1') / 'src' / 'a.py').write_text('x = 2\n')
    workspace.patch('rm1')
    pending = workspace.apply('rm1')
    assert pending['applied'] == [] and pending['pending_removals'] == ['src/old.py'] and pending['undeclared_removals'] == []
    assert (origin / 'src' / 'old.py').exists() and (origin / 'src' / 'a.py').read_text() == 'x = 1\n'
    done = workspace.apply('rm1', accept_removals=True)
    assert sorted(done['applied']) == ['src/a.py', 'src/old.py'] and not (origin / 'src' / 'old.py').exists() and (origin / 'src' / 'a.py').read_text() == 'x = 2\n'

def test_an_undeclared_removal_is_never_applied_even_when_accepted(origin):
    workspace.create(origin, 'rm2', ['src/old.py'])
    (work_of('rm2') / 'src' / 'old.py').unlink()
    result = workspace.apply('rm2', accept_removals=True)
    assert result['undeclared_removals'] == ['src/old.py'] and result['applied'] == [] and (origin / 'src' / 'old.py').exists()

def test_stale_dependencies_are_reported_at_apply_and_do_not_block(origin):
    workspace.create(origin, 'dep1', ['src/a.py'], dependencies=['theme.py', 'src/a.py', 'missing.py'])
    assert list(workspace.read_record('dep1')['dependencies']) == ['theme.py'] and workspace.stale_dependencies('dep1') == []
    (work_of('dep1') / 'src' / 'a.py').write_text('x = 2\n')
    (origin / 'theme.py').write_text('BLUE = 2\n')
    result = workspace.apply('dep1')
    assert result['stale_dependencies'] == ['theme.py'] and result['applied'] == ['src/a.py']

def test_revalidation_runs_the_checks_on_the_current_tree_plus_the_patch_and_blocks_a_failure(origin):
    # The job's change is fine in isolation, but the user's tree now has a second file the check inspects.
    checks = [Check(name='no-forbidden', argv=[sys.executable, '-c', "import pathlib,sys; sys.exit(1 if 'FORBIDDEN' in pathlib.Path('theme.py').read_text() else 0)"]).model_dump()]
    workspace.create(origin, 'rv1', ['src/a.py'], checks=checks)
    (work_of('rv1') / 'src' / 'a.py').write_text('x = 2\n')
    (origin / 'theme.py').write_text('FORBIDDEN = 1\n')  # changed after the job started
    blocked = workspace.apply('rv1', revalidate_first=True)
    assert blocked['applied'] == [] and blocked['revalidation'][0]['status'] == 'failed' and (origin / 'src' / 'a.py').read_text() == 'x = 1\n'
    assert not (workspace.ROOT / 'rv1-revalidate').exists()
    (origin / 'theme.py').write_text('BLUE = 1\n')
    assert workspace.apply('rv1', revalidate_first=True)['applied'] == ['src/a.py']

def test_revalidation_refuses_when_there_are_no_checks_or_a_check_needs_the_job_runner(origin):
    workspace.create(origin, 'rv2', ['src/a.py'])
    (work_of('rv2') / 'src' / 'a.py').write_text('x = 2\n')
    assert workspace.apply('rv2', revalidate_first=True)['applied'] == []  # nothing to validate with: not applied
    needy = [Check(name='db', argv=[sys.executable, '-c', 'pass'], requires_test_database=True).model_dump()]
    workspace.create(origin, 'rv3', ['src/a.py'], checks=needy)
    (work_of('rv3') / 'src' / 'a.py').write_text('x = 3\n')
    blocked = workspace.apply('rv3', revalidate_first=True)
    assert blocked['revalidation'][0]['status'] == 'blocked' and 'job runner' in blocked['revalidation'][0]['reason'] and blocked['applied'] == []

def test_post_apply_checks_run_in_the_real_tree_and_are_recorded(origin):
    failing = [Check(name='needs-x3', argv=[sys.executable, '-c', "import pathlib,sys; sys.exit(0 if 'x = 3' in pathlib.Path('src/a.py').read_text() else 1)"]).model_dump()]
    workspace.create(origin, 'pc1', ['src/a.py'], checks=failing)
    (work_of('pc1') / 'src' / 'a.py').write_text('x = 2\n')
    result = workspace.apply('pc1', run_checks=True)
    assert result['applied'] == ['src/a.py'] and result['post_checks_ok'] is False and result['post_checks'][0]['status'] == 'failed'
    assert workspace.read_record('pc1')['post_checks'][0]['name'] == 'needs-x3'
    assert (origin / 'src' / 'a.py').read_text() == 'x = 2\n'  # no automatic revert

def test_revert_restores_snapshot_content_deletes_created_files_and_refuses_after_a_later_change(origin):
    workspace.create(origin, 'rev1', ['src/a.py', 'src/new.py', 'src/old.py'], delete_paths=['src/old.py'])
    (work_of('rev1') / 'src' / 'a.py').write_text('x = 2\n')
    (work_of('rev1') / 'src' / 'new.py').write_text('fresh = 1\n')
    (work_of('rev1') / 'src' / 'old.py').unlink()
    workspace.apply('rev1', accept_removals=True)
    assert (origin / 'src' / 'new.py').exists() and not (origin / 'src' / 'old.py').exists()
    (origin / 'src' / 'a.py').write_text('x = 99  # user edit after apply\n')
    refused = workspace.revert('rev1')
    assert refused['reverted'] == [] and refused['conflicts'] == ['src/a.py'] and (origin / 'src' / 'new.py').exists()
    (origin / 'src' / 'a.py').write_text('x = 2\n')
    done = workspace.revert('rev1')
    assert sorted(done['reverted']) == ['src/a.py', 'src/new.py', 'src/old.py'] and done['state'] == 'reverted'
    assert (origin / 'src' / 'a.py').read_text() == 'x = 1\n' and not (origin / 'src' / 'new.py').exists() and (origin / 'src' / 'old.py').read_text() == 'legacy = True\n'
    with pytest.raises(workspace.WorkspaceError):
        workspace.revert('rev1')

def test_service_apply_options_and_revert_endpoints(store, origin):
    request = JobRequest(role='editor', task='x', repo=str(origin), allowed_paths=['src/a.py', 'src/old.py'], delete_paths=['src/old.py'], idempotency_key='svc-w9')
    job = store.submit(request)
    store.next()
    store.finish(job['id'], 'completed', {'usage': {}})
    workspace.create(origin, job['id'], ['src/a.py', 'src/old.py'], delete_paths=['src/old.py'])
    (work_of(job['id']) / 'src' / 'a.py').write_text('x = 2\n')
    (work_of(job['id']) / 'src' / 'old.py').unlink()
    headers = {'Authorization': 'Bearer ' + initialize()}
    with TestClient(create_app(store, start_workers=False), base_url='http://127.0.0.1:8765') as c:
        pending = c.post(f"/api/jobs/{job['id']}/apply", headers=headers).json()
        assert pending['pending_removals'] == ['src/old.py'] and pending['applied'] == []
        done = c.post(f"/api/jobs/{job['id']}/apply", json={'accept_removals': True}, headers=headers).json()
        assert done['state'] == 'applied' and sorted(done['applied']) == ['src/a.py', 'src/old.py']
        reverted = c.post(f"/api/jobs/{job['id']}/revert", headers=headers).json()
        assert reverted['state'] == 'reverted' and (origin / 'src' / 'old.py').exists()
        assert c.post(f"/api/jobs/{job['id']}/revert", headers=headers).status_code == 409

@pytest.mark.asyncio
async def test_the_packet_flags_undeclared_removals_and_the_result_shows_stale_dependencies(store, origin):
    from hub.report import parse_report
    from hub.runner import Runner
    from hub.presentation import result_summary
    job = store.submit(JobRequest(role='editor', task='Change a', repo=str(origin), allowed_paths=['src/a.py', 'src/old.py'], read_paths=['theme.py', 'src/a.py', 'src/old.py'],
                                  checks=[Check(name='ok', argv=[sys.executable, '-c', 'print(1)'])], idempotency_key='pkt-w9'))
    runner = Runner(store, 'token')
    async def model(_job, _request, directory, label, prompt, **kwargs):
        (Path(_request.repo) / 'src' / 'a.py').write_text('x = 2\n')
        (Path(_request.repo) / 'src' / 'old.py').unlink()  # removed without being declared
        return {'info': {'tokens': {}}}, parse_report('LOCAL_WORKER_REPORT\nStatus: COMPLETE\nFindings:\nApplied.\nFiles:\nsrc/a.py\nChecks:\nNot run.\nRisks:\nNone.\nEND_LOCAL_WORKER_REPORT')
    runner.execute_model = model
    await runner.run(store.next())
    final = store.get(job['id'])
    packet = final['result']['acceptance']
    assert packet['removed_files'] == ['src/old.py'] and packet['undeclared_removals'] == ['src/old.py'] and packet['dependencies_tracked'] == 1
    assert packet['next_action'] == 'frontier_decision'
    assert result_summary(final)['workspace']['stale_dependencies'] == []
    (origin / 'theme.py').write_text('BLUE = 5\n')
    live = result_summary(final)['workspace']
    assert live['stale_dependencies'] == ['theme.py'] and live['removed'] == ['src/old.py']
