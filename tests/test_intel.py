import json

import pytest
from fastapi.testclient import TestClient

from hub.intel import tools
from hub.service import create_app
from hub.settings import initialize

PY = '''class Base:
    def run(self):
        return helper(1)

class Child(Base):
    def run(self):
        return helper(2) + other()

def helper(n):
    return n

def other():
    return missing_name_call()
'''
TS = '''import { helper } from './lib';
export interface Shape { area(): number }
export class Square implements Shape { area() { return helper(2); } }
export class Big extends Square {}
export function make() { return new Big(); }
'''

@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'proj'
    (root / 'tests').mkdir(parents=True)
    (root / 'app.py').write_text(PY)
    (root / 'lib.ts').write_text('export function helper(n: number) { return n; }\n')
    (root / 'shapes.ts').write_text(TS)
    (root / 'tests' / 'test_app.py').write_text('from app import helper\n\ndef test_helper():\n    assert helper(3) == 3\n')
    (root / 'broken.py').write_text('def oops(:\n    pass\n')
    (root / 'rel.py').write_text('from .nowhere import x\n')
    (root / '.env').write_text('SECRET=1\n')
    return str(root)

def test_find_symbol_exact_and_near(repo):
    exact = tools.run('find_symbol', repo, {'name': 'helper'})
    assert exact['precision'] == 'syntactic' and {d['path'] for d in exact['definitions']} == {'app.py', 'lib.ts'}
    near = tools.run('find_symbol', repo, {'name': 'HELP'})
    assert near['precision'] == 'lexical' and near['definitions'] and 'note' in near
    assert tools.run('find_symbol', repo, {'name': 'helper', 'kind': 'class'})['definitions'] == []

def test_references_exclude_the_definition_and_secrets_are_never_indexed(repo):
    refs = tools.run('find_references', repo, {'name': 'helper'})['references']
    assert {'path': 'app.py', 'line': 3} in refs and {'path': 'shapes.ts', 'line': 3} in refs and not any(r['path'] == '.env' for r in refs)
    assert tools.run('find_references', repo, {'name': 'SECRET'})['references'] == []

def test_callers_and_callees_follow_call_edges(repo):
    callers = tools.run('callers', repo, {'name': 'helper'})['callers']
    assert {(c['path'], c['in']) for c in callers} >= {('app.py', 'run'), ('shapes.ts', 'area')}
    callees = tools.run('callees', repo, {'name': 'run', 'path': 'app.py'})['definitions']
    names = {c['name'] for d in callees for c in d['calls']}
    assert names >= {'helper', 'other'} and all(d['path'] == 'app.py' for d in callees)
    resolved = {c['name']: c['definitions'] for d in callees for c in d['calls']}
    assert resolved['helper'][0]['path'] == 'app.py' and resolved['other'][0]['kind'] == 'function'

def test_implementations_python_and_typescript(repo):
    py = tools.run('find_implementations', repo, {'name': 'Base'})
    assert [i['name'] for i in py['implementations']] == ['Child']
    ts = tools.run('find_implementations', repo, {'name': 'Shape'})
    assert [i['name'] for i in ts['implementations']] == ['Square']
    assert [i['name'] for i in tools.run('find_implementations', repo, {'name': 'Square'})['implementations']] == ['Big']

def test_diagnostics_report_syntax_errors_and_unresolved_relative_imports(repo):
    found = tools.run('diagnostics', repo, {})['diagnostics']
    assert any(d['path'] == 'broken.py' and d['severity'] == 'error' for d in found)
    assert any(d['path'] == 'rel.py' and 'unresolved relative import .nowhere' in d['message'] for d in found)
    assert tools.run('diagnostics', repo, {'paths': ['app.py']})['diagnostics'] == []

def test_symbol_context_bundles_definition_callers_callees_and_tests(repo):
    ctx = tools.run('symbol_context', repo, {'name': 'helper', 'path': 'app.py'})
    assert ctx['found'] and ctx['definition']['path'] == 'app.py' and ctx['signature'] == 'def helper(n):'
    assert ('app.py', 'run') in {(c['path'], c['in']) for c in ctx['callers']} and ctx['tests'] == ['tests/test_app.py']
    assert tools.run('symbol_context', repo, {'name': 'nothing_here'})['found'] is False
    small = tools.run('symbol_context', repo, {'name': 'Child', 'budget': 1})
    assert small['found']

def test_outline_lists_symbols_and_resolved_imports(repo):
    out = tools.run('outline', repo, {'path': 'shapes.ts'})
    assert {'Shape', 'Square', 'Big', 'make'} <= {s['name'] for s in out['symbols']}
    assert out['imports'][0]['file'] == 'lib.ts'

def test_bad_input_is_refused(repo, tmp_path):
    with pytest.raises(ValueError, match='Unknown tool'):
        tools.run('rm_rf', repo, {})
    with pytest.raises(ValueError, match='name is required'):
        tools.run('find_symbol', repo, {'name': ' '})
    with pytest.raises(ValueError):
        tools.run('find_symbol', '/', {'name': 'x'})

def test_service_endpoint_needs_auth_and_does_not_queue_a_job(store, repo):
    with TestClient(create_app(store, start_workers=False), base_url='http://127.0.0.1:8765') as c:
        assert c.post('/api/intel/find_symbol', json={'repo': repo, 'name': 'helper'}).status_code == 401
        headers = {'Authorization': 'Bearer ' + initialize()}
        ok = c.post('/api/intel/find_symbol', headers=headers, json={'repo': repo, 'name': 'helper'})
        assert ok.status_code == 200 and ok.json()['definitions']
        assert c.post('/api/intel/find_symbol', headers=headers, json={'repo': repo}).status_code == 409
        assert store.list() == []

def test_cli_intel_posts_the_tool_call(repo, monkeypatch, capsys):
    import sys
    from hub import cli, client
    seen = []
    monkeypatch.setattr(client, 'call', lambda method, path, data=None: seen.append((method, path, data)) or {'ok': True})
    monkeypatch.setattr(sys, 'argv', ['local-worker', 'intel', 'callers', 'helper', '--repo', repo, '--limit', '5'])
    cli.main()
    assert seen == [('POST', '/api/intel/callers', {'repo': repo, 'limit': 5, 'name': 'helper'})]
    monkeypatch.setattr(sys, 'argv', ['local-worker', 'intel', 'outline', 'app.py', '--repo', repo])
    cli.main()
    assert seen[1][2] == {'repo': repo, 'path': 'app.py'}
