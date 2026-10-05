import json
import pytest
from hub.models import JobRequest
from hub.runner import Runner
from hub.settings import STATE

def envelope(findings):
    return f'LOCAL_WORKER_REPORT\nStatus: COMPLETE\nFindings:\n{findings}\nFiles:\nNone\nChecks:\nNot run\nRisks:\nNone identified.\nEND_LOCAL_WORKER_REPORT'

async def run_with(store,repo,findings,events):
    job=store.submit(JobRequest(role='investigator',repo=str(repo),task='Find x',idempotency_key=str(abs(hash(findings+json.dumps(events))))));store.next()
    directory=STATE/'jobs'/job['id'];directory.mkdir(parents=True,exist_ok=True)
    (directory/'workspace').mkdir(exist_ok=True)
    runner=Runner(store,'token')
    async def fake_process(argv,job_,directory_,label,env,timeout):
        session={'info':{'outcome':'succeeded','location':{'directory':str(directory/'workspace')},'id':'s'},
                 'messages':[{'type':'user','text':'Find x'},{'type':'assistant','finish':'stop','content':[{'type':'text','text':envelope(findings)}]}]}
        (directory/(label+'.session.json')).write_text(json.dumps(session))
        (directory/'tools.jsonl').write_text(''.join(json.dumps({'time':0,'phase':'work','kind':kind,'data':data})+'\n' for kind,data in events))
    runner.process=fake_process
    _,report=await runner.execute_model(job,JobRequest.model_validate(job['request']),directory,'work','prompt')
    return report

READ=('read',{'evidence_id':'E1','path':'app.ts','start':1})

@pytest.mark.asyncio
async def test_correct_answer_without_citations_stays_complete_and_lists_what_was_read(store,repo):
    report=await run_with(store,repo,'The answer is 41 in app.ts.',[READ])
    assert report['status']=='COMPLETE' and 'E1 app.ts:1' in report['files']

@pytest.mark.asyncio
async def test_fabricated_evidence_id_is_downgraded(store,repo):
    report=await run_with(store,repo,'See E9 for the value.',[READ])
    assert report['status']=='PARTIAL' and 'never observed' in report['risks']

@pytest.mark.asyncio
async def test_nothing_read_is_downgraded(store,repo):
    report=await run_with(store,repo,'I believe it is 41.',[])
    assert report['status']=='PARTIAL' and 'No file evidence' in report['risks']

@pytest.mark.asyncio
async def test_a_partly_missing_read_keeps_complete_but_names_the_gap(store,repo):
    report=await run_with(store,repo,'Found it (E1).',[READ,('read_missing',{'path':'gone.ts','error':'x'})])
    assert report['status']=='COMPLETE' and 'gone.ts' in report['risks'] and 'E1 app.ts:1' in report['files']
