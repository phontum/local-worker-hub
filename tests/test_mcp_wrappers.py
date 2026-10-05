import asyncio

import pytest

from hub import mcp_adapter
from hub.mcp_adapter import create_server

@pytest.fixture
def submitted(monkeypatch, repo):
    sent = []
    def fake_call(method, path, body=None):
        sent.append((method, path, body))
        return {'id': 'a' * 32, 'state': 'queued', 'request': {'role': body['role']}}
    monkeypatch.setattr(mcp_adapter, 'call', fake_call)
    return sent

def invoke(name, args):
    return asyncio.run(create_server().call_tool(name, args))

def body(sent):
    assert len(sent) == 1 and sent[0][:2] == ('POST', '/api/jobs')
    return sent[0][2]

def test_the_wrappers_exist_and_none_of_the_surface_offers_unsafe_options():
    tools = {t.name: t for t in asyncio.run(create_server().list_tools())}
    for name in ('investigate_code', 'implement_change', 'fix_failing_test', 'add_regression_test', 'run_checks', 'apply_result', 'revert_result', 'discard_result'):
        assert name in tools
    for name in ('investigate_code', 'implement_change', 'fix_failing_test', 'add_regression_test', 'run_checks'):
        assert not {'in_place', 'agent_loop', 'board', 'skip_gate'} & set(tools[name].inputSchema['properties'])
    assert 'in_place' not in tools['submit_job'].inputSchema['properties'] and 'skip_gate' not in tools['submit_job'].inputSchema['properties']
    assert {'accept_removals', 'revalidate', 'run_checks', 'accept_stale'} <= set(tools['apply_result'].inputSchema['properties'])

def test_investigate_code_is_a_read_only_kinded_job(submitted, repo):
    result = invoke('investigate_code', {'repo': str(repo), 'question': 'Where is answer used?', 'kind': 'config_use', 'read_paths': ['app.ts']})
    request = body(submitted)
    assert request['role'] == 'investigator' and request['kind'] == 'config_use' and request['allowed_paths'] == [] and request['read_paths'] == ['app.ts'] and request['caller'] == 'mcp'
    assert len(request['idempotency_key']) == 32 and 'get_result' in str(result)

def test_investigate_code_rejects_an_editor_kind(submitted, repo):
    with pytest.raises(Exception, match='kind must be one of'):
        invoke('investigate_code', {'repo': str(repo), 'question': 'x', 'kind': 'mechanical'})

def test_implement_change_defaults_to_a_workspace_job_and_implements_with_a_repair_only_when_checks_exist(submitted, repo):
    invoke('implement_change', {'repo': str(repo), 'task': 'Rename x', 'files': ['app.ts']})
    plain = body(submitted)
    assert plain['role'] == 'editor' and plain['kind'] == 'mechanical' and plain['workflow'] == 'single' and plain['repair_attempts'] == 0 and plain['in_place'] is False and plain['allowed_paths'] == ['app.ts']
    submitted.clear()
    invoke('implement_change', {'repo': str(repo), 'task': 'Guard x', 'files': ['app.ts'], 'kind': 'guard', 'read_paths': ['other.ts'],
                                'checks': [{'name': 'unit', 'argv': ['python', '-c', 'pass']}], 'idempotency_key': 'retry-1'})
    checked = body(submitted)
    assert checked['workflow'] == 'implement' and checked['repair_attempts'] == 1 and checked['kind'] == 'guard' and checked['idempotency_key'] == 'retry-1'
    assert checked['read_paths'] == ['other.ts', 'app.ts']  # read scope always contains the editable files
    assert checked['checks'][0]['name'] == 'unit'

def test_fix_failing_test_uses_the_test_command_as_the_approved_check_with_one_repair(submitted, repo):
    invoke('fix_failing_test', {'repo': str(repo), 'test_command': ['pytest', '-q', 'tests/test_a.py::test_x'], 'files': ['app.ts'], 'test_id': 'tests/test_a.py::test_x', 'details': 'assert 1 == 2'})
    request = body(submitted)
    assert request['kind'] == 'fix_test' and request['workflow'] == 'implement' and request['repair_attempts'] == 1
    assert request['checks'][0]['argv'] == ['pytest', '-q', 'tests/test_a.py::test_x'] and 'assert 1 == 2' in request['task'] and 'not the test' in request['task']

def test_add_regression_test_authorizes_only_the_test_file(submitted, repo):
    invoke('add_regression_test', {'repo': str(repo), 'bug': 'total() is off by one', 'test_file': 'tests/test_total.py', 'test_command': ['pytest', '-q', 'tests/test_total.py']})
    request = body(submitted)
    assert request['kind'] == 'regression_test' and request['allowed_paths'] == ['tests/test_total.py'] and request['workflow'] == 'single'
    assert 'total() is off by one' in request['task'] and request['checks'][0]['name'] == 'regression test'

def test_run_checks_is_a_zero_token_validator_job(submitted, repo):
    invoke('run_checks', {'repo': str(repo), 'checks': [{'name': 'tests', 'argv': ['pytest', '-q']}]})
    request = body(submitted)
    assert request['role'] == 'validator' and request['kind'] == 'run_tests' and request['summary_mode'] == 'none' and request['checks'][0]['argv'] == ['pytest', '-q']

def test_tier0_tools_and_record_outcome_are_registered_read_only_where_they_should_be():
    tools = {t.name: t for t in asyncio.run(create_server().list_tools())}
    for name in ('find_symbol', 'find_references', 'find_implementations', 'callers', 'callees', 'diagnostics', 'symbol_context', 'outline'):
        assert tools[name].annotations.readOnlyHint is True and 'repo' in tools[name].inputSchema['properties']
    assert 'record_outcome' in tools and 'board' not in tools['submit_job'].inputSchema['properties']
