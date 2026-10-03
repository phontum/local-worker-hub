"""Paired frontier-directed tool exercises; no invented frontier usage or savings."""
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


def main():
    root=STATE/'benchmarks'/('pairs-'+uuid.uuid4().hex[:12]);root.mkdir(parents=True,mode=0o700)
    fixtures={'answer.js':'module.exports = { answer: 41 };\n',
              'config.js':'module.exports = { timeoutMs: 30000, retries: 3 };\n',
              'routes.js':"const { answer } = require('./answer');\nfunction health() { return { answer }; }\nmodule.exports = { health };\n"}
    cases=[('discovery-route','investigator','routes.js',None,None),
           ('discovery-settings','investigator','config.js',None,None),
           ('validation-pass','validator',None,None,None),('validation-fail','validator',None,None,None),
           ('edit-answer','editor','answer.js','answer: 41','answer: 42'),
           ('edit-timeout','editor','config.js','timeoutMs: 30000','timeoutMs: 45000')]
    outcomes=[]
    for name,role,path,old,new in cases:
        for variant in ('direct','local'):
            repo=root/name/variant;repo.mkdir(parents=True)
            for filename,content in fixtures.items():(repo/filename).write_text(content)
        direct,local=root/name/'direct',root/name/'local';fields={}
        started=time.monotonic()
        if role=='investigator':
            direct_evidence={'path':path,'numbered_source':'\n'.join(f'{i+1}: {s}' for i,s in enumerate((direct/path).read_text().splitlines()))}
            task=f'Read only {path}. Report the health handler and return value, or timeoutMs and retries if reading config.js, with concise path:line evidence. No inventory.'
        elif role=='editor':
            (direct/path).write_text((direct/path).read_text().replace(old,new));direct_evidence={'path':path,'content':(direct/path).read_text()}
            fields={'allowed_paths':[path]};task=f'Read {path}, copy its observed sha256 exactly, then replace only {old} with {new}. Preserve all other content. No checks requested.'
        else:
            code='print("fixture validation passed")' if name=='validation-pass' else 'print("fixture progress\\n"*4000);raise SystemExit(1)'
            argv=[sys.executable,'-c',code];p=subprocess.run(argv,cwd=direct,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            (direct/'output.log').write_bytes(p.stdout);direct_evidence={'exit_code':p.returncode,'output_bytes':len(p.stdout)}
            fields={'checks':[{'name':name,'argv':argv}]};task='Run only the supplied fixture check. Report exact outcomes; the failing fixture is deliberate.'
        direct_seconds=time.monotonic()-started
        request=JobRequest(role=role,repo=str(local),task=task,idempotency_key=root.name+'-'+name,timeout=120,**fields)
        started=time.monotonic();job=call('POST','/api/jobs',request.model_dump());ident=job['id']
        while call('GET','/api/jobs/'+ident+'/progress')['state'] in ('queued','running'):
            if time.monotonic()-started>240:
                call('POST','/api/jobs/'+ident+'/cancel');raise RuntimeError('Benchmark exceeded bounded wait')
            time.sleep(2)
        result=call('GET','/api/jobs/'+ident+'/result?view=summary');full=call('GET','/api/jobs/'+ident+'/result?view=full')
        (root/name/'local-result.json').write_text(json.dumps(full,indent=2))
        equal=all((direct/p).read_bytes()==(local/p).read_bytes() for p in fixtures)
        verified=equal and result.get('report_valid') and (role!='validator' or result['checks'][0]['exit_code']==(1 if name=='validation-fail' else 0))
        outcomes.append({'case':name,'role':role,'job_id':ident,'direct_tool_seconds':round(direct_seconds,4),
            'helper_wall_seconds':round(time.monotonic()-started,4),'direct_evidence':direct_evidence,
            'compact_result_bytes':len(json.dumps(result,indent=2).encode()),'full_result_bytes':len(json.dumps(full,indent=2).encode()),
            'local_usage':result.get('usage'),'mechanically_verified':bool(verified),'frontier_review':'pending',
            'baseline_frontier_tokens':None,'delegated_frontier_tokens':None,'baseline_frontier_cost':None,'delegated_frontier_cost':None})
        print(json.dumps(outcomes[-1]),flush=True)
        (root/'results.json').write_text(json.dumps({'measurement':'Paired frontier-directed native tool operations versus helper jobs. Timings exclude frontier deliberation and unavailable frontier usage; no net savings claim.','cases':outcomes},indent=2))
    print('Evidence: '+str(root),flush=True)


if __name__=='__main__':main()
