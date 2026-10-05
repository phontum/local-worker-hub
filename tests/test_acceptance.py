import sys

import pytest
from fastapi.testclient import TestClient

from hub import workspace
from hub.acceptance import build, diff_stats, review_focus
from hub.models import Check, JobRequest
from hub.runner import Runner
from hub.service import create_app
from hub.settings import initialize

PATCH = '''diff --git a/src/app.py b/src/app.py
index 111..222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,4 @@
-old = 1
+new = 2
+def helper(x):
+    return x
diff --git a/src/fresh.js b/src/fresh.js
new file mode 100644
--- /dev/null
+++ b/src/fresh.js
@@ -0,0 +1,2 @@
+export function fresh() {}
+export const later = async () => 1
'''

def test_diff_stats_and_review_focus_come_from_the_patch():
    stats = diff_stats(PATCH)
    assert stats == {'src/app.py': {'added': 3, 'removed': 1}, 'src/fresh.js': {'added': 2, 'removed': 0}}
    notes = review_focus(PATCH, stats, [], ['src/app.py', 'src/fresh.js', 'src/unused.py'])
    text = ' | '.join(notes)
    assert 'Source changed without a test change' in text and 'No approved check ran' in text
    assert 'New definitions: helper, fresh, later' in text and 'Authorized but unchanged: src/unused.py' in text


def test_packet_is_clean_only_with_a_complete_status_passing_checks_and_no_scope_escape():
    passing = [{'name': 't', 'status': 'passed', 'exit_code': 0}]
    clean = build(PATCH, passing, 'COMPLETE', ['src/app.py', 'src/fresh.js'], [{}], 'ready')
    assert clean['next_action'] == 'review_patch_then_apply_result' and clean['scope_ok'] and clean['repair_used'] is False
    assert build(PATCH, passing, 'PARTIAL', ['src/app.py'], [{}], 'ready')['next_action'] == 'frontier_decision'
    assert build(PATCH, [], 'COMPLETE', ['src/app.py'], [{}], 'ready')['next_action'] == 'frontier_decision'
    escaped = build(PATCH, passing, 'COMPLETE', ['src/app.py'], [{}], 'ready', outside=['secrets.txt'])
    assert escaped['scope_ok'] is False and escaped['outside_scope'] == ['secrets.txt'] and escaped['next_action'] == 'frontier_decision'
    failing = build(PATCH, [{'name': 't', 'status': 'failed', 'exit_code': 1, 'failures': [
        {'test_id': 'tests/test_a.py::test_x', 'file': 'tests/test_a.py', 'line': 9, 'message': 'assert 1 is None'}]}], 'COMPLETE', ['src/app.py'], [{}, {}], 'ready')
    assert failing['repair_used'] and failing['checks'][0]['failures'] == ['FAIL tests/test_a.py::test_x (tests/test_a.py:9) — assert 1 is None']


@pytest.mark.asyncio
async def test_service_apply_and_discard_endpoints(repo, store):
    request = JobRequest(role='editor', task='Set answer', repo=str(repo), allowed_paths=['app.ts'], idempotency_key='svc-apply',
                         checks=[Check(name='ok', argv=[sys.executable, '-c', 'print(1)'])])
    job = store.submit(request)
    runner = Runner(store, 'token')
    async def model(_job, _request, directory, label, prompt, **kwargs):
        from pathlib import Path
        (Path(_request.repo) / 'app.ts').write_text('export const answer = 42;\n')
        from hub.report import parse_report
        return {'info': {'tokens': {}}}, parse_report('LOCAL_WORKER_REPORT\nStatus: COMPLETE\nFindings:\nEdited.\nFiles:\napp.ts\nChecks:\nNot run.\nRisks:\nNone.\nEND_LOCAL_WORKER_REPORT')
    runner.execute_model = model
    await runner.run(store.next())
    headers = {'Authorization': 'Bearer ' + initialize()}
    with TestClient(create_app(store, start_workers=False), base_url='http://127.0.0.1:8765') as c:
        summary = c.get(f"/api/jobs/{job['id']}/result?view=summary", headers=headers).json()
        assert summary['workspace']['state'] == 'ready' and 'patch.diff' in summary['artifacts'] and summary['acceptance']['scope_ok']
        brief = c.get(f"/api/jobs/{job['id']}/result?view=brief", headers=headers).json()
        assert brief['workspace']['origin_unchanged'] and brief['response_bytes'] <= 2048
        patch = c.get(f"/api/jobs/{job['id']}/artifacts/patch.diff", headers=headers)
        assert patch.status_code == 200 and '+export const answer = 42;' in patch.text
        assert (repo / 'app.ts').read_text() == 'export const answer = 41;\n'
        (repo / 'app.ts').write_text('export const answer = 7; // user\n')
        refused = c.post(f"/api/jobs/{job['id']}/apply", headers=headers).json()
        assert refused['conflicts'] == ['app.ts'] and refused['applied'] == []
        (repo / 'app.ts').write_text('export const answer = 41;\n')
        done = c.post(f"/api/jobs/{job['id']}/apply", headers=headers).json()
        assert done['applied'] == ['app.ts'] and (repo / 'app.ts').read_text() == 'export const answer = 42;\n'
        assert c.get(f"/api/jobs/{job['id']}/result?view=brief", headers=headers).json()['workspace']['state'] == 'applied'
        assert c.post(f"/api/jobs/{job['id']}/apply", headers=headers).status_code == 409
        assert c.post(f"/api/jobs/{job['id']}/discard", headers=headers).json() == {'state': 'applied'}
    investigator = store.submit(JobRequest(role='investigator', task='x', repo=str(repo), idempotency_key='svc-inv'))
    with TestClient(create_app(store, start_workers=False), base_url='http://127.0.0.1:8765') as c:
        assert c.post(f"/api/jobs/{investigator['id']}/apply", headers=headers).status_code == 409
