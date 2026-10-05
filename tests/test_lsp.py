import json
import sys
import uuid
from pathlib import Path

import pytest

from hub import profiles
from hub.intel import lsp, tools
from hub.intel.lexical import LexicalProvider
from hub.intel.lsp import LspClient, LspError, LspProvider, Pool, path_uri
from hub.models import Check, JobRequest
from hub.scoped import ScopedFiles

FAKE = str(Path(__file__).parent / 'fake_lsp_server.py')

def server_argv(script=None):
    return [sys.executable, FAKE, json.dumps(script or {})]

@pytest.fixture
def root(tmp_path):
    r = tmp_path / 'proj'
    r.mkdir()
    (r / 'app.py').write_text('def helper(n):\n    return n\n\ndef run():\n    return helper(1)\n')
    (r / 'other.py').write_text('from app import helper\n\nvalue = helper(2)\n')
    return r

def files_for(root):
    return ScopedFiles(JobRequest(role='investigator', repo=str(root), task='x', idempotency_key=uuid.uuid4().hex))

def loc(root, name, line):
    return {'uri': path_uri(root / name), 'range': {'start': {'line': line, 'character': 0}, 'end': {'line': line, 'character': 3}}}

@pytest.fixture
def pool():
    p = Pool()
    yield p
    p.close_all()

def provider(root, pool, script, timeout=5):
    pool.clients[(str(root.resolve()), 'python')] = LspClient(server_argv(script), root, timeout=timeout).start()
    return LspProvider(files_for(root), {'python': server_argv(script)}, pool)

def test_client_round_trip_and_clean_shutdown(root):
    client = LspClient(server_argv({'results': {'ping': {'ok': 1}}}), root).start()
    assert client.request('ping', {}) == {'ok': 1}
    client.close()
    assert client.process.poll() is not None
    with pytest.raises(LspError):
        client.request('ping', {})

def test_semantic_references_replace_the_lexical_answer(root, pool):
    script = {'results': {'textDocument/references': [loc(root, 'app.py', 4), loc(root, 'other.py', 2), {'uri': 'file:///elsewhere/x.py', 'range': {'start': {'line': 0, 'character': 0}}}]}}
    out = provider(root, pool, script).references('helper')
    assert out['provider'] == 'lsp' and out['precision'] == 'semantic'
    assert out['references'] == [{'path': 'app.py', 'line': 5}, {'path': 'other.py', 'line': 3}]  # the location outside the repository is dropped

def test_a_server_error_falls_back_to_the_lexical_answer_with_the_reason(root, pool):
    out = provider(root, pool, {'errors': ['textDocument/references']}).references('helper')
    assert out['provider'] == 'codeindex' and out['precision'] == 'lexical' and 'not supported' in out['fallback'] and out['references']

def test_a_hung_server_times_out_and_falls_back(root, pool):
    out = provider(root, pool, {'hang': ['textDocument/references']}, timeout=1).references('helper')
    assert 'timed out' in out['fallback'] and out['references']

def test_a_crashed_server_falls_back_and_is_restarted_next_time(root, pool):
    p = provider(root, pool, {'crash_after': 'textDocument/references', 'results': {'textDocument/references': [loc(root, 'other.py', 2)]}})
    assert p.references('helper')['precision'] == 'semantic'
    import time
    time.sleep(0.3)
    second = p.references('helper')  # the dead client is replaced by a fresh server from the approved argv
    assert second['precision'] == 'semantic'

def test_a_missing_server_program_or_unapproved_language_falls_back(root, pool):
    missing = LspProvider(files_for(root), {'python': ['/nonexistent/language-server']}, pool).references('helper')
    assert 'could not start' in missing['fallback'] and missing['references']
    unapproved = LspProvider(files_for(root), {}, pool).references('helper')
    assert 'no approved language server' in unapproved['fallback']

def test_callers_use_the_call_hierarchy(root, pool):
    item = {'name': 'helper', 'uri': path_uri(root / 'app.py'), 'range': {}, 'selectionRange': {}}
    caller = {'name': 'run', 'uri': path_uri(root / 'app.py'), 'range': {}, 'selectionRange': {'start': {'line': 3, 'character': 4}}}
    script = {'results': {'textDocument/prepareCallHierarchy': [item], 'callHierarchy/incomingCalls': [{'from': caller, 'fromRanges': []}]}}
    out = provider(root, pool, script).callers('helper')
    assert out['precision'] == 'semantic' and out['callers'] == [{'path': 'app.py', 'in': 'run', 'line': 4}]

def test_diagnostics_come_from_the_server_and_include_type_errors(root, pool):
    script = {'diagnostics': [{'range': {'start': {'line': 1, 'character': 4}}, 'severity': 1, 'message': 'Type "str" is not assignable to "int"', 'source': 'pyright'}]}
    out = provider(root, pool, script).diagnostics(['app.py'])
    assert out['precision'] == 'semantic' and out['diagnostics'] == [{'path': 'app.py', 'line': 2, 'severity': 'error', 'message': 'Type "str" is not assignable to "int"', 'source': 'pyright'}]
    assert provider(root, Pool(), {}).diagnostics(['app.py'])['provider'] in ('codeindex', 'lsp')

def test_the_pool_keeps_at_most_two_servers_and_stops_the_least_recently_used(tmp_path, monkeypatch):
    p = Pool()
    roots = []
    for name in 'abc':
        r = tmp_path / name
        r.mkdir()
        roots.append(r)
    first = p.get(roots[0], 'python', server_argv())
    p.get(roots[1], 'python', server_argv())
    p.get(roots[2], 'python', server_argv())
    assert len(p.clients) == 2 and first.process.poll() is not None and (str(roots[0].resolve()), 'python') not in p.clients
    p.close_all()

def test_servers_start_only_when_the_reviewed_profile_names_them(root, monkeypatch):
    assert tools.provider_for(str(root)).__class__ is LexicalProvider
    profile = profiles.ProjectProfile(repo=str(root), constraints='c', groups={'g': [Check(name='ok', argv=['true'])]}, lsp={'python': ['pyright-langserver', '--stdio']})
    monkeypatch.setattr(profiles, 'load_profile', lambda r: (profile, 'a' * 64))
    assert lsp.approved_servers(str(root)) == {'python': ['pyright-langserver', '--stdio']}
    assert isinstance(tools.provider_for(str(root)), LspProvider)
