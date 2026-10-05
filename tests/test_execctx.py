import json
import uuid

import pytest

from hub import calls, contextpack, execctx, pipelines, testparse
from hub.codeindex import CodeIndex
from hub.models import JobRequest
from hub.scoped import ScopedFiles

PYTEST_OUT = '''=================================== FAILURES ===================================
___________________________________ test_a ____________________________________

    def test_a():
>       assert compute(2) == 5

tests/test_mod.py:7: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
src/mod.py:12: in compute
    return helper(x)
/home/u/.venv/lib/python3.12/site-packages/pluggy/x.py:5: in call
    pass
src/mod.py:20: in helper
    return x + 1
E   AssertionError: 3 != 5
=========================== short test summary info ============================
FAILED tests/test_mod.py::test_a - AssertionError: 3 != 5
============================== 1 failed in 0.10s ===============================
'''

def test_pytest_failures_carry_repository_frames_and_drop_library_frames():
    failure = testparse.parse(PYTEST_OUT, ['pytest'])['failures'][0]
    assert [(f['file'], f['line']) for f in failure['frames']] == [('tests/test_mod.py', 7), ('src/mod.py', 12), ('src/mod.py', 20)]
    assert failure['frames'][1]['function'] == 'compute'

def test_node_stacks_are_parsed_and_absolute_paths_made_relative():
    text = ' FAIL  src/a.test.ts > adds\nError: expected 1\n ❯ src/a.ts:12:5\n    at add (/repo/src/a.ts:30:3)\n    at x (/repo/node_modules/y/index.js:1:1)\n'
    frames = testparse.parse(text, ['vitest'], root='/repo')['failures'][0]['frames']
    assert [(f['file'], f['line']) for f in frames] == [('src/a.ts', 12), ('src/a.ts', 30)]

BIG = '\n'.join(f'def filler_{i}():\n    return {i}\n' for i in range(500))  # 1500 lines, no reason to be shown from the task alone
MOD = BIG + '''
def compute(x):
    return helper(x)


def helper(x):
    return x + 1
'''

@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'proj'
    (root / 'src').mkdir(parents=True)
    (root / 'tests').mkdir()
    (root / 'src' / 'mod.py').write_text(MOD)
    (root / 'tests' / 'test_mod.py').write_text('from src.mod import compute\n\n\ndef test_a():\n\n\n    assert compute(2) == 5\n')
    return root

def request_for(project, **kw):
    return JobRequest(role='editor', repo=str(project), task='Fix the failing test', allowed_paths=['src/mod.py'], idempotency_key=uuid.uuid4().hex, **kw)

def failures_for(project):
    line = MOD.split('\n').index('def helper(x):') + 2  # the `return x + 1` line
    compute = MOD.split('\n').index('def compute(x):') + 2
    return [{'tool': 'pytest', 'test_id': 'tests/test_mod.py::test_a', 'file': 'src/mod.py', 'line': line, 'message': 'AssertionError: 3 != 5',
             'frames': [{'file': 'tests/test_mod.py', 'line': 7, 'function': 'test_a', 'order': 'outermost_first'},
                        {'file': 'src/mod.py', 'line': compute, 'function': 'compute', 'order': 'outermost_first'},
                        {'file': 'src/mod.py', 'line': line, 'function': 'helper', 'order': 'outermost_first'}]}]

def test_frames_resolve_to_enclosing_functions_and_become_focus_ranges(project):
    index = CodeIndex.load(ScopedFiles(request_for(project)))
    text, focus = execctx.build(failures_for(project), index)
    assert 'in helper' in text and 'in compute' in text and 'AssertionError: 3 != 5' in text and 'tests/test_mod.py:7' in text
    (start, end), = [r for r in focus['src/mod.py'] if r[0] == MOD.split('\n').index('def helper(x):') + 1]
    assert end >= start and focus['src/mod.py'][0][0] == start  # the innermost frame comes first
    assert execctx.build([{'test_id': 't', 'frames': []}], index) == ('', {})
    assert execctx.build([{'test_id': 't', 'frames': [{'file': 'elsewhere.py', 'line': 3, 'function': None, 'order': 'outermost_first'}]}], index) == ('', {})

def test_focus_ranges_are_packed_ahead_of_the_head_of_a_large_file(project):
    snapshots = {'src/mod.py': ('src/mod.py', MOD, 'digest')}
    plain = contextpack.pack(snapshots, 'Fix the failing test', 6000)
    assert 'return x + 1' not in plain.text and plain.report['files']['src/mod.py']['whole'] is False
    index = CodeIndex.load(ScopedFiles(request_for(project)))
    _, focus = execctx.build(failures_for(project), index)
    packed = contextpack.pack(snapshots, 'Fix the failing test', 6000, focus=focus)
    assert 'def helper(x):' in packed.text and 'return x + 1' in packed.text and packed.report['focus']['src/mod.py']

@pytest.mark.asyncio
async def test_run_edit_shows_failure_evidence_and_the_failing_function(tmp_path, project, monkeypatch):
    directory = tmp_path / 'job'
    (directory / 'workspace').mkdir(parents=True)
    request = request_for(project)
    (directory / 'request.json').write_text(request.model_dump_json())
    (directory / 'failure-evidence.json').write_text(json.dumps({'failures': failures_for(project)}))
    prompts = []
    async def call(client, d, label, name, step, body, event):
        prompts.append(body['messages'][1]['content'])
        return {'message': {'content': 'FILE: src/mod.py\n<<<<<<< SEARCH\n    return x + 1\n=======\n    return x + 2\n>>>>>>> REPLACE\nEND OF EDITS\nSummary: bumped'}, 'done_reason': 'stop', 'eval_count': 50}
    monkeypatch.setattr(calls, 'call', call)
    await pipelines.run_edit(directory, 'edit', 'Task:\nFix the failing test', 'edit')
    assert 'Failure evidence' in prompts[0] and 'src/mod.py' in prompts[0] and 'return x + 1' in prompts[0]
    assert 'return x + 2' in (project / 'src' / 'mod.py').read_text()
    context = json.loads((directory / 'edit.context.json').read_text())
    assert context['focus']['src/mod.py']

@pytest.mark.asyncio
async def test_without_failure_evidence_nothing_changes(tmp_path, project, monkeypatch):
    assert pipelines.execution_focus(tmp_path, ScopedFiles(request_for(project))) == ('', {})
    (tmp_path / 'failure-evidence.json').write_text('not json')
    assert pipelines.execution_focus(tmp_path, ScopedFiles(request_for(project))) == ('', {})
