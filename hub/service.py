import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import secrets
import shutil
import time
import re
import uuid
from typing import Literal
import httpx
import psutil
from fastapi import FastAPI, HTTPException, Request, Response, Depends, Query
from pydantic import BaseModel, Field
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from .models import JobRequest, Review
from .settings import initialize, STATE, PROJECT
from .model_registry import model_name, allowed_names, models as registered_models, probe, OLLAMA
from . import workspace, outcomes
from .scoped import ScopeError
from .store import Store
from .runner import Runner, stop_process
from .presentation import result_summary, result_brief, progress_summary, history_item
from .profiles import show_profile, expand_profile


class ApplyOptions(BaseModel):
    accept_removals: bool = False
    accept_stale: bool = False
    revalidate: bool = False
    run_checks: bool = False


class AnalysisRequest(BaseModel):
    idempotency_key: str = Field(min_length=1,max_length=200)
    timeout: int = Field(default=120,ge=1,le=1800)
    caller: str = Field(default='cli',max_length=80)


def artifact_path(ident, name):
    allowed={'ask.json','report.txt','draft-report.txt','answer-review.json','work.web-verification.json','answer-review.web-verification.json','work.research-plan.json','answer-review.research-plan.json','scoped-diff.txt','patch.diff','job.diff','staged.diff','before-status.txt','after-status.txt','before-diff.txt','after-diff.txt','before-staged.txt','after-staged.txt'}
    if name not in allowed and not re.fullmatch(r'check-(?:\d+-)?\d+\.log',name) and not re.fullmatch(r'board-[a-z0-9.-]+\.(?:json|txt)',name):
        raise HTTPException(403,'Only reports, diffs and check outputs are exposed')
    path=STATE/'jobs'/ident/name
    if path.is_symlink():raise HTTPException(403,'Symlink artifact denied')
    if not path.is_file():raise HTTPException(404,'Artifact unavailable')
    return path

LOW_MEMORY_MB = int(os.environ.get('LOCAL_WORKER_MIN_AVAILABLE_MB', '2500'))

async def relieve_memory(threshold_mb=None):
    """Unload resident models when host RAM is low.

    llama.cpp keeps its prompt cache in host RAM, growing with every distinct prompt prefix (about 0.5 GB per long
    Gemma prompt, 8 GB default limit) and the Linux OOM killer ends Ollama mid-request on a small WSL host.
    Unloading frees it; the next request reloads (about 10s). LLAMA_ARG_CACHE_RAM on the Ollama service bounds it at the source.
    """
    threshold = (threshold_mb or LOW_MEMORY_MB)
    available = psutil.virtual_memory().available // 2**20
    if available >= threshold:
        return None
    unloaded = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5), trust_env=False) as client:
        try:
            unloaded = [m['name'] for m in (await client.get(OLLAMA + '/api/ps')).json().get('models', [])]
            for name in unloaded:
                await client.post(OLLAMA + '/api/generate', json={'model': name, 'keep_alive': 0})
            for _ in range(30):
                if not (await client.get(OLLAMA + '/api/ps')).json().get('models'):
                    break
                await asyncio.sleep(.5)
        except (httpx.HTTPError, ValueError):
            return {'available_mb': available, 'threshold_mb': threshold, 'unloaded': [], 'error': 'Ollama unavailable'}
    return {'available_mb': available, 'threshold_mb': threshold, 'unloaded': unloaded}

ALLOWED_HOSTS = {'127.0.0.1:8765','localhost:8765'}
ORIGINS = {'http://127.0.0.1:8765','http://localhost:8765'}

def create_app(store=None, start_workers=True):
    token=initialize();store=store or Store();runner=Runner(store,token)
    pair_codes={};browser_sessions={}

    async def sample_hardware():
        while True:
            memory=psutil.virtual_memory();swap=psutil.swap_memory()
            data={'cpu_percent':psutil.cpu_percent(),'ram_used':memory.used,'ram_total':memory.total,
                'ram_percent':memory.percent,'swap_used':swap.used,'swap_total':swap.total,'gpu':None,'scope':'device / WSL'}
            try:
                gpu_executable=shutil.which('nvidia-smi') or '/usr/lib/wsl/lib/nvidia-smi'
                p=await asyncio.create_subprocess_exec(gpu_executable,'--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu,power.draw',
                    '--format=csv,noheader,nounits',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
                out,_=await asyncio.wait_for(p.communicate(),3)
                if p.returncode==0:
                    fields=out.decode().splitlines()[0].split(',')
                    data['gpu']={'name':fields[0].strip(),'memory_total_mb':float(fields[1]),'memory_used_mb':float(fields[2]),
                        'utilization_percent':float(fields[3]),'temperature_c':float(fields[4]),'power_w':float(fields[5])}
            except (OSError,ValueError,IndexError,asyncio.TimeoutError):
                if 'p' in locals() and p.returncode is None:p.kill();await p.wait()
            store.hardware(data)
            await asyncio.sleep(5)

    @asynccontextmanager
    async def lifespan(app):
        tasks=[]
        if start_workers:
            store.interrupt_running()
            for job in workspace.recover():store.event(job,'workspace-apply-rolled-back',{'reason':'interrupted apply found at startup'})
            tasks=[asyncio.create_task(runner.scheduler()),asyncio.create_task(sample_hardware())]
        app.state.store=store;app.state.runner=runner
        yield
        store.interrupt_running()
        for p in list(runner.active.values()):await stop_process(p)
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)

    app=FastAPI(title='Local Worker Hub',lifespan=lifespan,docs_url=None,redoc_url=None)
    app.state.store=store;app.state.runner=runner

    @app.middleware('http')
    async def boundary(request:Request,call_next):
        if request.headers.get('host') not in ALLOWED_HOSTS:return Response('Invalid host',400)
        if request.headers.get('origin') and request.headers['origin'] not in ORIGINS:
            return Response('Cross-origin access denied',403)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Cache-Control']='no-store'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        return response

    def auth(request:Request):
        bearer=request.headers.get('authorization','')
        if secrets.compare_digest(bearer,'Bearer '+token):return 'api'
        cookie=request.cookies.get('worker_session','')
        if browser_sessions.get(cookie,0)>time.time():
            if request.method not in ('GET','HEAD') and request.headers.get('origin') not in ORIGINS:
                raise HTTPException(403,'Browser mutations require same-origin requests')
            return 'browser'
        raise HTTPException(401,'Pair this browser using local-worker dashboard')

    @app.get('/api/health')
    def health():return {'ok':True,'version':'0.2.0'}

    @app.post('/api/pair-code',dependencies=[Depends(auth)])
    def pair_code():
        pair_codes.clear();code=secrets.token_urlsafe(12);pair_codes[code]=time.time()+300
        return {'code':code,'expires_in':300}

    @app.post('/api/pair')
    async def pair(request:Request,response:Response):
        if request.headers.get('origin') not in ORIGINS:raise HTTPException(403,'Pair from the local dashboard')
        body=await request.json();code=body.get('code','')
        if not isinstance(code,str) or pair_codes.pop(code,0)<time.time():raise HTTPException(401,'Invalid or expired pairing code')
        session=secrets.token_urlsafe(32);browser_sessions[session]=time.time()+8*3600
        response.set_cookie('worker_session',session,httponly=True,samesite='strict',max_age=8*3600)
        return {'ok':True}

    @app.get('/api/jobs',dependencies=[Depends(auth)])
    def jobs(view:Literal['full','compact']='full'):
        items=store.list()
        return [history_item(j) for j in items] if view=='compact' else items

    @app.post('/api/jobs',dependencies=[Depends(auth)])
    def submit(request:JobRequest):
        if request.in_place and request.caller=='mcp':
            raise HTTPException(403,'in_place is not available to MCP callers: Editor jobs run in a private workspace and change your tree only through apply_result. Humans can use local-worker --in-place.')
        if request.board or request.board_mode:
            raise HTTPException(410,'The multi-model Board was retired: it was 3-4x slower with no measured benefit (benchmarks/RESULTS.md). Use the default flow, or --verify for strict research.')
        if request.agent_loop and request.role not in ('personal','researcher'):
            raise HTTPException(409,'The older tool loop is only available for public web roles (--verify research); repository roles use the host pipelines, which measured better.')
        try:
            if request.source_job_id:raise ValueError('Use the saved-result analysis endpoint')
            for ident in request.evidence_job_ids:
                evidence=store.get(ident)
                if not evidence or evidence['state'] in ('queued','running') or not evidence.get('result') or evidence['request'].get('repo')!=request.repo:
                    raise ValueError('Evidence must be a finished job from the same canonical repository')
            return store.submit(expand_profile(request))
        except ValueError as e:raise HTTPException(409,str(e))
        except OverflowError as e:raise HTTPException(429,str(e))

    @app.get('/api/jobs/{ident}',dependencies=[Depends(auth)])
    def job(ident:str,view:Literal['full','compact']='full'):
        value=store.get(ident)
        if not value:raise HTTPException(404,'Unknown job')
        if view=='compact':
            return history_item(value) | {'request':{k:v for k,v in value['request'].items() if k in ('role','task','repo','caller','summary_mode','workflow','execution_preset','handoff_id','review_pass','board','board_mode')},
                                         'review':value['review'],'result':result_summary(value) if value['result'] else None,
                                         'progress':progress_summary(value,store.queue_position(ident),queue_wait(value))}
        return value

    @app.get('/api/jobs/{ident}/result',dependencies=[Depends(auth)])
    def result(ident:str,view:Literal['full','summary','brief']='full'):
        value=job(ident)
        return result_brief(value) if view=='brief' else result_summary(value) if view=='summary' else value.get('result')

    def queue_wait(value):
        # Upper bound: the running job's unused budget plus the budgets of queued jobs ahead of this one.
        if value['state']!='queued':return None
        total=0
        for other in store.list(200):
            if other['id']==value['id'] or other['state'] not in ('queued','running') or (other['state']=='queued' and other['created']>value['created']):continue
            used=(time.time()-(other.get('started') or other['created'])) if other['state']=='running' else 0
            total+=max(0,other['request']['timeout']-used)
        return round(total)

    @app.get('/api/jobs/{ident}/progress',dependencies=[Depends(auth)])
    def progress(ident:str):
        value=job(ident);return progress_summary(value,store.queue_position(ident),queue_wait(value))

    @app.get('/api/jobs/{ident}/wait',dependencies=[Depends(auth)])
    async def wait(ident:str,timeout_seconds:int=Query(default=25,ge=1,le=30)):
        original=progress(ident);deadline=time.monotonic()+timeout_seconds
        while time.monotonic()<deadline:
            if original['state'] not in ('queued','running'):break
            await asyncio.sleep(min(.5,max(.01,deadline-time.monotonic())))
            current=progress(ident)
            if any(current.get(k)!=original.get(k) for k in ('state','phase','active_check','queue_position','check_results')):
                return current
        return progress(ident)

    @app.get('/api/jobs/{ident}/events',dependencies=[Depends(auth)])
    def events(ident:str,after:int=Query(default=0,ge=0),limit:int=Query(default=300,ge=1,le=1000),paged:bool=False):
        job(ident);rows=store.events(ident,after,limit+1)
        if not paged:return rows[:limit]
        compact=[dict(r,data={k:v for k,v in r['data'].items() if k not in ('output_tail','argv','cwd')}) for r in rows[:limit]]
        return {'events':compact,'next_cursor':rows[min(len(rows),limit)-1]['id'] if rows else after,'has_more':len(rows)>limit}

    @app.get('/api/project-profile',dependencies=[Depends(auth)])
    def project_profile(repo:str):
        try:return show_profile(repo)
        except (ValueError,OSError) as exc:raise HTTPException(404,str(exc))

    @app.get('/api/jobs/{ident}/trace',dependencies=[Depends(auth)])
    def trace(ident:str,offset:int=Query(default=0,ge=0),limit:int=Query(default=100,ge=1,le=200)):
        job(ident)
        from .trace import read_trace
        try:return read_trace(STATE/'jobs'/ident,offset,limit)
        except ValueError as exc:raise HTTPException(403,str(exc))

    @app.post('/api/jobs/{ident}/summarize',dependencies=[Depends(auth)])
    def summarize(ident:str,request:AnalysisRequest):
        source=job(ident)
        if source['state'] in ('queued','running') or not (source.get('result') or {}).get('checks'):
            raise HTTPException(409,'Analysis requires a finished job with check evidence')
        try:
            analysis=JobRequest(role='validator',repo=source['request']['repo'],source_job_id=ident,summary_mode='local',
                                task='Summarize saved check evidence. Do not diagnose beyond the supplied evidence. No checks will rerun.',
                                idempotency_key=request.idempotency_key,timeout=request.timeout,caller=request.caller)
            value=store.submit(analysis)
            return {'id':value['id'],'state':value['state'],'source_job_id':ident}
        except ValueError as exc:raise HTTPException(409,str(exc))
        except OverflowError as exc:raise HTTPException(429,str(exc))

    @app.post('/api/jobs/{ident}/cancel',dependencies=[Depends(auth)])
    def cancel(ident:str):job(ident);return store.cancel(ident)

    @app.post('/api/jobs/{ident}/review',dependencies=[Depends(auth)])
    def review(ident:str,request:Review):
        job(ident)
        try:value=store.review(ident,request)
        except ValueError as e:raise HTTPException(409,str(e))
        if request.decision!='accepted':
            shipped=outcomes.final_diff(value)  # what the frontier finally left in the tree, for retrieval and router evaluation
            store.event(ident,'final-frontier-diff',shipped)
        return value

    @app.post('/api/jobs/{ident}/outcome',dependencies=[Depends(auth)])
    def outcome(ident:str):
        """Capture the diff the frontier shipped after the worker's snapshot (call it once the frontier has finished its own edits)."""
        value=outcomes.final_diff(job(ident));store.event(ident,'final-frontier-diff',value);return value

    @app.post('/api/intel/{tool}',dependencies=[Depends(auth)])
    def intel(tool:str,body:dict):
        """Tier-0 code intelligence: deterministic, no model call, does not enter the job queue."""
        from .intel import tools as intel_tools
        try:return intel_tools.run(tool,str(body.get('repo') or ''),{k:v for k,v in body.items() if k!='repo'})
        except (ValueError,OSError) as e:raise HTTPException(409,str(e))

    @app.post('/api/testmap',dependencies=[Depends(auth)])
    def testmap_recommend(body:dict):
        """Likely tests for changed files and, when the project profile has a {tests} template check, the concrete approved check to run on them."""
        from . import testmap
        from .intel.tools import provider_for
        from .profiles import load_profile
        repo=str(body.get('repo') or '');changed=[str(p) for p in body.get('paths') or []][:50]
        if not changed:raise HTTPException(409,'paths is required')
        try:index=provider_for(repo).index
        except (ValueError,OSError) as e:raise HTTPException(409,str(e))
        templates=[]
        try:
            profile,_=load_profile(repo)
            templates=[c.model_dump() for group in profile.groups.values() for c in group if testmap.PLACEHOLDER in c.argv]
        except (ValueError,OSError):pass
        return testmap.recommend(index,changed,templates,testmap.history_from_jobs(store.list(100000)),int(body.get('limit') or 8))

    @app.post('/api/route',dependencies=[Depends(auth)])
    def route(body:dict):
        """Advisory tier for a task: Tier 0 (host tools), Tier 1 (local model) or Tier 2 (frontier keeps it), with the reasons and the evidence behind them."""
        from . import router
        role=str(body.get('role') or 'editor')
        if role not in ('investigator','editor','validator'):raise HTTPException(409,'role must be investigator, editor or validator')
        return router.estimate(role,body.get('kind') or None,str(body.get('task') or ''),int(body.get('files') or 0),store.list(100000),bool(body.get('tier0_answerable')),
                               bool(body.get('gate_tripped')),bool(body.get('ambiguous_cause')))

    @app.get('/api/outcomes',dependencies=[Depends(auth)])
    def outcome_stats(include_eval:bool=False):
        from . import router
        return {'by_kind':outcomes.stats(store.list(100000),include_eval),'calibration':router.calibration(store.list(100000))}

    @app.post('/api/jobs/{ident}/apply',dependencies=[Depends(auth)])
    def apply_result(ident:str,options:ApplyOptions|None=None):
        record=job(ident);options=options or ApplyOptions()
        if record['request']['role']!='editor' or record['state'] in ('queued','running'):raise HTTPException(409,'Apply needs a finished Editor job')
        try:value=workspace.apply(ident,options.accept_removals,options.revalidate,options.run_checks,options.accept_stale)
        except workspace.WorkspaceError as e:raise HTTPException(409,str(e))
        except ScopeError as e:raise HTTPException(403,str(e))
        store.event(ident,'workspace-'+value['state'],{k:value.get(k) for k in ('applied','conflicts')})
        return value

    @app.post('/api/jobs/{ident}/revert',dependencies=[Depends(auth)])
    def revert_result(ident:str):
        record=job(ident)
        if record['request']['role']!='editor' or record['state'] in ('queued','running'):raise HTTPException(409,'Revert needs a finished Editor job')
        try:value=workspace.revert(ident)
        except workspace.WorkspaceError as e:raise HTTPException(409,str(e))
        store.event(ident,'workspace-'+value['state'],{k:value.get(k) for k in ('reverted','conflicts')})
        return value

    @app.post('/api/jobs/{ident}/discard',dependencies=[Depends(auth)])
    def discard_result(ident:str):
        record=job(ident)
        if record['request']['role']!='editor' or record['state'] in ('queued','running'):raise HTTPException(409,'Discard needs a finished Editor job')
        try:value=workspace.discard(ident)
        except workspace.WorkspaceError as e:raise HTTPException(409,str(e))
        store.event(ident,'workspace-'+value['state'],{})
        return value

    @app.get('/api/jobs/{ident}/artifacts/{name}',dependencies=[Depends(auth)])
    def artifact(ident:str,name:str):
        job(ident)
        return FileResponse(artifact_path(ident,name),media_type='text/plain',filename=name)

    @app.get('/api/jobs/{ident}/artifact-page/{name}',dependencies=[Depends(auth)])
    def artifact_page(ident:str,name:str,offset:int=Query(default=0,ge=0),limit:int=Query(default=4000,ge=1,le=16000)):
        job(ident);path=artifact_path(ident,name)
        with path.open('rb') as f:
            f.seek(offset);data=f.read(limit);more=f.read(1)!=b''
        # Keep page boundaries outside a UTF-8 continuation sequence where possible.
        if more:
            for trim in range(4):
                try:data[:len(data)-trim if trim else None].decode('utf-8');break
                except UnicodeDecodeError:continue
            if trim and len(data)>trim:data=data[:-trim]
        return {'artifact':name,'offset':offset,'next_offset':offset+len(data),'has_more':more,
                'text':data.decode('utf-8',errors='replace'),'unit':'bytes'}

    @app.get('/api/chat-options',dependencies=[Depends(auth)])
    def chat_options():
        """Registered model aliases for the dashboard chat; `installed` is null when Ollama cannot be asked."""
        return {'models':[{'alias':alias,'name':value['name'],'installed':probe(value['name'],2).get('installed')} for alias,value in registered_models().items()]}

    @app.get('/api/summary',dependencies=[Depends(auth)])
    def summary():return store.summary()

    @app.get('/api/hardware',dependencies=[Depends(auth)])
    def hardware():return store.samples()

    @app.post('/inference/{ident}/v1/chat/completions',dependencies=[Depends(auth)])
    async def inference(ident:str,request:Request):
        current=job(ident)
        if current['state']!='running':raise HTTPException(409,'Inference requires a running job')
        body=await request.json()
        if body.get('model')!=model_name('qwen'):raise HTTPException(403,'Only the legacy OpenCode model is allowed on this endpoint')
        names=[t.get('function',{}).get('name','') for t in body.get('tools',[])]
        systems=[m.get('content','') for m in body.get('messages',[]) if m.get('role')=='system']
        store.event(ident,'request_metadata',{'tools':names,'system_chars':sum(len(str(s)) for s in systems),
            'report_contract_present':any('LOCAL_WORKER_REPORT' in str(s) for s in systems)})
        # V2 agent.request overlays are not yet forwarded. Enforce bounded runtime settings here.
        body['max_tokens']=4096
        body['reasoning_effort']='medium' if current['request']['role']=='editor' and names else 'none'
        started=time.monotonic()
        client=httpx.AsyncClient(timeout=httpx.Timeout(900,connect=5),trust_env=False)
        if body.get('stream'):body['stream_options']={'include_usage':True}
        sending=asyncio.create_task(client.send(client.build_request('POST','http://127.0.0.1:11434/v1/chat/completions',json=body),stream=True))
        try:
            while not sending.done():
                if store.get(ident)['state']!='running' or await request.is_disconnected():
                    sending.cancel();await asyncio.gather(sending,return_exceptions=True)
                    raise HTTPException(409,'Inference interrupted')
                await asyncio.wait({sending},timeout=.2)
            upstream=await sending
        except BaseException:
            sending.cancel();await asyncio.gather(sending,return_exceptions=True);await client.aclose();raise
        if upstream.status_code!=200:
            msg=(await upstream.aread())[:1000];await upstream.aclose();await client.aclose()
            raise HTTPException(502,'Ollama error: '+msg.decode(errors='replace'))
        def usage(value,first=None):
            u=value.get('usage')
            if not u:return
            cached=u.get('prompt_tokens_details',{}).get('cached_tokens',0) or 0
            prompt=u.get('prompt_tokens',0) or 0;output=u.get('completion_tokens',0) or 0
            duration=time.monotonic()-started
            store.event(ident,'inference',{'input':max(0,prompt-cached),'output':output,'cache_read':cached,'cache_write':0,
                'reasoning':u.get('completion_tokens_details',{}).get('reasoning_tokens',0) or 0,
                'reasoning_breakdown_available':'reasoning_tokens' in u.get('completion_tokens_details',{}),
                'context_tokens':prompt,'context_limit':16384,'seconds':duration,
                'tokens_per_second':output/(time.monotonic()-first) if first and time.monotonic()>first else output/duration if duration else None,
                'measurement':'Ollama usage; end-to-end request throughput (includes prefill/load for nonstreaming)','phase':'provider_request'})
        async def stream():
            first=None
            try:
                async for line in upstream.aiter_lines():
                    if line.startswith('data: ') and line[6:]!='[DONE]':
                        try:
                            value=json.loads(line[6:])
                            if first is None and any(c.get('delta') for c in value.get('choices',[])):first=time.monotonic()
                            usage(value,first)
                        except ValueError:pass
                    yield (line+'\n').encode()
            finally:await upstream.aclose();await client.aclose()
        if body.get('stream'):return StreamingResponse(stream(),media_type='text/event-stream')
        try:
            data=await upstream.aread();usage(json.loads(data));return Response(data,media_type='application/json')
        finally:await upstream.aclose();await client.aclose()

    @app.post('/inference/{ident}/chat',dependencies=[Depends(auth)])
    async def native_inference(ident:str,request:Request):
        current=job(ident)
        if current['state']!='running':raise HTTPException(409,'Inference requires a running job')
        body=await request.json()
        if body.get('model') not in allowed_names():
            raise HTTPException(403,'Only bounded local inference is allowed')
        options=body.get('options') or {}
        cap=8192 if current['request'].get('model_output')==8192 else 4096  # 8192 only when the job asked for it explicitly
        if options.get('num_ctx') not in (16384,32768) or not 1<=options.get('num_predict',0)<=cap:
            raise HTTPException(400,'Unsupported context or output budget')
        relieved=await relieve_memory()
        if relieved:store.event(ident,'memory-guard',relieved)
        started=time.monotonic()
        def record_usage(value):
            prompt=value.get('prompt_eval_count',0) or 0;output=value.get('eval_count',0) or 0
            store.event(ident,'inference',{'model':body['model'],'input':prompt,'output':output,'cache_read':0,'cache_write':0,
                'cache_breakdown_available':False,'reasoning':0,'reasoning_breakdown_available':False,'context_tokens':prompt,
                'context_limit':options['num_ctx'],'seconds':time.monotonic()-started,
                'prompt_eval_seconds':(value.get('prompt_eval_duration') or 0)/1e9,
                'eval_seconds':(value.get('eval_duration') or 0)/1e9,
                'tokens_per_second':output/((value.get('eval_duration') or 0)/1e9) if value.get('eval_duration') else None,
                'load_seconds':(value.get('load_duration') or 0)/1e9,
                'measurement':'Ollama native usage; cache and reasoning breakdown unavailable','phase':'provider_request'})
        if body.get('stream'):
            client=httpx.AsyncClient(timeout=httpx.Timeout(900,connect=5),trust_env=False)
            try:
                upstream=await client.send(client.build_request('POST','http://127.0.0.1:11434/api/chat',json=body),stream=True)
            except httpx.TransportError as error:
                # Ollama dropping the connection (for example killed by the OOM killer) is a gateway failure the
                # engine may retry once, not an opaque internal error.
                await client.aclose()
                store.event(ident,'inference-error',{'upstream_status':None,'message':'Ollama connection failed: '+type(error).__name__})
                raise HTTPException(502,'Ollama connection failed; see private inference-error event')
            except BaseException:
                await client.aclose();raise
            if upstream.status_code!=200:
                # Keep the upstream failure visible in private job history;
                # previously every model error became an opaque gateway 502.
                data=bytearray()
                try:
                    async with asyncio.timeout(5):
                        async for chunk in upstream.aiter_bytes():
                            data.extend(chunk[:max(0,4096-len(data))])
                            if len(data)>=4096:break
                    try:detail=json.loads(data).get('error','Ollama returned an error')
                    except (ValueError,AttributeError):detail='Ollama returned a non-JSON error'
                except (httpx.HTTPError,TimeoutError):detail='Ollama error body unavailable'
                finally:await upstream.aclose();await client.aclose()
                store.event(ident,'inference-error',{'upstream_status':upstream.status_code,'message':str(detail)[:500]})
                raise HTTPException(502,'Ollama request failed; see private inference-error event')
            async def native_stream():
                recorded=False
                try:
                    async for line in upstream.aiter_lines():
                        if store.get(ident)['state']!='running' or await request.is_disconnected():break
                        if not line:continue
                        value=json.loads(line)
                        if value.get('done') and not recorded:
                            record_usage(value);recorded=True
                        yield (line+'\n').encode()
                finally:await upstream.aclose();await client.aclose()
            return StreamingResponse(native_stream(),media_type='application/x-ndjson')
        async with httpx.AsyncClient(timeout=httpx.Timeout(900,connect=5),trust_env=False) as client:
            pending=asyncio.create_task(client.post('http://127.0.0.1:11434/api/chat',json=body))
            try:
                while not pending.done():
                    if store.get(ident)['state']!='running' or await request.is_disconnected():
                        pending.cancel();await asyncio.gather(pending,return_exceptions=True)
                        raise HTTPException(409,'Inference interrupted')
                    await asyncio.wait({pending},timeout=.2)
                response=await pending
            finally:
                if not pending.done():pending.cancel()
        if response.status_code!=200:
            raise HTTPException(502,'Ollama error: '+response.text[:800])
        value=response.json()
        record_usage(value)
        return value

    dist=PROJECT/'frontend/dist'
    if dist.exists():app.mount('/',StaticFiles(directory=dist,html=True),name='dashboard')
    else:
        @app.get('/')
        def placeholder():return Response('Dashboard build pending. CLI and MCP remain available.',media_type='text/plain')
    return app

def main():
    import uvicorn
    os.umask(0o077)
    uvicorn.run(create_app(),host='127.0.0.1',port=8765,access_log=False)

if __name__=='__main__':main()
