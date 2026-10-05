"""Worker capabilities. Native OpenCode file/shell tools remain disabled."""
import asyncio
import fnmatch
import hashlib
import ipaddress
import json
import re
import os
import socket
import time
from pathlib import Path
from typing import Annotated,Literal
from pydantic import Field
from urllib.parse import urlsplit
import fcntl
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.fastmcp import FastMCP
from .models import JobRequest,WebRequirement,WebResearchPlan

EXCLUDED = {'.git','.aws','.ssh','.codex','.claude','.venv','node_modules','dist','.next',
            '__pycache__','.local-archive','backup','backups','coverage','.cache'}

class ScopeError(ValueError): pass

def sensitive(path):
    p = Path(path)
    name = p.name.lower()
    if any(part in EXCLUDED for part in p.parts): return True
    if name.endswith('.env.example') or name == '.env.example': return False
    return ('env' in name and fnmatch.fnmatch(name,'*.env*')) or name in {'.env','id_rsa','id_ed25519',
        '.credentials.json','credentials','credentials.json','api-token'} or name.endswith(('.pem','.key','.sqlite','.sqlite3','.db','.sql.gz','.dump'))

def glob_match(path, pattern):
    """Forgiving path glob: `**/` may match zero directories and a trailing slash means "everything below"."""
    text = str(path)
    if pattern.endswith('/'):
        return text.startswith(pattern)
    if fnmatch.fnmatch(text, pattern) or Path(text).match(pattern):
        return True
    return '**/' in pattern and fnmatch.fnmatch(text, pattern.replace('**/', ''))

class ScopedFiles:
    def __init__(self, request, audit=lambda *a:None):
        self.request = request
        self.root = Path(request.repo).resolve() if request.repo else None
        self.audit = audit
        self.observed = {}
        self.observed_lines = {}
        self.evidence = []
        self.allowed = set()
        self.read_scope = []
        self.read_scope = [self.path(name, exists=False) for name in request.read_paths]
        for p in request.allowed_paths:
            target = self.path(p, exists=False)
            self.allowed.add(str(target.relative_to(self.root)))
        if self.read_scope and any(not self.readable(self.root/p) for p in self.allowed):
            raise ScopeError('Every authorized edit path must be within read_paths')

    def readable(self, target):
        return not self.read_scope or any(target == p or target.is_relative_to(p) for p in self.read_scope)

    def path(self, path, exists=True):
        if not self.root: raise ScopeError('This role has no repository access')
        p = Path(path)
        if p.is_absolute():
            try: p = p.relative_to(self.root)
            except ValueError: raise ScopeError('Path is outside the assigned repository')
        if '..' in p.parts or sensitive(p): raise ScopeError('Path is outside the readable scope')
        target = self.root / p
        for part in [target,*target.parents]:
            if part == self.root: break
            if part.is_symlink(): raise ScopeError('Symlinks are not accessible through worker tools')
        real = target.resolve(strict=exists)
        if not real.is_relative_to(self.root): raise ScopeError('Path escapes the repository')
        if getattr(self,'read_scope',None) and not self.readable(real):
            raise ScopeError('Path is outside the explicit read scope')
        return real

    def open_fd(self, path, flags, mode=0o644):
        # Traverse each parent with O_NOFOLLOW, including after path validation.
        target = self.path(path, exists=False)
        parts = target.relative_to(self.root).parts
        if not parts: raise ScopeError('A file path is required')
        fd = os.open(self.root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                nxt = os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                os.close(fd);fd=nxt
            result=os.open(parts[-1],flags|os.O_NOFOLLOW,mode,dir_fd=fd)
            if os.fstat(result).st_nlink>1:
                os.close(result);raise ScopeError('Hard-linked files are outside worker scope')
            return result
        finally: os.close(fd)

    def read_file(self, path: str, start: int=1, lines: int=120) -> str:
        if self.request.role not in ('investigator','editor'): raise ScopeError('Read is unavailable for this role')
        target=self.path(path)
        if not target.is_file() or target.stat().st_size > 2_000_000: raise ScopeError('Not a bounded text file')
        with os.fdopen(self.open_fd(path,os.O_RDONLY),'rb') as f: data=f.read(2_000_001)
        text=data.decode('utf-8'); rows=text.splitlines()
        start=max(1,start);lines=max(1,min(lines,120))
        body='\n'.join(f'{i+1}: {v}' for i,v in enumerate(rows) if start-1<=i<start-1+lines)[:12000]
        relative=str(target.relative_to(self.root))
        self.observed[relative]=hashlib.sha256(data).hexdigest()
        digest=self.observed[relative]
        coverage=self.observed_lines.get(relative)
        if not coverage or coverage['hash']!=digest:coverage={'hash':digest,'lines':set()}
        # Count only complete lines actually returned within the byte cap.
        visible = body.splitlines()
        coverage['lines'].update(range(start, start+len(visible)-(1 if len(body)>=12000 else 0)))
        self.observed_lines[relative]=coverage
        evidence_id=f'E{len(self.evidence)+1}'
        self.evidence.append({'id':evidence_id,'path':relative,'start':start,'lines':body})
        result={'path':relative,'evidence_id':evidence_id,'sha256':self.observed[relative],
                'total_lines':len(rows),'content':body,'truncated':len(rows)>start-1+lines or len(body)>=12000}
        self.audit('read',{'path':result['path'],'start':start,'lines':lines,'sha256':result['sha256'],
                           'evidence_id':evidence_id,'excerpt':body})
        return json.dumps(result)

    def read_files(self, paths: list[str]) -> str:
        if not 1 <= len(paths) <= 6: raise ScopeError('Read one to six known files')
        results=[]
        for path in paths:
            try:results.append(json.loads(self.read_file(path,lines=80)))
            except (OSError,UnicodeError,ScopeError) as exc:
                self.audit('read_missing',{'path':path,'error':str(exc)[:250]})
                results.append({'path':path,'error':str(exc)[:250]})
        return json.dumps({'files':results})

    def inventory(self):
        count=0
        for base,dirs,files in os.walk(self.root,followlinks=False):
            dirs[:]=[d for d in dirs if d not in EXCLUDED and not (Path(base)/d).is_symlink()]
            for name in sorted(files):
                p=(Path(base)/name).relative_to(self.root)
                if sensitive(p) or (self.root/p).is_symlink(): continue
                if not self.readable(self.root/p): continue
                yield p
                count+=1
                if count>=20000: return

    def glob_files(self, pattern: str='*') -> str:
        if self.request.role not in ('investigator','editor'): raise ScopeError('Discovery unavailable')
        if len(pattern)>300 or '..' in Path(pattern).parts: raise ScopeError('Invalid glob')
        paths=[str(p) for p in self.inventory() if glob_match(p,pattern)]
        self.audit('glob',{'pattern':pattern,'matches':len(paths)})
        return json.dumps({'paths':paths[:150],'truncated':len(paths)>150})

    def search_text(self, text: str, pattern: str='*') -> str:
        """Find a literal string in files. `text` is the exact text to look for; `pattern` optionally limits which files, as a path glob such as 'hub/*.py' or 'src/'."""
        if self.request.role not in ('investigator','editor'): raise ScopeError('Search unavailable')
        if not text or len(text)>500: raise ScopeError('Use a bounded literal search')
        matches=[]
        for p in self.inventory():
            if not glob_match(p,pattern): continue
            try:
                target=self.path(str(p))
                if target.stat().st_size>500000: continue
                with os.fdopen(self.open_fd(str(p),os.O_RDONLY),'rb') as f: data=f.read(500001)
                if b'\x00' in data: continue
                for n,line in enumerate(data.decode('utf-8').splitlines(),1):
                    if text in line: matches.append({'path':str(p),'line':n,'text':line[:300]})
                    if len(matches)>=60: break
            except (OSError,UnicodeError,ScopeError): continue
            if len(matches)>=60: break
        self.audit('search',{'text':text,'pattern':pattern,'matches':matches[:60],
                             'evidence_id':f'E{len(self.evidence)+1}'})
        evidence_id=f'E{len(self.evidence)+1}'
        self.evidence.append({'id':evidence_id,'matches':matches})
        result={'evidence_id':evidence_id,'matches':matches,'truncated':len(matches)>=60}
        if not matches:result['hint']="No matches. 'text' is the literal string to find and 'pattern' is a file path glob (for example 'hub/*.py' or 'src/'); check you did not swap them."
        return json.dumps(result)[:12000]

    def write_file(self, path: str, content: str, expected_sha256: str | None=None) -> str:
        if self.request.role!='editor': raise ScopeError('Only Editor can edit')
        target=self.path(path,exists=False);relative=str(target.relative_to(self.root))
        if relative not in self.allowed: raise ScopeError('File was not authorized for this job')
        encoded=content.encode()
        if len(encoded)>500000: raise ScopeError('Edit is too large')
        exists=target.exists()
        if exists:
            coverage=self.observed_lines.get(relative,{}).get('lines',set())
            total=len(target.read_text().splitlines())
            if not set(range(1,total+1))<=coverage:
                raise ScopeError('Whole-file replacement requires a complete fresh read; use edit_file or replace_lines for a partial read')
        if expected_sha256 is None and exists: expected_sha256=self.observed.get(relative)
        if exists and expected_sha256 is None: raise ScopeError('Read the file before editing')
        if not exists and expected_sha256 is not None: raise ScopeError('File disappeared since inspection')
        flags=os.O_RDWR if exists else os.O_RDWR|os.O_CREAT|os.O_EXCL
        fd=self.open_fd(path,flags)
        with os.fdopen(fd,'r+b') as f:
            fcntl.flock(f,fcntl.LOCK_EX)
            previous=f.read(2_000_001)
            if exists and hashlib.sha256(previous).hexdigest()!=expected_sha256:
                raise ScopeError('File changed since the last read. Read it again before editing. No edit applied.')
            f.seek(0);f.write(encoded);f.truncate();f.flush();os.fsync(f.fileno())
        result={'path':relative,'sha256':hashlib.sha256(encoded).hexdigest(),'bytes':len(encoded)}
        self.observed[relative]=result['sha256']
        self.audit('edit',result)
        return json.dumps(result)

    def edit_file(self, path: str, old: str, new: str, expected_sha256: str | None=None) -> str:
        target=self.path(path)
        relative=str(target.relative_to(self.root))
        expected_sha256=expected_sha256 or self.observed.get(relative)
        if expected_sha256 is None: raise ScopeError('Read the file before editing')
        with os.fdopen(self.open_fd(path,os.O_RDONLY),'rb') as f: data=f.read(500001)
        if hashlib.sha256(data).hexdigest()!=expected_sha256: raise ScopeError('File changed since the last read. Read it again before editing. No edit applied.')
        content=data.decode('utf-8')
        if not old or content.count(old)!=1:
            locations=[i+1 for i,line in enumerate(content.splitlines()) if old and old.splitlines()[0] in line][:10]
            raise ScopeError('Old text must match exactly once; matching line starts: '+str(locations)+'. Read a specific range and use replace_lines.')
        return self._replace_observed(path,content.replace(old,new,1),expected_sha256)

    def _replace_observed(self, path, content, expected_sha256):
        # A surgical edit preserves all unobserved content. write_file remains stricter.
        target=self.path(path);relative=str(target.relative_to(self.root))
        if self.request.role!='editor' or relative not in self.allowed:raise ScopeError('File was not authorized for editing')
        with os.fdopen(self.open_fd(path,os.O_RDWR),'r+b') as f:
            fcntl.flock(f,fcntl.LOCK_EX)
            before=f.read(2_000_001)
            if hashlib.sha256(before).hexdigest()!=expected_sha256:raise ScopeError('File changed since the last read. No edit applied.')
            data=content.encode('utf-8')
            if len(data)>500000:raise ScopeError('Edit is too large')
            f.seek(0);f.write(data);f.truncate();f.flush();os.fsync(f.fileno())
        digest=hashlib.sha256(data).hexdigest();self.observed[relative]=digest
        self.observed_lines.pop(relative,None)
        self.audit('edit',{'path':relative,'sha256':digest,'bytes':len(data)})
        return json.dumps({'path':relative,'sha256':digest,'bytes':len(data)})

    def delete_file(self, path, expected_sha256):
        """Delete an authorized file that the job declared in delete_paths, if it is still exactly what was read."""
        if self.request.role!='editor': raise ScopeError('Only Editor can edit')
        target=self.path(path);relative=str(target.relative_to(self.root))
        if relative not in self.allowed or relative not in set(self.request.delete_paths): raise ScopeError('Deleting this file was not declared in delete_paths')
        with os.fdopen(self.open_fd(path,os.O_RDWR),'r+b') as f:
            fcntl.flock(f,fcntl.LOCK_EX)
            if hashlib.sha256(f.read(2_000_001)).hexdigest()!=expected_sha256:raise ScopeError('File changed since the last read. Nothing deleted.')
            os.unlink(target)
        self.observed.pop(relative,None);self.observed_lines.pop(relative,None)
        self.audit('delete',{'path':relative,'sha256':expected_sha256})
        return json.dumps({'path':relative,'deleted':True})

    def replace_lines(self, path: str, start: int, end: int, content: str) -> str:
        """Replace an inclusive, freshly observed line range; preserve every surrounding line."""
        target=self.path(path);relative=str(target.relative_to(self.root))
        coverage=self.observed_lines.get(relative,{})
        if start<1 or end<start or not set(range(start,end+1))<=coverage.get('lines',set()):
            raise ScopeError('Read the complete requested line range before replacing it')
        with os.fdopen(self.open_fd(path,os.O_RDONLY),'rb') as f:data=f.read(500001)
        if hashlib.sha256(data).hexdigest()!=coverage.get('hash'):raise ScopeError('File changed since the last read. Read it again before editing.')
        text=data.decode('utf-8');rows=text.splitlines(keepends=True)
        newline='\r\n' if '\r\n' in text else '\n'
        replacement=content.replace('\r\n','\n').replace('\n',newline)
        if replacement and (end<len(rows) or rows[end-1].endswith(('\n','\r'))) and not replacement.endswith(newline):replacement+=newline
        return self._replace_observed(path,''.join(rows[:start-1])+replacement+''.join(rows[end:]),coverage['hash'])

async def public_url(url):
    parsed=urlsplit(url)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
        raise ScopeError('Only public HTTP(S) URLs without credentials are allowed')
    if parsed.port not in (None,80,443) or parsed.hostname.endswith(('.local','.internal','.localhost')):
        raise ScopeError('Private destinations are blocked')
    try: addresses=await asyncio.to_thread(socket.getaddrinfo,parsed.hostname,parsed.port or 443,type=socket.SOCK_STREAM)
    except OSError: raise ScopeError('URL hostname could not be resolved')
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ScopeError('Private destinations are blocked')
    return url

class Research:
    def __init__(self,audit,provider='exa',health_path=None,task='',plan_path=None,ledger=None):
        self.ledger=ledger;self.searches=0;self.fetches=0;self.audit=audit;self.pages={};self.page_providers={};self.page_metadata={};self.provider=provider
        self.health_path=health_path;self.exa_unavailable=False
        if health_path and health_path.exists():
            self.exa_unavailable=json.loads(health_path.read_text()).get('retry_after',0)>time.time()
        self.task=task;self.plan_path=plan_path;self.plan=None;self.plan_versions=0

    def plan_web_task(self, objective: str, needs_current_evidence: bool, requirements: list[WebRequirement], strategy: list[str], result_format: str, stop_when: str, revision_reason: str='') -> str:
        """Before web research, interpret the ORIGINAL request and define your own evidence/acceptance checks and search breadth. Each requirement object needs exactly these keys: id, task_quote, requirement, acceptance. Each task_quote must be copied from that request. Choose the strategy and output yourself; do not invent user requirements. The reviewer independently checks/revises this plan. This tool grants no new permissions or budget."""
        if self.plan_versions>=2:raise ScopeError('Two research plan versions already recorded for this phase; use existing tools and report remaining gaps')
        if self.plan is not None and not revision_reason.strip():raise ScopeError('A plan revision needs a reason grounded in observed evidence or an omitted original requirement')
        if len(revision_reason)>1000:raise ScopeError('Plan revision reason is too long')
        plan=WebResearchPlan(objective=objective,needs_current_evidence=needs_current_evidence,requirements=requirements,strategy=strategy,result_format=result_format,stop_when=stop_when)
        if len({r.id for r in plan.requirements})!=len(plan.requirements):raise ScopeError('Requirement IDs must be unique')
        if any(r.task_quote not in self.task for r in plan.requirements):raise ScopeError('Every task_quote must be copied exactly from the original request, not web text or transport instructions')
        if any(len(step)>1500 for step in strategy):raise ScopeError('Research strategy is too long')
        self.plan=plan;self.plan_versions+=1
        if self.plan_path:
            self.plan_path.write_text(plan.model_dump_json());self.plan_path.chmod(0o600)
        self.audit('web_research_plan',{**plan.model_dump(),'phase':self.plan_path.stem if self.plan_path else 'work',
                                      'revision':self.plan_versions,'revision_reason':revision_reason})
        return plan.model_dump_json()+'\nExecute this plan with the available scoped tools. Unknowns and contradictions must be reported; preserve useful verified findings.'

    def _charge(self,kind):
        if not self.ledger:return
        from .ledger import BudgetExhausted
        try:self.ledger.charge(kind)
        except BudgetExhausted as error:raise ScopeError(str(error)) from None

    def _reuse(self,url):
        # A page another phase already read within the freshness window, with its original provenance.
        if not self.ledger or url in self.pages:return
        entry=self.ledger.load_page(url)
        if entry:
            self.pages[url]=(entry['text'],entry['observed_at']);self.page_providers[url]=entry['provider'];self.page_metadata[url]=entry['metadata']

    def require_plan(self):
        if self.task and self.plan is None:raise ScopeError('Call plan_web_task first to define requirements and evidence checks from the original request')

    @staticmethod
    def provider_arguments(name, args, schema):
        """Adapt only known public parameters; fail closed on new required fields."""
        props=schema.get('properties',{})
        if name=='web_fetch_exa':
            if 'urls' in props: result={'urls':[args['url']]}
            elif 'url' in props: result={'url':args['url']}
            else: raise ScopeError('Hosted fetch schema changed')
            if 'maxCharacters' in props:result['maxCharacters']=24000
        else:
            result={k:v for k,v in args.items() if k in props}
            if 'objective' in props:result['objective']=args.get('objective') or args['query']
        if set(schema.get('required',[]))-set(result):
            raise ScopeError('Hosted public tool requires unsupported parameters')
        return result

    @staticmethod
    def provider_error(error):
        """Map a raw Exa/MCP failure to ProviderUnavailable (try the next provider) or ScopeError; anything else comes back unchanged."""
        from .web_provider import ProviderUnavailable
        import httpx
        if isinstance(error,TimeoutError):return ProviderUnavailable('Exa public tool timed out')
        if isinstance(error,httpx.TransportError):return ProviderUnavailable('Exa public transport unavailable')
        if isinstance(error,httpx.HTTPStatusError):
            code=error.response.status_code
            return ProviderUnavailable(f'Exa public tool HTTP {code}') if code==429 or code>=500 else ScopeError(f'Exa public tool HTTP {code}')
        return error

    async def call(self,name,args):
        try:return await self.exa_call(name,args)
        except BaseExceptionGroup as group:
            # The MCP client runs inside an anyio task group, which wraps every error raised in it (a rate limit, a timeout)
            # in an ExceptionGroup that no handler for the inner type would catch.
            error=group
            while isinstance(error,BaseExceptionGroup) and error.exceptions:error=error.exceptions[0]
            if not isinstance(error,Exception):raise  # cancellation and exit signals pass through untouched
            raise self.provider_error(error) from None
        except Exception as error:
            mapped=self.provider_error(error)
            if mapped is error:raise
            raise mapped from None

    async def exa_call(self,name,args):
        url='https://mcp.exa.ai/mcp?tools=web_search_exa,web_fetch_exa'
        async with asyncio.timeout(35):
            async with streamablehttp_client(url) as (read,write,_):
                async with ClientSession(read,write) as session:
                    await session.initialize()
                    tools=await session.list_tools()
                    available={t.name:t for t in tools.tools}
                    if name not in available: raise ScopeError('Hosted search tool schema changed or is unavailable')
                    schema=available[name].inputSchema
                    args=self.provider_arguments(name,args,schema)
                    result=await session.call_tool(name,args)
                    from .web_provider import check_exa_response,ProviderUnavailable
                    text='\n'.join(c.text for c in result.content if getattr(c,'type',None)=='text')[:24000]
                    check_exa_response(text)
                    if result.isError: raise ProviderUnavailable('Exa public tool returned an error')
                    return text

    async def search_web(self,query: str, objective: str='') -> str:
        """Discover public sources. Specify what evidence is needed; snippets alone may not verify a requirement."""
        self.require_plan()
        if not query.strip() or len(query)>500: raise ScopeError('Search requires a short public query')
        if self.searches>=3: raise ScopeError('Search budget exhausted; report available findings')
        # The role has no repo access; the caller must supply a sanitized public brief.
        if len(objective)>1000:raise ScopeError('Search objective is too long')
        if any(s in (query+' '+objective).lower() for s in ['localhost','127.0.0.1','api_key=','password=','/home/','bearer ']):
            raise ScopeError('Query appears to contain private context')
        self._charge('search')
        self.searches+=1
        from datetime import datetime, timezone
        from .web_provider import check_exa_response,ProviderUnavailable,langsearch_key
        provider=self.provider;reason=None
        if provider=='exa':
            try:
                if self.exa_unavailable:raise ProviderUnavailable('Exa free endpoint cooldown after rate limit or transient failure')
                text=check_exa_response(await self.call('web_search_exa',{'query':query,'objective':objective,'numResults':5}))
            except (ProviderUnavailable,TimeoutError) as error:
                reason=str(error) if isinstance(error,ProviderUnavailable) else 'Exa public search timed out'
                self.exa_unavailable=True
                if self.health_path:
                    self.health_path.write_text(json.dumps({'retry_after':time.time()+300}));self.health_path.chmod(0o600)
                self.audit('web_provider_unavailable',{'provider':'exa','reason':reason})
                try:langsearch_key()
                except (ValueError,OSError):raise ProviderUnavailable(reason+'; no usable private LangSearch key for fallback') from None
                provider='langsearch'
        self.audit('web_search',{'query':query,'provider':provider,'selected_provider':self.provider,'fallback_reason':reason,'retrieved_at':time.time()})
        prefix='SEARCH_EVIDENCE '+json.dumps({'selected_provider':self.provider,'provider':provider,'fallback_reason':reason})+'\n'
        if reason:prefix+='Exa unavailable; using configured free LangSearch fallback. Do not invent results if this search is unhelpful.\n'
        if provider=='langsearch':
            from .web_provider import langsearch
            retrieved=datetime.now(timezone.utc).isoformat()
            # LangSearch has no separate objective field. Keep the focused query
            # intact rather than diluting it with the model's research brief.
            pages=await langsearch(query)
            rows=[]
            for page in pages:
                url=page.get('url')
                if not isinstance(url,str):continue
                title=str(page.get('name') or '')[:500];body=page.get('text')
                # Only provider-supplied full webpage text can satisfy fetch_web;
                # a snippet is never promoted to an extracted page.
                if isinstance(body,str) and body:
                    self.pages[url]=(f'Title: {title}\nURL: {url}\n'+body[:24000],retrieved)
                    self.page_providers[url]='langsearch-text'
                rows.append(f'Title: {title}\nURL: {url}\n'+str(body or page.get('snippet') or '[No text returned]')[:1800])
            return prefix+'LangSearch response retrieved at '+retrieved+'\nSource discovery excerpts; use fetch_web(mode="current") for current facts.\n'+('\n\n'.join(rows) or '[No results returned]')
        return prefix+'Provider response retrieved at '+datetime.now(timezone.utc).isoformat()+'\nSearch excerpts: discover sources; verify required facts in the relevant page.\n'+text[:10000]

    async def fetch_web(self,url: str, start: Annotated[int,Field(ge=0)]=0,
                        characters: Annotated[int,Field(ge=1,le=8000)]=6000,
                        find: Annotated[str,Field(max_length=200)]='', mode: Literal['current','cached']='current') -> str:
        """Read an exact public URL. current tries the origin directly; blocked reads fall back to explicitly UNVERIFIED hosted extraction. cached opts into hosted text. Page with start/characters or literal find. No JavaScript, accounts or messaging."""
        self.require_plan()
        await public_url(url)
        if start<0 or not 1<=characters<=8000 or len(find)>200 or mode not in ('current','cached'):
            raise ScopeError('fetch_web requires start >= 0, characters 1..8000 (default 6000), find <= 200 characters and mode current|cached. Use next_start or a short literal find for relevant content; do not request the entire page in one call.')
        from datetime import datetime, timezone
        self._reuse(url)
        if url not in self.pages or (mode=='current' and not self.page_metadata.get(url,{}).get('origin_attempted')):
            if self.fetches>=5: raise ScopeError('Fetch budget exhausted; report available findings')
            self._charge('fetch')
            self.fetches+=1
            metadata={'method':'hosted-extraction','requested_url':url,'current_eligible':False,'origin_attempted':mode=='current'}
            if mode=='current':
                from .public_page import fetch_origin
                try:
                    text,metadata=await fetch_origin(url,public_url)
                    metadata['origin_attempted']=True
                    self.pages[url]=(text,metadata['observed_at']);self.page_providers[url]='origin-http'
                except ScopeError:raise
                except Exception as error:
                    if 'Private destinations' in str(error):raise ScopeError('Private destinations are blocked') from None
                    # Never include raw HTTP exceptions, headers or credentials.
                    reason=str(error) if isinstance(error,ValueError) else type(error).__name__
                    metadata['origin_error']=reason[:160]
                    self.audit('web_origin_unavailable',{'url':url,'reason':reason[:160]})
            if metadata['method']!='origin-http':
                if url not in self.pages:
                    from .web_provider import ProviderUnavailable
                    try:
                        if self.exa_unavailable:raise ProviderUnavailable('Exa hosted extraction unavailable during cooldown')
                        hosted=await self.call('web_fetch_exa',{'url':url})
                    except ProviderUnavailable as error:
                        hosted='Hosted extraction unavailable. Current origin content could not be verified.'
                        metadata['hosted_error']=str(error)
                    self.pages[url]=(hosted,datetime.now(timezone.utc).isoformat())
                    self.page_providers[url]='exa-keyless'
                metadata['method']=self.page_providers[url]
            self.page_metadata[url]=metadata
            if self.ledger:self.ledger.store_page(url,self.pages[url][0],metadata,self.page_providers[url],self.pages[url][1])
        text,retrieved=self.pages[url]
        metadata={**self.page_metadata.get(url,{'method':self.page_providers[url],'current_eligible':False,'origin_attempted':False}),
                  'observed_at':retrieved,'requested_url':url}
        prefix='WEB_EVIDENCE '+json.dumps(metadata,ensure_ascii=False)+'\n'
        prefix+=f'URL: {url}\nProvider: {self.page_providers[url]}\nProvider response retrieved at {retrieved}\n'
        prefix+=('Origin response observed; server/CDN caching and unrendered JavaScript may still affect content.\n' if metadata.get('current_eligible') else
                 'UNVERIFIED CURRENT CONTENT: origin freshness is not established; cached or incomplete extraction cannot verify current facts.\n')
        if find:
            match=re.search(re.escape(find),text[start:],re.I)
            if not match:
                self.audit('web_fetch',metadata|{'url':url,'start':start,'term_found':False})
                return prefix+f'Literal term not found in the {len(text)} extracted characters after {start}; extraction may be incomplete. Required information is unverified.'
            start=max(start,start+match.start()-400)
        end=min(len(text),start+characters)
        self.audit('web_fetch',metadata|{'url':url,'provider':self.page_providers[url],'retrieved_at':retrieved,'start':start,'end':end})
        return prefix+f'Characters {start}:{end} of {len(text)}; next_start: {end if end<len(text) else "none"}; extraction limit: 24000.\nPage text:\n'+text[start:end]

class DraftEvidence:
    def __init__(self,directory):self.directory=directory

    def read_draft_evidence(self, index: int, start: int=0, characters: int=6000) -> str:
        """Read recorded initial-pass tool evidence by index, without rerunning tools. No thinking is included."""
        if index<0 or start<0 or not 1<=characters<=8000:raise ScopeError('Invalid evidence page bounds')
        records=json.loads((self.directory/'draft-evidence.json').read_text())
        if index>=len(records):raise ScopeError('Unknown initial-pass evidence index')
        record=records[index];text=record['text'];end=min(len(text),start+characters)
        return json.dumps({k:v for k,v in record.items() if k!='text'},ensure_ascii=False)+'\n'+f'Characters {start}:{end} of {len(text)}; next_start: {end if end<len(text) else "none"}\n'+text[start:end]

TOOL_GROUPS = ('files','evidence','edit','web','draft')

def create_tools(job_dir, phase=None, groups=None):
    """Register the role's tools for a phase. `groups` can only narrow what the role/phase already grants."""
    directory=Path(job_dir).resolve()
    request=JobRequest.model_validate_json((directory/'request.json').read_text())
    def audit(kind,data):
        with (directory/'tools.jsonl').open('a') as f: f.write(json.dumps({'time':time.time(),'phase':phase or 'work','kind':kind,'data':data})+'\n')
    files=ScopedFiles(request,audit)
    allow=lambda group: groups is None or group in groups
    server=FastMCP('scoped')
    def guarded(fn):
        import functools
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def wrapper(*a,**kw):
                try: return await fn(*a,**kw)
                except Exception as e: audit('tool_error',{'tool':fn.__name__,'error':str(e)[:500]});raise
        else:
            @functools.wraps(fn)
            def wrapper(*a,**kw):
                try:return fn(*a,**kw)
                except Exception as e: audit('tool_error',{'tool':fn.__name__,'error':str(e)[:500]});raise
        return wrapper
    if request.role in ('investigator','editor'):
        if allow('files'):
            for fn in (files.read_file,files.read_files,files.glob_files,files.search_text): server.add_tool(guarded(fn))
        if allow('evidence') and (request.evidence_job_ids or phase in ('review','diagnose','answer_review')):
            from .evidence import EvidenceReader
            server.add_tool(guarded(EvidenceReader(directory,audit).read_check_output))
    if allow('edit') and request.role=='editor' and phase not in ('investigate','review','diagnose','answer_review'):
        for fn in (files.edit_file,files.replace_lines,files.write_file): server.add_tool(guarded(fn))
    if allow('web') and request.role in ('researcher','personal'):
        from .web_provider import selection
        plan_name='answer-review' if phase=='answer_review' else 'work'
        from .ledger import WebLedger
        ledger=WebLedger.open(directory)
        strict=request.verify or phase=='answer_review'
        provider=selection(directory)['search_provider']
        # The strict research tools speak Exa/LangSearch; a SearXNG setup keeps Exa for --verify.
        research=Research(audit,'exa' if provider=='searxng' else provider,directory/'web-provider-health.json',request.task if strict else '',directory/(plan_name+'.research-plan.json'),ledger)
        for fn in ((research.plan_web_task,) if strict else ())+(research.search_web,research.fetch_web): server.add_tool(guarded(fn))
    if allow('draft') and phase=='answer_review':server.add_tool(guarded(DraftEvidence(directory).read_draft_evidence))
    return server

def serve_tools(job_dir):
    create_tools(job_dir).run(transport='stdio')
