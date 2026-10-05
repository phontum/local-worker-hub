import json
import sys
import pytest
from hub import cli, client
from hub.models import JobRequest

ID='a'*32

def fake(monkeypatch,responses):
    seen=[]
    def call(method,path,data=None):
        seen.append((method,path,data))
        for marker,value in responses:
            if marker in path:
                return value.pop(0) if isinstance(value,list) and len(value)>1 else (value[0] if isinstance(value,list) else value)
        raise AssertionError('unexpected '+path)
    monkeypatch.setattr(client,'call',call);monkeypatch.setattr(cli.time,'sleep',lambda s:None)
    return seen

def run(monkeypatch,*argv):
    monkeypatch.setattr(sys,'argv',['local-worker',*argv])
    try:cli.main();return 0
    except SystemExit as exit_:return exit_.code

@pytest.mark.parametrize('status,error,code',[('COMPLETE',None,0),('PARTIAL',None,2)])
def test_delegate_prints_only_one_compact_brief_and_maps_exit_codes(monkeypatch,capsys,repo,status,error,code):
    brief={'id':ID,'state':'completed','status':status,'findings':'Found it','changed_files':[],'checks':[],'next_action':'frontier_review'}
    seen=fake(monkeypatch,[('/api/jobs/'+ID+'/result',brief),('/api/jobs/'+ID,[{'id':ID,'state':'running'},{'id':ID,'state':'completed'}]),('/api/jobs',{'id':ID,'state':'queued'})])
    monkeypatch.chdir(repo)
    assert run(monkeypatch,'delegate','--read-only','Find x')==code
    out,err=capsys.readouterr()
    assert json.loads(out)==brief and out.count('\n')==1 and err==''
    assert JobRequest.model_validate(seen[0][2]).role=='investigator'

def test_delegate_timeout_exit_code(monkeypatch,capsys,repo):
    fake(monkeypatch,[('/api/jobs/'+ID+'/result',{'id':ID,'state':'timed_out','status':None,'error':'slow'}),('/api/jobs/'+ID,{'id':ID,'state':'timed_out'}),('/api/jobs',{'id':ID,'state':'queued'})])
    monkeypatch.chdir(repo)
    assert run(monkeypatch,'delegate','--read-only','Find x')==124

def test_watch_prints_one_line_per_phase_change_then_done(monkeypatch,capsys):
    progress=[{'id':ID,'state':'queued','phase':'queued','elapsed_seconds':0},{'id':ID,'state':'running','phase':'model','elapsed_seconds':3,'eta_seconds':40},
              {'id':ID,'state':'running','phase':'model','elapsed_seconds':6},{'id':ID,'state':'completed','phase':'completed','elapsed_seconds':9}]
    fake(monkeypatch,[('/progress',progress),('/result',{'id':ID,'state':'completed','status':'COMPLETE'})])
    assert run(monkeypatch,'watch',ID)==0
    lines=capsys.readouterr().out.strip().splitlines()
    assert lines==['queued (queued, 0s elapsed)','model (running, 3s elapsed, ~40s left)','completed (completed, 9s elapsed)','DONE completed COMPLETE']

def test_watch_rejects_a_malformed_job_id(monkeypatch):
    fake(monkeypatch,[])
    assert run(monkeypatch,'watch','../etc')==4

def test_default_personal_timeout_and_verify_flag(monkeypatch,repo):
    seen=fake(monkeypatch,[('/api/jobs',{'id':ID,'state':'queued'})]);monkeypatch.chdir(repo)
    assert run(monkeypatch,'--async','What is a mutex?')==0
    plain=JobRequest.model_validate(seen[0][2]);assert plain.timeout==120 and not plain.verify
    assert run(monkeypatch,'--async','--verify','Cheapest card in stock?')==0
    strict=JobRequest.model_validate(seen[1][2]);assert strict.timeout==300 and strict.verify
    assert run(monkeypatch,'--async','--read-only','--verify','x')==2  # verify is for public web roles only

def test_model_flag_reaches_the_request_and_unknown_aliases_are_refused(monkeypatch,capsys):
    seen=fake(monkeypatch,[('/api/jobs',{'id':ID,'state':'queued'})])
    run(monkeypatch,'--async','--model','qwen','hello')
    posted=[data for method,path,data in seen if method=='POST' and path=='/api/jobs']
    assert posted and posted[0]['model']=='qwen' and posted[0]['role']=='personal'
    assert run(monkeypatch,'--async','--model','gpt-9','hello')==2
    assert 'Unknown local model alias' in capsys.readouterr().err
