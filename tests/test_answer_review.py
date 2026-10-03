import json
import sys
from unittest.mock import AsyncMock

import pytest

from hub import cli, engine
from hub.models import AnswerReview, Check, JobRequest
from hub.report import final_report, parse_report
from hub.runner import Runner
from hub.scoped import DraftEvidence, Research, ScopeError, create_tools
from hub.settings import STATE


def report(status='COMPLETE', findings='Draft answer'):
    return parse_report(f'LOCAL_WORKER_REPORT\nStatus: {status}\nFindings:\n{findings}\nFiles:\nNone\nChecks:\nNot run\nRisks:\nNone\nEND_LOCAL_WORKER_REPORT')


def assessment(status='COMPLETE', requirement_status='met'):
    return dict(status=status, findings='Corrected final answer', files='None', checks='Not run', risks='None',
        requirements=[dict(requirement='Give a corrected explanation',status=requirement_status,evidence='The final answer contains an explanation.',
            evidence_refs=[dict(source='answer',quote='Corrected final answer')])])


def test_review_defaults_and_authoritative_unknowns(repo):
    request=JobRequest(role='personal',task='Explain',idempotency_key='defaults',execution_preset='extended')
    assert request.needs_answer_review() and request.context_limit()==32768
    assert not request.model_copy(update={'review_pass':False}).needs_answer_review()
    assert request.model_copy(update={'execution_preset':'work','review_pass':True}).needs_answer_review()
    assert not request.model_copy(update={'execution_preset':'work'}).needs_answer_review()
    assert not request.model_copy(update={'workflow':'implement'}).needs_answer_review()
    direct=JobRequest(role='validator',repo=str(repo),task='Check',idempotency_key='direct',execution_preset='extended',review_pass=True,
        checks=[Check(name='version',argv=[sys.executable,'-V'])])
    assert not direct.needs_answer_review()
    assert AnswerReview.model_validate(assessment(requirement_status='unknown')).status=='PARTIAL'
    assert AnswerReview.model_validate(assessment(requirement_status='unmet')).status=='PARTIAL'
    assert AnswerReview.model_validate(assessment(status='PARTIAL')).status=='COMPLETE'
    with pytest.raises(ValueError):AnswerReview.model_validate(dict(assessment(),requirements=[]))


def test_provider_schema_adaptation_and_closed_unknowns():
    search={'properties':{'query':{},'objective':{},'numResults':{}},'required':['query','objective'],'additionalProperties':False}
    args=Research.provider_arguments('web_search_exa',{'query':'official docs','numResults':5},search)
    assert args['objective']=='official docs' and args['numResults']==5
    assert Research.provider_arguments('web_fetch_exa',{'url':'https://example.com'},
        {'properties':{'urls':{},'maxCharacters':{}},'required':['urls']})=={'urls':['https://example.com'],'maxCharacters':24000}
    search['required'].append('unknown')
    with pytest.raises(ScopeError):Research.provider_arguments('web_search_exa',{'query':'docs'},search)


def test_review_guards_final_format_and_exact_evidence():
    from hub.answer_review import validate_assessment
    value=AnswerReview.model_validate(assessment())
    guarded=validate_assessment(value,'Answer in exactly two bullets',{})
    assert guarded.status=='PARTIAL' and any(r.status=='unmet' for r in guarded.requirements)
    value=AnswerReview.model_validate(assessment())
    value.requirements[0].evidence_refs[0].source='R0'
    value.requirements[0].evidence_refs[0].quote='Fact never returned by provider'
    assert validate_assessment(value,'Explain',{'R0':'An unrelated excerpt'}).requirements[0].status=='unknown'
    value=AnswerReview.model_validate(assessment())
    assert validate_assessment(value,'Explain',{}).status=='COMPLETE'
    value=AnswerReview.model_validate(assessment())
    value.findings='- First\n- Second';value.requirements[0].evidence_refs[0].quote='First'
    assert validate_assessment(value,'Answer in exactly two bullets',{}).status=='COMPLETE'
    value=AnswerReview.model_validate(assessment())
    value.findings='1. First\n2. Second';value.requirements[0].evidence_refs[0].quote='First'
    value=validate_assessment(value,'Answer in exactly two bullets',{})
    assert value.findings=='- First\n- Second' and value.status=='COMPLETE'
    value=AnswerReview.model_validate(assessment())
    value.findings='First paragraph.\n\nSecond paragraph.'
    value.requirements[0].evidence_refs[0].quote='First paragraph...Second paragraph'
    value=validate_assessment(value,'Answer in exactly two bullets',{})
    assert value.findings=='- First paragraph.\n\n- Second paragraph.' and value.status=='COMPLETE'
    assert [ref.quote for ref in value.requirements[0].evidence_refs]==['First paragraph','Second paragraph']
    value=AnswerReview.model_validate(assessment())
    value.findings='First item\nSecond item';value.requirements[0].evidence_refs[0].quote='First item'
    assert validate_assessment(value,'Answer in exactly two bullets',{}).findings=='- First item\n\n- Second item'
    value=AnswerReview.model_validate(assessment())
    value.requirements[0].evidence_refs[0].source='R0';value.requirements[0].evidence_refs[0].quote='Literal source text'
    value=validate_assessment(value,'Explain',{'R0':'Literal\nsource text'})
    assert value.status=='COMPLETE' and value.requirements[0].evidence_refs[0].quote=='Literal\nsource text'
    value=AnswerReview.model_validate(assessment())
    value.requirements[0].evidence_refs[0].quote='Corrected final...Fabricated fact'
    assert validate_assessment(value,'Explain',{}).status=='PARTIAL'
    value=AnswerReview.model_validate(assessment())
    value.findings='Hello!';value.requirements[0].evidence_refs=[];value.requirements[0].evidence="Final answer says 'Hello!'"
    value=validate_assessment(value,'Say exactly Hello!',{})
    assert value.status=='COMPLETE' and value.requirements[0].evidence_refs[0].quote=='Hello!'


@pytest.mark.asyncio
async def test_web_pagination_keeps_evidence_and_no_refetch(monkeypatch):
    events=[];research=Research(lambda k,v:events.append((k,v)))
    text='intro '*1100+'required fact near end'
    research.call=AsyncMock(return_value=text)
    monkeypatch.setattr('hub.scoped.public_url',AsyncMock(side_effect=lambda url:url))
    first=await research.fetch_web('https://example.com',characters=6000,mode='cached')
    second=await research.fetch_web('https://example.com',start=6000,characters=2000,mode='cached')
    assert 'required fact near end' not in first and 'required fact near end' in second
    assert 'freshness' in first and 'next_start: 6000' in first
    assert research.call.await_count==1 and len(events)==2
    found=await research.fetch_web('https://example.com',find='required fact',characters=500,mode='cached')
    assert 'required fact near end' in found and research.call.await_count==1
    with pytest.raises(ScopeError):await research.fetch_web('https://example.com',start=-1)
    with pytest.raises(ScopeError):await research.fetch_web('https://example.com',characters=9000)


@pytest.mark.asyncio
@pytest.mark.parametrize('role',['personal','editor','investigator'])
async def test_review_authority_and_evidence_paging(tmp_path,repo,role):
    directory=tmp_path/role;directory.mkdir()
    request=JobRequest(role=role,repo=str(repo) if role!='personal' else None,
        allowed_paths=['app.ts'] if role=='editor' else [],task='Explain',idempotency_key=role)
    (directory/'request.json').write_text(request.model_dump_json())
    records=[{'index':0,'tool':'fetch_web','status':'completed','arguments':{'url':'https://example.com'},'text':'0123456789'}]
    (directory/'draft-evidence.json').write_text(json.dumps(records))
    names={t.name for t in await create_tools(directory,'answer_review').list_tools()}
    assert 'read_draft_evidence' in names and not {'edit_file','replace_lines','write_file'} & names
    if role=='personal':assert names=={'plan_web_task','search_web','fetch_web','read_draft_evidence'}
    evidence=DraftEvidence(directory)
    assert '3456' in evidence.read_draft_evidence(0,3,4)
    with pytest.raises(ScopeError):evidence.read_draft_evidence(1)


@pytest.mark.asyncio
@pytest.mark.parametrize('thinking',[None,False])
async def test_review_fresh_context_thinking_and_ledger(tmp_path,monkeypatch,thinking):
    directory=tmp_path/'job';directory.mkdir()
    request=JobRequest(role='personal',task='Explain with exactly two bullets',idempotency_key='engine',execution_preset='extended',model_thinking=thinking)
    (directory/'request.json').write_text(request.model_dump_json())
    # Extended must override a nonthinking profile unless the caller overrides it.
    (directory/'role-config.json').write_text('{"answer_review":{"thinking":false}}')
    seen=[]
    async def response(client,url,body,headers,on_segment):
        seen.append(json.loads(json.dumps(body)));on_segment('thinking','private local reasoning')
        content='LOCAL_WORKER_REPORT\nStatus: COMPLETE\nFindings:\nNo checklist yet\nFiles:\nNone\nChecks:\nNot run\nRisks:\nNone\nEND_LOCAL_WORKER_REPORT' if len(seen)==1 else json.dumps(assessment(requirement_status='unknown'))
        return {'message':{'role':'assistant','content':content,'thinking':'private local reasoning'},'done_reason':'stop'}
    monkeypatch.setattr(engine,'chat_response',response)
    await engine.run(directory,'answer-review','Original prompt and draft',phase='answer_review')
    assert len(seen)==2 and seen[0]['think']==(thinking is None)
    assert 'EVERY explicit user requirement' in seen[0]['messages'][0]['content']
    assert seen[-1]['format']['properties']['requirements']
    assert seen[-1]['think'] is False
    assert all('private local reasoning' not in json.dumps(s['messages']) for s in seen)
    session=json.loads((directory/'answer-review.session.json').read_text())
    assert final_report(session)['status']=='PARTIAL'
    assert json.loads((directory/'answer-review.json').read_text())['requirements'][0]['status']=='unknown'


@pytest.mark.asyncio
@pytest.mark.parametrize('packet',[{}, {'findings':'Hello!'}])
async def test_empty_structured_output_preserves_session_without_parser_crash(tmp_path,monkeypatch,packet):
    directory=tmp_path/'job';directory.mkdir()
    request=JobRequest(role='personal',task='Hello',idempotency_key='empty',execution_preset='extended')
    (directory/'request.json').write_text(request.model_dump_json());seen=[]
    async def response(client,url,body,headers,on_segment):
        seen.append(body)
        return {'message':{'role':'assistant','content':'No report yet' if len(seen)==1 else json.dumps(packet)},'done_reason':'stop'}
    monkeypatch.setattr(engine,'chat_response',response)
    await engine.run(directory,'work','Hello')
    assert len(seen)==2 and seen[0]['think'] is True and seen[1]['think'] is False
    assert final_report(json.loads((directory/'work.session.json').read_text())) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',[None,'timeout','missing_ledger'])
async def test_orchestrator_two_passes_preserves_original_and_draft(store,failure):
    original='Explain in exactly two bullets. Use Celsius, never Fahrenheit. Include a source.'
    request=JobRequest(role='personal',task=original,idempotency_key=str(failure),execution_preset='extended')
    job=store.submit(request);runner=Runner(store,'token');calls=[]
    async def model(_job,_request,directory,label,prompt,**kwargs):
        calls.append((label,prompt,kwargs))
        if label=='work':
            return {'messages':[{'content':[{'type':'tool','name':'fetch_web','state':{'status':'completed','content':[{'type':'text','text':'Recorded source says 10 C'}]}}]}]},report()
        if failure=='timeout':raise TimeoutError('Review budget exhausted')
        if failure!='missing_ledger':(directory/'answer-review.json').write_text(json.dumps(assessment()))
        return {'messages':[]},report(findings='Corrected final answer')
    runner.execute_model=model
    await runner.run(store.next());saved=store.get(job['id']);directory=STATE/'jobs'/job['id']
    assert [c[0] for c in calls]==['work','answer-review']
    assert original in calls[1][1]
    assert '<original_task>\n'+original+'\n</original_task>' in calls[1][1]
    assert calls[0][2]['timeout']==165 and calls[1][2]['phase']=='answer_review'
    assert (directory/'draft-report.txt').read_text()==report()['report']
    assert DraftEvidence(directory).read_draft_evidence(0).endswith('Recorded source says 10 C')
    if failure:
        assert saved['state']==('timed_out' if failure=='timeout' else 'failed')
        assert not saved['result']['report_valid'] and saved['result'].get('worker_status')!='COMPLETE'
        assert saved['result']['answer_review']['state']=='incomplete'
    else:
        assert saved['result']['report']==report(findings='Corrected final answer')['report']
        assert saved['result']['answer_review']['requirements'][0]['status']=='met'


@pytest.mark.asyncio
async def test_editor_review_checks_once_and_failing_check_authoritative(repo,store):
    request=JobRequest(role='editor',task='Change answer',repo=str(repo),allowed_paths=['app.ts'],review_pass=True,
        checks=[Check(name='failing',argv=[sys.executable,'-c','raise SystemExit(1)'])],idempotency_key='editor-review')
    job=store.submit(request);runner=Runner(store,'token');calls=[]
    async def model(_job,_request,directory,label,prompt,**kwargs):
        calls.append(label)
        if label=='work':(repo/'app.ts').write_text('export const answer = 42;\n')
        else:
            assert '+export const answer = 42' in prompt and '"exit_code": 1' in prompt
            (directory/'answer-review.json').write_text(json.dumps(assessment()))
        return {'messages':[]},report()
    runner.execute_model=model
    await runner.run(store.next());result=store.get(job['id'])['result']
    assert calls==['work','answer-review'] and len(result['checks'])==1
    assert result['worker_status']=='PARTIAL' and result['checks_failed']


@pytest.mark.asyncio
async def test_validator_summary_review_keeps_actual_failed_exit_code(repo,store):
    request=JobRequest(role='validator',task='Explain recorded failure',repo=str(repo),summary_mode='local',execution_preset='extended',
        checks=[Check(name='failing',argv=[sys.executable,'-c','raise SystemExit(1)'])],idempotency_key='validator-review')
    job=store.submit(request);runner=Runner(store,'token');calls=[]
    async def model(_job,_request,directory,label,prompt,**kwargs):
        calls.append(label)
        if label=='answer-review':
            records=json.loads((directory/'draft-evidence.json').read_text())
            assert records[-1]['tool']=='recorded_checks' and '"exit_code": 1' in records[-1]['text']
            (directory/'answer-review.json').write_text(json.dumps(assessment()))
        return {'messages':[]},report()
    runner.execute_model=model
    await runner.run(store.next());result=store.get(job['id'])['result']
    assert calls==['work','answer-review'] and len(result['checks'])==1
    assert result['checks'][0]['exit_code']==1 and result['worker_status']=='PARTIAL'


@pytest.mark.parametrize('flags,expected',[(['--extended'],True),(['--extended','--no-review'],False),(['--review'],True)])
def test_cli_review_selection(repo,monkeypatch,flags,expected):
    from hub import client
    seen=[]
    def call(method,path,data=None):seen.append(data);return {'id':'a'*32,'state':'queued'}
    monkeypatch.setattr(client,'call',call);monkeypatch.chdir(repo)
    monkeypatch.setattr(sys,'argv',['local-worker',*flags,'--async','Explain'])
    cli.main()
    assert JobRequest.model_validate(seen[0]).needs_answer_review()==expected
