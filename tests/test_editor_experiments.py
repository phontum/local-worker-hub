import json
import re
import sys
import uuid
from pathlib import Path

import pytest

from hub import calls, pipelines
from hub.skills.coding.editing import textedit, workspace
from hub.models import Check, JobRequest
from hub.phases import resolve_phase
from hub.report import final_report, parse_report
from hub.runner import Runner

# Match modes ------------------------------------------------------------------------------------------------------------

CONTENT = 'max = 1\nx = 1\nlabel("a")  # note\n'

def test_alignment_classifies_how_an_exact_match_sits_in_its_lines():
    assert textedit.alignment(CONTENT, CONTENT.index('x = 1', 3), 'x = 1') == 'line'  # the second occurrence is a whole line
    assert textedit.alignment(CONTENT, CONTENT.index('x = 1'), 'x = 1') == 'inside'  # the first one is inside 'max = 1' and cuts through the word max
    assert textedit.alignment(CONTENT, CONTENT.index('label'), 'label("a")') == 'word'  # a whole statement, but the line goes on

def test_each_mode_accepts_and_rejects_the_right_matches_and_counts_them():
    stats = {}
    assert textedit.apply('a\nx = 1\nb\n', 'x = 1', 'x = 2', 'line', stats) == 'a\nx = 2\nb\n' and stats == {'line': 1}
    # unique, but only as part of a word: the old default edits the wrong place, the stricter modes refuse
    assert textedit.apply('max = 1\n', 'x = 1', 'x = 2') == 'max = 2\n'  # substring mode edits the wrong thing: it changes max, which merely contains the searched text
    with pytest.raises(ValueError, match='only part of a line'):
        textedit.apply('max = 1\n', 'x = 1', 'x = 2', 'line')
    # a statement followed by more text on its line: substring mode accepts, line mode refuses
    assert textedit.apply('label("a")  # note\n', 'label("a")', 'label("b")') == 'label("b")  # note\n'
    with pytest.raises(ValueError, match='only part of a line'):
        textedit.apply('label("a")  # note\n', 'label("a")', 'label("b")', 'line')

def test_a_stricter_mode_can_disambiguate_a_search_that_substring_mode_finds_twice():
    content = 'max = 1\nx = 1\n'
    with pytest.raises(ValueError, match='2 places'):
        textedit.apply(content, 'x = 1', 'x = 9')
    assert textedit.apply(content, 'x = 1', 'x = 9', 'line') == 'max = 1\nx = 9\n'

def test_the_tolerant_path_is_unchanged_by_the_mode_and_counted():
    stats = {}
    assert textedit.apply('def f():\n\ta = 1\n', '    a = 1', '    a = 2', 'line', stats) == 'def f():\n    a = 2\n' and stats == {'tolerant': 1}  # tabs in the file, spaces in the SEARCH: no exact match

def test_plan_reports_match_statistics():
    snaps = {'a.py': ('a.py', 'x = 1\nlabel("a")  # n\n', 'h')}
    planned = textedit.plan([textedit.Edit('a.py', 'x = 1', 'x = 2'), textedit.Edit('a.py', 'label("a")', 'label("b")')], snaps, mode='substring')
    assert not planned.errors and planned.stats == {'line': 1, 'word': 1}
    refused = textedit.plan([textedit.Edit('a.py', 'label("a")', 'label("b")')], snaps, mode='line')
    assert 'only part of a line' in refused.errors[0]

# Request fields ----------------------------------------------------------------------------------------------------------

def test_defaults_follow_the_experiments(repo):
    request = JobRequest(role='editor', repo=str(repo), task='t', allowed_paths=['app.ts'], idempotency_key='dflt')
    assert (request.match_mode, request.continuation, request.refuse_oversized, request.model_output) == ('line', True, False, None)
    for retired in ({'edit_format': 'json'}, {'auto_split': True}, {'match_mode': 'word'}):
        with pytest.raises(ValueError):
            JobRequest(role='editor', repo=str(repo), task='t', allowed_paths=['app.ts'], idempotency_key='retired', **retired)

def test_output_cap_is_a_request_option_limited_to_4096_or_8192(repo):
    base = dict(role='editor', repo=str(repo), task='t', allowed_paths=['app.ts'])
    assert resolve_phase(JobRequest(idempotency_key='o1', **base), {}, 'edit')[0].output_limit == 4096
    assert resolve_phase(JobRequest(idempotency_key='o2', model_output=8192, **base), {}, 'edit')[0].output_limit == 8192
    with pytest.raises(ValueError):
        JobRequest(idempotency_key='o3', model_output=5000, **base)
    with pytest.raises(ValueError, match='workspace_from'):
        JobRequest(role='investigator', repo=str(repo), task='t', workspace_from='a' * 32, idempotency_key='o4')

# JSON format and the edit flow -------------------------------------------------------------------------------------------

COMPONENT = ''.join(f'export function Part{i}() {{\n  return label({i}, "Take Backup {i}");\n}}\n' for i in range(1, 41))

@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'proj'
    (root / 'src').mkdir(parents=True)
    (root / 'src' / 'Panel.tsx').write_text(COMPONENT)
    return root

def job(tmp_path, project, task, **kw):
    directory = tmp_path / ('job' + uuid.uuid4().hex[:6])
    directory.mkdir()
    (directory / 'workspace').mkdir()
    (directory / 'request.json').write_text(JobRequest(role='editor', repo=str(project), task=task, allowed_paths=['src/Panel.tsx'], idempotency_key=uuid.uuid4().hex, **kw).model_dump_json())
    return directory

def edit_json(*pairs):
    return json.dumps({'edits': [{'path': 'src/Panel.tsx', 'op': 'replace', 'search': f'  return label({i}, "Take Backup {i}");', 'replace': f'  return label({i}, "Create backup {i}");'} for i in pairs], 'summary': 'renamed'})

@pytest.mark.asyncio
async def test_match_mode_is_applied_and_the_statistics_are_recorded(tmp_path, project, monkeypatch):
    half_line = 'FILE: src/Panel.tsx\n<<<<<<< SEARCH\nTake Backup 7\n=======\nCreate backup 7\n>>>>>>> REPLACE\nEND OF EDITS'
    whole = 'FILE: src/Panel.tsx\n<<<<<<< SEARCH\n  return label(7, "Take Backup 7");\n=======\n  return label(7, "Create backup 7");\n>>>>>>> REPLACE\nEND OF EDITS'
    replies = [(half_line, 'stop', 30), (whole, 'stop', 40)]
    prompts = []
    async def call(client, d, label, name, step, body, event):
        text, reason, tokens = replies.pop(0)
        prompts.append(body['messages'][1]['content'])
        return {'message': {'content': text}, 'done_reason': reason, 'eval_count': tokens}
    monkeypatch.setattr(calls, 'call', call)
    directory = job(tmp_path, project, 'Rename seven', match_mode='line')
    await pipelines.run_edit(directory, 'edit', 'Task:\nRename seven', 'edit')
    turns = json.loads((directory / 'edit.turns.json').read_text())['turns']
    assert 'only part of a line' in prompts[1] and turns[1]['match'] == {'line': 1}
    assert 'Create backup 7' in (project / 'src' / 'Panel.tsx').read_text()

# Automatic decomposition -------------------------------------------------------------------------------------------------

MANY = 'Rename these: ' + '; '.join(f'Take Backup {i} -> Create backup {i}' for i in range(1, 16)) + '.'

def unit_reply(prompt):
    own_part = prompt.split('Authorized files:')[0].split('For context')[0]  # the unit's own changes, not the whole task shown for context
    numbers = re.findall(r'Take Backup (\d+) -> Create backup \1', own_part)
    blocks = ''.join(f'FILE: src/Panel.tsx\n<<<<<<< SEARCH\n  return label({n}, "Take Backup {n}");\n=======\n  return label({n}, "Create backup {n}");\n>>>>>>> REPLACE\n' for n in numbers)
    return blocks + 'END OF EDITS'

@pytest.mark.asyncio
async def test_with_refuse_oversized_the_gate_still_refuses(tmp_path, project, monkeypatch):
    async def call(*a, **k):
        raise AssertionError('no model call expected')
    monkeypatch.setattr(calls, 'call', call)
    directory = job(tmp_path, project, MANY, refuse_oversized=True)
    await pipelines.run_edit(directory, 'edit', 'Task:\n' + MANY, 'edit')
    assert final_report(json.loads((directory / 'edit.session.json').read_text()))['status'] == 'BLOCKED'
    assert json.loads((directory / 'edit.gate.json').read_text())['mode'] == 'refused'

# Workspace chaining through the runner ----------------------------------------------------------------------------------

def report_text(status='COMPLETE'):
    return parse_report(f'LOCAL_WORKER_REPORT\nStatus: {status}\nFindings:\nDone.\nFiles:\nx\nChecks:\nNot run.\nRisks:\nNone.\nEND_LOCAL_WORKER_REPORT')

@pytest.mark.asyncio
async def test_a_job_can_continue_another_jobs_workspace_and_one_apply_writes_both(store, tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'a.py').write_text('a = 1\n')
    (repo / 'b.py').write_text('b = 1\n')
    runner = Runner(store, 'token')
    def model_for(name, text):
        async def model(_job, _request, directory, label, prompt, **kwargs):
            (Path(_request.repo) / name).write_text(text)
            return {'info': {'tokens': {}}}, report_text()
        return model
    first = store.submit(JobRequest(role='editor', task='Change a', repo=str(repo), allowed_paths=['a.py'], checks=[Check(name='ok', argv=[sys.executable, '-c', 'print(1)'])], idempotency_key='chain-1'))
    runner.execute_model = model_for('a.py', 'a = 2\n')
    await runner.run(store.next())
    second = store.submit(JobRequest(role='editor', task='Change b', repo=str(repo), allowed_paths=['b.py'], workspace_from=first['id'], idempotency_key='chain-2'))
    runner.execute_model = model_for('b.py', 'b = 2\n')
    await runner.run(store.next())
    result = store.get(second['id'])['result']
    assert result['workspace']['chain'] == [first['id'], second['id']] and result['acceptance']['diff'] == {'files': 1, 'added': 1, 'removed': 1}
    assert [c['path'] for c in result['acceptance']['changed_files']] == ['b.py']  # the packet describes this job only
    assert (repo / 'a.py').read_text() == 'a = 1\n'
    assert workspace.read_record(first['id'])['state'] == 'chained'
    applied = workspace.apply(second['id'])
    assert sorted(applied['applied']) == ['a.py', 'b.py'] and (repo / 'a.py').read_text() == 'a = 2\n' and (repo / 'b.py').read_text() == 'b = 2\n'

