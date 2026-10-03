import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid
from urllib.parse import urlencode, quote
from .settings import URL, MODEL, PROJECT, STATE

def main():
    os.umask(0o077)
    if len(sys.argv)>1 and sys.argv[1]=='tools':
        p=argparse.ArgumentParser();p.add_argument('command');p.add_argument('--job-dir',required=True)
        from .scoped import serve_tools
        serve_tools(p.parse_args().job_dir);return
    if len(sys.argv)>1 and sys.argv[1]=='mcp':
        from .mcp_adapter import main as serve
        serve();return
    if len(sys.argv)>1 and sys.argv[1]=='serve':
        from .service import main as serve
        serve();return
    from .client import call
    command=sys.argv[1] if len(sys.argv)>1 else ''
    if command=='web':
        p=argparse.ArgumentParser();p.add_argument('command');p.add_argument('action',choices=['status','configure'])
        p.add_argument('provider',choices=['exa','langsearch'],nargs='?');args=p.parse_args()
        from .web_provider import selection, configure, langsearch_key
        from .settings import CONFIG
        try:
            if args.action=='configure':
                if not args.provider:p.error('configure requires exa or langsearch')
                if args.provider=='langsearch' and not (CONFIG/'langsearch-api-key').exists():
                    if not sys.stdin.isatty():p.error('Create the private ~/.config/local-worker/langsearch-api-key file first, or configure from an interactive terminal')
                    import getpass
                    key=getpass.getpass('Free LangSearch API key (hidden): ').strip()
                    if not key or len(key)>4096:p.error('Invalid key')
                    CONFIG.mkdir(mode=0o700,parents=True,exist_ok=True)
                    fd=os.open(CONFIG/'langsearch-api-key',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                    with os.fdopen(fd,'w') as out:out.write(key)
                configure(args.provider)
            value=selection()
            value['known_url_extraction']='direct public origin first; explicitly unverified hosted fallback; cached mode reuses LangSearch text'
            value['langsearch_key_configured']=(CONFIG/'langsearch-api-key').is_file()
            value['search_fallback']='Exa transient failure -> configured free LangSearch; reported in search evidence; per-job cooldown across passes'
            print(json.dumps(value,indent=2))
        except Exception as e:print(str(e),file=sys.stderr);sys.exit(4)
        return
    if command=='start':
        from .client import connect
        c=connect(start=True);c.close();print('Local Worker Hub is running at '+URL);return
    if command=='profile':
        p=argparse.ArgumentParser();p.add_argument('command');p.add_argument('action',choices=['show','seed']);p.add_argument('--repo',default=str(Path.cwd()))
        args=p.parse_args()
        if args.action=='seed':
            from .profiles import seed_profiles
            value=seed_profiles()
        else:value=call('GET','/api/project-profile?'+urlencode({'repo':args.repo}))
        print(json.dumps(value,indent=2));return
    if command=='artifact':
        p=argparse.ArgumentParser();p.add_argument('command');p.add_argument('job_id');p.add_argument('artifact');p.add_argument('--offset',type=int,default=0);p.add_argument('--limit',type=int,default=4000)
        args=p.parse_args()
        from .mcp_adapter import job_path
        print(json.dumps(call('GET',job_path(args.job_id)+'/artifact-page/'+quote(args.artifact,safe='')+'?'+urlencode({'offset':args.offset,'limit':args.limit})),indent=2));return
    if command=='trace':
        p=argparse.ArgumentParser();p.add_argument('command');p.add_argument('job_id');p.add_argument('--offset',type=int,default=0);p.add_argument('--limit',type=int,default=100)
        args=p.parse_args()
        from .mcp_adapter import job_path
        print(json.dumps(call('GET',job_path(args.job_id)+'/trace?'+urlencode({'offset':args.offset,'limit':args.limit})),indent=2));return
    if command=='summarize':
        p=argparse.ArgumentParser();p.add_argument('command');p.add_argument('job_id');p.add_argument('--idempotency-key',default=uuid.uuid4().hex);p.add_argument('--timeout',type=int,default=120)
        args=p.parse_args()
        from .mcp_adapter import job_path
        print(json.dumps(call('POST',job_path(args.job_id)+'/summarize',{'idempotency_key':args.idempotency_key,'timeout':args.timeout,'caller':'cli'}),indent=2));return
    if command in ('status','result','cancel','review','wait'):
        p=argparse.ArgumentParser();p.add_argument('command');p.add_argument('job_id');p.add_argument('decision',nargs='?');p.add_argument('--notes',default='')
        p.add_argument('--full',action='store_true');p.add_argument('--measurement-source',choices=['measured','manual_estimate'],default='measured')
        p.add_argument('--timeout-seconds',type=int,default=25)
        p.add_argument('--task-outcome',choices=['completed','partial','blocked'])
        p.add_argument('--review-effort-seconds',type=int)
        for name in ('baseline-frontier-tokens','delegated-frontier-tokens','baseline-frontier-cost','delegated-frontier-cost'):
            p.add_argument('--'+name,type=float if name.endswith('cost') else int)
        args=p.parse_args()
        suffix={'status':'' if args.full else '/progress','result':'/result?view='+('full' if args.full else 'brief'),
                'cancel':'/cancel','review':'/review','wait':'/wait?'+urlencode({'timeout_seconds':args.timeout_seconds})}[command]
        if command=='review':
            from .models import Review
            data=Review(decision=args.decision,notes=args.notes,measurement_source=args.measurement_source,
                       task_outcome=args.task_outcome,review_effort_seconds=args.review_effort_seconds,
                       **{n:getattr(args,n) for n in ('baseline_frontier_tokens','delegated_frontier_tokens','baseline_frontier_cost','delegated_frontier_cost')}).model_dump()
        else:data=None
        try:
            from .mcp_adapter import job_path
            value=call('POST' if command in ('cancel','review') else 'GET',job_path(args.job_id)+suffix,data)
            if command=='review':value={'id':value['id'],'review':value['review']}
            if command=='cancel':value={'id':value['id'],'state':value['state']}
            print(json.dumps(value,indent=2))
        except Exception as e:print(str(e),file=sys.stderr);sys.exit(4)
        return
    if command=='dashboard':
        result=call('POST','/api/pair-code')
        print(f"Dashboard: {URL}\nPairing code (valid five minutes): {result['code']}");return
    if command=='history':print(json.dumps(call('GET','/api/jobs?view=compact'),indent=2));return
    if command=='doctor':
        import shutil,subprocess
        result={'model':MODEL,'context':16384,'project':str(PROJECT),'state':str(STATE),
            'opencode':shutil.which('opencode'),'ollama':shutil.which('ollama')}
        try:result['opencode_version']=subprocess.check_output(['opencode','--version'],text=True,timeout=10).strip()
        except Exception as e:result['opencode_error']=str(e)
        aws=Path.home()/'.aws'
        result['codex_sandbox_note']='Protected .aws symlink can prevent Codex shell sandbox startup; credentials and sandbox are unchanged.' if aws.is_symlink() else None
        print(json.dumps(result,indent=2));return
    p=argparse.ArgumentParser(description='Local assistant. Bare prompts use public web with no repository access; --read-only selects repository investigation.')
    p.add_argument('--role',choices=['investigator','editor','validator','researcher','personal'])
    p.add_argument('--extended',action='store_true',help='Use 32K context, thinking and an independent requirements review')
    review=p.add_mutually_exclusive_group()
    review.add_argument('--review',dest='review_pass',action='store_true',help='Review the answer against every original requirement (also available at 16K)')
    review.add_argument('--no-review',dest='review_pass',action='store_false',help='Skip the optional answer review; implementation reviews still run')
    p.set_defaults(review_pass=None)
    p.add_argument('--preset',choices=['small','work','extended'],dest='execution_preset')
    p.add_argument('--read-path',action='append',default=[],help='Explicit readable file or directory root')
    p.add_argument('--evidence-job',action='append',default=[],help='Finished job from the same repository')
    p.add_argument('--handoff-id',help='Group related jobs and takeovers for accounting')
    modes=p.add_mutually_exclusive_group();modes.add_argument('--read-only',action='store_true');modes.add_argument('--write',action='store_true')
    p.add_argument('--repo');p.add_argument('--allow-path',action='append',default=[]);p.add_argument('--checks',help='JSON file containing approved check definitions')
    p.add_argument('--context-file');p.add_argument('--timeout',type=int);p.add_argument('--no-recovery',action='store_true')
    p.add_argument('--async',dest='asynchronous',action='store_true');p.add_argument('--json',action='store_true')
    p.add_argument('--caller',default='cli');p.add_argument('--caller-session');p.add_argument('--idempotency-key')
    p.add_argument('--summary-mode',choices=['none','local'],default='none')
    p.add_argument('--failure-policy',choices=['fail_fast','continue_independent'],default='fail_fast')
    p.add_argument('--profile-hash');p.add_argument('--check-group',action='append',default=[])
    p.add_argument('--profile-ref');p.add_argument('--workflow',choices=['single','implement'],default='single')
    p.add_argument('--repair-attempts',type=int,default=0);p.add_argument('--investigate-first',action='store_true')
    p.add_argument('--model-context',type=int,choices=[16384,32768]);p.add_argument('--model-thinking',choices=['on','off'])
    p.add_argument('--parameter',action='append',default=[],help='Explicit nonsecret profile parameter NAME=value')
    p.add_argument('prompt',nargs='*');args=p.parse_args()
    engineering=args.read_only or args.repo or args.read_path or args.allow_path or args.checks or args.context_file or args.profile_ref or args.profile_hash or args.evidence_job
    role=args.role or ('editor' if args.write else 'investigator' if engineering else 'personal')
    if role in ('personal','researcher') and engineering:p.error('Public web roles cannot accept repository, scope, checks or private context flags')
    preset='extended' if args.extended else args.execution_preset
    if args.extended and args.model_context==16384:p.error('--extended conflicts with --model-context 16384')
    if args.extended and args.execution_preset not in (None,'extended'):p.error('--extended conflicts with --preset')
    if role=='personal' and preset is None:preset='work'
    if args.read_only and role!='investigator':p.error('--read-only selects Investigator')
    if args.write and role!='editor':p.error('--write selects Editor')
    if not args.prompt and sys.stdin.isatty():p.error('Supply the task through arguments or stdin')
    try:
        from .models import JobRequest
        task=' '.join(args.prompt) if args.prompt else sys.stdin.read()
        checks=json.loads(Path(args.checks).read_text()) if args.checks else []
        parameters=dict(item.split('=',1) for item in args.parameter)
        request=JobRequest(role=role,task=task,repo=None if role in ('researcher','personal') else (args.repo or str(Path.cwd())),
            allowed_paths=args.allow_path,checks=checks,context=Path(args.context_file).read_text() if args.context_file else '',
            timeout=args.timeout if args.timeout is not None else (120 if preset=='small' else 300),no_recovery=args.no_recovery,caller=args.caller,caller_session=args.caller_session,
            idempotency_key=args.idempotency_key or uuid.uuid4().hex,summary_mode=args.summary_mode,failure_policy=args.failure_policy,
            profile_hash=args.profile_hash,profile_ref=args.profile_ref,check_groups=args.check_group,parameters=parameters,
            workflow=args.workflow,repair_attempts=args.repair_attempts,investigate_first=args.investigate_first,
            model_context=args.model_context,model_thinking=None if args.model_thinking is None else args.model_thinking=='on',
            execution_preset=preset,review_pass=args.review_pass,read_paths=args.read_path,evidence_job_ids=args.evidence_job,handoff_id=args.handoff_id)
    except Exception as e:p.error(str(e))
    ident=None
    try:
        job=call('POST','/api/jobs',request.model_dump());ident=job['id']
        print('local-worker: job '+ident,file=sys.stderr,flush=True)
        if args.asynchronous:print(json.dumps({'id':ident,'state':job['state']}));return
        last=None
        while job['state'] in ('queued','running'):
            if job['state']!=last:print('local-worker: '+job['state'],file=sys.stderr,flush=True);last=job['state']
            time.sleep(2 if last=='queued' else 5);job=call('GET','/api/jobs/'+ident+'?view=compact')
        result=call('GET','/api/jobs/'+ident+'/result?view=summary') or {}
        if args.json:print(json.dumps(result,indent=2))
        elif result.get('report'):
            if role=='personal':
                from .report import parse_report
                report=parse_report(result['report'])
                print(report['findings'] if report else result['report'])
                if report and report['risks'].strip().rstrip('.').lower() not in ('none','none identified'):
                    print('\n'+report['risks'])
            else:print(result['report'])
        else:print(result.get('error',job['state']),file=sys.stderr)
        if job['state']=='cancelled':sys.exit(130)
        if job['state']=='timed_out':sys.exit(124)
        if job['state']=='failed':sys.exit(3 if not result.get('error') else 4)
        if result.get('worker_status')!='COMPLETE':sys.exit(2)
    except KeyboardInterrupt:
        if ident:call('POST','/api/jobs/'+ident+'/cancel')
        sys.exit(130)
    except Exception as e:print('local-worker: '+str(e),file=sys.stderr);sys.exit(4)

if __name__=='__main__':main()
