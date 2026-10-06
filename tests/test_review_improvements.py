"""Sanitized regressions derived from October 6 frontier reviews."""
import asyncio
import json
import sys
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hub import calls, pipelines, service
from hub.models import Check, JobRequest, Review
from hub.report import final_report
from hub.runner import Runner
from hub.settings import initialize
from hub.skills.coding.delegation import outcomes
from hub.skills.coding.editing import mappings, workspace
from hub.skills.coding.execution.testparse import parse
from hub.skills.coding.validation.validation import analysis_evidence, parsed_log, run_check_sync


def completed(store, repo, key, caller='mcp', role='validator', tokens=0, created=100):
    q=JobRequest(role=role,repo=str(repo),task='Observe evidence',idempotency_key=key,caller=caller,
                 checks=[{'name':'fixture','argv':[sys.executable,'-c','print(1)']}] if role=='validator' else [])
    job=store.submit(q)
    with store.connect() as db:db.execute('UPDATE jobs SET created=? WHERE id=?',(created,job['id']))
    store.finish(job['id'],'completed',{'usage':{'input':tokens,'output':tokens},'checks':[{'status':'failed'}]})
    store.event(job['id'],'inference',{'input':tokens,'output':tokens,'context_limit':16384,'context_tokens':tokens})
    return job


def test_statistics_filters_usage_reviews_events_and_reset_without_deleting(store,repo,monkeypatch):
    monkeypatch.setattr(outcomes,'epoch',lambda:100)
    monkeypatch.setattr('hub.store.pricing',lambda:{'input_per_million':2,'cached_input_per_million':.1,'output_per_million':10})
    a=completed(store,repo,'old',tokens=7,created=99)
    b=completed(store,repo,'real',tokens=10,created=100)
    completed(store,repo,'eval',caller='eval',tokens=1000,created=100)
    completed(store,repo,'end',tokens=20,created=200)
    store.review(b['id'],Review(decision='accepted',baseline_frontier_tokens=10,delegated_frontier_tokens=20,review_effort_seconds=4))
    selected=store.summary(until=200,include_eval=False,include_history=False)
    assert selected['jobs']==1 and selected['reviewed']==1 and selected['accepted']==1
    assert selected['usage']['input']==10 and selected['api_equivalent_usd']==.00012
    assert selected['context_stats']['16384']['requests']==1
    assert selected['role_stats']['validator']['checks']=={'failed':1}
    assert selected['estimated_frontier_tokens_avoided']==-10
    assert selected['measurement_coverage']['review_effort_jobs']==1
    assert store.summary()['jobs']==4 and store.get(a['id']) is not None
    with pytest.raises(ValueError):store.summary(since=200,until=100)


def test_statistics_validator_only_manual_and_missing_pricing(store,repo,monkeypatch):
    job=completed(store,repo,'v',created=100)
    store.review(job['id'],Review(decision='accepted',baseline_frontier_tokens=20,delegated_frontier_tokens=10,measurement_source='manual_estimate'))
    monkeypatch.setattr('hub.store.pricing',lambda:{})
    s=store.summary()
    assert s['usage']['output']==0 and s['api_equivalent_usd'] is None
    assert s['estimated_frontier_tokens_avoided'] is None
    assert s['measurement_coverage']['manual_token_baselines']==1


def test_summary_today_uses_preference_timezone_and_rejects_bad_bounds(store,repo,monkeypatch):
    monkeypatch.setattr('hub.skills.personal.preferences.load',lambda:{'timezone':'Europe/Budapest'})
    today=datetime.now(ZoneInfo('Europe/Budapest')).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
    completed(store,repo,'yesterday',tokens=3,created=today-1)
    completed(store,repo,'today',tokens=5,created=today)
    with TestClient(service.create_app(store,start_workers=False),base_url='http://127.0.0.1:8765') as c:
        headers={'Authorization':'Bearer '+initialize()}
        s=c.get('/api/summary?period=today&include_eval=false',headers=headers).json()
        assert s['jobs']==1 and s['today']['usage']['output']==5 and s['today']['since']==today
        assert s['today']['timezone']=='Europe/Budapest'
        assert c.get('/api/summary?since=200&until=100',headers=headers).status_code==422
        assert c.get('/api/summary?period=today&since=0',headers=headers).status_code==422
        assert c.get('/api/summary?since=nan',headers=headers).status_code==422


def unittest_log():
    return ('FF\n'+'='*70+'\nFAIL: test_guard (test_demo.Demo)\n'+'-'*70+
            '\nTraceback (most recent call last):\n  File "/repo/test_demo.py", line 13, in test_guard\n'
            '    self.assertTrue(guard())\nAssertionError: None is not true\n\n'+'='*70+
            '\nFAIL: test_contract (test_demo.Demo)\n'+'-'*70+
            '\nTraceback (most recent call last):\n  File "/repo/test_demo.py", line 37, in test_contract\n'
            '    self.assertIsInstance(result, list)\nAssertionError: ('+'x'*30000+
            ') is not an instance of list\n\n'+'-'*70+'\nRan 2 tests in 0.016s\n\nFAILED (failures=2)\n')


def test_unittest_failures_survive_large_dump_and_repair_prompt(tmp_path):
    log=tmp_path/'check.log';log.write_text(unittest_log())
    counts,failures=parsed_log(log,[sys.executable,'-m','unittest'],'/repo')
    assert counts['failed']==2 and len(failures)==2
    assert [(f['file'],f['line']) for f in failures]==[('test_demo.py',13),('test_demo.py',37)]
    assert failures[0]['message']=='AssertionError: None is not true'
    assert len(failures[1]['message'])<=300
    text=analysis_evidence([{'name':'demo','status':'failed','counts':counts,'failures':failures,'output_tail':unittest_log()[-10000:]}])
    assert 'None is not true' in text and 'test_contract' in text


def test_unittest_error_pass_and_unknown_formats():
    error='ERROR: test_decode (test_api.Api)\n'+ '-'*70+'\nTraceback (most recent call last):\n  File "api.py", line 7, in decode\n    raise ValueError()\nValueError: bad data\n\nRan 1 test in 0.01s\nFAILED (errors=1)\n'
    parsed=parse(error,argv=['python','-m','unittest'])
    assert parsed['counts']['error']==1 and parsed['failures'][0]['message']=='ValueError: bad data'
    assert parse('Ran 3 tests in 0.01s\n\nOK (skipped=1)')['counts']['passed']==2
    assert parse('unrelated output')['tool']=='unknown'


def test_expected_test_coverage_preserves_exit_code(tmp_path):
    missing=run_check_sync(Check(name='suite',argv=[sys.executable,'-c','print("ignored")'],expected_test_files=['missing.py']),tmp_path)
    assert missing['status']=='blocked' and missing['exit_code'] is None
    zero=run_check_sync(Check(name='suite',argv=[sys.executable,'-c','print("Ran 0 tests in 0.01s\\n\\nOK")'],minimum_tests=1),tmp_path)
    assert zero['status']=='failed' and zero['exit_code']==0 and 'at least 1' in zero['reason']


def test_literal_mapping_scope_counts_semicolons_and_conflicts():
    items=[{'path':'ui.css','old':'.15s ease','new':'var(--duration) var(--ease)','expected_count':2}]
    before={'ui.css':'transition: color .15s ease; transform .15s ease;','other.css':'x'}
    after=mappings.literal_contents(items,before)
    assert after['ui.css'].count('var(--duration)')==2 and 'other.css' not in after
    assert not mappings.verify_literals(items,before,{**before,**after})['unmet']
    assert mappings.verify_literals(items,before,{'ui.css':'transition: color var(--duration) var(--ease); transform .15s ease;'})['unmet']
    with pytest.raises(ValueError):mappings.literal_contents([{**items[0],'expected_count':3}],before)
    with pytest.raises(ValueError):mappings.literal_contents([items[0],items[0]],before)
    assert mappings.LiteralMapping(old='a;b',new='c;d').new=='c;d'


@pytest.mark.asyncio
async def test_literal_editor_private_apply_and_stale_refusal(store,repo):
    request=JobRequest(role='editor',kind='mechanical',execution_mode='literal',repo=str(repo),task='Rename answer',allowed_paths=['app.ts'],
        literal_mappings=[{'path':'app.ts','old':'answer','new':'reply','expected_count':1}],idempotency_key=uuid.uuid4().hex,
        checks=[{'name':'check','argv':[sys.executable,'-c','from pathlib import Path; assert "reply" in Path("app.ts").read_text()']}])
    job=store.submit(request);runner=Runner(store,'token')
    async def no_model(*args,**kwargs):raise AssertionError('Literal jobs never invoke a model')
    runner.execute_model=no_model
    await runner.run(store.next())
    result=store.get(job['id'])['result']
    assert result['worker_status']=='COMPLETE' and result['usage']['output']==0
    assert (repo/'app.ts').read_text()=='export const answer = 41;\n'
    (repo/'app.ts').write_text('user edit\n')
    assert workspace.apply(job['id'])['conflicts']==['app.ts']
    (repo/'app.ts').write_text('export const answer = 41;\n')
    workspace.apply(job['id'])
    assert (repo/'app.ts').read_text()=='export const reply = 41;\n'


@pytest.mark.asyncio
async def test_literal_count_failure_applies_nothing_across_files(store,repo):
    (repo/'other.ts').write_text('const value = 1;\n')
    q=JobRequest(role='editor',kind='mechanical',execution_mode='literal',repo=str(repo),task='Two exact changes',
        allowed_paths=['app.ts','other.ts'],literal_mappings=[{'path':'app.ts','old':'answer','new':'reply','expected_count':1},
        {'path':'other.ts','old':'value','new':'count','expected_count':2}],idempotency_key=uuid.uuid4().hex)
    j=store.submit(q);await Runner(store,'token').run(store.next())
    r=store.get(j['id'])['result']
    assert r['worker_status']=='BLOCKED' and r['acceptance']['diff']['files']==0
    assert (repo/'app.ts').read_text()=='export const answer = 41;\n'


@pytest.mark.asyncio
async def test_model_omitted_typed_transition_is_transactionally_rejected(tmp_path,repo,monkeypatch):
    before='a { transition: color .15s ease; }\nb { transition: transform .15s ease; }\n'
    (repo/'ui.css').write_text(before)
    directory=tmp_path/'edit-job';directory.mkdir();(directory/'workspace').mkdir()
    q=JobRequest(role='editor',kind='mechanical',repo=str(repo),task='Replace every transition',allowed_paths=['ui.css'],
        literal_mappings=[{'path':'ui.css','old':'.15s ease','new':'var(--duration) var(--ease)','expected_count':2}],idempotency_key='typed')
    (directory/'request.json').write_text(q.model_dump_json())
    async def call(*args,**kwargs):
        return {'message':{'content':'FILE: ui.css\n<<<<<<< SEARCH\na { transition: color .15s ease; }\n=======\na { transition: color var(--duration) var(--ease); }\n>>>>>>> REPLACE\nEND OF EDITS'},'done_reason':'stop'}
    monkeypatch.setattr(calls,'call',call)
    await pipelines.run_edit(directory,'edit',q.task,'edit')
    assert (repo/'ui.css').read_text()==before
    report=final_report(json.loads((directory/'edit.session.json').read_text()))
    assert report['status']=='PARTIAL' and 'expected old occurrences' in report['risks']


def test_literal_mode_rejects_missing_counts_scope_and_model_review(repo):
    base=dict(role='editor',kind='mechanical',execution_mode='literal',repo=str(repo),task='Rename',allowed_paths=['app.ts'],idempotency_key='l')
    for extra in ({'literal_mappings':[{'path':'app.ts','old':'answer','new':'reply'}]},
                  {'literal_mappings':[{'path':'elsewhere','old':'answer','new':'reply','expected_count':1}]},
                  {'literal_mappings':[{'path':'app.ts','old':'answer','new':'reply','expected_count':1}],'review_pass':True}):
        with pytest.raises(ValidationError):JobRequest(**base,**extra)


@pytest.mark.asyncio
async def test_investigator_unknown_requirement_cannot_be_complete(tmp_path,repo,monkeypatch):
    directory=tmp_path/'job';directory.mkdir();(directory/'workspace').mkdir()
    task='Does answer enumerate every caller?'
    q=JobRequest(role='investigator',kind='check_requirement',repo=str(repo),read_paths=['app.ts'],task=task,idempotency_key='i')
    (directory/'request.json').write_text(q.model_dump_json())
    outputs=[{'ranges':[{'path':'app.ts','start':1,'end':1}]},
             {'answer':'There are no other callers.','refs':[{'path':'app.ts','line':1,'note':'constant'}],'complete':True,'more':[],
              'requirements':[{'task_quote':task,'status':'unknown','refs':[]}],'unknowns':['Caller coverage not established']}]
    async def call(*args,**kwargs):return {'message':{'content':json.dumps(outputs.pop(0))}}
    monkeypatch.setattr(calls,'call',call)
    await pipelines.run_investigate(directory,'work',task)
    report=final_report(json.loads((directory/'work.session.json').read_text()))
    assert report['status']=='PARTIAL' and 'Caller coverage' in report['risks']
    assert 'Observed: export const answer = 41' in report['findings']
    packet=json.loads((directory/'work.investigation.json').read_text())
    assert packet['semantic_verification']=='frontier_required' and packet['search_scope']=='app.ts'


@pytest.mark.asyncio
async def test_all_callers_packet_includes_direct_backup_path(tmp_path,repo,monkeypatch):
    (repo/'api.py').write_text('def slackDM(email):\n    return email\n')
    (repo/'notify.py').write_text('from api import slackDM\ndef notify(email):\n    return slackDM(email)\n')
    (repo/'backup.py').write_text('from api import slackDM\ndef backup(email):\n    return slackDM(email)\n')
    directory=tmp_path/'caller-job';directory.mkdir();(directory/'workspace').mkdir()
    task='Find all callers of slackDM including backup'
    q=JobRequest(role='investigator',kind='check_requirement',repo=str(repo),task=task,read_paths=['api.py','notify.py','backup.py'],idempotency_key='caller')
    (directory/'request.json').write_text(q.model_dump_json())
    outputs=[{'ranges':[{'path':'notify.py','start':1,'end':3}]},
             {'answer':'notify and backup both call slackDM.','refs':[{'path':'backup.py','line':3,'note':'direct call'}],'complete':True,'more':[],
              'requirements':[{'task_quote':task,'status':'met','refs':[{'path':'backup.py','line':3,'note':'direct backup'},{'path':'notify.py','line':3,'note':'normal call'}]}]}]
    async def call(*args,**kwargs):return {'message':{'content':json.dumps(outputs.pop(0))}}
    monkeypatch.setattr(calls,'call',call)
    await pipelines.run_investigate(directory,'work',task)
    packet=json.loads((directory/'work.investigation.json').read_text())
    assert {'backup.py','notify.py'} <= {h['path'] for h in packet['reference_hits']}
    assert packet['unread_reference_hits']==[]
    assert final_report(json.loads((directory/'work.session.json').read_text()))['status']=='COMPLETE'


def test_pilot_keeps_missing_measurements_unknown_and_negative_totals():
    from benchmarks.eval_frontier_work import cases,measurement_summary
    assert len(cases())==12
    assert measurement_summary([{}])['frontier_tokens_avoided'] is None
    rows=[{'baseline_frontier_tokens':10,'delegated_frontier_tokens':20,'baseline_frontier_effort_seconds':2,'delegated_frontier_effort_seconds':7}]
    assert measurement_summary(rows)['frontier_tokens_avoided']==-10
    assert measurement_summary(rows)['frontier_effort_seconds_avoided']==-5
