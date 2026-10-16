"""Client lifecycle adapters. No shadow answers are returned to client hooks."""
import argparse
import json
from pathlib import Path
import shlex
import sys
import time
import uuid

from .integration import enabled,atomic
from .settings import CONFIG,STATE,PROJECT
from .store import Store
from .taskstore import Tasks
from .skills.coding.editing.candidates import capture
from .skills.coding.validation.profiles import load_profile


def pending_path(client,session):
    import hashlib
    return STATE/'hook-sessions'/(hashlib.sha256((client+':'+session).encode()).hexdigest()+'.json')


def event(client,payload):
    if not enabled():return {}
    repo=str(Path(payload.get('cwd','.')).resolve())
    try:policy=load_profile(repo)[0].experiments
    except (ValueError,OSError):return {}
    if not policy.enabled:return {}
    session=payload.get('session_id')
    if not isinstance(session,str) or not session:return {}
    path=pending_path(client,session);name=payload.get('hook_event_name')
    if name=='UserPromptSubmit':
        key=payload.get('turn_id') or uuid.uuid4().hex
        ident=uuid.uuid4().hex;directory=STATE/'tasks'/ident
        value={'id':ident,'key':client+':'+session+':'+key,'created':time.time(),'state':'provisional','reviews':[],
               'input':{'client':client,'session_id':session,'task_key':key,'repo':repo,'prompt':payload.get('prompt','')},
               'sampled':False,'sampling_probability':0,'policy':policy.model_dump(),'shadow_job':None,'capture_scope':[]}
        try:value['baseline']=capture(repo,directory/'snapshots'/'baseline')
        except (OSError,ValueError,TimeoutError,UnicodeError) as exc:value.update(baseline=None,capture_error=str(exc))
        Tasks(Store()).put(value)
        atomic(path,json.dumps({'task_id':ident,'task_key':key,'repo':repo}))
        context=(f'Local Worker task observation: client={client}, session_id={session}, task_key={key}, task_id={ident}. '
                 'Before edits call record_task_decision with keep/delegate intent and an exact structured spec, scopes and approved checks. '
                 'Call finish_frontier_task only when the original task is finished. Shadow content is sealed until then.')
        return {'hookSpecificOutput':{'hookEventName':name,'additionalContext':context}}
    try:pending=json.loads(path.read_text())
    except (OSError,ValueError):return {}
    tasks=Tasks(Store());value=tasks.get(pending['task_id'])
    if name=='Stop' and value.get('completion_marked'):
        tasks.finish(value['id'],value.get('outcome','completed'),payload.get('last_assistant_message'),hook_final=True)
    elif name in ('Interrupt','StopFailure','SessionEnd') and value['state']!='completed':
        value['interruption']=name;value['last_observed']=time.time();tasks.put(value)
    elif name=='PreToolUse':
        value['tool_activity']=True;tasks.put(value)
    return {}


def setup(home=None):
    home=Path(home or Path.home());launcher=home/'.local/bin/local-worker'
    command=shlex.quote(str(launcher))+' hook event --client '
    for client,path in [('codex',home/'.codex/hooks.json'),('claude',home/'.claude/settings.json')]:
        data=json.loads(path.read_text()) if path.exists() else {}
        if path.exists():
            import shutil
            backup=CONFIG/'hook-backups'/(client+'-'+str(time.time_ns())+'.json')
            backup.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            shutil.copyfile(path,backup);backup.chmod(0o600)
        hooks=data.setdefault('hooks',{})
        for name in ('UserPromptSubmit','PreToolUse','Stop'):
            definition={'type':'command','command':command+client,'timeout':6}
            groups=hooks.setdefault(name,[])
            if not any(definition in g.get('hooks',[]) for g in groups):groups.append({'hooks':[definition]})
        atomic(path,json.dumps(data,indent=2)+'\n')
    return {'installed':True,'codex_hook_trust_required':True,'fresh_session_required':True,
            'note':'Enable experiments in a reviewed repository profile; review new Codex hooks in /hooks.'}


def main(argv):
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['event','setup'])
    parser.add_argument('--client',choices=['claude','codex']);args=parser.parse_args(argv)
    if args.action=='setup':print(json.dumps(setup(),indent=2));return
    try:value=event(args.client,json.load(sys.stdin))
    except Exception:
        # Observational hooks fail open without injecting errors or model content.
        value={}
    print(json.dumps(value))
