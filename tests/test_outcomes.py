import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hub.skills.coding.delegation import outcomes
from hub.skills.coding.editing import workspace
from hub.models import JobRequest, Review
from hub.service import create_app
from hub.settings import initialize

GIT = ['git', '-c', 'user.name=t', '-c', 'user.email=t@t']

@pytest.fixture(autouse=True)
def isolated_root(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, 'ROOT', tmp_path / 'workspaces')

@pytest.fixture
def origin(tmp_path):
    repo = tmp_path / 'origin'
    repo.mkdir()
    (repo / 'a.py').write_text('x = 1\ny = 2\n')
    (repo / 'b.ts').write_text('export const z = 1;\n')
    for step in (['init', '-q'], ['add', '-A'], ['commit', '-qm', 'init']):
        subprocess.run(GIT + ['-C', str(repo), *step], check=True, capture_output=True)
    return repo

def job_for(ident, **request):
    return {'id': ident, 'state': 'completed', 'request': {'role': 'editor', 'task': 'secret task text', 'caller': 'mcp', **request}, 'result': {'worker_status': 'COMPLETE'}, 'review': None}

def test_final_diff_is_what_the_frontier_left_after_the_snapshot(origin, tmp_path):
    workspace.create(origin, 'o1', ['a.py', 'b.ts'])
    (origin / 'a.py').write_text('x = 10\ny = 2\n')       # the frontier took over and edited a.py itself
    jobs = tmp_path / 'jobs'
    (jobs / 'o1').mkdir(parents=True)
    result = outcomes.final_diff(job_for('o1', allowed_paths=['a.py', 'b.ts']), jobs)
    assert result['available'] and [f['path'] for f in result['files']] == ['a.py'] and result['files'][0] == {'path': 'a.py', 'added': 1, 'removed': 1}
    text = (jobs / 'o1' / 'final-frontier.diff').read_text()
    assert '-x = 1' in text and '+x = 10' in text and 'b.ts' not in text

def test_final_diff_reports_unavailable_without_a_workspace(tmp_path):
    assert outcomes.final_diff(job_for('nope'), tmp_path)['available'] is False

def test_the_row_has_shape_not_content_and_marks_benchmarks_as_not_real(tmp_path):
    job = job_for('r1', allowed_paths=['a.py', 'c.ts'], read_paths=['d.go'], checks=[{'name': 'n'}], kind='fix_test')
    job['review'] = {'decision': 'takeover', 'reason': 'wrong_edit', 'review_effort_seconds': 30}
    row = outcomes.row(job, tmp_path)
    assert row['languages'] == ['go', 'python', 'typescript'] and row['allowed_files'] == 2 and row['checks'] == 1 and row['decision'] == 'takeover' and row['real']
    assert 'secret' not in str(row) and outcomes.is_real(job_for('x', caller='eval')) is False

def test_stats_count_decisions_by_role_and_kind_and_skip_benchmarks():
    def reviewed(caller, kind, decision, reason=None):
        j = job_for('s', caller=caller, kind=kind)
        j['review'] = {'decision': decision, 'reason': reason}
        return j
    jobs = [reviewed('mcp', 'fix_test', 'accepted'), reviewed('cli', 'fix_test', 'takeover', 'wrong_edit'), reviewed('eval', 'fix_test', 'accepted'), job_for('u', kind='fix_test')]
    table = outcomes.stats(jobs)
    assert table == [{'role': 'editor', 'kind': 'fix_test', 'reviewed': 2, 'accepted': 1, 'rejected': 0, 'takeover': 1, 'reasons': {'wrong_edit': 1}, 'accept_rate': 0.5}]
    assert outcomes.stats(jobs, include_eval=True)[0]['reviewed'] == 3

def test_rejection_without_a_reason_code_is_refused():
    with pytest.raises(ValueError, match='reason code'):
        Review(decision='takeover', notes='Took over because it was wrong')

def test_a_takeover_review_records_the_final_diff_and_stats_are_served(store, origin, tmp_path):
    request = JobRequest(role='editor', task='Edit a', repo=str(origin), allowed_paths=['a.py'], idempotency_key='fd1', caller='mcp')
    submitted = store.submit(request)
    store.next()
    store.finish(submitted['id'], 'completed', {'worker_status': 'COMPLETE'})
    workspace.create(origin, submitted['id'], ['a.py'])
    (origin / 'a.py').write_text('x = 5\ny = 2\n')
    headers = {'Authorization': 'Bearer ' + initialize()}
    with TestClient(create_app(store, start_workers=False), base_url='http://127.0.0.1:8765') as c:
        body = Review(decision='takeover', notes='Rewrote it myself', reason='wrong_localization').model_dump()
        assert c.post(f"/api/jobs/{submitted['id']}/review", headers=headers, json=body).status_code == 200
        events = [e for e in store.all_events(submitted['id']) if e['kind'] == 'final-frontier-diff']
        assert events and events[-1]['data']['files'][0]['path'] == 'a.py'
        table = c.get('/api/outcomes', headers=headers).json()['by_kind']
        assert table[0]['takeover'] == 1 and table[0]['reasons'] == {'wrong_localization': 1}

# Dataset export -----------------------------------------------------------------------------------------------------------

def make_reviewed_job(store, origin, tmp_path, caller='mcp', decision='takeover'):
    from hub.skills.coding.delegation import outcomes as out
    request = JobRequest(role='editor', task='Edit a with PRIVATE text', repo=str(origin), allowed_paths=['a.py'], kind='mechanical', idempotency_key='ds-' + caller + decision, caller=caller)
    submitted = store.submit(request)
    store.next()
    store.finish(submitted['id'], 'completed', {'worker_status': 'PARTIAL', 'changed_files': ['a.py'], 'checks': [{'name': 'unit', 'status': 'failed', 'failures': [{'x': 1}]}],
                                                 'usage': {'input': 100, 'output': 20}, 'acceptance': {'diff': {'files': 1, 'added': 2, 'removed': 1}, 'scope_ok': True}})
    directory = tmp_path / 'jobs' / submitted['id']
    directory.mkdir(parents=True)
    (directory / 'work.context.json').write_text(json.dumps({'budget_chars': 9000, 'used_chars': 400, 'files': {'a.py': {'lines': 2, 'shown': 2, 'whole': True, 'ranges': [[1, 2]], 'secret': 'dropped'}}}))
    (directory / 'work.turns.json').write_text(json.dumps({'turns': [{'step': 0, 'done_reason': 'stop', 'output_tokens': 40, 'truncated': False, 'errors': ['x'], 'blocks': 1}]}))
    (directory / 'job.diff').write_text('--- a/a.py\n+++ b/a.py\n-x = 1\n+x = 2\n')
    (directory / 'work.session.json').write_text(json.dumps({'messages': [{'type': 'user', 'text': 'Task:\nEdit a with PRIVATE text'}]}))
    store.review(submitted['id'], Review(decision=decision, notes='Rewrote it myself', reason='wrong_edit') if decision != 'accepted' else Review(decision='accepted'))
    return store.get(submitted['id']), directory

def test_the_dataset_record_is_structure_only_by_default_and_complete_with_source(store, origin, tmp_path):
    import json as _json
    from hub.skills.coding.delegation import outcomes as out
    job, directory = make_reviewed_job(store, origin, tmp_path)
    (directory / 'final-frontier.json').write_text(_json.dumps({'available': True, 'changed_files': 1}))
    (directory / 'final-frontier.diff').write_text('+x = 9\n')
    plain = out.dataset_record(job, directory)
    assert plain['schema'] == 1 and plain['kind'] == 'mechanical' and plain['local']['status'] == 'PARTIAL' and plain['local']['checks'] == [{'name': 'unit', 'status': 'failed', 'failures': 1}]
    assert plain['context']['files'] == {'a.py': {'lines': 2, 'shown': 2, 'whole': True, 'ranges': [[1, 2]]}} and plain['turns'][0]['errors'] == 1
    assert plain['frontier'] == {'decision': 'takeover', 'reason': 'wrong_edit', 'effort_seconds': None, 'task_outcome': None, 'notes_chars': 17, 'final_diff': {'available': True, 'changed_files': 1}}
    assert plain['local_tokens'] == 120 and len(plain['spec_sha256']) == 64
    assert 'source' not in plain and 'PRIVATE' not in _json.dumps(plain)
    full = out.dataset_record(job, directory, include_source=True)
    assert full['source']['task'].endswith('PRIVATE text') and full['source']['prompt'].startswith('Task:') and '+x = 2' in full['source']['local_patch']
    assert full['source']['final_frontier_diff'] == '+x = 9\n' and full['source']['review_notes'] == 'Rewrote it myself'

def test_the_dataset_filters_benchmarks_unreviewed_kinds_and_age(store, origin, tmp_path):
    from hub.skills.coding.delegation import outcomes as out
    kept, _ = make_reviewed_job(store, origin, tmp_path, 'mcp', 'takeover')
    make_reviewed_job(store, origin, tmp_path, 'eval', 'accepted')
    jobs = store.list()
    assert [r['job'] for r in out.dataset(jobs, jobs_dir=tmp_path / 'jobs')] == [kept['id']]
    assert len(out.dataset(jobs, include_eval=True, jobs_dir=tmp_path / 'jobs')) == 2
    assert out.dataset(jobs, kinds=['fix_test'], jobs_dir=tmp_path / 'jobs') == []
    assert out.dataset(jobs, since_days=1, now=kept['created'] + 5 * 86400, jobs_dir=tmp_path / 'jobs') == []

def test_the_dataset_endpoint_and_cli_write_jsonl(store, origin, tmp_path, monkeypatch):
    import sys
    from hub import cli, client
    make_reviewed_job(store, origin, tmp_path)
    headers = {'Authorization': 'Bearer ' + initialize()}
    with TestClient(create_app(store, start_workers=False), base_url='http://127.0.0.1:8765') as c:
        body = c.get('/api/dataset', headers=headers).json()
        assert body['schema'] == 1 and len(body['records']) == 1 and 'source' not in body['records'][0]
        assert c.get('/api/dataset?include_source=true&kinds=fix_test', headers=headers).json()['records'] == []
        monkeypatch.setattr(client, 'call', lambda method, path, data=None: c.request(method, path, headers=headers).json())
        target = tmp_path / 'out.jsonl'
        monkeypatch.setattr(sys, 'argv', ['local-worker', 'dataset', 'export', '--out', str(target)])
        cli.main()
        lines = target.read_text().splitlines()
        assert len(lines) == 1 and json.loads(lines[0])['frontier']['reason'] == 'wrong_edit' and oct(target.stat().st_mode)[-3:] == '600'

# Stats epoch --------------------------------------------------------------------------------------------------------------

def test_the_epoch_only_decides_what_is_counted_and_never_deletes(store, origin, tmp_path, monkeypatch):
    import time as _time
    from hub import router
    from hub.skills.coding.delegation import outcomes as out
    monkeypatch.setattr(out, 'CONFIG', tmp_path / 'cfg')
    old, _ = make_reviewed_job(store, origin, tmp_path, 'mcp', 'takeover')
    assert out.epoch() is None and out.counted(store.list()) == store.list()
    since = out.reset_epoch(_time.time() + 1)
    assert out.epoch() == since and oct((tmp_path / 'cfg' / 'stats-epoch.json').stat().st_mode)[-3:] == '600'
    assert out.counted(store.list()) == [] and len(out.counted(store.list(), include_history=True)) == 1
    assert out.stats(out.counted(store.list())) == [] and out.stats(out.counted(store.list(), True))[0]['takeover'] == 1
    assert store.get(old['id'])['review']['decision'] == 'takeover'  # nothing was removed
    fresh = router.estimate('editor', 'mechanical', 'x' * 200, 2, out.counted(store.list()))
    assert fresh['basis']['real_reviews'] == 0
    assert router.estimate('editor', 'mechanical', 'x' * 200, 2, out.counted(store.list(), True))['basis']['real_reviews'] == 1
    monkeypatch.setattr(out, 'epoch', lambda: _time.time() - 10_000)
    assert len(out.counted(store.list())) == 1  # a job submitted after the epoch counts again

def test_the_reset_endpoint_and_stats_cli(store, origin, tmp_path, monkeypatch, capsys):
    import sys
    from hub import cli, client
    from hub.skills.coding.delegation import outcomes as out
    monkeypatch.setattr(out, 'CONFIG', tmp_path / 'cfg')
    make_reviewed_job(store, origin, tmp_path)
    headers = {'Authorization': 'Bearer ' + initialize()}
    with TestClient(create_app(store, start_workers=False), base_url='http://127.0.0.1:8765') as c:
        assert c.get('/api/outcomes', headers=headers).json()['by_kind'][0]['takeover'] == 1
        monkeypatch.setattr(client, 'call', lambda method, path, data=None: c.request(method, path, headers=headers).json())
        monkeypatch.setattr(sys, 'argv', ['local-worker', 'stats', 'reset'])
        cli.main()
        assert 'earlier job(s) are kept' in capsys.readouterr().out
        shown = c.get('/api/outcomes', headers=headers).json()
        assert shown['by_kind'] == [] and shown['since'] is not None
        assert c.get('/api/outcomes?include_history=true', headers=headers).json()['by_kind'][0]['takeover'] == 1
        assert c.get('/api/dataset', headers=headers).json()['records'] == [] and len(c.get('/api/dataset?include_history=true', headers=headers).json()['records']) == 1
        monkeypatch.setattr(sys, 'argv', ['local-worker', 'stats'])
        cli.main()
        assert 'No reviewed delegations counted yet' in capsys.readouterr().out

def test_reset_accepts_a_start_date(store, origin, tmp_path, monkeypatch, capsys):
    import sys
    import time as _time
    from hub import cli, client
    from hub.skills.coding.delegation import outcomes as out
    monkeypatch.setattr(out, 'CONFIG', tmp_path / 'cfg')
    headers = {'Authorization': 'Bearer ' + initialize()}
    with TestClient(create_app(store, start_workers=False), base_url='http://127.0.0.1:8765') as c:
        monkeypatch.setattr(client, 'call', lambda method, path, data=None: c.request(method, path, headers=headers, json=data).json())
        monkeypatch.setattr(sys, 'argv', ['local-worker', 'stats', 'reset', '--since', '2026-10-05'])
        cli.main()
        assert out.epoch() == _time.mktime(_time.strptime('2026-10-05', '%Y-%m-%d')) and 'Counting from 2026-10-05 00:00' in capsys.readouterr().out
        assert c.post('/api/outcomes/reset', headers=headers, json={'since': 'yesterday'}).status_code == 409
