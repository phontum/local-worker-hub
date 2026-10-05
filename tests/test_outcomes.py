import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hub import outcomes, workspace
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
