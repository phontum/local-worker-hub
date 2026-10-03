import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from hub.models import JobRequest, Check
from hub.scoped import ScopedFiles, ScopeError, Research, public_url
from hub.report import parse_report, final_report
from hub.runner import runtime_config, worker_env

REPORT='LOCAL_WORKER_REPORT\nStatus: COMPLETE\nFindings:\nFound app.ts:1.\nFiles:\napp.ts\nChecks:\nNot run.\nRisks:\nNone identified.\nEND_LOCAL_WORKER_REPORT'

def request(repo,role='investigator',**kwargs):
    return JobRequest(repo=str(repo),role=role,task='Inspect app.ts',idempotency_key='test',**kwargs)

def test_real_read_with_hash_and_lines(repo):
    data=json.loads(ScopedFiles(request(repo)).read_file('app.ts'))
    assert data['path']=='app.ts' and '1: export' in data['content']
    assert data['sha256']==hashlib.sha256((repo/'app.ts').read_bytes()).hexdigest()

@pytest.mark.parametrize('path',['../outside','/etc/passwd','.env','sops.env','backup/dump.sql','.git/config'])
def test_path_boundaries(repo,path):
    with pytest.raises((ScopeError,FileNotFoundError)):ScopedFiles(request(repo)).read_file(path)

def test_symlink_and_hardlink_escape(repo,tmp_path):
    outside=tmp_path/'outside';outside.write_text('secret')
    (repo/'link').symlink_to(outside)
    import os
    os.link(outside,repo/'hardlink')
    scope=ScopedFiles(request(repo))
    for p in ['link','hardlink']:
        with pytest.raises(ScopeError):scope.read_file(p)

def test_secret_search_excluded(repo):
    (repo/'.env').write_text('SENSITIVE_SECRET=private')
    (repo/'sops.env').write_text('SENSITIVE_SECRET=private')
    (repo/'.env.example').write_text('EXAMPLE=placeholder')
    scope=ScopedFiles(request(repo))
    assert json.loads(scope.search_text('SENSITIVE_SECRET'))['matches']==[]
    assert '.env' not in json.loads(scope.glob_files('*'))['paths']
    assert '.env.example' in json.loads(scope.glob_files('*'))['paths']

def test_editor_exact_scope_and_concurrent_change(repo):
    scope=ScopedFiles(request(repo,'editor',allowed_paths=['app.ts']))
    read=json.loads(scope.read_file('app.ts'))
    (repo/'app.ts').write_text('user changed it\n')
    with pytest.raises(ScopeError):scope.edit_file('app.ts','41','42',read['sha256'])
    assert (repo/'app.ts').read_text()=='user changed it\n'
    with pytest.raises(ScopeError):scope.write_file('other.ts','no',None)
    assert not (repo/'other.ts').exists()

def test_allowed_new_file_and_investigator_cannot_edit(repo):
    scope=ScopedFiles(request(repo,'editor',allowed_paths=['new.ts']))
    scope.write_file('new.ts','new',None)
    assert (repo/'new.ts').read_text()=='new'
    with pytest.raises(ScopeError):ScopedFiles(request(repo)).write_file('app.ts','bad',None)

def test_researcher_no_project_context(repo):
    for extra in [{'repo':str(repo)},{'context':'work code'},{'checks':[Check(name='test',argv=['true'])]}]:
        with pytest.raises(ValueError):JobRequest(role='researcher',task='React docs',idempotency_key='r',**extra)
    scope=ScopedFiles(JobRequest(role='researcher',task='React docs',idempotency_key='r'))
    with pytest.raises(ScopeError):scope.read_file('app.ts')

def test_scope_and_checks_required(repo):
    with pytest.raises(ValueError):request(repo,'editor')
    with pytest.raises(ValueError):request(repo,'validator')
    with pytest.raises(ValueError):request(repo,'validator',checks=[Check(name='bad',argv=['echo','x'],cwd='../../')])

def test_report_tolerates_preamble_but_not_ambiguous_or_truncated():
    assert parse_report('Here are the findings.\n'+REPORT)['status']=='COMPLETE'
    assert parse_report(REPORT+'\n'+REPORT) is None
    assert parse_report(REPORT.replace('Checks:\nNot run.\n','')) is None
    session={'info':{'outcome':'succeeded'},'messages':[{'type':'user'},{'type':'assistant','finish':'length','content':[{'type':'text','text':REPORT}]}]}
    assert final_report(session) is None
    session['messages'][-1]['finish']='stop';assert final_report(session)
    session['messages'].append({'type':'user'});assert final_report(session) is None

def test_runtime_denies_native_and_cloud_and_has_isolated_environment(repo,tmp_path):
    req=request(repo)
    cfg=runtime_config(req,tmp_path/'job','token')
    agent=cfg['agents']['local-worker']
    assert agent['permissions'][0]['effect']=='deny'
    assert not any(r['action'] in ('shell','read','edit','websearch','task') for r in agent['permissions'][1:])
    assert cfg['experimental']['policies'][-1]['resource']=='ollama'
    env=worker_env(tmp_path/'job',cfg)
    assert env['PWD']==str(tmp_path/'job/workspace')
    assert env['XDG_CONFIG_HOME']==str(tmp_path/'job/config')
    assert 'AWS_SECRET_ACCESS_KEY' not in env
    recovered=runtime_config(req,tmp_path/'job','token',True)
    assert 'mcp' not in recovered
    assert len(recovered['agents']['local-worker-report']['permissions'])==1

@pytest.mark.parametrize('url',['http://127.0.0.1','http://[::1]','http://10.0.0.1','file:///etc/passwd','https://user:pass@example.com','http://example.com:8765'])
async def test_fetch_private_destination_denied(url):
    with pytest.raises(ScopeError):await public_url(url)

async def test_web_budgets_errors_and_audit(monkeypatch):
    events=[];research=Research(lambda k,d:events.append((k,d)))
    research.call=AsyncMock(return_value='Official docs https://react.dev')
    for _ in range(3):assert 'react.dev' in await research.search_web('React 19 useEffect official docs')
    with pytest.raises(ScopeError):await research.search_web('more')
    with pytest.raises(ScopeError):await research.search_web('password=secret')
    assert len(events)==3
    async def public(url):return url
    monkeypatch.setattr('hub.scoped.public_url',public)
    for i in range(5):await research.fetch_web('https://react.dev/'+str(i),mode='cached')
    # Paging an already retrieved page does not spend another provider request.
    await research.fetch_web('https://react.dev/0',start=1,mode='cached')
    with pytest.raises(ScopeError):await research.fetch_web('https://react.dev/5',mode='cached')
    research.call.side_effect=RuntimeError('429 rate limit')
    other=Research(lambda *a:None);other.call=research.call
    with pytest.raises(RuntimeError):await other.search_web('React')
