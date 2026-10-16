"""Private task observations, independent of whether a task was delegated."""
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time
import uuid

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from .settings import STATE
from .integration import require_enabled, atomic
from .scoped import ScopedFiles
from .skills.coding.editing import candidates
from .skills.coding.delegation.spec import DelegationSpec, compile_spec
from .skills.coding.execution.options import ExperimentPolicy


class TaskDecision(BaseModel):
    model_config=ConfigDict(extra='forbid')
    client: Literal['claude','codex']
    session_id: str = Field(min_length=1,max_length=200)
    task_key: str = Field(min_length=1,max_length=200)
    repo: str
    decision: Literal['keep','delegate','observe']
    reason: str = Field(default='',max_length=1000)
    spec: DelegationSpec
    observation_paths: list[str] = Field(default_factory=list,max_length=100)
    task_id: str | None = Field(default=None,pattern=r'^[a-f0-9]{32}$')


class ComparisonReview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    labels: list[Literal['completely_wrong','nearly_correct','right_idea_wrong_implementation','correct_with_extras',
                       'partial_solution','equivalent_solution','unknown']] = Field(default_factory=list,max_length=7)
    requirements: dict[str,Literal['met','unmet','unknown']] = Field(default_factory=dict,max_length=100)
    evidence: dict[str,str] = Field(default_factory=dict,max_length=100)
    notes: str = Field(min_length=10,max_length=8000)


class Tasks:
    def __init__(self,store):
        self.store=store
        with store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY,key TEXT UNIQUE,data TEXT NOT NULL)')

    def get(self,ident):
        with self.store.connect() as db:
            row=db.execute('SELECT data FROM tasks WHERE id=?',(ident,)).fetchone()
        if not row:raise ValueError('Unknown task')
        return json.loads(row['data'])

    def put(self,value):
        with self.store.connect() as db:
            db.execute('INSERT INTO tasks VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',
                       (value['id'],value['key'],json.dumps(value)))

    def list(self):
        with self.store.connect() as db:
            return [json.loads(r['data']) for r in db.execute('SELECT data FROM tasks')]

    def directory(self,ident):
        if len(ident)!=32 or any(c not in '0123456789abcdef' for c in ident):raise ValueError('Invalid task id')
        return STATE/'tasks'/ident

    def record(self,request):
        require_enabled()
        key=request.client+':'+request.session_id+':'+request.task_key
        with self.store.connect() as db:
            row=db.execute('SELECT data FROM tasks WHERE key=?',(key,)).fetchone()
        if row:
            old=json.loads(row['data'])
            if old['state']!='provisional':
                if old['input']!=request.model_dump(mode='json'):raise ValueError('Task key reused with different input')
                return self.brief(old)
        compiled=compile_spec(request.spec,request.repo)
        files=ScopedFiles(compiled)
        # Validate observation scope using the same path/secret/link guards.
        for name in request.observation_paths:files.path(name,exists=False)
        from .skills.coding.validation.profiles import load_profile
        try:policy=load_profile(request.repo)[0].experiments.model_dump()
        except (ValueError,OSError):policy={}
        selected=False;probability=0
        if policy.get('enabled') and policy.get('shadow') and request.decision=='keep':
            probability=policy.get('sample_rate',.2)
            today=int(time.time()//86400)
            used=sum(t.get('sampled',False) and int(t['created']//86400)==today for t in self.list())
            if used<policy.get('daily_shadows',10):selected=secrets.randbelow(1_000_000)<probability*1_000_000
            else:probability=0
        ident=old['id'] if row else uuid.uuid4().hex
        value={'id':ident,'key':key,'input':request.model_dump(mode='json'),'created':time.time(),'state':'active',
               'sampled':selected,'sampling_probability':probability,'policy':policy,'shadow_job':None,'reviews':[],
               'capture_scope':request.observation_paths,'reason':request.reason,'baseline':None}
        directory=self.directory(ident);directory.mkdir(parents=True,exist_ok=True,mode=0o700)
        # Measurements and graph/recipes need a shared baseline even when no shadow is selected.
        try:
            if row:
                if old['input']['repo']!=str(Path(request.repo).resolve()):raise ValueError('Hook snapshot repository mismatch')
                value['baseline']=old['baseline'];value['hook_managed']=True;value['capture_scope']=[]
                value['original_prompt']=old['input'].get('prompt','')
                if not value['baseline']:raise ValueError('Hook snapshot unavailable')
            else:value['baseline']=candidates.capture(request.repo,directory/'snapshots'/'baseline',request.observation_paths)
        except (OSError,ValueError,TimeoutError,UnicodeError) as exc:
            value.update(state='capture_failed',capture_error=str(exc),sampled=False)
        self.put(value)
        if selected and value['baseline']:
            snapshot=directory/'snapshots'/'baseline'
            job=compiled.model_copy(update={'repo':str(snapshot),'purpose':'shadow','task_id':ident,'caller':'shadow',
                                           'idempotency_key':'shadow:'+ident,'in_place':False,'workspace_from':None, 'experiments':ExperimentPolicy.model_validate(policy)})
            submitted=self.store.submit(job);value['shadow_job']=submitted['id'];self.put(value)
        return self.brief(value)

    def brief(self,value):
        return {k:value.get(k) for k in ('id','state','sampled','sampling_probability','capture_error','shadow_job')} | {'repo':value['input'].get('repo'),'goal':value['input'].get('spec',{}).get('goal'),'decision':value['input'].get('decision')}

    def finish(self,ident,outcome='completed',answer=None,hook_final=False):
        require_enabled();value=self.get(ident)
        if value['state']=='completed':return self.brief(value)
        if not value['baseline']:raise ValueError('No valid baseline')
        if value.get('hook_managed') and not hook_final:
            value.update(completion_marked=True,outcome=outcome);self.put(value);return self.brief(value)
        request=value['input'];directory=self.directory(ident)
        value['final']=candidates.capture(request['repo'],directory/'snapshots'/'frontier',value['capture_scope'])
        value.update(state='completed',ended=time.time(),outcome=outcome,answer=answer)
        self.put(value)
        return self.brief(value)

    def comparison(self,ident,include_source=False):
        value=self.get(ident)
        if value['state']!='completed':raise ValueError('Shadow content is sealed until frontier completion')
        directory=self.directory(ident)
        if not (directory/'snapshots'/'baseline').exists():return {'available':False,'reason':'snapshots expired'}
        from .skills.coding.intelligence.structural import compare
        baseline=candidates.contents(directory/'snapshots'/'baseline')
        frontier=candidates.contents(directory/'snapshots'/'frontier')
        job=self.store.get(value['shadow_job']) if value['shadow_job'] else None
        if not job:return {'available':False,'reason':'task was not shadowed','task':self.brief(value)}
        if job['state'] in ('queued','running'):return {'available':False,'reason':'shadow is unfinished'}
        from .skills.coding.editing import workspace
        try:worker=candidates.contents(Path(workspace.read_record(job['id'])['path']))
        except workspace.WorkspaceError:worker=baseline
        result={'available':True,'task_id':ident,'shadow_state':job['state'],'worker_status':(job.get('result') or {}).get('worker_status'),
                'outcome':value['outcome'],'censored':job['state'] in ('cancelled','interrupted','timed_out'),'comparison':compare(baseline,worker,frontier),'reviews':value['reviews'],
                'checks':(job.get('result') or {}).get('checks',[])}
        if include_source:
            result['source']={'baseline':baseline,'worker':worker,'frontier':frontier,'answer':(job.get('result') or {}).get('answer')}
        else:
            # Text/literals are private source; normal comparisons expose structure and hashes only.
            for arm in ('worker','frontier'):
                for file in result['comparison'].get(arm,[]):
                    if isinstance(file,dict):
                        file.pop('literal_changes',None)
                        for edit in file.get('edits',[]):edit.pop('old',None);edit.pop('new',None)
        return result

    def review(self,ident,review):
        value=self.get(ident)
        if value['state']!='completed':raise ValueError('Review needs a finalized task')
        for criterion,status in review.requirements.items():
            if status=='met' and not review.evidence.get(criterion):raise ValueError('Met claims need supporting evidence')
        value['reviews'].append({'created':time.time(),**review.model_dump()});self.put(value)
        return {'task_id':ident,'reviews':value['reviews']}
