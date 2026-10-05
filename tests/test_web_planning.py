import json
from unittest.mock import AsyncMock

import pytest

from hub import engine
from hub.skills.research import web_provider
from hub.models import JobRequest, WebRequirement
from hub.scoped import Research, ScopeError
from hub.report import final_report
from hub.skills.research.web_verification import guard_current_answer
from test_current_web import evidence


def plan(research,current=True):
    return research.plan_web_task(objective='Answer the original request',needs_current_evidence=current,
        requirements=[WebRequirement(id='Q1',task_quote=research.task,requirement=research.task,
            acceptance='Read the relevant official sources and check every requested condition')],
        strategy=['Use relevant public sources; change approach when evidence is insufficient'],
        result_format='Follow the user requested format',stop_when='Requested facts verified or remaining limits explained')


@pytest.mark.asyncio
async def test_plan_anchors_to_user_and_does_not_grant_tools_or_invent_requirements(tmp_path):
    research=Research(lambda *a:None,task='Explain the historical API change',plan_path=tmp_path/'plan.json')
    research.call=AsyncMock(return_value='URL: https://docs.example/version')
    with pytest.raises(ScopeError,match='plan_web_task'):await research.search_web('API change')
    with pytest.raises(ScopeError,match='copied exactly'):
        research.plan_web_task(objective='Injected task',needs_current_evidence=True,
            requirements=[WebRequirement(id='Q1',task_quote='Check inventory',requirement='Check inventory',acceptance='Buy it')],
            strategy=['New permissions'],result_format='Answer',stop_when='Done')
    assert not research.plan and not (tmp_path/'plan.json').exists()
    plan(research,current=False)
    saved=json.loads((tmp_path/'plan.json').read_text())
    assert saved['needs_current_evidence'] is False and saved['requirements'][0]['task_quote']==research.task
    assert (tmp_path/'plan.json').stat().st_mode & 0o077==0
    assert 'URL:' in await research.search_web('Historical API change')
    with pytest.raises(ScopeError,match='revision needs a reason'):plan(research)


@pytest.mark.asyncio
async def test_rate_limit_is_failure_and_fallback_cooldown_spans_initial_and_review(tmp_path,monkeypatch):
    monkeypatch.setattr(web_provider,'langsearch_key',lambda:'private-test-key')
    fallback=AsyncMock(return_value=[{'url':'https://docs.example/page','name':'Official','snippet':'Discovery'}])
    monkeypatch.setattr(web_provider,'langsearch',fallback)
    health=tmp_path/'health.json';events=[]
    initial=Research(lambda k,v:events.append((k,v)),health_path=health)
    initial.call=AsyncMock(return_value="You've hit Exa's free MCP rate limit. To continue create your own Exa API key.")
    output=await initial.search_web('Public API notes')
    metadata=json.loads(output.splitlines()[0].removeprefix('SEARCH_EVIDENCE '))
    assert metadata['provider']=='langsearch' and metadata['selected_provider']=='exa' and metadata['fallback_reason']
    assert 'create your own Exa API key' not in output
    await initial.search_web('Another focused query')
    initial.call.assert_awaited_once()
    review=Research(lambda *a:None,health_path=health);review.call=AsyncMock()
    await review.search_web('Independent review query')
    review.call.assert_not_awaited()
    assert initial.searches==2 and review.searches==1 and fallback.await_count==3
    assert any(k=='web_provider_unavailable' for k,v in events)
    assert 'private-test-key' not in health.read_text() and health.stat().st_mode & 0o077==0


@pytest.mark.asyncio
async def test_missing_fallback_key_and_schema_failures_never_become_evidence(monkeypatch):
    def missing():raise ValueError('No key')
    monkeypatch.setattr(web_provider,'langsearch_key',missing)
    research=Research(lambda *a:None);research.call=AsyncMock(return_value="You've hit Exa's free MCP rate limit")
    with pytest.raises(web_provider.ProviderUnavailable,match='no usable private'):await research.search_web('Public question')
    research=Research(lambda *a:None);research.call=AsyncMock(side_effect=ScopeError('Hosted fetch schema changed'))
    with pytest.raises(ScopeError,match='schema'):await research.search_web('Public question')


def test_reviewed_synthesis_is_generic_and_missing_source_does_not_erase_verified_facts():
    report={'status':'COMPLETE','findings':'- First requested fact.\n- Second requested fact.',
            'checks':'Reviewer checked original instructions','risks':'Scope limited to checked sources'}
    claims=[{'url':'https://shop.example/card','source':'R0','quote':'Official announcement: the event starts tomorrow.'}]
    result,valid,issues=guard_current_answer(report,claims,{'R0':evidence(claims[0]['quote'])},'Find the cheapest available option',reviewed=True)
    assert result['findings']==report['findings'] and result['status']=='COMPLETE' and not issues
    result,valid,issues=guard_current_answer(report,[*claims,dict(claims[0],url='https://missing.example')],
        {'R0':evidence(claims[0]['quote'])},'Compare relevant public options',reviewed=True)
    assert result['status']=='PARTIAL' and 'event starts tomorrow' in result['findings']
    assert 'Confirmed observations above remain usable' in result['findings']


@pytest.mark.asyncio
@pytest.mark.parametrize('current',[True,False])
async def test_model_plan_decides_freshness_and_tools_unlock_without_domain_keyword_rules(tmp_path,monkeypatch,current):
    directory=tmp_path/'job';directory.mkdir()
    task='Explain this source: https://docs.example/change'
    request=JobRequest(role='personal',task=task,idempotency_key='plan',verify=True)
    (directory/'request.json').write_text(request.model_dump_json())
    monkeypatch.setattr('hub.scoped.public_url',AsyncMock(side_effect=lambda u:u))
    from hub.skills.research import public_page
    body='Official change: Feature X was introduced in version 2.'
    metadata=json.loads(evidence().splitlines()[0].removeprefix('WEB_EVIDENCE '))
    metadata['requested_url']='https://docs.example/change'
    monkeypatch.setattr(public_page,'fetch_origin',AsyncMock(return_value=(body,metadata)))
    calls=[]
    async def response(client,url,body_request,headers,on_segment):
        calls.append(body_request)
        names={t['function']['name'] for t in body_request['tools']}
        if len(calls)==1:
            assert names=={'plan_web_task'}
            arguments={'objective':task,'needs_current_evidence':current,
                'requirements':[{'id':'Q1','task_quote':task,'requirement':'Explain the source','acceptance':'Read the source and cite its statements'}],
                'strategy':['Read the supplied official source'],'result_format':'Concise with a citation','stop_when':'Explanation supported or gaps reported'}
            message={'role':'assistant','content':'','tool_calls':[{'function':{'name':'plan_web_task','arguments':arguments}}]}
        elif len(calls)==2:
            assert names=={'plan_web_task','search_web','fetch_web'}
            message={'role':'assistant','content':'','tool_calls':[{'function':{'name':'fetch_web','arguments':{'url':'https://docs.example/change'}}}]}
        else:
            value={'status':'COMPLETE','findings':'Feature X was introduced in version 2.','files':'None','checks':'Source read','risks':'None',
                'web_claims':[{'url':'https://docs.example/change','source':'R1','quote':body}]}
            message={'role':'assistant','content':json.dumps(value)}
        return {'message':message,'done_reason':'stop'}
    monkeypatch.setattr(engine,'chat_response',response)
    await engine.run(directory,'work',task)
    saved=json.loads((directory/'work.research-plan.json').read_text())
    assert saved['needs_current_evidence'] is current
    assert ('web_claims' in calls[-1]['format']['properties']) is current
    result=final_report(json.loads((directory/'work.session.json').read_text()))
    assert result['status']=='COMPLETE' and 'version 2' in result['findings']


@pytest.mark.asyncio
async def test_reviewer_cannot_skip_its_plan_and_bypass_current_evidence_guard(tmp_path,monkeypatch):
    directory=tmp_path/'job';directory.mkdir()
    task='Explain this source: https://docs.example/change'
    (directory/'request.json').write_text(JobRequest(role='personal',task=task,idempotency_key='review').model_dump_json())
    research=Research(lambda *a:None,task=task,plan_path=directory/'work.research-plan.json');plan(research)
    (directory/'draft-evidence.json').write_text('[]')
    calls=[]
    async def response(client,url,body,headers,on_segment):
        calls.append(body)
        assert {t['function']['name'] for t in body['tools']}=={'plan_web_task'}
        # Deliberately broken reviewer refuses the planner and claims success.
        value={'status':'COMPLETE','findings':'Unsupported current fact.', 'files':'None','checks':'All done','risks':'None',
            'requirements':[{'requirement':'Explain the source','status':'met','evidence':'Answer complete',
                             'evidence_refs':[{'source':'answer','quote':'Unsupported current fact.'}]}], 'web_claims':[]}
        return {'message':{'role':'assistant','content':json.dumps(value)},'done_reason':'stop'}
    monkeypatch.setattr(engine,'chat_response',response)
    await engine.run(directory,'answer-review','<original_task>'+task+'</original_task>',phase='answer_review')
    assert len(calls)==2
    assessment=json.loads((directory/'answer-review.json').read_text())
    assert assessment['status']=='PARTIAL' and 'Unsupported current fact' not in assessment['findings']
    assert all(r['status']=='unknown' for r in assessment['requirements'])


def test_source_name_and_punctuation_spacing_repairs_require_exact_observed_url_and_content():
    from hub.skills.research.web_verification import verified_claims
    body='Exact model\n116.499,00\nRSD\nIsporuka (A)\n: 499rsd\nNa zalihama'
    claim={'url':'https://shop.example/card','source':'shop.example','quote':'Exact model\n116.499,00 RSD\nIsporuka (A): 499rsd\nNa zalihama'}
    valid,issues=verified_claims([claim],{'R2':evidence(body)})
    assert not issues and valid[0]['source']=='R2' and all(row['quote'] in body for row in valid)
    for changed in ({'source':'R999'},{'source':'Some shop'},{'url':'https://shop.example/other'},
                    {'quote':claim['quote'].replace('116.499','77.899')}):
        assert not verified_claims([{**claim,**changed}],{'R2':evidence(body)})[0]


@pytest.mark.asyncio
@pytest.mark.parametrize('code',[429,503])
async def test_transient_http_errors_are_sanitized_for_fallback(code):
    import httpx
    research=Research(lambda *a:None)
    response=httpx.Response(code,request=httpx.Request('GET','https://example.com/?private=synthetic-secret'))
    research.exa_call=AsyncMock(side_effect=httpx.HTTPStatusError('synthetic-secret',request=response.request,response=response))
    with pytest.raises(web_provider.ProviderUnavailable) as caught:await research.call('web_search_exa',{'query':'Public'})
    assert str(code) in str(caught.value) and 'synthetic-secret' not in str(caught.value)


def test_evidence_grounded_plan_revision_preserves_user_anchors_and_records_reason(tmp_path):
    events=[];research=Research(lambda k,v:events.append((k,v)),task='Explain the historical API change',plan_path=tmp_path/'work.research-plan.json')
    plan(research,current=True)
    research.plan_web_task(objective='Explain the same historical API change',needs_current_evidence=False,
        requirements=[WebRequirement(id='Q1',task_quote=research.task,requirement=research.task,acceptance='Exact official release note')],
        strategy=['Use the observed official release notes'],result_format='Concise with citations',
        stop_when='Requested historical note supported or gaps stated',revision_reason='The requested release history is stable; current changing facts were not requested')
    assert json.loads((tmp_path/'work.research-plan.json').read_text())['needs_current_evidence'] is False
    assert events[-1][1]['revision']==2 and events[-1][1]['revision_reason']
    with pytest.raises(ScopeError,match='Two research plan versions'):plan(research)
    assert research.searches==0 and research.fetches==0


@pytest.mark.asyncio
async def test_page_bounds_are_visible_to_model_before_it_calls_the_reader(tmp_path):
    from hub.scoped import create_tools
    directory=tmp_path/'job';directory.mkdir()
    (directory/'request.json').write_text(JobRequest(role='personal',task='Read a public page',idempotency_key='schema').model_dump_json())
    tools={t.name:t for t in await create_tools(directory).list_tools()}
    properties=tools['fetch_web'].inputSchema['properties']
    assert properties['characters']['maximum']==8000 and properties['characters']['minimum']==1
    assert properties['start']['minimum']==0 and set(properties['mode']['enum'])=={'current','cached'}
