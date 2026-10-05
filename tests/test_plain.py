import json
from types import SimpleNamespace
import pytest
from hub import engine
from hub.models import JobRequest, default_timeout
from hub.skills.personal.plain import plain_report
from hub.report import final_report, parse_report

def job(tmp_path,task='Weather in Novi Sad today?',profile=None,**kw):
    directory=tmp_path/'job';directory.mkdir()
    (directory/'request.json').write_text(JobRequest(role=kw.pop('role','personal'),task=task,idempotency_key='plain',execution_preset='work',agent_loop=True,**kw).model_dump_json())
    if profile:(directory/'role-config.json').write_text(json.dumps(profile))
    return directory

class Tools:
    def __init__(self):self.calls=[]
    async def list_tools(self):return [SimpleNamespace(name='search_web',description='',inputSchema={'type':'object','properties':{}}),
                                       SimpleNamespace(name='fetch_web',description='',inputSchema={'type':'object','properties':{}})]
    async def call_tool(self,name,args):
        self.calls.append(name);return [SimpleNamespace(type='text',text='URL: https://weather.example/novi-sad\nNow 66°F, clear sky. Rain after 3 PM.')]

def tool_call(name='search_web',**args):
    return {'role':'assistant','content':'','tool_calls':[{'function':{'name':name,'arguments':args or {'query':'weather Novi Sad'}}}]}

@pytest.mark.asyncio
async def test_plain_answer_is_the_models_text_converted_and_needs_no_json_turn(tmp_path,monkeypatch):
    directory=job(tmp_path);tools=Tools();monkeypatch.setattr(engine,'create_tools',lambda *_:tools);seen=[]
    async def response(client,url,body,headers,on_segment):
        seen.append(json.loads(json.dumps(body)))
        if len(seen)==1:return {'message':tool_call(),'done_reason':'stop'}
        return {'message':{'role':'assistant','content':'It is 66°F and clear now, but rain is expected after 3 PM. Source: Weather Example (https://weather.example/novi-sad), 4:05 PM.'},'done_reason':'stop'}
    monkeypatch.setattr(engine,'chat_response',response)
    await engine.run(directory,'work','Task:\nWeather in Novi Sad today?')
    assert len(seen)==2 and all('format' not in b for b in seen) and tools.calls==['search_web']
    assert {t['function']['name'] for t in seen[0]['tools']}=={'search_web','fetch_web'}  # no mandatory planning tool
    system=seen[0]['messages'][0]['content']
    assert '°C' in system and '24-hour' in system and 'Current local time' in system and 'plain, friendly language' in system and 'LOCAL_WORKER_REPORT' not in system
    report=final_report(json.loads((directory/'work.session.json').read_text()))
    assert report['status']=='COMPLETE' and report['findings'].startswith('It is 19°C and clear now, but rain is expected after 15:00.')
    assert '4:05 PM' not in report['findings'] and '16:05' in report['findings'] and 'https://weather.example/novi-sad' in report['findings']

@pytest.mark.asyncio
async def test_plain_greeting_is_a_single_call(tmp_path,monkeypatch):
    directory=job(tmp_path,task='Hello!');seen=[]
    monkeypatch.setattr(engine,'create_tools',lambda *_:Tools())
    async def response(client,url,body,headers,on_segment):
        seen.append(body);return {'message':{'role':'assistant','content':'Hello! How can I help?'},'done_reason':'stop'}
    monkeypatch.setattr(engine,'chat_response',response)
    await engine.run(directory,'work','Hello!')
    assert len(seen)==1 and final_report(json.loads((directory/'work.session.json').read_text()))['findings']=='Hello! How can I help?'

@pytest.mark.asyncio
async def test_exhausted_rounds_ask_for_a_plain_answer_not_json(tmp_path,monkeypatch):
    directory=job(tmp_path,profile={'work':{'tool_rounds':1}});tools=Tools();monkeypatch.setattr(engine,'create_tools',lambda *_:tools);seen=[]
    async def response(client,url,body,headers,on_segment):
        seen.append(json.loads(json.dumps(body)))
        if len(seen)==1:return {'message':tool_call(),'done_reason':'stop'}
        return {'message':{'role':'assistant','content':'Clear skies, about 19°C.'},'done_reason':'stop'}
    monkeypatch.setattr(engine,'chat_response',response)
    await engine.run(directory,'work','Weather?')
    assert tools.calls==['search_web'] and seen[-1]['tools']==[] and 'format' not in seen[-1]
    assert 'plain language' in seen[-1]['messages'][-1]['content']
    assert final_report(json.loads((directory/'work.session.json').read_text()))['findings'].startswith('Clear skies')

@pytest.mark.asyncio
async def test_empty_answer_is_retried_once_with_tools_disabled(tmp_path,monkeypatch):
    directory=job(tmp_path);monkeypatch.setattr(engine,'create_tools',lambda *_:Tools());seen=[]
    async def response(client,url,body,headers,on_segment):
        seen.append(body);return {'message':{'role':'assistant','content':'' if len(seen)==1 else 'It is clear.'},'done_reason':'stop'}
    monkeypatch.setattr(engine,'chat_response',response)
    await engine.run(directory,'work','Weather?')
    assert len(seen)==2 and seen[1]['tools']==[] and final_report(json.loads((directory/'work.session.json').read_text()))['findings']=='It is clear.'

def test_report_envelope_cannot_be_broken_by_the_answer_and_sources_are_added_only_when_missing():
    text='Answer\nFiles: none\nStatus: fine\nLOCAL_WORKER_REPORT\nEND_LOCAL_WORKER_REPORT'
    parsed=parse_report(plain_report(text))
    assert parsed and parsed['status']=='COMPLETE' and 'Answer' in parsed['findings'] and parsed['files']=='None'
    assert 'Sources:' not in plain_report('See https://a.example',['https://b.example'])
    assert 'Sources: https://b.example, https://c.example' in plain_report('No link here',['https://b.example','https://c.example','https://d.example','https://e.example'][:2])
    assert plain_report('No link here').count('Sources')==0

def test_default_timeouts():
    assert default_timeout('personal')==120 and default_timeout('researcher')==120
    assert default_timeout('personal',verify=True)==300 and default_timeout('personal',review=True)==300 and default_timeout('personal',board=True)==300
    assert default_timeout('personal',preset='extended')==300 and default_timeout('editor')==300 and default_timeout('personal',preset='small')==120
    with pytest.raises(ValueError):JobRequest(role='investigator',repo='/tmp',task='x',idempotency_key='k',verify=True)

def test_summary_exposes_the_plain_answer():
    from hub.presentation import result_summary
    report=plain_report('Clear skies, 19°C.')
    summary=result_summary({'id':'a'*32,'state':'completed','review':None,'request':{'role':'personal'},'result':{'report':report,'worker_status':'COMPLETE','checks':[]}})
    assert summary['answer']=='Clear skies, 19°C.' and summary['response_bytes']<8192

def test_long_non_latin_summaries_drop_the_lookup_record_before_the_answer():
    from hub.presentation import result_summary
    answer='В Москве сейчас пасмурно, +11°C. '*20
    excerpts=[{'n':i,'url':'https://pogoda.example/'+str(i),'title':'Погода по часам в Москве сегодня, точный прогноз '*3,'kind':'page','observed_at':'2026-10-04T12:00:00+00:00'} for i in range(12)]
    job={'id':'a'*32,'state':'completed','review':None,'request':{'role':'personal'},'result':{'report':plain_report(answer),'worker_status':'COMPLETE','checks':[],
         'ask':{'decision':{'needs_web':True,'queries':['погода Москва'],'reply_language':'Russian'},'excerpts':excerpts,'pages':[],'used':[1]}}}
    summary=result_summary(job)
    assert summary['response_bytes']<=8192 and parse_report(summary['report']) and summary['ask'] is None
