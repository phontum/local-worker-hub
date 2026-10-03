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
    assert ('diagnose-1','diagnose') in calls if repairs==2 else all(kind!='diagnose' for _,kind in calls)
