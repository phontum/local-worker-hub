"""Twelve paired tool exercises and an explicit frontier measurement sheet.

Direct tool timings are not frontier effort. Independent frontier review and
paired usage/effort observations are required before claiming net savings.
All runtime evidence belongs in --out, outside public source.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from hub.client import call
from hub.models import JobRequest
from hub.skills.coding.editing import mappings
from hub.skills.coding.validation.validation import run_check_sync


FILES={
    'ui.css':'a { transition: color .15s ease; }\nb { transition: transform .15s ease; }\n',
    'api.py':'def scenario():\n    return (0, {}, {})\n\ndef guard(value):\n    if value != "local":\n        raise RuntimeError("local only")\n',
    'calls.py':'from api import scenario\n\ndef normal():\n    return scenario()\n\ndef backup():\n    return scenario()\n',
    'events.py':'def evaluate(blocks):\n    first = blocks[0]\n    return first\n',
    'store.sql':'INSERT INTO notices (user_id, kind, dedupe_key) VALUES (1, 2, 3)\nON CONFLICT (user_id, kind, dedupe_key) DO NOTHING;\n',
    'unrelated.txt':'preserve this user content\n',
}


def cases():
    return [
        {'name':'validation-pass','role':'validator','check':{'name':'pass','argv':[sys.executable,'-c','print("Ran 3 tests in 0.01s\\n\\nOK")'],'minimum_tests':3},'expected':'COMPLETE'},
        {'name':'validation-fail','role':'validator','check':{'name':'fail','argv':[sys.executable,'-c','print("deliberate failure");raise SystemExit(1)']},'expected':'PARTIAL'},
        {'name':'validation-missing-suite','role':'validator','check':{'name':'missing','argv':[sys.executable,'-c','print("ignored file")'],'expected_test_files':['missing.py']},'expected':'PARTIAL'},
        {'name':'validation-zero-tests','role':'validator','check':{'name':'zero','argv':[sys.executable,'-c','print("Ran 0 tests in 0.01s\\n\\nOK")'],'minimum_tests':1},'expected':'PARTIAL'},
        {'name':'edit-css-all','role':'editor','mapping':{'path':'ui.css','old':'.15s ease','new':'var(--duration) var(--ease)','expected_count':2},'expected':'COMPLETE'},
        {'name':'edit-return-contract','role':'editor','mapping':{'path':'api.py','old':'return (0, {}, {})','new':'return (1, {}, {})','expected_count':1},'expected':'COMPLETE'},
        {'name':'edit-count-mismatch','role':'editor','mapping':{'path':'ui.css','old':'.15s ease','new':'var(--duration) var(--ease)','expected_count':3},'expected':'BLOCKED'},
        {'name':'edit-preserve-old-substring','role':'editor','mapping':{'path':'api.py','old':'local only','new':'local only: demo','expected_count':1},'expected':'COMPLETE'},
        {'name':'investigate-contract','role':'investigator','task':'Explain the exact return value of scenario in api.py and whether guard returns a boolean or raises. Cite implementation bodies for both.','scope':['api.py'],'words':['return (0, {}, {})','raise RuntimeError']},
        {'name':'investigate-all-callers','role':'investigator','task':'Find every caller of scenario in calls.py, including the direct backup path. Cite both functions and do not infer absence outside this scope.','scope':['api.py','calls.py'],'words':['normal','backup']},
        {'name':'investigate-first-block','role':'investigator','task':'Explain whether evaluate in events.py processes every block or returns only the first block. Cite the selection and return.','scope':['events.py'],'words':['blocks[0]']},
        {'name':'investigate-dedupe','role':'investigator','task':'Explain the exact deduplication key in store.sql. Identify all three columns and the conflict behavior.','scope':['store.sql'],'words':['user_id','kind','dedupe_key','DO NOTHING']},
    ]


def fixture(path):
    path.mkdir(parents=True)
    for name,text in FILES.items():(path/name).write_text(text)


def wait(ident,timeout):
    started=time.monotonic()
    while time.monotonic()-started<timeout:
        status=call('GET',f'/api/jobs/{ident}/wait?timeout_seconds=25')
        if status['state'] not in ('queued','running'):return
    call('POST',f'/api/jobs/{ident}/cancel')
    raise TimeoutError('Pilot job exceeded bounded wall time')


def run_case(root,case):
    name=case['name'];direct=root/name/'direct';local=root/name/'local'
    fixture(direct);fixture(local)
    role=case['role'];fields={};direct_check=None
    started=time.monotonic()
    if role=='validator':
        from hub.models import Check
        direct_check=run_check_sync(Check.model_validate(case['check']),direct)
        task='Run this explicitly approved check. Report exact outcomes; failures are intentional fixtures.'
        fields={'checks':[case['check']]}
    elif role=='editor':
        task='Perform only the explicit replacements, preserving unrelated content.'
        before={p:(direct/p).read_text() for p in FILES}
        try:
            for p,text in mappings.literal_contents([case['mapping']],before).items():(direct/p).write_text(text)
        except ValueError:pass  # the negative count fixture must leave both trees untouched
        fields={'execution_mode':'literal','allowed_paths':[case['mapping']['path']],'literal_mappings':[case['mapping']]}
    else:
        task=case['task'];fields={'read_paths':case['scope']}
        (root/name/'direct-evidence.json').write_text(json.dumps({p:(direct/p).read_text() for p in case['scope']}))
    direct_seconds=time.monotonic()-started
    request=JobRequest(role=role,kind='run_tests' if role=='validator' else 'mechanical' if role=='editor' else 'explain',
                       repo=str(local),task=task,caller='benchmark',handoff_id=root.name+'-'+name,
                       execution_preset='work',timeout=120,idempotency_key=uuid.uuid4().hex,**fields)
    started=time.monotonic();ident=call('POST','/api/jobs',request.model_dump())['id'];wait(ident,180)
    result=call('GET',f'/api/jobs/{ident}/result?view=full')
    (root/name/'local-result.json').write_text(json.dumps(result,indent=2))
    identical=all((direct/p).read_bytes()==(local/p).read_bytes() for p in FILES)
    candidate=True
    if role=='editor' and result.get('worker_status')=='COMPLETE':
        candidate=all((direct/p).read_bytes()==(Path(result['workspace']['origin'])/p).read_bytes() for p in FILES if p!=case['mapping']['path'])
        # Inspect the candidate before applying; independent frontier acceptance is a separate step.
        from hub.skills.coding.editing.workspace import read_record
        work=Path(read_record(ident)['path'])
        candidate=candidate and all((direct/p).read_bytes()==(work/p).read_bytes() for p in FILES)
        if candidate:call('POST',f'/api/jobs/{ident}/apply',{})
        identical=all((direct/p).read_bytes()==(local/p).read_bytes() for p in FILES)
    status_ok=result.get('worker_status')==case.get('expected','COMPLETE')
    if direct_check:status_ok=status_ok and result['checks'][0]['status']==direct_check['status'] and result['checks'][0]['exit_code']==direct_check['exit_code']
    packet=result.get('investigation') or {}
    evidence_ok=role!='investigator' or (bool(packet.get('requirements')) and not packet.get('unknowns') and
                                       all(word in (result.get('report') or '') for word in case['words']))
    row={'case':name,'role':role,'job_id':ident,'expected_status':case.get('expected','COMPLETE'),
         'worker_status':result.get('worker_status'),'candidate_matches':candidate,'trees_match':identical,
         'mechanical_acceptance':bool(identical and candidate and status_ok and evidence_ok),
         'direct_tool_seconds':round(direct_seconds,4),'delegated_wall_seconds':round(time.monotonic()-started,4),
         'local_usage':result.get('usage'),'frontier_review':'pending','baseline_frontier_tokens':None,
         'delegated_frontier_tokens':None,'baseline_frontier_effort_seconds':None,'delegated_frontier_effort_seconds':None}
    return row


def measurement_summary(rows):
    measured=[r for r in rows if r.get('baseline_frontier_tokens') is not None and r.get('delegated_frontier_tokens') is not None]
    efforts=[r for r in rows if r.get('baseline_frontier_effort_seconds') is not None and r.get('delegated_frontier_effort_seconds') is not None]
    return {'matched_token_tasks':len(measured),'frontier_tokens_avoided':sum(r['baseline_frontier_tokens']-r['delegated_frontier_tokens'] for r in measured) if measured else None,
            'matched_effort_tasks':len(efforts),'frontier_effort_seconds_avoided':sum(r['baseline_frontier_effort_seconds']-r['delegated_frontier_effort_seconds'] for r in efforts) if efforts else None}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True,help='New private directory outside this repository')
    p.add_argument('--cases',nargs='*',choices=[c['name'] for c in cases()])
    args=p.parse_args()
    checkout=Path(__file__).resolve().parents[1]
    root=args.out.resolve()
    if root==checkout or root.is_relative_to(checkout) or root.exists():p.error('--out must be a new directory outside the checkout')
    root.mkdir(parents=True,mode=0o700)
    rows=[]
    for case in cases():
        if args.cases and case['name'] not in args.cases:continue
        row=run_case(root,case);rows.append(row)
        result={'measurement':'Paired tool exercises; frontier deliberation, orchestration, review, retry and takeover effort must be observed separately. No inferred savings.',
                'cases':rows,'summary':measurement_summary(rows)}
        (root/'results.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(row),flush=True)
    (root/'frontier-measurements.json').write_text(json.dumps({'frontier_model':None,'frontier_configuration':None,
        'instructions':'Run both arms with the same frontier configuration from identical starting states. Fill observed complete-task totals, including orchestration/review/retries/takeover. Keep unavailable values null. Review correctness independently. Tool seconds are not frontier effort.',
        'tasks':[{k:r[k] for k in ('case','job_id','frontier_review','baseline_frontier_tokens','delegated_frontier_tokens','baseline_frontier_effort_seconds','delegated_frontier_effort_seconds')} for r in rows]},indent=2))
    print('Private evidence: '+str(root),flush=True)


if __name__=='__main__':main()
