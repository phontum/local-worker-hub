import asyncio
import json
import os
import sys
import httpx
import pytest
from hub import engine, client
from hub.models import JobRequest
from hub.report import final_report
from hub.runner import Runner
from hub.history import import_history

def test_used_client_lifecycle(monkeypatch):
    c=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'ok':True})),base_url='http://localhost')
    c.get('/api/health')
    monkeypatch.setattr(client,'connect',lambda:c)
    assert client.call('GET','/api/health')=={'ok':True}
    assert c.is_closed

@pytest.mark.asyncio
async def test_engine_denies_out_of_scope_edit(tmp_path,repo,monkeypatch):
    directory=tmp_path/'job';directory.mkdir();(directory/'workspace').mkdir()
    request=JobRequest(role='editor',task='Update answer',repo=str(repo),allowed_paths=['app.ts'],idempotency_key='engine',agent_loop=True)
    (directory/'request.json').write_text(request.model_dump_json())
    untouched=repo/'other.txt';untouched.write_text('original')
    n=0;observed=[]
    async def post(self,url,**kw):
        nonlocal n
        observed.append(kw['json']);n+=1
        if n==1:
            message={'role':'assistant','content':'','tool_calls':[{'type':'function','function':{'name':'write_file',
                'arguments':{'path':'other.txt','content':'replacement'}}}]};finish='tool_calls'
        elif n==2:message={'role':'assistant','content':'Unable to edit.'};finish='stop'
        else:
            message={'role':'assistant','content':json.dumps({'status':'BLOCKED','findings':'Out-of-scope write was denied',
                'files':'None changed','checks':'Not run','risks':'Requested file outside authorized scope'})};finish='stop'
        return httpx.Response(200,json={'message':message,'done_reason':finish,'prompt_eval_count':100,'eval_count':12},request=httpx.Request('POST',url))
    async def response(client,url,body,headers,on_segment):
        value=(await post(client,url,json=body)).json()
        for kind in ('thinking','content'):
            if value['message'].get(kind):on_segment(kind,value['message'][kind])
        return value
    monkeypatch.setattr(engine,'chat_response',response)
    await engine.run(directory,'work','Update answer')
    assert untouched.read_text()=='original'
    assert 'Tool error' in observed[1]['messages'][3]['content']
    assert final_report(json.loads((directory/'work.session.json').read_text()))['status']=='BLOCKED'
    assert not observed[-1]['tools']

@pytest.mark.asyncio
@pytest.mark.parametrize('cancel',[True,False])
async def test_process_stops_on_cancel_and_timeout(tmp_path,repo,store,cancel):
    request=JobRequest(role='investigator',task='Wait',repo=str(repo),idempotency_key=str(cancel))
    job=store.submit(request);store.next();directory=tmp_path/'run';directory.mkdir();(directory/'workspace').mkdir()
    runner=Runner(store,'test')
    async def cancel_later():
        await asyncio.sleep(.2);store.cancel(job['id'])
    timer=asyncio.create_task(cancel_later()) if cancel else None
    with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
        await runner.process([sys.executable,'-c','import time;time.sleep(30)'],job,directory,'wait',dict(os.environ),.4)
    if timer:await timer
    assert not runner.active

def test_legacy_import_once_without_acceptance(tmp_path,store):
    d=tmp_path/'legacy-run';d.mkdir();(d/'metadata.json').write_text(json.dumps({'cwd':'/previous/repo','write':False}))
    (d/'task.txt').write_text('Find implementation');(d/'report.txt').write_text('Previous unverified report')
    assert import_history(store,tmp_path)==1
    assert import_history(store,tmp_path)==0
    job=store.list()[0]
    assert job['review'] is None and not job['result']['workspace_verified']
    assert job['result']['usage']=={}


@pytest.mark.asyncio
@pytest.mark.parametrize('recover',[True,False])
async def test_inference_retry_is_identical_bounded_and_never_repeats_tools(tmp_path,monkeypatch,recover):
    import copy
    directory=tmp_path/'job';directory.mkdir()
    request=JobRequest(role='personal',task='Say Hello!',idempotency_key='retry',verify=True)
    (directory/'request.json').write_text(request.model_dump_json())
    from types import SimpleNamespace
    class Tools:
        async def list_tools(self):return []
    monkeypatch.setattr(engine,'create_tools',lambda *_:Tools())
    attempts=[]
    async def response(client,url,body,headers,on_segment):
        attempts.append(copy.deepcopy(body))
        if len(attempts)==1 or not recover:
            r=httpx.Response(502,request=httpx.Request('POST',url))
            raise httpx.HTTPStatusError('Opaque upstream failure',request=r.request,response=r)
        return {'message':{'role':'assistant','content':json.dumps({'status':'COMPLETE','findings':'Hello!','files':'None','checks':'Not run','risks':'None'})},'done_reason':'stop'}
    monkeypatch.setattr(engine,'chat_response',response)
    if recover:
        await engine.run(directory,'work',request.task)
        assert final_report(json.loads((directory/'work.session.json').read_text()))['findings']=='Hello!'
    else:
        with pytest.raises(httpx.HTTPStatusError):await engine.run(directory,'work',request.task)
    assert len(attempts)==2 and attempts[0]==attempts[1]
    assert (directory/'inference-retry.json').exists()
    attempts.clear()
    with pytest.raises(httpx.HTTPStatusError):await engine.run(directory,'review',request.task,phase='answer_review')
    assert len(attempts)==1 # one retry for the whole job, not another in its review
