import json
import pytest
from hub import engine, phases
from hub.models import JobRequest
from hub.scoped import create_tools

REPORT=json.dumps({'status':'COMPLETE','findings':'Hi','files':'None','checks':'Not run','risks':'None'})

def request(**kw):
    return JobRequest(role=kw.pop('role','personal'),task=kw.pop('task','Hello'),idempotency_key=kw.pop('key','phase'),**kw)

@pytest.mark.parametrize('kw,phase,profiles,expected',[
    ({},'work',{},{'context':16384,'thinking':False,'steps':12,'output_limit':4096,'model':'gemma4:12b-it-qat'}),
    ({'execution_preset':'extended'},'work',{},{'context':32768,'thinking':True}),
    ({'model_thinking':False,'execution_preset':'extended'},'work',{},{'thinking':False}),
    ({},'answer_review',{},{'thinking':True}),
    ({},'review',{'review':{'thinking':False,'steps':5,'output':100,'tool_rounds':3,'temperature':0.5}},{'thinking':False,'steps':5,'output_limit':256,'tool_rounds':3,'temperature':0.5}),
    ({},'work',{'personal':{'steps':99}},{'steps':18}),
])
def test_resolve_phase_preserves_existing_rules(kw,phase,profiles,expected):
    spec,_=phases.resolve_phase(request(**kw),profiles,phase)
    assert all(getattr(spec,key)==value for key,value in expected.items())

def test_resolve_phase_report_only_editor_and_errors():
    spec,_=phases.resolve_phase(request(execution_preset='extended'),{},'work',report_only=True)
    assert spec.thinking is False and spec.steps==2
    with pytest.raises(ValueError):phases.resolve_phase(request(),{'work':{'context':4096}},'work')
    with pytest.raises(ValueError):phases.resolve_phase(request(),{'work':{'model':'nope'}},'work')
    spec,_=phases.resolve_phase(request(),{'work':{'model':'gemma','tool_groups':['web']}},'work')
    assert spec.model=='gemma4:12b-it-qat' and spec.tool_groups==('web',)

@pytest.mark.asyncio
async def test_default_model_is_never_probed(monkeypatch):
    monkeypatch.setattr(phases,'probe',lambda name:(_ for _ in ()).throw(AssertionError('probed')))
    spec,_=phases.resolve_phase(request(),{},'work')
    assert await phases.model_support(spec) is None

@pytest.mark.asyncio
@pytest.mark.parametrize('found,expected',[({'installed':True,'capabilities':['tools']},{'tools'}),({'installed':None},None)])
async def test_non_default_model_capabilities(monkeypatch,found,expected):
    monkeypatch.setattr(phases,'probe',lambda name:found)
    spec,_=phases.resolve_phase(request(),{'work':{'model':'qwen'}},'work')
    assert await phases.model_support(spec)==expected

@pytest.mark.asyncio
async def test_missing_model_fails_before_inference(monkeypatch):
    monkeypatch.setattr(phases,'probe',lambda name:{'installed':False})
    spec,_=phases.resolve_phase(request(),{'work':{'model':'qwen'}},'work')
    with pytest.raises(RuntimeError,match='not installed'):await phases.model_support(spec)

@pytest.mark.asyncio
@pytest.mark.parametrize('capabilities,think,tools',[([],False,False),(['thinking','tools'],True,True)])
async def test_engine_gates_think_and_tools_by_capability(tmp_path,monkeypatch,capabilities,think,tools):
    directory=tmp_path/'job';directory.mkdir()
    (directory/'request.json').write_text(request(execution_preset='extended',agent_loop=True).model_dump_json())
    (directory/'role-config.json').write_text(json.dumps({'work':{'model':'qwen'}}))
    monkeypatch.setattr(phases,'probe',lambda name:{'installed':True,'capabilities':capabilities})
    seen=[]
    async def response(client,url,body,headers,on_segment):
        seen.append(body)
        return {'message':{'role':'assistant','content':'No tool use' if len(seen)==1 else REPORT},'done_reason':'stop'}
    monkeypatch.setattr(engine,'chat_response',response)
    await engine.run(directory,'work','Hello')
    first=seen[0]
    assert first['model']=='qwen3.5:9b' and first['think']==think and bool(first['tools'])==tools

@pytest.mark.asyncio
async def test_tool_groups_only_narrow_access(tmp_path):
    directory=tmp_path/'job';directory.mkdir()
    (directory/'request.json').write_text(request(role='researcher',task='Public docs question',verify=True).model_dump_json())
    async def listed(groups):
        return {tool.name for tool in await create_tools(directory,'work',groups).list_tools()}
    assert await listed(None)=={'plan_web_task','search_web','fetch_web'}
    assert await listed(('web',))=={'plan_web_task','search_web','fetch_web'}
    assert await listed(('files','edit'))==set()
    repo=tmp_path/'repo';repo.mkdir()
    (directory/'request.json').write_text(JobRequest(role='investigator',task='Find x',repo=str(repo),idempotency_key='groups').model_dump_json())
    assert await listed(('web','edit'))==set()  # a repository role cannot be widened to web or edit tools
    assert 'read_file' in await listed(('files',))
