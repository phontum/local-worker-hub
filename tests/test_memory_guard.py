from collections import namedtuple
import pytest
import hub.service as service

Memory=namedtuple('Memory','available')

class FakeClient:
    posts=[]
    loaded=['gemma4:12b-it-qat']
    def __init__(self,**kw):pass
    async def __aenter__(self):return self
    async def __aexit__(self,*a):pass
    async def get(self,url):
        class Response:
            def json(inner):return {'models':[{'name':n} for n in FakeClient.loaded]}
        return Response()
    async def post(self,url,json=None):
        FakeClient.posts.append((url,json));FakeClient.loaded=[]

@pytest.fixture(autouse=True)
def fake(monkeypatch):
    FakeClient.posts=[];FakeClient.loaded=['gemma4:12b-it-qat']
    monkeypatch.setattr(service,'LOW_MEMORY_MB',2500)
    monkeypatch.setattr(service.httpx,'AsyncClient',FakeClient)

@pytest.mark.asyncio
async def test_enough_memory_does_nothing(monkeypatch):
    monkeypatch.setattr(service.psutil,'virtual_memory',lambda:Memory(4000*2**20))
    assert await service.relieve_memory() is None and FakeClient.posts==[]

@pytest.mark.asyncio
async def test_low_memory_unloads_resident_models(monkeypatch):
    monkeypatch.setattr(service.psutil,'virtual_memory',lambda:Memory(1200*2**20))
    result=await service.relieve_memory()
    assert result=={'available_mb':1200,'threshold_mb':2500,'unloaded':['gemma4:12b-it-qat']}
    assert FakeClient.posts[0][1]=={'model':'gemma4:12b-it-qat','keep_alive':0}

@pytest.mark.asyncio
async def test_unreachable_ollama_is_reported_not_raised(monkeypatch):
    import httpx
    monkeypatch.setattr(service.psutil,'virtual_memory',lambda:Memory(100*2**20))
    async def broken(self,url):raise httpx.ConnectError('down')
    monkeypatch.setattr(FakeClient,'get',broken)
    assert (await service.relieve_memory())['error']=='Ollama unavailable'

def test_dropped_ollama_connection_is_a_retryable_gateway_error(store,monkeypatch):
    import httpx
    from fastapi.testclient import TestClient
    from hub.models import JobRequest
    from hub.settings import initialize
    job=store.submit(JobRequest(role='personal',task='Public greeting',idempotency_key='dropped'));store.next()
    class Dropping:
        def __init__(self,**kw):pass
        def build_request(self,*a,**kw):return object()
        async def send(self,*a,**kw):raise httpx.RemoteProtocolError('Server disconnected without sending a response.')
        async def aclose(self):pass
    monkeypatch.setattr(service.psutil,'virtual_memory',lambda:Memory(6000*2**20))
    monkeypatch.setattr(service.httpx,'AsyncClient',Dropping)
    with TestClient(service.create_app(store,start_workers=False),base_url='http://127.0.0.1:8765') as c:
        response=c.post('/inference/'+job['id']+'/chat',headers={'Authorization':'Bearer '+initialize()},
            json={'model':'qwen3.5:9b','stream':True,'options':{'num_ctx':16384,'num_predict':100},'messages':[]})
    assert response.status_code==502
    assert [e['data']['message'] for e in store.all_events(job['id']) if e['kind']=='inference-error']==['Ollama connection failed: RemoteProtocolError']
