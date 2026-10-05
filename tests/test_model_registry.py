import json
import pytest
from fastapi.testclient import TestClient
from hub import engine, model_registry
from hub.models import JobRequest
from hub.service import create_app
from hub.settings import initialize

def test_defaults_and_alias_resolution(tmp_path,monkeypatch):
    monkeypatch.setattr(model_registry,'CONFIG',tmp_path)
    assert model_registry.model_name()=='gemma4:12b-it-qat'
    assert model_registry.resolve('qwen')=={'name':'qwen3.5:9b','num_ctx':16384}
    assert model_registry.allowed_names()=={'qwen3.5:9b','gemma4:12b-it-qat','qwen2.5-coder:14b','qwen2.5-coder:7b'}
    with pytest.raises(ValueError):model_registry.resolve('missing')

def test_override_file_is_validated(tmp_path,monkeypatch):
    monkeypatch.setattr(model_registry,'CONFIG',tmp_path)
    (tmp_path/'models.json').write_text(json.dumps({
        'gemma':{'name':'gemma4:e4b-it-qat'},
        'bad-name':{'name':'x; rm -rf /','num_ctx':16384},
        'bad_ctx':{'name':'ok:1b','num_ctx':999},
        'Upper':{'name':'ok:1b'},'extra':'not-a-dict'}))
    found=model_registry.models()
    assert found['gemma']['name']=='gemma4:e4b-it-qat' and found['gemma']['num_ctx']==16384
    assert set(found)=={'qwen','gemma','coder','coder7'}
    (tmp_path/'models.json').write_text('not json')
    assert model_registry.models()['gemma']['name']=='gemma4:12b-it-qat'

def test_native_endpoint_allows_registry_models_only_and_openai_endpoint_stays_qwen(store):
    job=store.submit(JobRequest(role='personal',task='Public greeting',idempotency_key='registry'));store.next()
    headers={'Authorization':'Bearer '+initialize()}
    with TestClient(create_app(store,start_workers=False),base_url='http://127.0.0.1:8765') as c:
        base={'stream':True,'options':{'num_ctx':999,'num_predict':100},'messages':[]}
        assert c.post('/inference/'+job['id']+'/chat',headers=headers,json={**base,'model':'llama3:8b'}).status_code==403
        # A registered model passes the model check and is then stopped by the context check.
        assert c.post('/inference/'+job['id']+'/chat',headers=headers,json={**base,'model':'gemma4:12b-it-qat'}).status_code==400
        assert c.post('/inference/'+job['id']+'/v1/chat/completions',headers=headers,json={'model':'gemma4:12b-it-qat','messages':[]}).status_code==403

@pytest.mark.asyncio
@pytest.mark.parametrize('profile,expected',[({},'gemma4:12b-it-qat'),({'work':{'model':'qwen'}},'qwen3.5:9b')])
async def test_engine_requests_the_profile_model(tmp_path,monkeypatch,profile,expected):
    directory=tmp_path/'job';directory.mkdir()
    request=JobRequest(role='personal',task='Hello',idempotency_key='engine-model',agent_loop=True)
    (directory/'request.json').write_text(request.model_dump_json());(directory/'role-config.json').write_text(json.dumps(profile))
    seen=[]
    async def response(client,url,body,headers,on_segment):
        seen.append(body['model'])
        return {'message':{'role':'assistant','content':'LOCAL_WORKER_REPORT\nStatus: COMPLETE\nFindings:\nHi\nFiles:\nNone\nChecks:\nNot run\nRisks:\nNone\nEND_LOCAL_WORKER_REPORT'},'done_reason':'stop'}
    monkeypatch.setattr(engine,'chat_response',response)
    await engine.run(directory,'work','Hello')
    assert seen and set(seen)=={expected}

def test_probe_never_raises(monkeypatch):
    import httpx
    class Broken:
        def __init__(self,**kw):pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def post(self,*a,**kw):raise httpx.ConnectError('down')
        def get(self,*a,**kw):raise httpx.ConnectError('down')
    monkeypatch.setattr(model_registry.httpx,'Client',Broken)
    assert model_registry.probe('qwen3.5:9b')['installed'] is None
    assert model_registry.residency() is None
