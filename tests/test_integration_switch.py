import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from hub import integration
from hub.models import JobRequest
from hub.service import create_app
from hub.settings import initialize


@pytest.fixture
def switch_env(tmp_path, monkeypatch):
    config = tmp_path / 'config'
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setattr(integration, 'CONFIG', config)
    monkeypatch.setattr('hub.skills.coding.execution.control.CONFIG',config)
    monkeypatch.setattr(integration, 'PROJECT', home / 'empty-project')
    monkeypatch.setattr(integration, 'systemctl', Mock())
    yield home


def write(home, name, data):
    p = home / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data)
    return p


def test_off_removes_only_owned_components_and_on_restores_user_edits(switch_env, monkeypatch):
    home = switch_env
    path = write(home, '.claude.json', json.dumps({'mcpServers': {'local-worker': {'command':'local-worker'}, 'other':{'command':'other'}},
        'hooks':{'Stop':[{'hooks':[{'command':'local-worker hook'}, {'command':'my-hook'}]}]}}))
    instructions = write(home, '.codex/AGENTS.md', 'Personal instructions\n' + integration.START + '\nUse local-worker\n' + integration.END + '\n')
    write(home, '.claude/CLAUDE.md', instructions.read_text())
    toml = write(home, '.codex/config.toml', '[features]\nx = true\n[mcp_servers.local-worker]\ncommand = "local-worker"\n[mcp_servers.other]\ncommand = "other"\n')
    off = integration.switch_off(home)
    assert not off['enabled']
    assert 'local-worker' not in path.read_text()
    assert 'my-hook' in path.read_text() and 'other' in path.read_text()
    assert instructions.read_text() == 'Personal instructions\n'
    assert 'local-worker' not in toml.read_text()
    data=json.loads(path.read_text());data['unrelated']=42;path.write_text(json.dumps(data))
    monkeypatch.setattr('hub.client.connect', lambda **kw: Mock())
    assert integration.switch_on(home)['enabled']
    assert json.loads(path.read_text())['unrelated']==42
    assert 'my-hook' in path.read_text() and 'local-worker' in path.read_text()
    assert integration.switch_off(home)['enabled'] is False


def test_disabled_gates_api_and_store(switch_env, store, repo):
    integration.save({'enabled':False,'phase':'disabled'})
    request=JobRequest(role='investigator',repo=str(repo),task='x',idempotency_key='x')
    with pytest.raises(RuntimeError,match='switched off'):store.submit(request)
    with TestClient(create_app(store,start_workers=False),base_url='http://127.0.0.1:8765') as client:
        assert client.get('/api/health').status_code==200
        assert client.get('/api/jobs',headers={'Authorization':'Bearer '+initialize()}).status_code==503


def test_restoration_conflict_keeps_execution_disabled(switch_env, monkeypatch):
    home=switch_env
    path=write(home,'.claude.json','{"mcpServers":{"local-worker":{"command":"local-worker"}}}')
    integration.switch_off(home)
    path.write_text('{"mcpServers":{"local-worker":{"command":"custom"}}}')
    result=integration.switch_on(home)
    assert not result['enabled'] and result['residual']
    assert 'custom' in path.read_text()


def test_corrupt_control_fails_closed(switch_env):
    write(integration.CONFIG,'integration-control.json','broken')
    assert not integration.enabled()


def test_off_blocks_direct_apply_and_scheduler_does_not_block_during_switch(switch_env,store,repo):
    from hub.skills.coding.editing import workspace
    queued=store.submit(JobRequest(role='investigator',repo=str(repo),task='Read answer',idempotency_key='switch-lock'))
    with integration.locked():
        assert store.next() is None
    assert store.next()['id']==queued['id']
    integration.save({'enabled':False,'phase':'disabled'})
    with pytest.raises(RuntimeError,match='switched off'):
        workspace.apply('a'*32)
