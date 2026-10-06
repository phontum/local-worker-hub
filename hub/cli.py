import argparse
import json
import os
from pathlib import Path
import sys
import time
import uuid
from urllib.parse import urlencode, quote
from .settings import URL, MODEL, PROJECT, STATE
from .models import default_timeout
LOW_MEMORY_MB = int(os.environ.get('LOCAL_WORKER_MIN_AVAILABLE_MB', '2500'))

def setup_searxng(port, assume_yes):
    """Write private SearXNG settings (random secret) and start the localhost-only container with docker compose."""
    import secrets, shutil, subprocess
    from .settings import CONFIG
    target=CONFIG/'searxng';target.mkdir(mode=0o700,parents=True,exist_ok=True)
    settings=target/'settings.yml'
    if not settings.exists():
        template=(PROJECT/'deploy/searxng/settings.template.yml').read_text()
        settings.write_text(template.replace('__SECRET__',secrets.token_hex(32)));settings.chmod(0o644)  # read by the container user
    shutil.copyfile(PROJECT/'deploy/searxng/compose.yml',target/'compose.yml')
    command=['docker','compose','-f',str(target/'compose.yml'),'up','-d']
    print('Will run: SEARXNG_PORT='+str(port)+' '+' '.join(command),file=sys.stderr)
    if not assume_yes and (not sys.stdin.isatty() or input('Start the local SearXNG container now? [y/N] ').strip().lower()!='y'):
        raise SystemExit('Not started. Run the command above yourself, then: local-worker web configure searxng')
    subprocess.run(command,check=True,env={**os.environ,'SEARXNG_PORT':str(port)})
    from .skills.research.web_provider import searxng_health
    for _ in range(30):
        if searxng_health(f'http://127.0.0.1:{port}'):return
        time.sleep(2)
    raise SystemExit('SearXNG did not answer JSON searches within 60s; check: docker logs local-worker-searxng')

def exit_code(state, worker_status, error=None):
    if state == 'cancelled': return 130
    if state == 'timed_out': return 124
    if state == 'failed': return 3 if not error else 4
    return 0 if worker_status == 'COMPLETE' else 2

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
        p=argparse.ArgumentParser();p.add_argument('command');p.add_argument('action',choices=['status','configure','setup-searxng'])
        p.add_argument('provider',choices=['exa','langsearch','searxng'],nargs='?');p.add_argument('--port',type=int,default=8888);p.add_argument('--yes',action='store_true');args=p.parse_args()
        from .skills.research.web_provider import selection, configure, langsearch_key
        from .settings import CONFIG
        try:
            if args.action=='setup-searxng':
                setup_searxng(args.port,args.yes)
                configure('searxng',f'http://127.0.0.1:{args.port}')
            elif args.action=='configure':
                if not args.provider:p.error('configure requires exa, langsearch or searxng')
                if args.provider=='langsearch' and not (CONFIG/'langsearch-api-key').exists():
                    if not sys.stdin.isatty():p.error('Create the private ~/.config/local-worker/langsearch-api-key file first, or configure from an interactive terminal')
                    import getpass
                    key=getpass.getpass('Free LangSearch API key (hidden): ').strip()
                    if not key or len(key)>4096:p.error('Invalid key')
                    CONFIG.mkdir(mode=0o700,parents=True,exist_ok=True)
                    fd=os.open(CONFIG/'langsearch-api-key',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                    with os.fdopen(fd,'w') as out:out.write(key)
                configure(args.provider,*( [f'http://127.0.0.1:{args.port}'] if args.provider=='searxng' else []))
            value=selection()
            value['known_url_extraction']='direct public origin first; explicitly unverified hosted fallback; cached mode reuses LangSearch text'
            value['langsearch_key_configured']=(CONFIG/'langsearch-api-key').is_file()
            if value['search_provider']=='searxng':
                from .skills.research.web_provider import searxng_health
                value['searxng_healthy']=searxng_health(value.get('searxng_url','http://127.0.0.1:8888'))
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
            from .skills.coding.validation.profiles import seed_profiles
            value=seed_profiles()
        else:value=call('GET','/api/project-profile?'+urlencode({'repo':args.repo}))
        print(json.dumps(value,indent=2));return
    if command=='spec':
        p=argparse.ArgumentParser(description='DelegationSpec tools. check FILE: validate a spec and show the task it compiles to (offline). submit FILE: run it. draft TASK: show what the host understands of a natural-language task (no model).')
        p.add_argument('command');p.add_argument('action',choices=['check','submit','draft']);p.add_argument('target');p.add_argument('--repo',default='.')
        p.add_argument('--allow-path',action='append',default=[]);p.add_argument('--read-path',action='append',default=[]);p.add_argument('--kind');p.add_argument('--idempotency-key')
        args=p.parse_args()
        from .skills.coding.delegation.spec import DelegationSpec, compile_spec, draft_from_task
        repo=str(Path(args.repo).resolve())
        if args.action=='draft':
            from .skills.coding.intelligence.codeindex import CodeIndex
            from .models import JobRequest
            from .scoped import ScopedFiles
            index=CodeIndex.load(ScopedFiles(JobRequest(role='investigator',repo=repo,task='draft',idempotency_key=__import__('uuid').uuid4().hex)))
            print(draft_from_task(args.target,args.allow_path,args.read_path,args.kind,index=index).model_dump_json(indent=2,exclude_defaults=True));return
        try:
            spec=DelegationSpec.model_validate_json(Path(args.target).read_text())
            request=compile_spec(spec,repo,'cli',args.idempotency_key)
        except Exception as e:print(str(e),file=sys.stderr);sys.exit(2)
        if args.action=='check':
            print(f"role={request.role} kind={request.kind} edit={request.allowed_paths} read={request.read_paths} checks={len(request.checks)} workflow={request.workflow}\n\n{request.task}");return
        try:value=call('POST','/api/jobs',request.model_dump());print(json.dumps({'id':value['id'],'state':value['state']}))
        except Exception as e:print(str(e),file=sys.stderr);sys.exit(2)
        return
    if command=='intel':
        from .skills.coding.intelligence.intel.tools import TOOLS
        p=argparse.ArgumentParser(description='Deterministic code intelligence (no model call): find_symbol, find_references, find_implementations, callers, callees, symbol_context, outline, diagnostics.')
        p.add_argument('command');p.add_argument('tool',choices=sorted(TOOLS));p.add_argument('target',nargs='?',help='symbol name, or file path for outline');p.add_argument('--repo',default='.');p.add_argument('--path',help='disambiguate by defining file (callees, symbol_context)');p.add_argument('--limit',type=int)
        args=p.parse_args()
        body={'repo':str(Path(args.repo).resolve()),'limit':args.limit,'path':args.path}
        if args.tool=='outline':body['path']=args.target
        elif args.tool=='diagnostics':body['paths']=[args.target] if args.target else None
        else:body['name']=args.target
        try:print(json.dumps(call('POST','/api/intel/'+args.tool,{k:v for k,v in body.items() if v is not None}),indent=2))
        except Exception as e:print(str(e),file=sys.stderr);sys.exit(2)
        return
    if command=='outcome':
        p=argparse.ArgumentParser(description='Capture the diff you finally shipped after the worker snapshot, so a takeover or rejection can be learned from.');p.add_argument('command');p.add_argument('job_id')
        args=p.parse_args()
        from .mcp_adapter import job_path
        print(json.dumps(call('POST',job_path(args.job_id)+'/outcome'),indent=2));return
    if command=='dataset':
        p=argparse.ArgumentParser(description='Export delegation records as JSONL (private, local). Structure only by default: hashes, counts, paths, ranges, reason codes.')
        p.add_argument('command');p.add_argument('action',choices=['export']);p.add_argument('--out',required=True);p.add_argument('--since-days',type=int);p.add_argument('--kinds',default='',help='comma-separated job kinds')
        p.add_argument('--include-source',action='store_true',help='ALSO write task text, the exact prompt, the local patch, the final frontier diff and review notes (private source and model output)')
        p.add_argument('--include-unreviewed',action='store_true');p.add_argument('--include-eval',action='store_true',help='include benchmark jobs');p.add_argument('--all-history',action='store_true',help='ignore the stats reset')
        args=p.parse_args()
        query={'kinds':args.kinds,'include_source':str(args.include_source).lower(),'include_unreviewed':str(args.include_unreviewed).lower(),'include_eval':str(args.include_eval).lower(),'include_history':str(args.all_history).lower()}
        if args.since_days:query['since_days']=args.since_days
        records=call('GET','/api/dataset?'+urlencode(query))['records']
        out=Path(args.out);out.write_text(''.join(json.dumps(r)+'\n' for r in records));out.chmod(0o600)
        print(f"Wrote {len(records)} record(s) to {out}"+(' (contains private source)' if args.include_source else ' (structure only)'));return
    if command=='stats':
        p=argparse.ArgumentParser(description='Frontier acceptance by role and kind over reviewed delegations (benchmark jobs excluded, counted from the last reset). `stats reset` starts counting from now; nothing is deleted.')
        p.add_argument('command');p.add_argument('action',nargs='?',choices=['show','reset'],default='show');p.add_argument('--include-eval',action='store_true');p.add_argument('--all-history',action='store_true',help='ignore the reset and count every job');p.add_argument('--since',help='with reset: start counting at this local date (YYYY-MM-DD, 00:00) instead of now')
        args=p.parse_args()
        if args.action=='reset':
            begin=time.mktime(time.strptime(args.since,'%Y-%m-%d')) if args.since else None
            done=call('POST','/api/outcomes/reset',{'since':begin} if begin else {});print(f"Counting from {time.strftime('%Y-%m-%d %H:%M',time.localtime(done['since']))}. {done['kept']} earlier job(s) are kept and still available with --all-history.");return
        shown=call('GET','/api/outcomes?include_eval='+str(args.include_eval).lower()+'&include_history='+str(args.all_history).lower())
        print('Counting '+('all history' if shown['since'] is None else 'jobs submitted since '+time.strftime('%Y-%m-%d %H:%M',time.localtime(shown['since']))))
        for item in shown['by_kind']:
            print(f"{item['role']:<12}{item['kind']:<18}reviewed {item['reviewed']:<4}accepted {item['accepted']:<4}rejected {item['rejected']:<4}takeover {item['takeover']:<4}rate {item['accept_rate']:.0%}  {item['reasons'] or ''}")
        if not shown['by_kind']:print('No reviewed delegations counted yet.')
        return
    if command=='incident':
        p=argparse.ArgumentParser();p.add_argument('command');p.add_argument('job_id');p.add_argument('--out');p.add_argument('--fixture',metavar='DIR',help='Write a replayable regression fixture (PRIVATE SOURCE and raw model replies) to DIR instead of the metadata-only export');p.add_argument('--include-notes',action='store_true',help='Include the review notes (written by you; check them for private text first)')
        args=p.parse_args()
        from .mcp_adapter import job_path
        from .skills.coding.delegation import incident
        from .settings import STATE
        job=call('GET',job_path(args.job_id))
        if args.fixture:
            fixture=incident.export_fixture(job,STATE/'jobs'/job['id'],STATE/'workspaces'/job['id']/'base',args.fixture)
            print(f"Wrote {args.fixture}/fixture.json with {len(fixture['allowed_paths'])} file(s) and {len(fixture['replies'])} reply(ies). It contains private source: review it before committing; move it to tests/data/incidents/ to make it a regression test.");return
        text=json.dumps(incident.build(job,STATE/'jobs'/job['id'],args.include_notes),indent=2)
        if args.out:Path(args.out).write_text(text+'\n');print('Wrote '+args.out)
        else:print(text)
        return
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
    if command in ('apply','discard','revert'):
        p=argparse.ArgumentParser();p.add_argument('command');p.add_argument('job_id')
        if command=='apply':
            p.add_argument('--accept-removals',action='store_true');p.add_argument('--revalidate',action='store_true');p.add_argument('--run-checks',action='store_true');p.add_argument('--accept-stale',action='store_true')
        args=p.parse_args()
        from .mcp_adapter import job_path
        body={'accept_removals':args.accept_removals,'revalidate':args.revalidate,'run_checks':args.run_checks,'accept_stale':args.accept_stale} if command=='apply' else None
        try:print(json.dumps(call('POST',job_path(args.job_id)+'/'+command,body),indent=2))
        except Exception as e:print(str(e),file=sys.stderr);sys.exit(2)
        return
    if command in ('status','result','cancel','review','wait'):
        p=argparse.ArgumentParser();p.add_argument('command');p.add_argument('job_id');p.add_argument('decision',nargs='?');p.add_argument('--notes',default='');p.add_argument('--reason',choices=['truncated','wrong_edit','oversized','no_change','check_failed','scope','wrong_localization','context_missing','other'])
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
            data=Review(decision=args.decision,notes=args.notes,reason=args.reason,measurement_source=args.measurement_source,
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
    if command=='watch':
        p=argparse.ArgumentParser(description='One line per phase change, then DONE <state> <status>; for background monitoring.')
        p.add_argument('command');p.add_argument('job_id');p.add_argument('--interval',type=float,default=3)
        args=p.parse_args()
        from .mcp_adapter import job_path
        last=None
        while True:
            try:status=call('GET',job_path(args.job_id)+'/progress')
            except Exception as e:print('local-worker: '+str(e),file=sys.stderr);sys.exit(4)
            if (status['state'],status['phase'])!=last:
                last=(status['state'],status['phase'])
                eta=f", ~{round(status['eta_seconds'])}s left" if status.get('eta_seconds') else ''
                print(f"{status['phase']} ({status['state']}, {round(status['elapsed_seconds'])}s elapsed{eta})",flush=True)
            if status['state'] not in ('queued','running'):
                result=call('GET',job_path(args.job_id)+'/result?view=brief') or {}
                print(f"DONE {status['state']} {result.get('status')}",flush=True)
                sys.exit(exit_code(status['state'],result.get('status'),result.get('error')))
            time.sleep(args.interval)
    if command=='dashboard':
        result=call('POST','/api/pair-code')
        print(f"Dashboard: {URL}\nPairing code (valid five minutes): {result['code']}");return
    if command=='history':print(json.dumps(call('GET','/api/jobs?view=compact'),indent=2));return
    if command=='doctor':
        import shutil,subprocess
        from .model_registry import models, probe, residency
        result={'model':MODEL,'models':{alias:{**value,**probe(value['name'])} for alias,value in models().items()},'loaded':residency(),'context':16384,'project':str(PROJECT),'state':str(STATE),
            'opencode':shutil.which('opencode'),'ollama':shutil.which('ollama')}
        from .skills.research.browser_page import status as browser_status
        result['browser']=browser_status()
        try:result['opencode_version']=subprocess.check_output(['opencode','--version'],text=True,timeout=10).strip()
        except Exception as e:result['opencode_error']=str(e)
        aws=Path.home()/'.aws'
        import psutil
        memory=psutil.virtual_memory();result['host_memory_mb']={'total':memory.total//2**20,'available':memory.available//2**20}
        try:
            environment=subprocess.check_output(['systemctl','show','ollama','--property=Environment'],text=True,timeout=10)
            result['ollama_prompt_cache_limit']=next((part.split('=',1)[1].strip('"') for part in environment.replace('Environment=','').split() if part.strip('"').startswith('LLAMA_ARG_CACHE_RAM=')),None)
        except Exception:result['ollama_prompt_cache_limit']='unknown'
        if not result['ollama_prompt_cache_limit'] or result['ollama_prompt_cache_limit']=='unknown':
            result['memory_note']='llama.cpp prompt cache lives in host RAM and defaults to 8 GB; on a small host set LLAMA_ARG_CACHE_RAM=512 (MiB) in the Ollama service environment. The hub also unloads models when available RAM drops below '+str(LOW_MEMORY_MB)+' MB.'
        result['codex_sandbox_note']='Protected .aws symlink can prevent Codex shell sandbox startup; credentials and sandbox are unchanged.' if aws.is_symlink() else None
        print(json.dumps(result,indent=2));return
    delegate=len(sys.argv)>1 and sys.argv[1]=='delegate'
    if delegate:sys.argv.pop(1)
    p=argparse.ArgumentParser(description='Local assistant. `local-worker delegate ...` runs quietly and prints one compact JSON brief when finished (run it in the background). Bare prompts use public web with no repository access; --read-only selects repository investigation.')
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
    p.add_argument('--repair-attempts',type=int,default=0);p.add_argument('--investigate-first',action='store_true');p.add_argument('--in-place',action='store_true',help='Edit the repository directly instead of a private workspace');p.add_argument('--no-gate',action='store_true',help='Skip the complexity gate that refuses oversized Editor tasks');p.add_argument('--no-continuation',action='store_true');p.add_argument('--continue-from',help='Job id of an earlier Editor job whose private workspace this job continues');p.add_argument('--model-output',type=int,choices=[4096,8192]);p.add_argument('--match-mode',choices=['substring','line']);p.add_argument('--kind',choices=['find_code','explain','config_use','compare','check_requirement','mechanical','guard','regression_test','run_tests','fix_test'],help='Delegation shape: checks the role and tunes defaults')
    p.add_argument('--model',help='Local model alias from the registry for every phase of this job (default: roles.json)')
    p.add_argument('--model-context',type=int,choices=[16384,32768]);p.add_argument('--model-thinking',choices=['on','off'])
    p.add_argument('--board',action='store_true',help=argparse.SUPPRESS)
    p.add_argument('--agent-loop',dest='agent_loop',action='store_true',help='Use the older model-driven tool loop instead of the host pipelines (for comparison)')
    p.add_argument('--verify',action='store_true',help='Strict origin-proof research for public web roles: slower, may end PARTIAL, prints the evidence report')
    p.add_argument('--board-mode',choices=['lite','full'],help=argparse.SUPPRESS)
    p.add_argument('--parameter',action='append',default=[],help='Explicit nonsecret profile parameter NAME=value')
    p.add_argument('prompt',nargs='*');args=p.parse_args()
    engineering=args.read_only or args.repo or args.read_path or args.allow_path or args.checks or args.context_file or args.profile_ref or args.profile_hash or args.evidence_job
    role=args.role or ('editor' if args.write else 'investigator' if engineering else 'personal')
    if role in ('personal','researcher') and engineering:p.error('Public web roles cannot accept repository, scope, checks or private context flags')
    preset='extended' if args.extended else args.execution_preset
    if args.extended and args.model_context==16384:p.error('--extended conflicts with --model-context 16384')
    if args.extended and args.execution_preset not in (None,'extended'):p.error('--extended conflicts with --preset')
    if role=='personal' and preset is None:preset='work'
    if args.board or args.board_mode:p.error('--board was retired: it was 3-4x slower with no measured benefit. Use the default flow, or --verify for strict research.')

    if args.agent_loop and role not in ('personal','researcher'):p.error('--agent-loop is only available for public web roles; repository roles use the host pipelines')
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
            timeout=args.timeout if args.timeout is not None else default_timeout(role,preset,args.board,args.verify,bool(args.review_pass)),no_recovery=args.no_recovery,caller=args.caller,caller_session=args.caller_session,
            idempotency_key=args.idempotency_key or uuid.uuid4().hex,summary_mode=args.summary_mode,failure_policy=args.failure_policy,
            profile_hash=args.profile_hash,profile_ref=args.profile_ref,check_groups=args.check_group,parameters=parameters,
            workflow=args.workflow,repair_attempts=args.repair_attempts,investigate_first=args.investigate_first,in_place=args.in_place,skip_gate=args.no_gate,workspace_from=args.continue_from,continuation=not args.no_continuation,model_output=args.model_output,match_mode=args.match_mode or 'line',kind=args.kind,
            model=args.model,model_context=args.model_context,model_thinking=None if args.model_thinking is None else args.model_thinking=='on',
            execution_preset=preset,review_pass=args.review_pass,read_paths=args.read_path,evidence_job_ids=args.evidence_job,handoff_id=args.handoff_id,board=args.board,board_mode=args.board_mode,verify=args.verify,agent_loop=args.agent_loop)
    except Exception as e:p.error(str(e))
    ident=None
    try:
        job=call('POST','/api/jobs',request.model_dump());ident=job['id']
        if not delegate:print('local-worker: job '+ident,file=sys.stderr,flush=True)
        if args.asynchronous:print(json.dumps({'id':ident,'state':job['state']}));return
        last=None
        while job['state'] in ('queued','running'):
            if job['state']!=last and not delegate:print('local-worker: '+job['state'],file=sys.stderr,flush=True)
            last=job['state']
            time.sleep(2 if last=='queued' else 5);job=call('GET','/api/jobs/'+ident+'?view=compact')
        if delegate:
            brief=call('GET','/api/jobs/'+ident+'/result?view=brief') or {}
            print(json.dumps(brief,ensure_ascii=False),flush=True)
            code=exit_code(job['state'],brief.get('status'),brief.get('error'))
            if code:sys.exit(code)
            return
        result=call('GET','/api/jobs/'+ident+'/result?view=summary') or {}
        if result.get('truncated') and not args.json:result={**result,**(call('GET','/api/jobs/'+ident+'/result?view=full') or {})}
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
