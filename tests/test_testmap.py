import sys
import uuid

import pytest

from hub import testmap
from hub.codeindex import CodeIndex
from hub.models import JobRequest
from hub.scoped import ScopedFiles

@pytest.fixture
def index(tmp_path):
    root = tmp_path / 'proj'
    (root / 'pkg').mkdir(parents=True)
    (root / 'tests').mkdir()
    (root / 'pkg' / '__init__.py').write_text('')
    (root / 'pkg' / 'billing.py').write_text('def compute_invoice(x):\n    return x\n\ndef run():\n    pass\n')
    (root / 'pkg' / 'api.py').write_text('from pkg.billing import compute_invoice\n\ndef endpoint():\n    return compute_invoice(1)\n')
    (root / 'pkg' / 'other.py').write_text('def unrelated():\n    return 1\n')
    (root / 'tests' / 'test_api.py').write_text('from pkg.api import endpoint\n\ndef test_endpoint():\n    assert endpoint() == 1\n')
    (root / 'tests' / 'test_billing.py').write_text('def test_math():\n    assert True\n')
    (root / 'tests' / 'test_mentions.py').write_text('def test_x():\n    compute_invoice(2)\n')
    (root / 'tests' / 'test_other.py').write_text('from pkg.other import unrelated\n')
    request = JobRequest(role='investigator', repo=str(root), task='x', idempotency_key=uuid.uuid4().hex)
    return CodeIndex.load(ScopedFiles(request))

def test_tests_are_found_by_import_name_and_mentioned_symbols(index):
    ranked = {r['path']: r for r in testmap.select(index, ['pkg/billing.py'])}
    assert 'tests/test_other.py' not in ranked
    assert any('imports pkg/billing.py via another module' in why for why in ranked['tests/test_api.py']['reasons'])
    assert any('named after billing.py' in why for why in ranked['tests/test_billing.py']['reasons'])
    assert any('mentions compute_invoice' in why for why in ranked['tests/test_mentions.py']['reasons'])
    order = [r['path'] for r in testmap.select(index, ['pkg/billing.py'])]
    assert order.index('tests/test_billing.py') < order.index('tests/test_mentions.py')

def test_history_boosts_a_test_that_failed_after_earlier_changes(index):
    jobs = [{'request': {'caller': 'mcp'}, 'result': {'changed_files': ['pkg/other.py'], 'checks': [{'failures': [{'file': 'tests/test_api.py'}]}]}},
            {'request': {'caller': 'eval'}, 'result': {'changed_files': ['pkg/other.py'], 'checks': [{'failures': [{'file': 'tests/test_billing.py'}]}]}}]
    history = testmap.history_from_jobs(jobs)
    assert history == {'pkg/other.py': {'tests/test_api.py': 1}}
    ranked = {r['path']: r for r in testmap.select(index, ['pkg/other.py'], history)}
    assert 'failed 1x' in ' '.join(ranked['tests/test_api.py']['reasons']) and 'tests/test_billing.py' not in ranked

TEMPLATE = {'name': 'pytest', 'argv': [sys.executable, '-m', 'pytest', '-q', testmap.PLACEHOLDER], 'cwd': '.', 'timeout': 300}

def test_the_template_is_filled_only_with_validated_test_paths(index):
    filled = testmap.fill_template(TEMPLATE, ['tests/test_api.py', 'tests/test_billing.py'], index)
    assert filled['argv'][-2:] == ['tests/test_api.py', 'tests/test_billing.py'] and filled['name'] == 'pytest (selected)' and testmap.PLACEHOLDER not in filled['argv']
    hostile = ['--rootdir=/etc', 'tests/test_api.py; rm -rf /', '../escape_test.py', 'pkg/billing.py', 'tests/missing_test.py', '$(id)']
    assert testmap.fill_template(TEMPLATE, hostile, index) is None
    assert testmap.fill_template(TEMPLATE, ['--rootdir=/etc', 'tests/test_api.py'], index)['argv'][-1] == 'tests/test_api.py'
    assert testmap.fill_template({'name': 'plain', 'argv': ['pytest']}, ['tests/test_api.py'], index) is None
    assert testmap.fill_template({'name': 'two', 'argv': ['pytest', testmap.PLACEHOLDER, testmap.PLACEHOLDER]}, ['tests/test_api.py'], index) is None

def test_an_unfilled_template_is_recognised_so_it_can_never_run():
    assert testmap.refuse_unfilled(['pytest', testmap.PLACEHOLDER]) and testmap.refuse_unfilled(['x{tests}y']) and not testmap.refuse_unfilled(['pytest', 'tests/a.py'])

def test_recommend_returns_ranked_tests_and_concrete_approved_checks(index):
    out = testmap.recommend(index, ['pkg/billing.py'], [TEMPLATE, {'name': 'lint', 'argv': ['ruff', 'check']}])
    assert [c['name'] for c in out['checks']] == ['pytest (selected)'] and out['tests'][0]['path'].startswith('tests/')

# Service and MCP wiring ----------------------------------------------------------------------------------------------

def test_service_recommends_tests_and_routes(store, tmp_path):
    from fastapi.testclient import TestClient
    from hub.service import create_app
    from hub.settings import initialize
    root = tmp_path / 'svc'
    (root / 'tests').mkdir(parents=True)
    (root / 'mod.py').write_text('def compute_total(x):\n    return x\n')
    (root / 'tests' / 'test_mod.py').write_text('from mod import compute_total\n')
    headers = {'Authorization': 'Bearer ' + initialize()}
    with TestClient(create_app(store, start_workers=False), base_url='http://127.0.0.1:8765') as c:
        assert c.post('/api/testmap', json={'repo': str(root), 'paths': ['mod.py']}).status_code == 401
        out = c.post('/api/testmap', headers=headers, json={'repo': str(root), 'paths': ['mod.py']}).json()
        assert out['tests'][0]['path'] == 'tests/test_mod.py' and out['checks'] == []
        assert c.post('/api/testmap', headers=headers, json={'repo': str(root), 'paths': []}).status_code == 409
        advice = c.post('/api/route', headers=headers, json={'role': 'editor', 'kind': 'fix_test', 'task': 'x' * 200, 'files': 1}).json()
        assert advice['tier'] == 1 and advice['basis']['note'] == 'prior-dominated'
        assert c.post('/api/route', headers=headers, json={'role': 'personal'}).status_code == 409
        stats = c.get('/api/outcomes', headers=headers).json()
        assert stats['by_kind'] == [] and stats['calibration'] == []

def test_unfilled_templates_never_reach_a_check_runner(tmp_path):
    from hub.models import Check
    from hub.validation import run_check_sync
    result = run_check_sync(Check(name='t', argv=['pytest', testmap.PLACEHOLDER]), tmp_path)
    assert result['status'] == 'blocked' and 'fill it with recommend_checks' in result['reason']

def test_the_mcp_tools_exist_and_are_read_only():
    import asyncio
    from hub.mcp_adapter import create_server
    tools = {t.name: t for t in asyncio.run(create_server().list_tools())}
    assert tools['recommend_checks'].annotations.readOnlyHint and tools['estimate_delegation'].annotations.readOnlyHint

def test_a_profile_group_holding_a_template_cannot_be_selected_directly(repo, monkeypatch):
    from hub import profiles
    from hub.models import Check
    profile = profiles.ProjectProfile(repo=str(repo), constraints='c', groups={'plain': [Check(name='ok', argv=['true'])], 'selected': [Check(name='sel', argv=['pytest', testmap.PLACEHOLDER])]})
    monkeypatch.setattr(profiles, 'load_profile', lambda r: (profile, 'a' * 64))
    ok = JobRequest(role='validator', repo=str(repo), task='x', profile_ref='a' * 12, check_groups=['plain'], idempotency_key='p1')
    assert [c.name for c in profiles.expand_profile(ok).checks] == ['ok']
    bad = JobRequest(role='validator', repo=str(repo), task='x', profile_ref='a' * 12, check_groups=['selected'], idempotency_key='p2')
    with pytest.raises(ValueError, match='template'):
        profiles.expand_profile(bad)
