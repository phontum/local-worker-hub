import pytest
from fastapi.testclient import TestClient
from hub.models import JobRequest, Review
from hub.settings import initialize
from hub.service import create_app

def task(repo,key='one',**kw):return JobRequest(repo=str(repo),task='Inspect app.ts',idempotency_key=key,**kw)

def test_idempotency_queue_cancel_review_and_restart(store,repo):
    job=store.submit(task(repo));assert store.submit(task(repo))['id']==job['id']
    with pytest.raises(ValueError):store.submit(JobRequest(repo=str(repo),task='Different',idempotency_key='one'))
    assert store.next()['id']==job['id']
    store.interrupt_running();assert store.get(job['id'])['state']=='interrupted'
    second=store.submit(task(repo,'two'));store.cancel(second['id'])
    assert store.next() is None
    store.finish(second['id'],'completed',{'usage':{'output':2}})
    assert store.get(second['id'])['state']=='cancelled'
    store.review(job['id'],Review(decision='takeover',baseline_frontier_tokens=100,delegated_frontier_tokens=120))
    assert store.summary()['estimated_frontier_tokens_avoided']==-20
    assert store.summary()['api_equivalent_usd'] is None

def test_auth_pairing_csrf_and_submission(store,repo):
    app=create_app(store,start_workers=False)
    with TestClient(app,base_url='http://127.0.0.1:8765') as c:
        assert c.get('/api/jobs').status_code==401
        headers={'Authorization':'Bearer '+initialize()}
        code=c.post('/api/pair-code',headers=headers).json()['code']
        assert c.post('/api/pair',json={'code':code},headers={'Origin':'https://evil.example'}).status_code==403
        assert c.post('/api/pair',json={'code':code},headers={'Origin':'http://127.0.0.1:8765'}).status_code==200
        assert c.get('/api/jobs').status_code==200
        job=c.post('/api/jobs',headers=headers,json=task(repo).model_dump()).json()
        assert c.post('/api/jobs/'+job['id']+'/cancel').status_code==403
        assert c.post('/api/jobs/'+job['id']+'/cancel',headers={'Origin':'http://127.0.0.1:8765'}).status_code==200
        assert c.get('/api/jobs',headers={'Host':'evil.example'}).status_code==400
        assert c.get('/api/jobs/'+job['id']+'/artifacts/work.session.json').status_code==403

def test_missing_metrics_and_no_double_counting(store,repo):
    job=store.submit(task(repo));store.next()
    store.event(job['id'],'inference',{'input':10,'output':3})
    store.finish(job['id'],'completed',{'usage':{'input':10,'output':3,'cache_read':7}})
    summary=store.summary()
    assert summary['usage']['input']==10
    assert summary['usage']['output']==3
    assert summary['estimated_frontier_tokens_avoided'] is None
    assert summary['subscription_dollars_saved'] is None
    assert store.samples()==[]


def test_streamed_ollama_error_is_bounded_and_visible_only_in_private_events(store,monkeypatch):
    import httpx
    import hub.service as service
    job=store.submit(JobRequest(role='personal',task='Public greeting',idempotency_key='error'));store.next()
    class Upstream:
        status_code=500
        async def aiter_bytes(self):yield b'{"error":"model output parsing failed"}'
        async def aclose(self):pass
    class Client:
        def __init__(self,**kw):pass
        def build_request(self,*a,**kw):return object()
        async def send(self,*a,**kw):return Upstream()
        async def aclose(self):pass
    monkeypatch.setattr(service.httpx,'AsyncClient',Client)
    with TestClient(create_app(store,start_workers=False),base_url='http://127.0.0.1:8765') as c:
        response=c.post('/inference/'+job['id']+'/chat',headers={'Authorization':'Bearer '+initialize()},
            json={'model':'qwen3.5:9b','stream':True,'options':{'num_ctx':16384,'num_predict':1024}})
        assert response.status_code==502 and 'inference-error event' in response.json()['detail']
        assert c.get('/api/jobs/'+job['id']+'/events').status_code==401
    errors=[e for e in store.all_events(job['id']) if e['kind']=='inference-error']
    assert errors[0]['data']=={'upstream_status':500,'message':'model output parsing failed'}
