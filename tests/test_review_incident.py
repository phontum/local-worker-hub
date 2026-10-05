import json
import sys

import pytest
from fastapi.testclient import TestClient

from hub import incident
from hub.mcp_adapter import create_server
from hub.models import Check, JobRequest, Review
from hub.runner import Runner
from hub.service import create_app
from hub.settings import STATE, initialize


def test_rejection_and_takeover_need_notes_but_acceptance_does_not():
    assert Review(decision='accepted').notes == ''
    for decision in ('rejected', 'takeover'):
        with pytest.raises(ValueError, match='needs notes'):
            Review(decision=decision)
        with pytest.raises(ValueError, match='needs notes'):
            Review(decision=decision, notes='bad')
    assert Review(decision='rejected', notes='Edit was cut off mid-block', reason='truncated').reason == 'truncated'
    with pytest.raises(ValueError):
        Review(decision='rejected', notes='long enough note here', reason='made-up')


def test_summary_counts_rejections_and_takeovers_by_kind_and_reason(store, repo):
    # A review stored before reasons were required has none; summaries must still count it (model_construct skips today's validator).
    for key, kind, review in (('r1', 'mechanical', Review(decision='rejected', notes='Output was cut off', reason='truncated')),
                              ('r2', 'mechanical', Review.model_construct(decision='takeover', reason=None, baseline_frontier_tokens=None, delegated_frontier_tokens=None, baseline_frontier_cost=None, delegated_frontier_cost=None, measurement_source='measured', task_outcome=None, review_effort_seconds=None, notes='Gave up on the large file')),
                              ('r3', 'fix_test', Review(decision='accepted'))):
        job = store.submit(JobRequest(role='editor', task='x', repo=str(repo), allowed_paths=['app.ts'], idempotency_key=key, kind=kind))
        store.next()
        store.finish(job['id'], 'completed', {'usage': {}})
        store.review(job['id'], review)
    stats = store.summary()['review_stats']
    assert stats['by_kind']['mechanical'] == {'accepted': 0, 'rejected': 1, 'takeover': 1} and stats['by_kind']['fix_test']['accepted'] == 1
    assert stats['reasons'] == {'truncated': 1, 'unspecified': 1}


def test_mcp_submit_job_no_longer_offers_in_place_and_record_review_offers_a_reason():
    import asyncio
    tools = {t.name: t for t in asyncio.run(create_server().list_tools())}
    assert 'in_place' not in tools['submit_job'].inputSchema['properties']
    assert 'reason' in tools['record_review'].inputSchema['properties']


def test_service_refuses_in_place_from_mcp_callers_but_not_from_the_cli(store, repo):
    headers = {'Authorization': 'Bearer ' + initialize()}
    body = JobRequest(role='editor', task='x', repo=str(repo), allowed_paths=['app.ts'], idempotency_key='ip-mcp', in_place=True, caller='mcp').model_dump()
    with TestClient(create_app(store, start_workers=False), base_url='http://127.0.0.1:8765') as c:
        refused = c.post('/api/jobs', json=body, headers=headers)
        assert refused.status_code == 403 and 'private workspace' in refused.json()['detail']
        assert c.post('/api/jobs', json=dict(body, caller='cli', idempotency_key='ip-cli'), headers=headers).status_code == 200
        assert c.post('/api/jobs', json=dict(body, in_place=False, idempotency_key='ip-ws'), headers=headers).status_code == 200


@pytest.mark.asyncio
async def test_incident_export_is_metadata_only(store, repo):
    secret_task = 'Rename the Zebra Invoice panel; replace `QuarterlyLedgerTotals` with `Reporting data`; use SEARCH/REPLACE blocks. 1. first 2. second'
    (repo / 'ledger_panel.tsx').write_text('export const QuarterlyLedgerTotals = 1;\n' * 50)
    job = store.submit(JobRequest(role='editor', task=secret_task, repo=str(repo), allowed_paths=['ledger_panel.tsx'], kind='mechanical', idempotency_key='inc-export',
                                  checks=[Check(name='zebra-check', argv=[sys.executable, '-c', 'pass'])], in_place=True))
    runner = Runner(store, 'token')
    async def model(_job, _request, directory, label, prompt, **kwargs):
        from pathlib import Path
        from hub.report import parse_report
        (directory / (label + '.turns.json')).write_text(json.dumps({'turns': [{'step': 0, 'done_reason': 'length', 'output_tokens': 4096, 'limit': 4096, 'truncated': True,
                                                                                  'errors': ['The model output was cut off at 4096 of 4096 tokens; split'], 'planned_files': []}],
                                                                      'changed': [], 'errors': ['x'], 'staged_not_applied': []}))
        return {'info': {'tokens': {}}}, parse_report('LOCAL_WORKER_REPORT\nStatus: PARTIAL\nFindings:\nNo edits were applied (ledger_panel.tsx).\nFiles:\nNone\nChecks:\nNot run.\nRisks:\ncut off\nEND_LOCAL_WORKER_REPORT')
    runner.execute_model = model
    await runner.run(store.next())
    store.review(job['id'], Review(decision='takeover', notes='Took over: Zebra Invoice edit was cut off', reason='truncated'))
    final = store.get(job['id'])
    exported = incident.build(final, STATE / 'jobs' / job['id'])
    text = json.dumps(exported)
    for private in ('Zebra', 'Invoice', 'QuarterlyLedgerTotals', 'ledger_panel', 'zebra-check', 'Reporting data'):
        assert private not in text
    assert exported['turns'][0]['truncated'] and exported['turns'][0]['error_categories'] == ['truncated']
    assert exported['request']['task']['quoted_strings'] == 2 and exported['request']['task']['protocol_words'] == 2 and exported['request']['task']['numbered_items'] == 0
    assert exported['review'] == {'decision': 'takeover', 'reason': 'truncated', 'notes_chars': len('Took over: Zebra Invoice edit was cut off'), 'review_effort_seconds': None}
    assert 'file1' in exported['files'] and exported['files']['file1']['lines_at_export'] == 50
    with_notes = incident.build(final, STATE / 'jobs' / job['id'], include_notes=True)
    assert with_notes['review']['notes'] == 'Took over: Zebra Invoice edit was cut off'  # opt-in only; the default export above had no notes
    assert 'notes' not in exported['review']
