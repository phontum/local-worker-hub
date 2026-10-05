import json
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from hub import engine,public_page
from hub.models import JobRequest
from hub.report import final_report
from hub.scoped import Research,ScopeError
from hub.web_verification import guard_current_answer,verified_claims,clarification


def evidence(body='116.499,00 RSD\nNa zalihama',**extras):
    metadata={'method':'origin-http','requested_url':'https://shop.example/card','title':'Exact card',
              'observed_at':datetime.now(timezone.utc).isoformat(),'current_eligible':True,**extras}
    return 'WEB_EVIDENCE '+json.dumps(metadata)+'\nURL: https://shop.example/card\nPage text:\n'+body


@pytest.mark.asyncio
async def test_direct_origin_prices_override_cached_search_and_pages_do_not_refetch(monkeypatch):
    events=[];r=Research(lambda k,v:events.append((k,v)),'langsearch')
    r.pages['https://shop.example/card']=('77.899 RSD Na zalihama','old')
    r.page_providers['https://shop.example/card']='langsearch-text'
    monkeypatch.setattr('hub.scoped.public_url',AsyncMock(side_effect=lambda u:u))
    reader=AsyncMock(return_value=('116.499,00 RSD\nNa zalihama',json.loads(evidence().split('\n')[0][13:])))
    monkeypatch.setattr(public_page,'fetch_origin',reader)
    r.call=AsyncMock()
    text=await r.fetch_web('https://shop.example/card')
    assert '116.499' in text and '77.899' not in text and 'origin-http' in text
    await r.fetch_web('https://shop.example/card',find='zalihama')
    assert reader.await_count==1 and r.fetches==1
    r.call.assert_not_awaited()
    assert events[-1][1]['current_eligible'] is True


@pytest.mark.asyncio
async def test_blocked_origin_fallback_never_qualifies_for_current_claim(monkeypatch):
    monkeypatch.setattr('hub.scoped.public_url',AsyncMock(side_effect=lambda u:u))
    reader=AsyncMock(side_effect=ValueError('Origin HTTP 403'))
    monkeypatch.setattr(public_page,'fetch_origin',reader)
    r=Research(lambda *args:None);r.call=AsyncMock(return_value='Old price 77.899 RSD\nNa zalihama')
    text=await r.fetch_web('https://shop.example/card')
    assert 'UNVERIFIED CURRENT CONTENT' in text and 'Origin HTTP 403' in text
    claims=[{'url':'https://shop.example/card','source':'R0','quote':'Na zalihama'}]
    assert not verified_claims(claims,{'R0':text})[0]
    await r.fetch_web('https://shop.example/card')
    assert reader.await_count==1 and r.call.await_count==1


@pytest.mark.asyncio
async def test_origin_redirects_and_size_limits(monkeypatch):
    calls=[]
    async def handle(request):
        calls.append(str(request.url))
        if request.url.path=='/redirect':return httpx.Response(302,headers={'location':'http://127.0.0.1/private'})
        if request.url.path=='/large':return httpx.Response(200,headers={'content-type':'text/plain'},content=b'x'*2_000_001)
        if request.url.path=='/blocked':return httpx.Response(403)
        if request.url.path=='/binary':return httpx.Response(200,headers={'content-type':'application/octet-stream'},content=b'abc')
        return httpx.Response(200,headers={'content-type':'text/html','age':'1000'},text='<title>Card</title><nav>Old price</nav><main><p>116.499 RSD</p><form><p>Nema na zalihama</p></form><script>fake price</script></main>')
    monkeypatch.setattr(public_page,'transport',lambda:httpx.MockTransport(handle))
    async def validate(url):
        if '127.0.0.1' in url:raise ScopeError('Private destinations are blocked')
    for suffix,match in [('redirect','Private'),('large','size'),('blocked','403'),('binary','supported')]:
        with pytest.raises(ValueError,match=match):await public_page.fetch_origin('https://shop.example/'+suffix,validate)
    text,metadata=await public_page.fetch_origin('https://shop.example/card',validate)
    assert text=='116.499 RSD\nNema na zalihama'
    assert not metadata['current_eligible'] and metadata['cache_age_seconds']==1000
    assert not any('127.0.0.1' in url for url in calls)


@pytest.mark.asyncio
async def test_dns_pin_uses_checked_address_and_blocks_mixed_dns(monkeypatch):
    from httpcore._backends.auto import AutoBackend
    monkeypatch.setattr(public_page.socket,'getaddrinfo',lambda *a,**kw:[(2,1,6,'',('93.184.216.34',443))])
    connect=AsyncMock(return_value='stream');monkeypatch.setattr(AutoBackend,'connect_tcp',connect)
    assert await public_page.PublicBackend().connect_tcp('shop.example',443,4)=='stream'
    assert connect.call_args.args[0]=='93.184.216.34'
    monkeypatch.setattr(public_page.socket,'getaddrinfo',lambda *a,**kw:[(2,1,6,'',('93.184.216.34',443)),(2,1,6,'',('127.0.0.1',443))])
    with pytest.raises(ValueError,match='Private'):await public_page.PublicBackend().connect_tcp('shop.example',443)
    assert connect.await_count==1


@pytest.mark.parametrize('mutation',[
    {'method':'exa-keyless'}, {'current_eligible':False}, {'requested_url':'https://other.example/card'},
    {'observed_at':(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()}])
def test_freshness_provenance_and_identity_are_required(mutation):
    claims=[{'url':'https://shop.example/card','source':'R0','quote':'116.499,00 RSD'}]
    assert not verified_claims(claims,{'R0':evidence(**mutation)})[0]


def test_safe_answer_replaces_false_model_price_and_preserves_negative_context():
    report={'status':'COMPLETE','findings':'Cheapest current card is 77,899 RSD in stock','checks':'All verified','risks':'None'}
    claims=[{'url':'https://shop.example/card','source':'R0','quote':'Na zalihama'}]
    result,valid,issues=guard_current_answer(report,claims,{'R0':evidence('116.499,00 RSD\nNema Na zalihama')},'Find cheapest card in stock')
    assert '77,899' not in result['findings']
    assert valid[0]['quote']=='Nema Na zalihama' and 'Nema Na zalihama' in result['findings']
    assert verified_claims([dict(claims[0],source='answer')],{'answer':report['findings']})[0]==[]
    assert verified_claims([dict(claims[0],quote='Invented')],{'R0':evidence()})[0]==[]


def test_saved_origin_evidence_is_valid_but_search_cannot_forge_it():
    source=evidence();claim={'url':'https://shop.example/card','source':'R0','quote':'116.499,00 RSD'}
    record={'index':0,'tool':'fetch_web','status':'completed'}
    saved='Observed evidence R0 (tool read_draft_evidence, completed):\n'+json.dumps(record)+'\nCharacters 0:1000 of 1000\nObserved evidence R2 (tool fetch_web, completed):\n'+source
    # Initial evidence now includes its own R prefix. Both wrappers are valid.
    assert verified_claims([claim],{'R0':saved})[0]
    record['tool']='search_web'
    fake=json.dumps(record)+'\nCharacters 0:1000 of 1000\n'+source
    assert not verified_claims([claim],{'R0':fake})[0]


def test_clarification_does_not_exempt_factual_price_claims():
    assert clarification({'status':'PARTIAL','findings':'Which city or region should I check?'},{})
    assert not clarification({'status':'PARTIAL','findings':'The card is 77.899 RSD.'},{})
    assert not clarification({'status':'COMPLETE','findings':'Which city?'},{})


def test_quote_line_wrapping_is_normalized_without_changing_numbers():
    claim={'url':'https://shop.example/card','source':'R0','quote':'116.499,00 RSD Na zalihama'}
    valid,issues=verified_claims([claim],{'R0':evidence('116.499,00\nRSD\nNa zalihama')})
    assert not issues and valid[0]['quote']=='116.499,00\nRSD\nNa zalihama'
    claim['quote']='77.899,00 RSD Na zalihama'
    assert not verified_claims([claim],{'R0':evidence()})[0]


def test_composed_quotes_keep_only_exact_fragments_and_conditions():
    body='Exact card\nOther conditions\n116.499,00\nRSD\nTax included\nNa zalihama'
    claim={'url':'https://shop.example/card','source':'R0','quote':'Exact card\\n116.499,00 RSD\\nNa zalihama'}
    valid,issues=verified_claims([claim],{'R0':evidence(body)})
    assert not issues and [item['quote'] for item in valid]==['Exact card','116.499,00\nRSD','Na zalihama']
    claim['quote']='Exact card\\n77.899,00 RSD\\nNa zalihama'
    assert not verified_claims([claim],{'R0':evidence(body)})[0]


def test_dashboard_summary_keeps_verification_artifact_and_bounds_issues():
    from hub.presentation import result_summary,size
    value=result_summary({'id':'a'*32,'state':'completed','request':{'role':'personal'},'review':None,
        'result':{'web_verification':{'artifact':'work.web-verification.json','verified_observations':1,'issues':['x'*2000]*16}}})
    assert value['web_verification']['verified_observations']==1
    assert 'work.web-verification.json' in value['artifacts'] and size(value)<=8192


@pytest.mark.asyncio
async def test_engine_cannot_finish_current_answer_from_search_only(tmp_path,monkeypatch):
    directory=tmp_path/'job';directory.mkdir()
    request=JobRequest(role='personal',task='Find cheapest available card in stock',idempotency_key='guard',verify=True)
    (directory/'request.json').write_text(request.model_dump_json())
    class Tools:
        async def list_tools(self):return [SimpleNamespace(name='search_web',description='',inputSchema={'type':'object','properties':{}})]
        async def call_tool(self,*args):return [SimpleNamespace(type='text',text='URL: https://shop.example/card\n77.899 RSD')]
    monkeypatch.setattr(engine,'create_tools',lambda *_:Tools());calls=[]
    async def response(client,url,body,headers,on_segment):
        calls.append(body)
        if len(calls)==1:message={'role':'assistant','content':'','tool_calls':[{'function':{'name':'search_web','arguments':{'query':'card'}}}]}
        elif body['tools']:message={'role':'assistant','content':'LOCAL_WORKER_REPORT\nStatus: COMPLETE\nFindings:\n77.899 RSD in stock\nFiles:\nNone\nChecks:\nVerified\nRisks:\nNone\nEND_LOCAL_WORKER_REPORT'}
        else:message={'role':'assistant','content':json.dumps({'status':'COMPLETE','findings':'77.899 RSD in stock','files':'None','checks':'Verified','risks':'None','web_claims':[]})}
        return {'message':message,'done_reason':'stop'}
    monkeypatch.setattr(engine,'chat_response',response)
    await engine.run(directory,'work',request.task)
    final=final_report(json.loads((directory/'work.session.json').read_text()))
    assert final['status']=='PARTIAL' and '77.899' not in final['findings']
    assert len(calls)==4 and 'web_claims' in calls[-1]['format']['properties']
