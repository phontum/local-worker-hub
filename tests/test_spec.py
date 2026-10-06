import json
import sys
import uuid

import pytest
from pydantic import ValidationError

from hub.skills.coding.editing import mappings
from hub.skills.coding.delegation import spec as specmod
from hub.models import JobRequest
from hub.skills.coding.delegation.spec import Change, Criterion, DelegationSpec, Mapping, Scope, Target, compile_spec, draft_from_task, task_text, verify

SPEC = {
    'goal': 'Rename the progress labels and keep the public API',
    'kind': 'mechanical',
    'targets': [{'path': 'a.py', 'symbol': 'label', 'lines': [1, 9]}],
    'changes': [{'description': 'Rename labels', 'mappings': [{'old': 'Take Backup', 'new': 'Create Backup'}, {'old': 'Restore', 'new': 'Recover'}]}],
    'invariants': [{'kind': 'file_unchanged', 'path': 'b.py'}, {'kind': 'no_new_files'}],
    'acceptance': [{'kind': 'symbol_exists', 'symbol': 'label', 'path': 'a.py'}, {'kind': 'check_passes', 'name': 'unit'}, {'kind': 'text', 'text': 'Labels read naturally'}],
    'scope': {'edit': ['a.py'], 'read': ['b.py']},
    'checks': [{'name': 'unit', 'argv': [sys.executable, '-c', 'print(1)']}],
}

def test_a_spec_compiles_to_the_request_the_hub_already_runs(repo):
    request = compile_spec(DelegationSpec.model_validate(SPEC), str(repo), 'mcp', 'k-1')
    assert (request.role, request.kind, request.allowed_paths, request.workflow, request.repair_attempts, request.caller) == ('editor', 'mechanical', ['a.py'], 'implement', 1, 'mcp')
    assert request.read_paths == ['b.py', 'a.py'] and request.spec['goal'].startswith('Rename') and len(request.checks) == 1
    assert 'Take Backup -> Create Backup' in request.task and 'Must stay true:' in request.task and '- b.py is unchanged' in request.task and 'Targets:\n- a.py (label) lines 1-9' in request.task
    assert [(o, n) for o, n in mappings.extract(request.task)] == [('Take Backup', 'Create Backup'), ('Restore', 'Recover')]  # the existing parser reads the compiled text

def test_roles_scope_rules_and_kind_rules(repo):
    ask = DelegationSpec(goal='Where is the timeout set?', kind='config_use', targets=[Target(path='app.ts')])
    request = compile_spec(ask, str(repo))
    assert request.role == 'investigator' and request.read_paths == ['app.ts'] and request.allowed_paths == []
    with pytest.raises(ValueError, match='scope.edit'):
        compile_spec(DelegationSpec(goal='x', kind='mechanical'), str(repo))
    with pytest.raises(ValueError, match='Only editing'):
        compile_spec(DelegationSpec(goal='x', kind='explain', scope=Scope(edit=['a.py'])), str(repo))
    with pytest.raises(ValueError, match='requires the check'):
        compile_spec(DelegationSpec(goal='x', kind='regression_test', scope=Scope(edit=['t.py'])), str(repo))
    assert compile_spec(DelegationSpec(goal='run', kind='run_tests', checks=[SPEC['checks'][0]]), str(repo)).role == 'validator'

def test_the_model_rejects_malformed_input():
    for bad in ({'goal': ''}, {'goal': 'x', 'surprise': 1}, {'goal': 'x', 'changes': [{'description': 'd', 'mappings': [{'old': 'a\nb', 'new': 'c'}]}]},
                {'goal': 'x', 'invariants': [{'kind': 'symbol_exists'}]}, {'goal': 'x', 'acceptance': [{'kind': 'check_passes'}]}, {'goal': 'x', 'evidence': {'job_ids': ['a'] * 5}}):
        with pytest.raises(ValidationError):
            DelegationSpec.model_validate(bad)

def test_mechanical_criteria_are_decided_from_files_and_checks():
    spec = DelegationSpec.model_validate(SPEC)
    before = {'a.py': 'def label():\n    return "Take Backup"\n', 'b.py': 'x = 1\n'}
    good = {'a.py': 'def label():\n    return "Create Backup"\n', 'b.py': 'x = 1\n'}
    checks = [{'name': 'unit', 'status': 'passed'}]
    result = verify(spec, before, good, checks)
    assert result['unmet'] == 0 and result['for_frontier'] == 1 and {r['status'] for r in result['results']} == {'met', 'frontier_judgement'}
    bad = {'a.py': 'def renamed():\n    return 1\n', 'b.py': 'x = 2\n', 'c.py': 'new = 1\n'}
    broken = verify(spec, {**before, 'c.py': ''}, bad, [{'name': 'unit', 'status': 'failed'}])
    statuses = {r['criterion']: r['status'] for r in broken['results']}
    assert statuses['b.py is unchanged'] == 'unmet' and statuses['no new files are created'] == 'unmet' and statuses['label is defined in a.py'] == 'unmet' and statuses['check "unit" passes'] == 'unmet'
    assert verify(spec, before, good, [])['unknown'] == 1  # no check result: not assumed met
    assert 'Spec criteria not met' in specmod.unmet_summary(broken) and 'c.py' in specmod.unmet_summary(broken)

def test_symbol_absent_and_unknown_cases():
    spec = DelegationSpec(goal='x', acceptance=[Criterion(kind='symbol_absent', symbol='legacy'), Criterion(kind='symbol_exists', symbol='keep')])
    out = verify(spec, {'a.py': ''}, {'a.py': 'def legacy():\n    pass\n'}, [])
    assert [r['status'] for r in out['results']] == ['unmet', 'unknown']

def test_typed_mappings_replace_text_parsing_in_verification():
    before, after = {'a.py': 'Take Backup here'}, {'a.py': 'Take Backup here'}
    assert mappings.verify('no arrows in this task', before, after, [('Take Backup', 'Create Backup')])['unmet']
    assert mappings.verify('no arrows in this task', before, after)['checked'] == 0

def test_a_draft_spec_of_a_natural_language_task_is_deterministic(repo):
    draft = draft_from_task('Rename Take Backup -> Create Backup; Restore -> Recover in app.ts', edit=['app.ts'])
    # the same clause rules as mappings.extract: the draft shows exactly what the host would verify, so a sloppy sentence is visible before the job runs
    assert [(m.old, m.new) for c in draft.changes for m in c.mappings] == [('Rename Take Backup', 'Create Backup'), ('Restore', 'Recover in app.ts')]
    assert draft.scope.edit == ['app.ts'] and draft.targets[0].path == 'app.ts'
    assert draft_from_task('Explain the retry logic').changes == []

def test_delegate_tool_submits_the_compiled_request(monkeypatch, repo):
    import asyncio
    from hub import mcp_adapter
    sent = []
    def fake(method, path, body=None):
        sent.append(body)
        return {'id': 'a' * 32, 'state': 'queued', 'request': {'role': body['role']}}
    monkeypatch.setattr(mcp_adapter, 'call', fake)
    result = asyncio.run(mcp_adapter.create_server().call_tool('delegate', {'repo': str(repo), 'spec': SPEC, 'idempotency_key': 'dk1'}))
    body = sent[0]
    assert body['role'] == 'editor' and body['spec']['goal'].startswith('Rename') and body['idempotency_key'] == 'dk1'
    assert 'Take Backup -> Create Backup' in json.dumps(result, default=str)

def test_cli_spec_check_prints_the_compiled_task(tmp_path, repo, monkeypatch, capsys):
    from hub import cli
    path = tmp_path / 'spec.json'
    path.write_text(json.dumps(SPEC))
    monkeypatch.setattr(sys, 'argv', ['local-worker', 'spec', 'check', str(path), '--repo', str(repo)])
    cli.main()
    out = capsys.readouterr().out
    assert 'role=editor' in out and 'Take Backup -> Create Backup' in out
    path.write_text(json.dumps({'goal': 'x', 'kind': 'mechanical'}))
    with pytest.raises(SystemExit):
        cli.main()

# Runner integration -----------------------------------------------------------------------------------------------------

import subprocess
from pathlib import Path

GIT = ['git', '-c', 'user.name=t', '-c', 'user.email=t@t']

@pytest.fixture
def spec_repo(tmp_path):
    root = tmp_path / 'sr'
    root.mkdir()
    (root / 'a.py').write_text('def label():\n    return "Take Backup"\n')
    (root / 'b.py').write_text('x = 1\n')
    for step in (['init', '-q'], ['add', '-A'], ['commit', '-qm', 'i']):
        subprocess.run(GIT + ['-C', str(root), *step], check=True, capture_output=True)
    return root

async def run_spec_job(store, spec_repo, edit_b):
    from hub.report import parse_report
    from hub.runner import Runner
    raw = {**SPEC, 'changes': [{'description': 'Rename', 'mappings': [{'old': 'Take Backup', 'new': 'Create Backup'}]}], 'scope': {'edit': ['a.py', 'b.py']}, 'acceptance': [SPEC['acceptance'][0]],
           'kind': 'mechanical', 'checks': [{'name': 'unit', 'argv': [sys.executable, '-c', 'print(1)']}]}
    request = compile_spec(DelegationSpec.model_validate(raw), str(spec_repo), 'mcp', uuid.uuid4().hex)
    job = store.submit(request)
    runner = Runner(store, 'token')
    async def model(_job, req, directory, label, prompt, **kwargs):
        (Path(req.repo) / 'a.py').write_text('def label():\n    return "Create Backup"\n')
        if edit_b:
            (Path(req.repo) / 'b.py').write_text('x = 2\n')
        return {'info': {'tokens': {}}}, parse_report('LOCAL_WORKER_REPORT\nStatus: COMPLETE\nFindings:\nDone.\nFiles:\na.py\nChecks:\nNot run.\nRisks:\nNone.\nEND_LOCAL_WORKER_REPORT')
    runner.execute_model = model
    await runner.run(store.next())
    return store.get(job['id'])['result']

@pytest.mark.asyncio
async def test_an_unmet_invariant_downgrades_a_complete_report_to_partial(store, spec_repo):
    result = await run_spec_job(store, spec_repo, edit_b=True)
    statuses = {r['criterion']: r['status'] for r in result['spec_verification']['results']}
    assert statuses['b.py is unchanged'] == 'unmet' and result['worker_status'] == 'PARTIAL' and 'Spec criteria not met' in result['completion']['remaining_issue']

@pytest.mark.asyncio
async def test_a_satisfied_spec_stays_complete_and_lists_what_is_left_for_the_frontier(store, spec_repo):
    result = await run_spec_job(store, spec_repo, edit_b=False)
    assert result['spec_verification']['unmet'] == 0 and result['worker_status'] == 'COMPLETE'
    assert result['mappings']['checked'] == 1 and result['mappings']['unmet'] == []
