"""Replays of recorded Editor incidents (tests/data/incidents/*/fixture.json) through the real edit pipeline with a fake model.

Always checked: the user's files are either untouched or the report says COMPLETE; a truncated reply never applies anything. When a fixture
sets `expected`, its status and changed files must match too."""
import hashlib
import json
import uuid
from pathlib import Path

import pytest

from hub import calls, incident, pipelines
from hub.models import JobRequest
from hub.report import final_report

DATA = Path(__file__).parent / 'data' / 'incidents'

def digests(root, paths):
    return {p: hashlib.sha256((root / p).read_bytes()).hexdigest() if (root / p).exists() else None for p in paths}

async def replay(fixture, files, tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    for path in fixture['allowed_paths']:
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes((files / path).read_bytes())
    directory = tmp_path / ('job' + uuid.uuid4().hex[:6])
    (directory / 'workspace').mkdir(parents=True)
    (directory / 'request.json').write_text(JobRequest(role='editor', repo=str(root), task=fixture['task'], allowed_paths=fixture['allowed_paths'],
        delete_paths=fixture.get('delete_paths', []), idempotency_key='fx' + uuid.uuid4().hex[:8]).model_dump_json())
    queue = list(fixture['replies']) or [{'text': '', 'done_reason': 'stop', 'output_tokens': 0}]
    async def call(client, d, label, name, step, body, event):
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        return {'message': {'content': reply['text']}, 'done_reason': reply['done_reason'], 'eval_count': reply['output_tokens']}
    monkeypatch.setattr(calls, 'call', call)
    before = digests(root, fixture['allowed_paths'])
    await pipelines.run_edit(directory, 'edit', 'Task:\n' + fixture['task'], 'edit')
    report = final_report(json.loads((directory / 'edit.session.json').read_text()))
    meta = json.loads((directory / 'edit.turns.json').read_text())
    return root, before, report, meta

def check_invariants(root, before, report, meta, fixture):
    after = digests(root, fixture['allowed_paths'])
    if report['status'] != 'COMPLETE':
        assert after == before, 'a PARTIAL job must not leave edits in the tree'
    if any(t['truncated'] for t in meta['turns']) and not meta.get('changed'):
        assert after == before
    expected = fixture.get('expected')
    if expected:
        assert report['status'] == expected['status']
        assert sorted(p for p in before if after[p] != before[p]) == sorted(expected.get('changed', []))

def fixture_dirs():
    return sorted(p.parent for p in DATA.glob('*/fixture.json'))

@pytest.mark.asyncio
@pytest.mark.parametrize('folder', fixture_dirs() or [None], ids=lambda f: f.name if f else 'none-recorded')
async def test_recorded_incident_replays_safely(folder, tmp_path, monkeypatch):
    if folder is None:
        pytest.skip('no recorded incident fixtures yet')
    fixture = json.loads((folder / 'fixture.json').read_text())
    root, before, report, meta = await replay(fixture, folder / 'files', tmp_path, monkeypatch)
    check_invariants(root, before, report, meta, fixture)

def make_job_dir(tmp_path):
    """A finished Editor job's private directory, as the pipeline writes it: raw replies in the trace, one record per turn."""
    directory = tmp_path / 'jobs' / 'j1'
    directory.mkdir(parents=True)
    reply = 'FILE: a.py\n<<<<<<< SEARCH\nx = 1\n=======\nx = 2\n>>>>>>> REPLACE\nEND OF EDITS\nSummary: bumped'
    rows = [{'phase': 'edit', 'step': 1, 'kind': 'content', 'text': reply[:30]}, {'phase': 'edit', 'step': 1, 'kind': 'content', 'text': reply[30:]},
            {'phase': 'edit', 'step': 1, 'kind': 'thinking', 'text': 'ignored'}, {'phase': 'ask-decide', 'step': 0, 'kind': 'content', 'text': 'ignored'}]
    (directory / 'trace.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
    (directory / 'edit.turns.json').write_text(json.dumps({'turns': [{'step': 1, 'done_reason': 'stop', 'output_tokens': 40}]}))
    return directory, reply

def test_export_rebuilds_replies_and_pre_images_from_a_job(tmp_path):
    directory, reply = make_job_dir(tmp_path)
    base = tmp_path / 'base'
    base.mkdir()
    (base / 'a.py').write_text('x = 1\n')
    job = {'request': {'role': 'editor', 'task': 'Set x to 2', 'allowed_paths': ['a.py', '../escape.py', 'missing.py']}, 'result': {'worker_status': 'PARTIAL'},
           'review': {'decision': 'takeover', 'reason': 'wrong_edit'}, 'state': 'completed'}
    fixture = incident.export_fixture(job, directory, base, tmp_path / 'out')
    assert fixture['replies'] == [{'text': reply, 'done_reason': 'stop', 'output_tokens': 40}]
    assert fixture['allowed_paths'] == ['a.py'] and (tmp_path / 'out' / 'files' / 'a.py').read_text() == 'x = 1\n'
    assert fixture['recorded'] == {'decision': 'takeover', 'reason': 'wrong_edit'} and fixture['expected'] is None
    with pytest.raises(ValueError):
        incident.export_fixture({'request': {'role': 'investigator'}}, directory, base, tmp_path / 'x')

@pytest.mark.asyncio
async def test_an_exported_fixture_replays_through_the_pipeline(tmp_path, monkeypatch):
    directory, _ = make_job_dir(tmp_path)
    base = tmp_path / 'base'
    base.mkdir()
    (base / 'a.py').write_text('x = 1\n')
    job = {'request': {'role': 'editor', 'task': 'Set x to 2', 'allowed_paths': ['a.py']}, 'result': {}, 'review': None, 'state': 'completed'}
    out = tmp_path / 'out'
    incident.export_fixture(job, directory, base, out)
    fixture = json.loads((out / 'fixture.json').read_text())
    fixture['expected'] = {'status': 'COMPLETE', 'changed': ['a.py']}
    root, before, report, meta = await replay(fixture, out / 'files', tmp_path / 'replay', monkeypatch)
    check_invariants(root, before, report, meta, fixture)
    assert (root / 'a.py').read_text() == 'x = 2\n'
