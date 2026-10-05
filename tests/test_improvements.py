import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi.testclient import TestClient
from hub import client
from hub.skills.coding.validation import profiles
from hub.models import Check, JobRequest
from hub.runner import Runner
from hub.settings import STATE, initialize
from hub.service import create_app
from hub.presentation import result_summary, progress_summary, size
from hub.skills.coding.validation.validation import test_counts as parse_counts, direct_report


def request(repo, checks, **kw):
    return JobRequest(role='validator',repo=str(repo),task='Run approved checks',idempotency_key=kw.pop('key','validation'),checks=checks,**kw)


def check(name, code='print("ok")', **kw):
    return Check(name=name,argv=[sys.executable,'-c',code],**kw)


@pytest.mark.asyncio
async def test_direct_validation_has_no_inference(repo,store):
    job=store.submit(request(repo,[check('ok')]))
    job=store.next();runner=Runner(store,'token')
    runner.execute_model=AsyncMock(side_effect=AssertionError('No inference allowed'))
    await runner.run(job)
    result=store.get(job['id'])['result']
    assert result['worker_status']=='COMPLETE' and result['report_valid']
    assert result['report_origin']=='harness' and result['model'] is None
    assert sum(result['usage'].values())==0 and result['metrics']['analysis_seconds']==0
    runner.execute_model.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('policy,expected',[('fail_fast',['failed','skipped','skipped']),('continue_independent',['failed','passed','skipped'])])
async def test_independent_checks_and_dependencies(repo,store,policy,expected):
    job=store.submit(request(repo,[check('bad','raise SystemExit(1)'),check('independent'),check('dependent',depends_on=['bad'])],failure_policy=policy))
    runner=Runner(store,'token');await runner.run(store.next())
    result=store.get(job['id'])['result']
    assert [c['status'] for c in result['checks']]==expected
    assert result['worker_status']=='PARTIAL'
    assert result['checks'][2]['artifact'] is None


@pytest.mark.asyncio
async def test_topological_order_and_invalid_dependencies(repo,store):
    job=store.submit(request(repo,[check('child',depends_on=['parent']),check('parent')]))
    runner=Runner(store,'token');await runner.run(store.next())
    assert [c['name'] for c in store.get(job['id'])['result']['checks']]==['parent','child']
    for checks in ([check('a'),check('a')],[check('a',depends_on=['missing'])],[check('a',depends_on=['b']),check('b',depends_on=['a'])]):
        with pytest.raises(ValueError):request(repo,checks)


@pytest.mark.asyncio
async def test_cancel_preserves_previous_check_and_stops_group(repo,store):
    job=store.submit(request(repo,[check('first'),check('long','import time; time.sleep(60)')]))
    runner=Runner(store,'token');task=asyncio.create_task(runner.run(store.next()))
    for _ in range(100):
        await asyncio.sleep(.02)
        if (store.get(job['id']).get('progress') or {}).get('active_check')=='long':break
    store.cancel(job['id']);await asyncio.wait_for(task,5)
    value=store.get(job['id'])
    assert value['state']=='cancelled'
    assert [c['status'] for c in value['result']['checks']]==['passed','cancelled']
    assert not runner.active


@pytest.mark.asyncio
async def test_timeout_and_missing_env_continue(repo,store):
    job=store.submit(request(repo,[check('slow','import time;time.sleep(60)',timeout=1),
                                  check('needs',required_env=['NOT_INHERITED']),check('independent')],failure_policy='continue_independent'))
    runner=Runner(store,'token');await runner.run(store.next())
    checks=store.get(job['id'])['result']['checks']
    assert checks[0]['timed_out'] and checks[1]['status']=='blocked' and checks[2]['status']=='passed'


@pytest.mark.asyncio
@pytest.mark.parametrize('url',['postgresql://user:secret@prod.example/app_test','postgresql://att:x@127.0.0.1/production'])
async def test_profile_test_db_guard(repo,store,url):
    job=store.submit(request(repo,[check('database','raise AssertionError("must not execute")',environment={'TEST_DATABASE_URL':url},requires_test_database=True)]))
    runner=Runner(store,'token');await runner.run(store.next())
    assert store.get(job['id'])['result']['checks'][0]['status']=='blocked'


@pytest.mark.asyncio
async def test_analysis_uses_saved_checks_without_rerunning(repo,store):
    source=store.submit(request(repo,[check('once','print("original")')]))
    runner=Runner(store,'token');await runner.run(store.next())
    original=store.get(source['id'])['result']
    analysis=JobRequest(role='validator',repo=str(repo),task='Summarize saved evidence',source_job_id=source['id'],summary_mode='local',idempotency_key='analysis')
    child=store.submit(analysis)
    runner.checks=AsyncMock(side_effect=AssertionError('No reruns'))
    async def model(*args):
        assert 'once' in args[4]
        return {'info':{}},direct_report(original['checks'])
    runner.execute_model=AsyncMock(side_effect=model)
    await runner.run(store.next())
    result=store.get(child['id'])['result']
    assert result['source_job_id']==source['id']
    assert result['metrics']['check_seconds']==0
    assert store.get(source['id'])['result']==original
    assert not (STATE/'jobs'/child['id']/'check-0.log').exists()
    runner.checks.assert_not_called()


def test_summary_budget_and_unknown_counts(repo,store):
    job=store.submit(request(repo,[check('x')]))
    store.finish(job['id'],'completed',{'report':'ü'*100000,'checks':[
        {'name':'x'*120,'reason':'ü'*1000,'status':'blocked','exit_code':None,'output_tail':'source'*50000,'artifact':f'check-{i}.log'} for i in range(12)],
        'before':{'status':'M source\n'*100000},'changed_files':['x'*2000]*100})
    value=store.get(job['id']);compact=result_summary(value)
    assert size(compact)<=8192 and compact['truncated']
    assert 'before' not in compact and all('output_tail' not in c for c in compact['checks'])
    store.checkpoint(job['id'],{'checks':[{'name':'x'*120,'status':'failed','exit_code':1} for _ in range(12)]})
    assert size(progress_summary(store.get(job['id'])))<=2048
    store.checkpoint(job['id'],{'checks':[{'name':'😀'*120,'status':'failed','exit_code':1} for _ in range(12)]})
    store.progress(job['id'],{'phase':'check','active_check':'😀'*120})
    assert size(progress_summary(store.get(job['id'])))<=2048
    assert parse_counts('Tests may have passed?') is None
    assert parse_counts('Ran 287 tests in 185.456s\n\nFAILED (errors=13)')['passed']==274
    assert parse_counts('# tests 4\n# pass 4\n# fail 0\n# skipped 0\n# cancelled 0\n# todo 0')['passed']==4
    assert parse_counts(' Tests  295 passed (295)')['passed']==295


def test_profile_review_hash_and_expansion(repo,store,monkeypatch,tmp_path):
    config=tmp_path/'config';(config/'projects').mkdir(parents=True);monkeypatch.setattr(profiles,'CONFIG',config)
    path=config/'projects/example.json'
    value={'repo':str(repo),'constraints':'No remote commands','source_files':['app.ts'],'parameter_names':['TEST_DATABASE_URL'],
           'groups':{'safe':[check('safe').model_dump()], 'database':[check('database',depends_on=['safe'],required_env=['TEST_DATABASE_URL']).model_dump()]}}
    path.write_text(json.dumps(value))
    shown=profiles.show_profile(repo)
    req=JobRequest(role='validator',repo=str(repo),task='Validate',idempotency_key='profile',profile_hash=shown['profile_hash'],check_groups=['database'],parameters={'TEST_DATABASE_URL':'local'})
    expanded=profiles.expand_profile(req)
    assert [c.name for c in expanded.checks]==['database','safe']
    assert expanded.checks[0].environment['TEST_DATABASE_URL']=='local'
    assert profiles.expand_profile(expanded)==expanded
    (repo/'app.ts').write_text('changed')
    with pytest.raises(ValueError,match='changed'):profiles.expand_profile(req)


def test_paged_events_artifact_auth_and_restart_evidence(repo,store):
    job=store.submit(request(repo,[check('safe')]))
    for i in range(350):store.event(job['id'],'sample',{'i':i})
    store.next();store.checkpoint(job['id'],{'checks':[{'name':'saved','exit_code':0,'status':'passed'}]})
    store.interrupt_running()
    assert store.get(job['id'])['result']['checks'][0]['name']=='saved'
    directory=STATE/'jobs'/job['id'];directory.mkdir(parents=True,exist_ok=True)
    (directory/'check-0.log').write_text('abcdefghij')
    app=create_app(store,start_workers=False)
    with TestClient(app,base_url='http://127.0.0.1:8765') as c:
        url='/api/jobs/'+job['id']
        assert c.get(url+'/artifact-page/check-0.log').status_code==401
        headers={'Authorization':'Bearer '+initialize()}
        first=c.get(url+'/events?paged=true&limit=300',headers=headers).json()
        second=c.get(url+'/events?paged=true&after='+str(first['next_cursor']),headers=headers).json()
        assert first['has_more'] and len(first['events'])+len(second['events'])==350
        page=c.get(url+'/artifact-page/check-0.log?limit=4',headers=headers).json()
        assert page['text']=='abcd' and page['next_offset']==4 and page['has_more']
        assert c.get(url+'/artifact-page/request.json',headers=headers).status_code==403
        assert c.get(url+'/artifact-page/check-0.log?limit=16001',headers=headers).status_code==422
        assert c.get(url+'/result?view=summary',headers=headers).json()['checks'][0]['exit_code']==0


def test_read_client_does_not_initialize_or_start(monkeypatch,tmp_path):
    monkeypatch.setattr(client,'CONFIG',tmp_path)
    monkeypatch.setattr(client,'initialize',lambda:pytest.fail('No initialization on reads'))
    monkeypatch.setattr(client.subprocess,'run',lambda *a,**k:pytest.fail('No service startup on reads'))
    with pytest.raises(RuntimeError,match='not initialized'):client.connect()
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_mcp_annotations_and_legacy_ids():
    from hub.mcp_adapter import create_server, job_path
    tools={t.name:t for t in await create_server().list_tools()}
    for name in ('get_job','get_result','read_artifact','get_project_profile'):
        assert tools[name].annotations.readOnlyHint
    assert not tools['submit_job'].annotations.readOnlyHint
    assert job_path('legacy-'+'a'*24).endswith('a'*24)


def test_transport_retries_only_identical_safe_requests(monkeypatch):
    calls=[]
    class C:
        def close(self):pass
        def request(self,method,path,json=None):
            calls.append((method,path,json))
            if len(calls)==1:raise httpx.ReadTimeout('late')
            return httpx.Response(200,json={'id':'same'})
    monkeypatch.setattr(client,'connect',lambda **kw:C())
    assert client.call('POST','/api/jobs',{'idempotency_key':'same'})=={'id':'same'}
    assert calls[0]==calls[1]
    calls.clear()
    with pytest.raises(RuntimeError):client.call('POST','/api/pair-code')
    assert len(calls)==1


@pytest.mark.asyncio
async def test_next_dev_blocks_build_without_stopping_dev(repo,store,monkeypatch):
    import psutil
    class Process:
        info={'pid':123,'cmdline':['node','next','dev'],'cwd':str(repo)}
    monkeypatch.setattr(psutil,'process_iter',lambda *args:[Process()])
    job=store.submit(request(repo,[check('build','raise AssertionError("never")',guard_next_dev=True),check('independent')],failure_policy='continue_independent'))
    runner=Runner(store,'token');await runner.run(store.next())
    outcomes=store.get(job['id'])['result']['checks']
    assert outcomes[0]['status']=='blocked' and 'next dev' in outcomes[0]['reason']
    assert outcomes[1]['exit_code']==0


@pytest.mark.asyncio
async def test_queued_profile_change_blocks_execution(repo,store,monkeypatch,tmp_path):
    config=tmp_path/'config';(config/'projects').mkdir(parents=True);monkeypatch.setattr(profiles,'CONFIG',config)
    path=config/'projects/example.json'
    path.write_text(json.dumps({'repo':str(repo),'constraints':'initial','groups':{'safe':[check('safe').model_dump()]}}))
    req=JobRequest(role='validator',repo=str(repo),task='Validate',idempotency_key='queued-profile',profile_hash=profiles.show_profile(repo)['profile_hash'],check_groups=['safe'])
    job=store.submit(profiles.expand_profile(req));path.write_text(path.read_text().replace('initial','changed'))
    runner=Runner(store,'token');runner.checks=AsyncMock(side_effect=AssertionError('No commands after profile change'))
    await runner.run(store.next())
    assert store.get(job['id'])['state']=='failed'
    assert 'changed' in store.get(job['id'])['result']['error']
    runner.checks.assert_not_called()


def test_manual_estimates_do_not_become_measured_savings(repo,store):
    from hub.models import Review
    job=store.submit(request(repo,[check('ok')]))
    store.finish(job['id'],'completed',{})
    store.review(job['id'],Review(decision='accepted',baseline_frontier_tokens=100,delegated_frontier_tokens=50,
        baseline_frontier_cost=1,delegated_frontier_cost=.5,measurement_source='manual_estimate'))
    summary=store.summary()
    assert summary['estimated_frontier_tokens_avoided'] is None and summary['estimated_frontier_cost_avoided'] is None
    assert summary['manual_estimate_baselines']==1 and summary['matched_baselines']==0
