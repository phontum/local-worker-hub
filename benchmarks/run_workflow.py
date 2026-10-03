"""Repeatable, bounded Ollama workflow evaluation; reports no invented savings."""
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

CASES = {
    'simple': {
        'source': 'module.exports = { timeoutMs: 30000, retries: 3 };\n',
        'task': 'Change only timeoutMs from 30000 to 45000 in config.js. Preserve retries and formatting.',
        'expected': 'module.exports = { timeoutMs: 45000, retries: 3 };\n',
        'checks': [{'name':'exact','argv':['node','-e',
            "const x=require('./config.js'); if(x.timeoutMs!==45000||x.retries!==3)process.exit(1)"]}],
    },
    'repair': {
        'source': 'module.exports = { timeoutMs: 30000, retries: 3 };\n',
        'task': 'Change timeoutMs to 45000 in config.js and preserve retries at 3. Run the supplied check; correct any supported failure.',
        'expected': 'module.exports = { timeoutMs: 45000, retries: 3 };\n',
        'checks': [{'name':'exact','argv':['node','-e',
            "const fs=require('fs');const s=fs.readFileSync('config.js','utf8');if(s!== 'module.exports = { timeoutMs: 45000, retries: 3 };\\n')process.exit(1)"]}],
    },
}


def run_case(root, name, variant, repeat, timeout):
    case=CASES[name]
    repo=root/f'{name}-{variant[0]}k-{int(variant[1])}-{variant[2]}repair-{repeat}'
    repo.mkdir(mode=0o700)
    (repo/'config.js').write_text(case['source'])
    subprocess.run(['git','init','-q',str(repo)],check=True)
    context,thinking,repairs=variant
    request=JobRequest(role='editor',workflow='implement',repair_attempts=repairs,
        repo=str(repo),allowed_paths=['config.js'],checks=case['checks'],
        task=case['task'],idempotency_key='workflow-eval-'+uuid.uuid4().hex,
        timeout=timeout,model_context=context,model_thinking=thinking)
    start=time.monotonic()
    ident=call('POST','/api/jobs',request.model_dump())['id']
    while True:
        progress=call('GET',f'/api/jobs/{ident}/wait?timeout_seconds=25')
        if progress['state'] not in ('queued','running'):break
        if time.monotonic()-start>timeout+45:
            call('POST',f'/api/jobs/{ident}/cancel');break
    result=call('GET',f'/api/jobs/{ident}/result?view=summary')
    actual=(repo/'config.js').read_text()
    checks=result.get('checks') or []
    return {'case':name,'variant':{'context':context,'thinking':thinking,'repairs':repairs},
        'repeat':repeat,'job_id':ident,'wall_seconds':round(time.monotonic()-start,2),
        'state':progress['state'],'worker_status':result.get('worker_status'),
        'mechanically_correct':actual==case['expected'] and bool(checks) and all(c.get('exit_code')==0 for c in checks),
        'attempts':len(result.get('attempts') or []),'usage':result.get('usage'),
        'frontier_review':'pending','frontier_tokens':None,'net_savings':None}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--cases',nargs='+',choices=sorted(CASES),default=sorted(CASES))
    parser.add_argument('--variants',nargs='+',choices=['baseline','32k','thinking-off','two-repairs'],
                        default=['baseline','32k','thinking-off','two-repairs'])
    parser.add_argument('--repeats',type=int,default=2)
    parser.add_argument('--timeout',type=int,default=300)
    parser.add_argument('--max-seconds',type=int,default=3600)
    args=parser.parse_args()
    variants={'baseline':(16384,True,1),'32k':(32768,True,1),
              'thinking-off':(16384,False,1),'two-repairs':(16384,True,2)}
    root=STATE/'benchmarks'/('workflow-'+uuid.uuid4().hex[:12]);root.mkdir(parents=True,mode=0o700)
    outcomes=[];started=time.monotonic()
    for name in args.cases:
        for variant_name in args.variants:
            for repeat in range(args.repeats):
                if time.monotonic()-started+args.timeout>args.max_seconds:
                    print('Time budget reached; remaining cases were not run.',flush=True)
                    print('Evidence: '+str(root),flush=True)
                    return
                row=run_case(root,name,variants[variant_name],repeat,args.timeout)
                row['variant_name']=variant_name
                outcomes.append(row)
                (root/'results.json').write_text(json.dumps({'description':'Controlled local workflow runs; mechanical correctness is not frontier acceptance or token savings.',
                    'cases':outcomes},indent=2))
                print(json.dumps(row),flush=True)
    print('Evidence: '+str(root),flush=True)


if __name__=='__main__':main()
