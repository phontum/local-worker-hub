import json
import sys

import pytest

from hub.models import Check, JobRequest
from hub.report import parse_report
from hub.runner import Runner
from hub.scoped import ScopedFiles, ScopeError, create_tools


def report(status, finding):
    return parse_report('LOCAL_WORKER_REPORT\nStatus: '+status+'\nFindings:\n'+finding+
                        '\nFiles:\napp.ts\nChecks:\nNot run.\nRisks:\nReview required.\nEND_LOCAL_WORKER_REPORT')


def test_internal_freshness_and_bounded_reads(repo):
    request=JobRequest(role='editor',repo=str(repo),task='Edit app',idempotency_key='fresh',allowed_paths=['app.ts'])
    scope=ScopedFiles(request)
    files=json.loads(scope.read_files(['app.ts','missing.ts']))['files']
    assert files[0]['evidence_id']=='E1' and 'error' in files[1]
    scope.edit_file('app.ts','41','42')
    assert '42' in (repo/'app.ts').read_text()
    scope.read_file('app.ts')
    (repo/'app.ts').write_text('user updated it\n')
    with pytest.raises(ScopeError):scope.edit_file('app.ts','42','43')
    assert (repo/'app.ts').read_text()=='user updated it\n'


@pytest.mark.asyncio
async def test_review_phase_cannot_edit(tmp_path,repo):
    directory=tmp_path/'job';directory.mkdir()
    request=JobRequest(role='editor',repo=str(repo),task='Edit',allowed_paths=['app.ts'],idempotency_key='phases')
    (directory/'request.json').write_text(request.model_dump_json())
    review={tool.name for tool in await create_tools(directory,'review').list_tools()}
    edit={tool.name for tool in await create_tools(directory,'edit').list_tools()}
    assert 'read_file' in review and 'edit_file' not in review and 'write_file' not in review
    assert 'edit_file' in edit and 'write_file' in edit


@pytest.mark.asyncio
async def test_job_snapshots_role_configuration(repo,store,monkeypatch,tmp_path):
    import hub.runner as runner_module
    config=tmp_path/'config';config.mkdir()
    (config/'roles.json').write_text('{"editor":{"context":16384,"thinking":true}}')
    monkeypatch.setattr(runner_module,'CONFIG',config)
    request=JobRequest(role='validator',repo=str(repo),task='Check fixture',
        checks=[Check(name='ok',argv=[sys.executable,'-c','print("ok")'])],
        idempotency_key='snapshot',model_context=32768,model_thinking=False)
    job=store.submit(request)
    runner=Runner(store,'token')
    await runner.run(store.next())
    from hub.settings import STATE
    snapshot=STATE/'jobs'/job['id']/'role-config.json'
    assert json.loads(snapshot.read_text())['editor']['context']==16384
    assert store.get(job['id'])['result']['checks'][0]['exit_code']==0


@pytest.mark.asyncio
@pytest.mark.parametrize('repairs,expected_attempts',[(1,2),(2,3)])
async def test_bounded_workflow_repairs_and_rechecks(repo,store,repairs,expected_attempts):
    check=Check(name='answer',argv=[sys.executable,'-c',
        "from pathlib import Path; assert '42' in Path('app.ts').read_text()"])
    request=JobRequest(role='editor',workflow='implement',repair_attempts=repairs,
        repo=str(repo),task='Set answer to 42',allowed_paths=['app.ts'],checks=[check],
        idempotency_key='repair-'+str(repairs),timeout=60)
    job=store.submit(request)
    calls=[]
    async def fake_model(_job,_request,_directory,label,_prompt,**kwargs):
        calls.append((label,kwargs.get('phase')))
        if label=='edit':
            (repo/'app.ts').write_text('export const answer = 43;\n')
            result=report('COMPLETE','Made initial edit.')
        elif label.startswith('repair-'):
            (repo/'app.ts').write_text('export const answer = '+('42' if label=='repair-'+str(repairs) else '44')+';\n')
            result=report('COMPLETE','Made targeted repair.')
        elif label.startswith('diagnose-'):
            result=report('PARTIAL','The previous edit still returns the wrong value.')
        else:
            result=report('COMPLETE' if '42' in (repo/'app.ts').read_text() else 'PARTIAL',
                          'Source and check agree.' if '42' in (repo/'app.ts').read_text() else 'The answer is still wrong.')
        return {'info':{'tokens':{}}},result
    runner=Runner(store,'token');runner.execute_model=fake_model
    await runner.run(store.next())
    result=store.get(job['id'])['result']
    assert result['worker_status']=='COMPLETE'
    assert len(result['attempts'])==expected_attempts
    assert result['checks'][0]['exit_code']==0
    assert (repo/'app.ts').read_text()=='export const answer = 42;\n'
    # Checks decide: no review or model diagnosis runs unless requested.
    assert all(kind not in ('diagnose','review') for _,kind in calls)


def implement_request(repo,**kw):
    check=Check(name='answer',argv=[sys.executable,'-c',"from pathlib import Path; assert '42' in Path('app.ts').read_text()"])
    return JobRequest(role='editor',workflow='implement',repo=str(repo),task='Set answer to 42',allowed_paths=['app.ts'],checks=[check],timeout=60,
                      idempotency_key=kw.pop('key'),**kw)

def scripted(repo,calls,edit_value='42',editor_status='COMPLETE',review_status='PARTIAL'):
    async def fake_model(_job,_request,_directory,label,prompt,**kwargs):
        calls.append((label,kwargs.get('phase'),prompt))
        if label in ('edit',) or label.startswith('repair-'):
            if edit_value:(repo/'app.ts').write_text('export const answer = '+edit_value+';\n')
            return {'info':{'tokens':{}}},report(editor_status,'Edited.')
        return {'info':{'tokens':{}}},report(review_status,'The review dislikes the style.')
    return fake_model

@pytest.mark.asyncio
async def test_passing_checks_are_complete_even_if_the_editor_hedged(repo,store):
    job=store.submit(implement_request(repo,key='hedge'));calls=[];runner=Runner(store,'token');runner.execute_model=scripted(repo,calls,editor_status='PARTIAL')
    await runner.run(store.next());result=store.get(job['id'])['result']
    assert result['worker_status']=='COMPLETE' and [c[0] for c in calls]==['edit'] and result['attempts'][0]['review_status']=='skipped'

@pytest.mark.asyncio
async def test_requested_review_is_advisory_and_cannot_downgrade_passing_checks(repo,store):
    job=store.submit(implement_request(repo,key='advisory',review_pass=True));calls=[];runner=Runner(store,'token');runner.execute_model=scripted(repo,calls,review_status='PARTIAL')
    await runner.run(store.next());result=store.get(job['id'])['result']
    assert [c[0] for c in calls]==['edit','review-0'] and result['worker_status']=='COMPLETE'
    assert 'Local review (advisory): The review dislikes the style.' in result['report']

@pytest.mark.asyncio
async def test_failed_checks_repair_from_check_output_not_review_and_end_partial(repo,store):
    job=store.submit(implement_request(repo,key='fail',repair_attempts=1,review_pass=True));calls=[];runner=Runner(store,'token');runner.execute_model=scripted(repo,calls,edit_value='43',review_status='COMPLETE')
    await runner.run(store.next());result=store.get(job['id'])['result']
    labels=[c[0] for c in calls]
    assert labels==['edit','review-0','repair-1','review-1']
    assert 'The approved checks failed' in calls[2][2] and 'The review dislikes' not in calls[2][2]
    assert result['worker_status']=='PARTIAL' and 'Checks failed: answer' in result['report'] and result['checks_failed']

@pytest.mark.asyncio
async def test_no_authorized_change_is_never_complete(repo,store):
    (repo/'app.ts').write_text('export const answer = 42;\n')
    job=store.submit(implement_request(repo,key='nochange'));calls=[];runner=Runner(store,'token');runner.execute_model=scripted(repo,calls,edit_value=None)
    await runner.run(store.next());result=store.get(job['id'])['result']
    assert result['worker_status']=='PARTIAL' and 'No authorized file change was made' in result['report']
