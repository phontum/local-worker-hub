import json
import sys
from pathlib import Path

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
            (Path(_request.repo)/'app.ts').write_text('export const answer = 43;\n')
            result=report('COMPLETE','Made initial edit.')
        elif label.startswith('repair-'):
            (Path(_request.repo)/'app.ts').write_text('export const answer = '+('42' if label=='repair-'+str(repairs) else '44')+';\n')
            result=report('COMPLETE','Made targeted repair.')
        elif label.startswith('diagnose-'):
            result=report('PARTIAL','The previous edit still returns the wrong value.')
        else:
            current=(Path(_request.repo)/'app.ts').read_text()
            result=report('COMPLETE' if '42' in current else 'PARTIAL','Source and check agree.' if '42' in current else 'The answer is still wrong.')
        return {'info':{'tokens':{}}},result
    runner=Runner(store,'token');runner.execute_model=fake_model
    await runner.run(store.next())
    result=store.get(job['id'])['result']
    assert result['worker_status']=='COMPLETE'
    assert len(result['attempts'])==expected_attempts
    assert result['checks'][0]['exit_code']==0
    assert (repo/'app.ts').read_text()=='export const answer = 41;\n' and result['workspace']['origin_unchanged']  # the user's tree waits for apply_result
    from hub import workspace
    assert workspace.apply(job['id'])['applied']==['app.ts'] and (repo/'app.ts').read_text()=='export const answer = 42;\n'
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
            if edit_value:(Path(_request.repo)/'app.ts').write_text('export const answer = '+edit_value+';\n')
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


@pytest.mark.asyncio
async def test_private_workspace_yields_patch_acceptance_packet_and_untouched_origin(repo,store):
    from hub.settings import STATE
    job=store.submit(implement_request(repo,key='private'));calls=[];runner=Runner(store,'token');runner.execute_model=scripted(repo,calls)
    await runner.run(store.next());stored=store.get(job['id']);result=stored['result']
    assert (repo/'app.ts').read_text()=='export const answer = 41;\n'
    assert result['repo']==str(repo) and result['workspace']['state']=='ready' and result['workspace']['origin_unchanged']
    patch=(STATE/'jobs'/job['id']/'patch.diff').read_text()
    assert '-export const answer = 41;' in patch and '+export const answer = 42;' in patch
    packet=result['acceptance']
    assert packet['scope_ok'] and packet['diff']=={'files':1,'added':1,'removed':1} and packet['next_action']=='review_patch_then_apply_result'
    assert packet['checks'][0]['status']=='passed' and packet['changed_files'][0]['path']=='app.ts'
    assert any('Source changed without a test change' in note for note in packet['review_focus'])
    assert result['completion']['next_action']=='review_patch_then_apply_result'


@pytest.mark.asyncio
async def test_apply_is_refused_when_the_user_edited_the_file_during_the_job(repo,store):
    from hub import workspace
    job=store.submit(implement_request(repo,key='conflict'));runner=Runner(store,'token');runner.execute_model=scripted(repo,[])
    await runner.run(store.next())
    (repo/'app.ts').write_text('export const answer = 99; // user edit\n')
    assert workspace.apply(job['id'])['conflicts']==['app.ts']
    assert (repo/'app.ts').read_text()=='export const answer = 99; // user edit\n'


@pytest.mark.asyncio
async def test_in_place_opt_out_edits_the_repository_directly(repo,store):
    job=store.submit(implement_request(repo,key='inplace',in_place=True))
    async def fake_model(_job,_request,_directory,label,prompt,**kwargs):
        assert _request.repo==str(repo)
        (Path(_request.repo)/'app.ts').write_text('export const answer = 42;\n')
        return {'info':{'tokens':{}}},report('COMPLETE','Edited.')
    runner=Runner(store,'token');runner.execute_model=fake_model
    await runner.run(store.next());result=store.get(job['id'])['result']
    assert (repo/'app.ts').read_text()=='export const answer = 42;\n' and 'workspace' not in result and result['worker_status']=='COMPLETE'


def test_in_place_is_editor_only(repo):
    with pytest.raises(ValueError):
        JobRequest(role='investigator',repo=str(repo),task='x',idempotency_key='ip',in_place=True)


@pytest.mark.asyncio
async def test_the_final_report_lists_what_really_changed_even_when_the_repair_turn_changed_nothing(repo,store):
    # Incident shape: the edit turn changed a file, the repair turn applied nothing and reported "No change was applied".
    (repo/'b.ts').write_text('export const b = 1;\n')
    check=Check(name='never',argv=[sys.executable,'-c','raise SystemExit(1)'])
    job=store.submit(JobRequest(role='editor',workflow='implement',repair_attempts=1,repo=str(repo),task='Edit both',allowed_paths=['app.ts','b.ts'],checks=[check],
                                idempotency_key='cumulative',timeout=60,in_place=True))
    async def fake_model(_job,_request,_directory,label,prompt,**kwargs):
        if label=='edit':
            (Path(_request.repo)/'app.ts').write_text('export const answer = 42;\n')
            return {'info':{'tokens':{}}},report('PARTIAL','Applied 1 file(s): app.ts.')
        return {'info':{'tokens':{}}},report('PARTIAL','No edits were applied (b.ts: SEARCH does not match).')
    runner=Runner(store,'token');runner.execute_model=fake_model
    await runner.run(store.next());result=store.get(job['id'])['result']
    assert result['worker_status']=='PARTIAL'
    text=result['report']
    assert 'Changed 1 file(s) (+1 -1): app.ts' in text and 'app.ts (+1 -1)' in text
    assert '- edit (PARTIAL): Applied 1 file(s): app.ts.' in text and '- repair-1 (PARTIAL): No edits were applied' in text
    assert text.count('No authorized file changed') == 0
    packet=result['acceptance']
    assert packet['workspace']=='in_place' and packet['diff']['files']==1 and packet['next_action']=='frontier_decision' and packet['repair_used']


@pytest.mark.asyncio
async def test_a_repair_that_cannot_finish_in_the_remaining_time_is_skipped_not_cut_off(repo,store):
    job=store.submit(implement_request(repo,key='budget',execution_preset='work',repair_attempts=1));calls=[];runner=Runner(store,'token')
    async def model(_job,_request,_directory,label,prompt,**kwargs):
        calls.append(label)
        (Path(_request.repo)/'app.ts').write_text('export const answer = 43;\n')  # check wants 42
        runner.model_remaining[_job['id']]=10  # almost no model time left after the first turn
        return {'info':{'tokens':{}}},report('COMPLETE','Edited.')
    runner.execute_model=model
    await runner.run(store.next());stored=store.get(job['id']);result=stored['result']
    assert calls==['edit'] and stored['state']=='completed' and result['worker_status']=='PARTIAL'
    assert 'No repair was attempted: 10s of model time left' in result['report'] and 'Checks failed: answer' in result['report']
    assert result['acceptance']['diff']['files']==1  # the patch is kept for the frontier
