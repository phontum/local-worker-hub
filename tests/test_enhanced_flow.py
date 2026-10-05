import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from hub import cli, engine
from hub.models import JobRequest, Check
from hub.runner import Runner
from hub.scoped import ScopedFiles, ScopeError, create_tools
from hub.trace import append_trace, read_trace


def test_presets_personal_and_explicit_scope(repo):
    personal=JobRequest(role='personal',task='Hello',idempotency_key='personal',execution_preset='work')
    assert personal.context_limit()==16384 and personal.model_budget()==300
    assert JobRequest(role='personal',task='Hello',idempotency_key='extended',execution_preset='extended').context_limit()==32768
    assert JobRequest(role='personal',task='Hello',idempotency_key='small',execution_preset='small').model_budget()==120
    for args in ({'repo':str(repo)}, {'context':'private'}, {'read_paths':['.']}, {'evidence_job_ids':['a'*32]}):
        with pytest.raises(ValueError):JobRequest(role='personal',task='Hello',idempotency_key='no',**args)
    (repo/'visible').mkdir();(repo/'visible'/'a.txt').write_text('allowed');(repo/'hidden.txt').write_text('private')
    scope=ScopedFiles(JobRequest(repo=str(repo),task='Read',idempotency_key='scope',read_paths=['visible','app.ts']))
    assert 'hidden.txt' not in scope.glob_files('*')
    assert 'allowed' in scope.read_file('visible/a.txt')
    with pytest.raises(ScopeError):scope.read_file('hidden.txt')
    with pytest.raises(ScopeError):ScopedFiles(JobRequest(role='editor',repo=str(repo),task='Edit',idempotency_key='mismatch',read_paths=['visible'],allowed_paths=['app.ts']))


def test_range_edit_freshness_partial_rewrite_and_newlines(repo):
    target=repo/'large.ts';target.write_bytes(b'first\r\n'+b'same\r\n'*140+b'last\r\n')
    scope=ScopedFiles(JobRequest(role='editor',repo=str(repo),task='Edit',idempotency_key='lines',allowed_paths=['large.ts']))
    scope.read_file('large.ts',start=60,lines=3)
    with pytest.raises(ScopeError,match='complete fresh read'):scope.write_file('large.ts','fragment')
    with pytest.raises(ScopeError,match='matching line starts'):scope.edit_file('large.ts','same','changed')
    scope.replace_lines('large.ts',60,60,'changed')
    assert target.read_bytes().count(b'changed\r\n')==1 and target.read_bytes().endswith(b'last\r\n')
    with pytest.raises(ScopeError):scope.replace_lines('large.ts',61,61,'bad')
    scope.read_file('large.ts',start=60,lines=3);target.write_bytes(target.read_bytes()+b'user\r\n')
    before=target.read_bytes()
    with pytest.raises(ScopeError,match='changed since'):scope.replace_lines('large.ts',60,60,'bad')
    assert target.read_bytes()==before


@pytest.mark.asyncio
async def test_personal_tools_no_repo_and_review_no_edits(tmp_path,repo):
    for role,phase in [('personal','work'),('editor','review')]:
        directory=tmp_path/role;directory.mkdir()
        request=JobRequest(role=role,repo=str(repo) if role=='editor' else None,allowed_paths=['app.ts'] if role=='editor' else [],task='Test',idempotency_key=role)
        (directory/'request.json').write_text(request.model_dump_json())
        names={t.name for t in await create_tools(directory,phase).list_tools()}
        if role=='personal':
            assert names=={'search_web','fetch_web'}  # plain mode has no mandatory planning tool
            strict=JobRequest(role='personal',task='Test',idempotency_key='strict',verify=True);(directory/'request.json').write_text(strict.model_dump_json())
            assert {t.name for t in await create_tools(directory,phase).list_tools()}=={'plan_web_task','search_web','fetch_web'}
        else:assert 'replace_lines' not in names and 'edit_file' not in names and 'read_check_output' in names


@pytest.mark.asyncio
async def test_stream_assembly_thinking_calls_and_incomplete():
    chunks=[{'message':{'thinking':'Consider '}},{'message':{'thinking':'the source.','content':'Answer'}},
            {'message':{'tool_calls':[{'function':{'name':'read_file','arguments':{'path':'a.ts'}}}]}},
            {'done':True,'done_reason':'stop','prompt_eval_count':21,'eval_count':12}]
    async def handle(request):return httpx.Response(200,text='\n'.join(json.dumps(c) for c in chunks))
    segments=[]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result=await engine.chat_response(client,'http://local/chat',{}, {},lambda kind,text:segments.append((kind,text)))
        assert result['message']['thinking']=='Consider the source.'
        assert result['message']['tool_calls'][0]['function']['name']=='read_file'
        assert result['eval_count']==12 and len(segments)==3
        chunks.pop()
        with pytest.raises(RuntimeError,match='before completion'):await engine.chat_response(client,'http://local/chat',{}, {},lambda *_:None)


def test_trace_paging_unicode_partial_and_auth(tmp_path,repo,store):
    from hub.service import create_app
    from hub.settings import STATE, initialize
    request=JobRequest(repo=str(repo),task='Read',idempotency_key='trace')
    job=store.submit(request);directory=STATE/'jobs'/job['id'];directory.mkdir(parents=True)
    append_trace(directory,'review',0,'thinking','héllo 🌤'*400)
    first=read_trace(directory,limit=1);second=read_trace(directory,first['next_offset'],limit=200)
    assert first['has_more'] and not second['has_more']
    assert ''.join(r['text'] for r in first['segments']+second['segments'])=='héllo 🌤'*400
    assert directory.joinpath('trace.jsonl').stat().st_mode & 0o777==0o600
    with TestClient(create_app(store,start_workers=False)) as client:
        assert client.get('/api/jobs/'+job['id']+'/trace',headers={'Host':'127.0.0.1:8765'}).status_code==401
        response=client.get('/api/jobs/'+job['id']+'/trace',headers={'Host':'127.0.0.1:8765','Authorization':'Bearer '+initialize()})
        assert response.status_code==200 and response.json()['available']
    with (directory/'trace.jsonl').open('ab') as out:out.write(b'{"partial":')
    last=read_trace(directory,second['next_offset']);assert last['segments']==[] and last['next_offset']==second['next_offset']


def test_evidence_provenance_and_scoped_log_reader(repo,store,tmp_path):
    from hub.evidence import prepare_evidence, EvidenceReader
    from hub.settings import STATE
    source=store.submit(JobRequest(role='validator',repo=str(repo),task='Check',checks=[Check(name='test',argv=['python','-V'])],idempotency_key='source'))
    store.finish(source['id'],'completed',{'report':'observed failure','checks':[{'name':'test','status':'failed','exit_code':1,'artifact':'check-0.log'}]})
    source_dir=STATE/'jobs'/source['id'];source_dir.mkdir(parents=True);(source_dir/'check-0.log').write_text('specific failure')
    request=JobRequest(repo=str(repo),task='Explain',idempotency_key='follow',evidence_job_ids=[source['id']])
    directory=tmp_path/'follow';directory.mkdir();(directory/'request.json').write_text(request.model_dump_json())
    assert 'observed failure' in prepare_evidence(store,request,directory)
    reader=EvidenceReader(directory,lambda *_:None)
    assert 'specific failure' in reader.read_check_output(source['id'],'check-0.log')
    with pytest.raises(ScopeError):reader.read_check_output('b'*32,'check-0.log')
    with pytest.raises(ScopeError):reader.read_check_output(source['id'],'request.json')
    store.finish(source['id'],'completed',{'report':'other','checks':[]})
    other=tmp_path/'other';other.mkdir()
    different=JobRequest(repo=str(other),task='Explain',idempotency_key='wrong',evidence_job_ids=[source['id']])
    with pytest.raises(ValueError,match='same canonical'):prepare_evidence(store,different,directory)


@pytest.mark.asyncio
async def test_separate_model_budget_cumulative_and_scoped_diff(repo,store):
    request=JobRequest(role='editor',repo=str(repo),task='Edit',idempotency_key='budget',execution_preset='small',
        allowed_paths=['app.ts'],read_paths=['app.ts'],checks=[Check(name='slow',argv=[sys.executable,'-c','import time;time.sleep(.1)'])],timeout=1)
    runner=Runner(store,'test');job=store.submit(request);job=store.next();runner.model_remaining[job['id']]=1
    from hub.settings import STATE
    directory=STATE/'jobs'/job['id'];directory.mkdir(parents=True);(directory/'workspace').mkdir();(directory/'request.json').write_text(request.model_dump_json())
    results=await runner.checks(job,request,directory)
    assert results[0]['exit_code']==0 and runner.model_remaining[job['id']]==1
    async def slow(*args,**kwargs):await asyncio.sleep(.06);raise RuntimeError('test response')
    runner.process=slow
    with pytest.raises(RuntimeError):await runner.execute_model(job,request,directory,'work','task')
    assert runner.model_remaining[job['id']]<.95
    runner.model_remaining[job['id']]=0
    with pytest.raises(TimeoutError):await runner.execute_model(job,request,directory,'review','task')
    (repo/'app.ts').write_text('changed\n');(repo/'new.ts').write_text('new file\n')
    diff_request=JobRequest(role='editor',repo=str(repo),task='Edit',idempotency_key='diff',allowed_paths=['app.ts','new.ts'])
    diff=runner.scoped_diff(diff_request,{'app.ts':'old\n','new.ts':''})
    assert '+changed' in diff and '+new file' in diff


@pytest.mark.parametrize('flags,role,context',[([], 'personal',16384),(['--extended'],'personal',32768),(['--read-only'],'investigator',16384)])
def test_cli_personal_and_engineering_routing(monkeypatch,repo,flags,role,context,capsys):
    from hub import client
    seen=[]
    def call(method,path,data=None):
        if method=='POST':seen.append(data);return {'id':'a'*32,'state':'queued'}
        raise AssertionError(path)
    monkeypatch.setattr(client,'call',call);monkeypatch.chdir(repo)
    monkeypatch.setattr(sys,'argv',['local-worker',*flags,'--async','Hello'])
    cli.main()
    request=JobRequest.model_validate(seen[0]);assert request.role==role and request.context_limit()==context
    assert (request.repo is None)==(role=='personal')


@pytest.mark.parametrize('outcome',['complete','incomplete','cancelled'])
def test_native_stream_usage_once_and_upstream_closed(monkeypatch,store,outcome):
    from hub.service import create_app
    from hub.settings import initialize
    job=store.submit(JobRequest(role='personal',task='Hello',idempotency_key=outcome));store.next()
    closed=[]
    class Chunks(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"message":{"thinking":"local trace"},"done":false}\n'
            if outcome=='cancelled':store.cancel(job['id'])
            if outcome!='incomplete':
                yield b'{"message":{"content":"answer"},"done":true,"prompt_eval_count":10,"eval_count":5}\n'
                yield b'{"done":true,"prompt_eval_count":10,"eval_count":5}\n'
        async def aclose(self):closed.append(True)
    async def send(client,request,**kwargs):return httpx.Response(200,stream=Chunks(),request=request)
    monkeypatch.setattr(httpx.AsyncClient,'send',send)
    with TestClient(create_app(store,start_workers=False)) as client:
        response=client.post('/inference/'+job['id']+'/chat',headers={'Host':'127.0.0.1:8765','Authorization':'Bearer '+initialize()},
            json={'model':'qwen3.5:9b','stream':True,'options':{'num_ctx':16384,'num_predict':100},'messages':[]})
        assert response.status_code==200 and 'local trace' in response.text
    usage=[e for e in store.all_events(job['id']) if e['kind']=='inference']
    assert len(usage)==(1 if outcome=='complete' else 0) and closed


def test_editor_profile_and_handoff_accounting(repo,store,tmp_path,monkeypatch):
    from hub import profiles
    from hub.models import Review
    config=tmp_path/'profiles';(config/'projects').mkdir(parents=True)
    (config/'projects'/'fixture.json').write_text(json.dumps({'repo':str(repo),'constraints':'Preserve unrelated changes','source_files':['app.ts'],
        'groups':{'unit':[{'name':'unit','argv':['node','--version']}]}}))
    monkeypatch.setattr(profiles,'CONFIG',config)
    profile=profiles.show_profile(str(repo))
    request=JobRequest(role='editor',repo=str(repo),task='Edit',allowed_paths=['app.ts'],workflow='implement',idempotency_key='profile',
        profile_ref=profile['profile_ref'],check_groups=['unit'])
    expanded=profiles.expand_profile(request)
    assert expanded.checks[0].name=='unit' and expanded.context=='Preserve unrelated changes'
    (repo/'app.ts').write_text('changed')
    with pytest.raises(ValueError,match='changed'):profiles.expand_profile(request)
    for key in ['a','b']:
        j=store.submit(JobRequest(repo=str(repo),task='Read',idempotency_key=key,handoff_id='one-task'))
        store.finish(j['id'],'completed',{'usage':{'output':10},'metrics':{'analysis_seconds':2,'check_seconds':3}})
        if key=='a':first=j['id']
        else:second=j['id']
    store.review(first,Review(decision='accepted',baseline_frontier_tokens=100,delegated_frontier_tokens=120,review_effort_seconds=5))
    with pytest.raises(ValueError,match='once per handoff'):store.review(second,Review(decision='accepted',baseline_frontier_tokens=100,delegated_frontier_tokens=20))
    store.review(second,Review(decision='takeover',review_effort_seconds=7))
    summary=store.summary();group=next(g for g in summary['handoff_stats'] if g['id']=='one-task')
    assert summary['estimated_frontier_tokens_avoided']==-20
    assert group['jobs']==2 and group['local_output_tokens']==20 and group['review_effort_seconds']==12 and group['takeovers']==1


@pytest.mark.asyncio
async def test_personal_search_only_answer_is_unverified_and_tool_trace_saved(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from hub.report import final_report
    directory=tmp_path/'personal';directory.mkdir();(directory/'workspace').mkdir()
    request=JobRequest(role='personal',task='Weather in a public city',idempotency_key='sources',execution_preset='work',verify=True)
    (directory/'request.json').write_text(request.model_dump_json())
    class Tools:
        async def list_tools(self):return [SimpleNamespace(name='search_web',description='public search',inputSchema={'type':'object','properties':{}})]
        async def call_tool(self,name,args):return [SimpleNamespace(type='text',text='URL: https://example.org/weather\nForecast: 15 C')]
    monkeypatch.setattr(engine,'create_tools',lambda *_:Tools())
    calls=[]
    async def response(client,url,body,headers,on_segment):
        calls.append(body)
        if len(calls)==1:message={'role':'assistant','content':'','tool_calls':[{'function':{'name':'search_web','arguments':{'query':'public city weather'}}}]}
        elif body['tools']:message={'role':'assistant','content':'LOCAL_WORKER_REPORT\nStatus: COMPLETE\nFindings:\nIt is 15 C.\nFiles:\nNone.\nChecks:\nNot run.\nRisks:\nNone.\nEND_LOCAL_WORKER_REPORT'}
        else:message={'role':'assistant','content':json.dumps({'status':'COMPLETE','findings':'It is 15 C.','files':'None','checks':'Not run','risks':'None','web_claims':[]})}
        return {'message':message,'done_reason':'stop','eval_count':10,'prompt_eval_count':20}
    monkeypatch.setattr(engine,'chat_response',response)
    await engine.run(directory,'work','Weather in public city')
    saved=json.loads((directory/'work.session.json').read_text());report=final_report(saved)
    assert report['status']=='PARTIAL' and '15 C' not in report['findings']
    assert 'Sources retrieved' not in report['findings']
    assert 'frontier architect' not in calls[0]['messages'][0]['content']
    page=read_trace(directory)
    assert {r['kind'] for r in page['segments']}=={'tool_call','tool_result'}


@pytest.mark.asyncio
async def test_completion_uses_failed_check_not_model_assurance(repo,store):
    from hub.report import parse_report
    request=JobRequest(role='validator',repo=str(repo),task='Interpret',idempotency_key='assurance',summary_mode='local',
        checks=[Check(name='known-failure',argv=[sys.executable,'-c','raise SystemExit(1)'])])
    job=store.submit(request);runner=Runner(store,'test')
    async def fake(*args,**kwargs):
        report=parse_report('LOCAL_WORKER_REPORT\nStatus: COMPLETE\nFindings:\nEverything is fine.\nFiles:\nNone.\nChecks:\nPassed.\nRisks:\nNone identified.\nEND_LOCAL_WORKER_REPORT')
        return {'info':{'tokens':{}}},report
    runner.execute_model=fake
    await runner.run(store.next());result=store.get(job['id'])['result']
    assert result['worker_status']=='PARTIAL' and 'known-failure' in result['completion']['remaining_issue']
    assert result['completion']['next_action']=='frontier_decision'


@pytest.mark.asyncio
async def test_cancel_flushes_received_thinking(tmp_path,monkeypatch):
    directory=tmp_path/'cancel';directory.mkdir();(directory/'workspace').mkdir()
    request=JobRequest(role='personal',task='Hello',idempotency_key='flush',agent_loop=True)
    (directory/'request.json').write_text(request.model_dump_json())
    received=asyncio.Event()
    async def response(client,url,body,headers,on_segment):
        on_segment('thinking','partial thought');received.set();await asyncio.Event().wait()
    monkeypatch.setattr(engine,'chat_response',response)
    task=asyncio.create_task(engine.run(directory,'work','Hello'));await received.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert ''.join(r['text'] for r in read_trace(directory)['segments'])=='partial thought'
