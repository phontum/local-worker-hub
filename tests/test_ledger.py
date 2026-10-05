from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
import pytest
from hub.ledger import WebLedger, NAME
from hub.models import JobRequest
from hub.public_page import evidence_metadata
from hub.scoped import Research, ScopeError, create_tools

def phase(ledger):
    research=Research(lambda *a:None,ledger=ledger)
    research.call=AsyncMock(return_value='Official docs page text')
    return research

@pytest.fixture
def public(monkeypatch):
    monkeypatch.setattr('hub.scoped.public_url',AsyncMock(side_effect=lambda url:url))

@pytest.mark.asyncio
async def test_search_budget_is_shared_across_phases(tmp_path):
    ledger=WebLedger.create(tmp_path,searches=2,fetches=2)
    first,second=phase(ledger),phase(ledger)
    await first.search_web('first public query');await second.search_web('second public query')
    for research in (first,second):
        with pytest.raises(ScopeError,match='Job-wide search budget'):await research.search_web('third public query')
    assert ledger.read()['used']['search']==2

@pytest.mark.asyncio
async def test_cached_pages_are_reused_without_spending_fetches(tmp_path,public):
    ledger=WebLedger.create(tmp_path,searches=2,fetches=2)
    first,second=phase(ledger),phase(ledger)
    await first.fetch_web('https://example.com/a',mode='cached')
    again=await second.fetch_web('https://example.com/a',mode='cached')
    assert 'Official docs page text' in again and second.call.await_count==0
    assert ledger.read()['used']['fetch']==1

@pytest.mark.asyncio
async def test_current_reads_never_reuse_discovery_text_but_do_reuse_fresh_origin_reads(tmp_path,public,monkeypatch):
    ledger=WebLedger.create(tmp_path,searches=2,fetches=3)
    observed=datetime.now(timezone.utc).isoformat()
    origin=AsyncMock(return_value=('Price 100 In stock',{'method':'origin-http','requested_url':'https://example.com/a','current_eligible':True,'observed_at':observed}))
    monkeypatch.setattr('hub.public_page.fetch_origin',origin)
    first,second,third=phase(ledger),phase(ledger),phase(ledger)
    await first.fetch_web('https://example.com/a',mode='cached')
    current=await second.fetch_web('https://example.com/a',mode='current')
    assert origin.await_count==1 and ledger.read()['used']['fetch']==2
    reused=await third.fetch_web('https://example.com/a',mode='current')
    assert origin.await_count==1 and ledger.read()['used']['fetch']==2
    meta=evidence_metadata(reused)
    assert meta['current_eligible'] is True and meta['observed_at']==observed and 'Price 100 In stock' in reused

def test_stale_pages_expire_and_wids_are_stable(tmp_path):
    ledger=WebLedger.create(tmp_path,max_age=600)
    old=(datetime.now(timezone.utc)-timedelta(minutes=20)).isoformat()
    ledger.store_page('https://example.com/old','text',{},'origin-http',old)
    ledger.store_page('https://example.com/new','text',{},'origin-http',datetime.now(timezone.utc).isoformat())
    assert ledger.load_page('https://example.com/old') is None and ledger.load_page('https://example.com/new')['text']=='text'
    assert [ledger.wid('https://example.com/a'),ledger.wid('https://example.com/b'),ledger.wid('https://example.com/a')]==['W1','W2','W1']

def test_open_requires_an_existing_ledger(tmp_path):
    assert WebLedger.open(tmp_path) is None
    WebLedger.create(tmp_path);assert WebLedger.open(tmp_path) is not None and (tmp_path/NAME).stat().st_mode&0o077==0

@pytest.mark.asyncio
async def test_phase_tools_enforce_the_job_ledger_only_when_present(tmp_path,monkeypatch):
    request=JobRequest(role='researcher',task='Find RTX 5070 price',idempotency_key='ledger-tools',verify=True)
    (tmp_path/'request.json').write_text(request.model_dump_json())
    plan={'objective':'o','needs_current_evidence':False,'requirements':[{'id':'Q1','task_quote':'RTX 5070','requirement':'r','acceptance':'a'}],
          'strategy':['s'],'result_format':'f','stop_when':'w'}
    monkeypatch.setattr(Research,'call',AsyncMock(return_value='Results'))
    WebLedger.create(tmp_path,searches=0,fetches=0)
    tools=create_tools(tmp_path,'work')
    await tools.call_tool('plan_web_task',plan)
    with pytest.raises(Exception,match='Job-wide search budget'):await tools.call_tool('search_web',{'query':'RTX 5070 price'})
    (tmp_path/NAME).unlink()
    tools=create_tools(tmp_path,'work')
    await tools.call_tool('plan_web_task',plan)
    assert 'Results' in str(await tools.call_tool('search_web',{'query':'RTX 5070 price'}))
