"""Representative bounded handoffs. Mechanical outcomes are not frontier acceptance."""
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
from hub.settings import STATE

CASES={
    'discovery':{
        'files':{'routes.js':"const {readSettings}=require('./service'); module.exports={get:readSettings};\n",
                 'service.js':"const {defaultLead}=require('./config'); exports.readSettings=()=>({lead:defaultLead});\n",
                 'config.js':"exports.defaultLead=10;\n"},
        'role':'investigator','task':'Trace routes.js get through service.js to the default lead. Report the default value and verified path:line evidence for all three files.',
        'expected_words':['routes.js','service.js','config.js','10']},
    'settings-slice':{
        'files':{'settings.js':"exports.normalize = x => ({enabled: !!x.enabled, lead: Number(x.lead)});\n",
                 'view.js':"exports.render = x => '<label>Lead minutes <input value="+'"'+"' + x.lead + '"+'"'+"></label>';\n"},
        'role':'editor','task':'In settings.js normalize missing or nonnumeric lead to 10 and clamp numeric lead to 0..60; preserve zero and enabled. In view.js render the normalized numeric lead so user text cannot enter the HTML. Preserve the label and editable input; do not add readonly. Use the approved tests and one targeted repair if justified.',
        'check':"const assert=require('node:assert/strict'),s=require('./settings'),v=require('./view');assert.deepEqual(s.normalize({enabled:true}),{enabled:true,lead:10});assert.equal(s.normalize({lead:0}).lead,0);assert.equal(s.normalize({lead:-1}).lead,0);assert.equal(s.normalize({lead:100}).lead,60);assert.equal(s.normalize({lead:'abc'}).lead,10);assert.ok(v.render({lead:15}).includes('15'));assert.ok(!v.render({lead:15}).includes('readonly'));assert.ok(!v.render({lead:'<script>'}).includes('<script>'));"},
    'backend-fix':{
        'files':{'totals.py':"def logout_total(row):\n    return row['logout_during_shift_s'] + row['late_logout_s']\n"},
        'role':'editor','task':'Fix logout_total in totals.py to treat missing or None logout_during_shift_s / late_logout_s as zero. Preserve sums for present numeric values and do not suppress unrelated errors. Use the supplied regression check.',
        'python_check':"from totals import logout_total; assert logout_total({})==0; assert logout_total({'late_logout_s':None})==0; assert logout_total({'logout_during_shift_s':2,'late_logout_s':3})==5"},
    'log-diagnosis':{
        'files':{'decoder.py':"def decode(value):\n    return int(value)\n"},
        'role':'investigator','task':'Investigate the recorded failing check. Read decoder.py and the attached check output. Explain why decode(None) fails and cite actual evidence. Do not edit.',
        'baseline_check':"from decoder import decode; decode(None)",
        'expected_words':['decoder.py','None']},
}

def wait(ident, limit):
    started=time.monotonic()
    while True:
        status=call('GET',f'/api/jobs/{ident}/wait?timeout_seconds=25')
        if status['state'] not in ('queued','running'):return status
        if time.monotonic()-started>limit:
            call('POST',f'/api/jobs/{ident}/cancel');return {'state':'cancelled'}

def run_case(root,name,preset,timeout):
    case=CASES[name];repo=root/(name+'-'+preset);repo.mkdir(mode=0o700)
    for path,text in case['files'].items():(repo/path).write_text(text)
    (repo/'unrelated.txt').write_text('user changes must survive\n')
    subprocess.run(['git','init','-q',str(repo)],check=True)
    evidence=[];handoff='evaluation-'+uuid.uuid4().hex
    if case.get('baseline_check'):
        source=JobRequest(role='validator',repo=str(repo),task='Record the known failure for diagnosis',handoff_id=handoff,
            checks=[{'name':'baseline','argv':[sys.executable,'-c',case['baseline_check']]}],idempotency_key=uuid.uuid4().hex)
        ident=call('POST','/api/jobs',source.model_dump())['id'];wait(ident,60);evidence=[ident]
    checks=[]
    if case.get('check'):checks=[{'name':'behavior','argv':['node','-e',case['check']]}]
    if case.get('python_check'):checks=[{'name':'regression','argv':[sys.executable,'-c',case['python_check']]}]
    request=JobRequest(role=case['role'],repo=str(repo),task=case['task'],read_paths=list(case['files']),
        allowed_paths=list(case['files']) if case['role']=='editor' else [],checks=checks,
        workflow='implement' if checks else 'single',repair_attempts=1 if checks else 0,
        execution_preset=preset,timeout=timeout,evidence_job_ids=evidence,handoff_id=handoff,idempotency_key=uuid.uuid4().hex)
    started=time.monotonic();ident=call('POST','/api/jobs',request.model_dump())['id'];status=wait(ident,timeout+120)
    result=call('GET',f'/api/jobs/{ident}/result?view=summary')
    report=result.get('report') or ''
    correct=(status['state']=='completed' and result.get('worker_status')=='COMPLETE' and
        all(c.get('exit_code')==0 for c in result.get('checks',[])) and
        all(word.casefold() in report.casefold() for word in case.get('expected_words',[])) and
        (repo/'unrelated.txt').read_text()=='user changes must survive\n')
    return {'case':name,'preset':preset,'job_id':ident,'evidence_job_ids':evidence,'repo':str(repo),
        'seconds':round(time.monotonic()-started,2),'state':status['state'],'worker_status':result.get('worker_status'),
        'mechanically_correct':correct,'usage':result.get('usage'),'metrics':result.get('metrics'),
        'frontier_review':'pending','baseline_frontier_tokens':None,'delegated_frontier_tokens':None,'net_savings':None}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--cases',nargs='+',choices=list(CASES),default=list(CASES))
    parser.add_argument('--presets',nargs='+',choices=['work','extended'],default=['work'])
    parser.add_argument('--timeout',type=int,default=300);parser.add_argument('--max-seconds',type=int,default=1800)
    args=parser.parse_args();root=STATE/'benchmarks'/('delegation-'+uuid.uuid4().hex[:12]);root.mkdir(parents=True,mode=0o700)
    rows=[];started=time.monotonic()
    for case in args.cases:
        for preset in args.presets:
            if time.monotonic()-started+args.timeout+120>args.max_seconds:break
            row=run_case(root,case,preset,args.timeout);rows.append(row)
            (root/'results.json').write_text(json.dumps({'cases':rows,'note':'Controlled fixtures; frontier acceptance and net savings require independent review and matched measurements.'},indent=2))
            print(json.dumps(row),flush=True)
    print('Evidence: '+str(root),flush=True)

if __name__=='__main__':main()
