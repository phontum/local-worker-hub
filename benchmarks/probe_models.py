"""Phase 0 board probe: capabilities, residency, swap cost, structured-output validity and arbiter quality.

Calls native Ollama directly, one model resident at a time (the hub's MAX_LOADED_MODELS=1 setting).
Results are private measurements, not frontier acceptance. They live under hub state, never in the repo.
"""
import argparse
import json
import random
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from hub.settings import STATE

OLLAMA='http://127.0.0.1:11434'
MODELS={'qwen':'qwen3.5:9b','gemma':'gemma4:12b-it-qat'}

# Each task lists literal spans of the task that a careful reader must anchor a requirement to.
TASKS=[
    {'id':'gpu-ns','task':"Hey, find the cheapest RTX 5070 in Novi Sad today. It should be in stock.",
     'gold':['cheapest','RTX 5070','Novi Sad','today','in stock'],'skip':False,'web':True},
    {'id':'gpu-ns-2','task':"Which shop in Novi Sad sells the Ryzen 7 9700X for the lowest price right now? I want to pick it up this week.",
     'gold':['Novi Sad','Ryzen 7 9700X','lowest price','right now','pick it up this week'],'skip':False,'web':True},
    {'id':'docs-version','task':"According to the official FastAPI documentation, how do you declare a dependency with yield? Answer in exactly two bullets and cite the source URL.",
     'gold':['official FastAPI documentation','dependency with yield','exactly two bullets','cite the source URL'],'skip':False,'web':True},
    {'id':'docs-compare','task':"Compare what changed for Python 3.13 free-threading versus Python 3.12. Use only python.org pages. Do not use blogs.",
     'gold':['Python 3.13 free-threading','Python 3.12','only python.org pages','Do not use blogs'],'skip':False,'web':True},
    {'id':'weather','task':"Will it rain in Belgrade tomorrow afternoon? Give a yes/no and the chance if available.",
     'gold':['Belgrade','tomorrow afternoon','yes/no','chance'],'skip':False,'web':True},
    {'id':'train','task':"Is there a direct train from Novi Sad to Subotica this Saturday morning, and what does a second-class ticket cost?",
     'gold':['direct train','Novi Sad to Subotica','this Saturday morning','second-class ticket'],'skip':False,'web':True},
    {'id':'stable-knowledge','task':"Explain the difference between a mutex and a semaphore. Use exactly three bullets and do not search the web.",
     'gold':['mutex','semaphore','exactly three bullets','do not search the web'],'skip':False,'web':False},
    {'id':'hello','task':"Hello!",'gold':[],'skip':True,'web':False},
    {'id':'arith','task':"What is 17 times 23?",'gold':[],'skip':True,'web':False},
    {'id':'thanks','task':"Thanks, that helped.",'gold':[],'skip':True,'web':False},
]

ROLES={
    'direct':('Propose the strongest straightforward plan to satisfy the task exactly as written.',0.2),
    'skeptic':('Be skeptical. Find ambiguities, risks, edge cases and implicit intent behind the request, then give an independent plan. Derive extra requirements only when a literal quote from the task justifies them.',0.6),
    'first_principles':('Solve from first principles. Ignore conventional approaches and ask what the user ultimately needs, then give a plan.',0.4),
    'challenger':('Challenge the most likely assumptions and interpretations of this task. Propose an alternative approach to the obvious one and list what could make the obvious approach wrong.',0.6),
}

PROPOSAL_SCHEMA={'type':'object','additionalProperties':False,
    'required':['requirements','assumptions','plan','risks','hypotheses_to_verify','open_questions','rationale'],
    'properties':{
        'requirements':{'type':'array','minItems':1,'maxItems':12,'items':{'type':'object','additionalProperties':False,
            'required':['id','kind','task_quote','requirement','acceptance'],
            'properties':{'id':{'type':'string'},'kind':{'type':'string','enum':['explicit','derived']},
                'task_quote':{'type':'string'},'requirement':{'type':'string'},'acceptance':{'type':'string'}}}},
        'assumptions':{'type':'array','maxItems':8,'items':{'type':'object','additionalProperties':False,
            'required':['text','risk_if_wrong','how_to_check'],
            'properties':{'text':{'type':'string'},'risk_if_wrong':{'type':'string'},'how_to_check':{'type':'string'}}}},
        'plan':{'type':'array','minItems':1,'maxItems':10,'items':{'type':'object','additionalProperties':False,
            'required':['step','requirement_ids'],'properties':{'step':{'type':'string'},'requirement_ids':{'type':'array','items':{'type':'string'}}}}},
        'risks':{'type':'array','maxItems':8,'items':{'type':'string'}},
        'hypotheses_to_verify':{'type':'array','maxItems':8,'items':{'type':'string'}},
        'open_questions':{'type':'array','maxItems':6,'items':{'type':'string'}},
        'rationale':{'type':'string','maxLength':600}}}

ARBITER_SCHEMA={'type':'object','additionalProperties':False,
    'required':['requirements','decisions','dissent','plan','needs_web','needs_current_evidence','open_questions'],
    'properties':{
        'requirements':{'type':'array','minItems':1,'maxItems':15,'items':{'type':'object','additionalProperties':False,
            'required':['id','kind','task_quote','requirement','acceptance','supported_by'],
            'properties':{'id':{'type':'string'},'kind':{'type':'string','enum':['explicit','derived']},
                'task_quote':{'type':'string'},'requirement':{'type':'string'},'acceptance':{'type':'string'},
                'supported_by':{'type':'array','items':{'type':'string','enum':['A','B','C','D']}}}}},
        'decisions':{'type':'array','maxItems':10,'items':{'type':'object','additionalProperties':False,
            'required':['topic','chosen','basis'],'properties':{'topic':{'type':'string'},'chosen':{'type':'string'},
                'basis':{'type':'string','enum':['requirement','evidence','reasoning']}}}},
        'dissent':{'type':'array','maxItems':6,'items':{'type':'object','additionalProperties':False,
            'required':['candidate','point','why_not_adopted'],'properties':{'candidate':{'type':'string','enum':['A','B','C','D']},
                'point':{'type':'string'},'why_not_adopted':{'type':'string'}}}},
        'plan':{'type':'array','minItems':1,'maxItems':10,'items':{'type':'string'}},
        'needs_web':{'type':'boolean'},'needs_current_evidence':{'type':'boolean'},
        'open_questions':{'type':'array','maxItems':6,'items':{'type':'string'}}}}

TRIAGE_SCHEMA={'type':'object','additionalProperties':False,'required':['skip_board','needs_external_info','ambiguity'],
    'properties':{'skip_board':{'type':'boolean'},'needs_external_info':{'type':'boolean'},
                  'ambiguity':{'type':'string','enum':['low','medium','high']}}}

PROPOSER_SYSTEM=('You are one independent proposer on a review board. You cannot see other proposers. '
    'Return ONLY JSON matching the schema. Each requirement needs a task_quote copied EXACTLY (character for character) from <task>. '
    'kind=explicit means the quote states it directly; kind=derived means it is implied by the quote. '
    'You have no tools and no knowledge of current prices, stock or events: never state such facts, put them under hypotheses_to_verify. ')
ARBITER_SYSTEM=('You are the arbiter. Candidates A-D are anonymous independent proposals. Agreement between candidates is NOT evidence. '
    'Compare them per point against the ORIGINAL task in <task>, keep every explicit requirement anchored to an exact task_quote, '
    'keep a derived requirement only if its quote justifies it, resolve disagreements by the task text, and record minority points in dissent. '
    'Return ONLY JSON matching the schema.')
TRIAGE_SYSTEM=('Decide whether a multi-model review board is worthwhile. skip_board is true for greetings, thanks, trivial arithmetic or '
    'anything a single quick answer handles. needs_external_info is true when current or external facts are required. Return ONLY JSON.')

def call(client,model,system,user,schema,think,temperature,num_predict,ctx,keep_alive='30m'):
    body={'model':model,'stream':False,'format':schema,'keep_alive':keep_alive,
          'messages':[{'role':'system','content':system},{'role':'user','content':user}],
          'options':{'num_ctx':ctx,'num_predict':num_predict,'temperature':temperature}}
    if think is not None:body['think']=think
    started=time.monotonic()
    try:
        response=client.post(OLLAMA+'/api/chat',json=body)
    except httpx.HTTPError as error:
        return {'ok':False,'error':type(error).__name__,'seconds':time.monotonic()-started}
    seconds=time.monotonic()-started
    if response.status_code!=200:
        return {'ok':False,'error':'HTTP %d: %s'%(response.status_code,response.text[:300]),'seconds':seconds}
    value=response.json();message=value.get('message') or {}
    return {'ok':True,'content':message.get('content') or '','thinking_chars':len(message.get('thinking') or ''),
            'prompt_tokens':value.get('prompt_eval_count'),'output_tokens':value.get('eval_count'),
            'load_seconds':(value.get('load_duration') or 0)/1e9,'eval_seconds':(value.get('eval_duration') or 0)/1e9,
            'done_reason':value.get('done_reason'),'seconds':seconds}

def parse(result):
    if not result.get('ok'):return None
    try:
        value=json.loads(result['content'])
        return value if isinstance(value,dict) else None
    except ValueError:return None

def unload_all(client):
    for item in client.get(OLLAMA+'/api/ps').json().get('models',[]):
        client.post(OLLAMA+'/api/generate',json={'model':item['name'],'keep_alive':0})
    for _ in range(30):
        if not client.get(OLLAMA+'/api/ps').json().get('models'):return
        time.sleep(1)

def residency(client,name):
    for item in client.get(OLLAMA+'/api/ps').json().get('models',[]):
        if item['name']==name or item.get('model')==name:
            size=item.get('size') or 0;vram=item.get('size_vram') or 0
            return {'size_gb':round(size/1e9,2),'vram_gb':round(vram/1e9,2),'on_gpu_fraction':round(vram/size,3) if size else None,
                    'context':item.get('context_length')}
    return None

def capabilities(client,name):
    response=client.post(OLLAMA+'/api/show',json={'model':name})
    if response.status_code!=200:return {'error':response.status_code}
    data=response.json()
    return {'capabilities':data.get('capabilities'),'details':data.get('details')}

def anchors(task,requirements):
    valid=[r for r in requirements if isinstance(r,dict) and isinstance(r.get('task_quote'),str) and r['task_quote'] and r['task_quote'] in task]
    return valid

def coverage(task_spec,requirements):
    """Share of gold spans that some valid anchor overlaps (contains or is contained)."""
    gold=task_spec['gold']
    if not gold:return None
    quotes=[r['task_quote'] for r in anchors(task_spec['task'],requirements)]
    covered=[g for g in gold if any(g.lower() in q.lower() or q.lower() in g.lower() for q in quotes)]
    return {'covered':len(covered),'total':len(gold),'missing':[g for g in gold if g not in covered]}

def user_task(task):return '<task>\n'+task+'\n</task>'

def label_brief(task,proposals,order):
    parts=[user_task(task)]
    for label,key in zip('ABCD',order):
        parts.append('Candidate '+label+':\n'+json.dumps(proposals[key],ensure_ascii=False))
    return '\n\n'.join(parts)

def run_model_proposals(client,alias,model,ctx,tasks,roles,think,results):
    unload_all(client)
    block={'model':model,'think':think,'swap_in_load_seconds':None,'residency':None,'runs':[]}
    results['models'][alias]=block
    first=True
    for spec in tasks:
        if spec['skip']:continue
        for role in roles:
            prompt,temperature=ROLES[role]
            result=call(client,model,PROPOSER_SYSTEM+prompt,user_task(spec['task']),PROPOSAL_SCHEMA,think,temperature,4096 if think else 2048,ctx)
            if first and result.get('ok'):
                block['swap_in_load_seconds']=result.get('load_seconds');block['residency']=residency(client,model);first=False
            value=parse(result)
            requirements=value.get('requirements',[]) if value else []
            row={'task':spec['id'],'role':role,'ok':result.get('ok'),'error':result.get('error'),'json_valid':value is not None,
                 'seconds':round(result.get('seconds',0),1),'output_tokens':result.get('output_tokens'),'thinking_chars':result.get('thinking_chars'),
                 'done_reason':result.get('done_reason'),
                 'anchor_valid':len(anchors(spec['task'],requirements)),'requirements':len(requirements),
                 'derived':sum(1 for r in requirements if isinstance(r,dict) and r.get('kind')=='derived'),
                 'gold_coverage':coverage(spec,requirements),'proposal':value}
            block['runs'].append(row)
            print(f"  {alias:6} {spec['id']:16} {role:16} json={row['json_valid']!s:5} anchors={row['anchor_valid']}/{row['requirements']} {row['seconds']}s",flush=True)

def run_triage(client,alias,model,ctx,tasks,results):
    unload_all(client)
    rows=[]
    for spec in tasks:
        result=call(client,model,TRIAGE_SYSTEM,user_task(spec['task']),TRIAGE_SCHEMA,False,0,256,ctx)
        value=parse(result)
        rows.append({'task':spec['id'],'ok':value is not None,'expected_skip':spec['skip'],'expected_external':spec['web'],
                     'skip':value.get('skip_board') if value else None,'external':value.get('needs_external_info') if value else None,
                     'seconds':round(result.get('seconds',0),1)})
    correct=sum(1 for r in rows if r['ok'] and r['skip']==r['expected_skip'] and r['external']==r['expected_external'])
    results['triage'][alias]={'accuracy':f'{correct}/{len(rows)}','rows':rows}
    print(f'  triage {alias}: {correct}/{len(rows)} exactly right',flush=True)

def run_arbiter(client,alias,model,ctx,tasks,think,results,seed):
    unload_all(client)
    rng=random.Random(seed);rows=[]
    for spec in tasks:
        if spec['skip']:continue
        proposals={}
        for model_alias in ('qwen','gemma'):
            for run in results['models'].get(model_alias,{}).get('runs',[]):
                if run['task']==spec['id'] and run['proposal']:proposals[(model_alias,run['role'])]=run['proposal']
        keys=list(proposals)
        if len(keys)<2:continue
        rng.shuffle(keys)
        keys=keys[:4]
        brief=label_brief(spec['task'],{k:proposals[k] for k in keys},keys)
        result=call(client,model,ARBITER_SYSTEM,brief,ARBITER_SCHEMA,think,0.2,4096,ctx)
        value=parse(result);requirements=value.get('requirements',[]) if value else []
        proposer_anchored=set()
        for k in keys:
            for r in anchors(spec['task'],proposals[k].get('requirements',[])):
                if r.get('kind')=='explicit':proposer_anchored.add(r['task_quote'].lower())
        arbiter_quotes=[r['task_quote'].lower() for r in anchors(spec['task'],requirements)]
        dropped=[q for q in proposer_anchored if not any(q in a or a in q for a in arbiter_quotes)]
        adopted={}
        for r in requirements:
            for label in r.get('supported_by',[]) if isinstance(r,dict) else []:
                if label in 'ABCD' and label:
                    source=keys['ABCD'.index(label)][0] if 'ABCD'.index(label)<len(keys) else None
                    if source:adopted[source]=adopted.get(source,0)+1
        rows.append({'task':spec['id'],'json_valid':value is not None,'seconds':round(result.get('seconds',0),1),
            'anchor_valid':len(anchors(spec['task'],requirements)),'requirements':len(requirements),
            'dropped_anchored_explicit':len(dropped),'derived':sum(1 for r in requirements if isinstance(r,dict) and r.get('kind')=='derived'),
            'gold_coverage':coverage(spec,requirements),'adopted_by_source_model':adopted,
            'needs_web':value.get('needs_web') if value else None,'expected_web':spec['web'],'output':value})
        print(f"  arbiter {alias:6} {spec['id']:16} json={rows[-1]['json_valid']!s:5} anchors={rows[-1]['anchor_valid']}/{rows[-1]['requirements']} dropped={len(dropped)} {rows[-1]['seconds']}s",flush=True)
    results['arbiter'][alias]=rows

def summarize(results):
    lines=[]
    for alias,block in results['models'].items():
        runs=block['runs']
        if not runs:continue
        n=len(runs);valid=sum(r['json_valid'] for r in runs)
        total_req=sum(r['requirements'] for r in runs);total_anchor=sum(r['anchor_valid'] for r in runs)
        gold=[r['gold_coverage'] for r in runs if r['gold_coverage']]
        recall=sum(g['covered'] for g in gold)/max(1,sum(g['total'] for g in gold))
        median=sorted(r['seconds'] for r in runs)[n//2]
        lines.append(f"{alias}: json_valid {valid}/{n}, anchor_valid {total_anchor}/{total_req}, gold_recall {recall:.2f}, median {median}s, "
                     f"load {block['swap_in_load_seconds']}s, residency {block['residency']}")
    for alias,rows in results['arbiter'].items():
        if not rows:continue
        n=len(rows);valid=sum(r['json_valid'] for r in rows)
        total_req=sum(r['requirements'] for r in rows);total_anchor=sum(r['anchor_valid'] for r in rows)
        gold=[r['gold_coverage'] for r in rows if r['gold_coverage']]
        recall=sum(g['covered'] for g in gold)/max(1,sum(g['total'] for g in gold))
        lines.append(f"arbiter {alias}: json_valid {valid}/{n}, anchor_valid {total_anchor}/{total_req}, gold_recall {recall:.2f}, "
                     f"dropped_anchored {sum(r['dropped_anchored_explicit'] for r in rows)}")
    for alias,value in results['triage'].items():lines.append(f"triage {alias}: {value['accuracy']}")
    return lines

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models',nargs='+',choices=list(MODELS),default=list(MODELS))
    parser.add_argument('--arbiters',nargs='+',choices=list(MODELS),default=list(MODELS))
    parser.add_argument('--roles',nargs='+',choices=list(ROLES),default=['direct','skeptic'])
    parser.add_argument('--tasks',nargs='+',help='Task IDs; default all')
    parser.add_argument('--ctx',type=int,default=16384,choices=[8192,16384,32768])
    parser.add_argument('--think',choices=['on','off'],default='on')
    parser.add_argument('--seed',type=int,default=7)
    parser.add_argument('--skip-arbiter',action='store_true')
    parser.add_argument('--out',type=Path)
    args=parser.parse_args()
    tasks=[t for t in TASKS if not args.tasks or t['id'] in args.tasks]
    think=args.think=='on'
    out=args.out or STATE/'benchmarks'/('board-probe-'+time.strftime('%Y%m%d-%H%M%S'))
    out.mkdir(parents=True,mode=0o700,exist_ok=True)
    results={'started':time.time(),'ctx':args.ctx,'think':think,'seed':args.seed,'models':{},'arbiter':{},'triage':{},'capabilities':{}}
    with httpx.Client(timeout=httpx.Timeout(900,connect=5),trust_env=False) as client:
        for alias in args.models:
            results['capabilities'][alias]=capabilities(client,MODELS[alias]);print(alias,results['capabilities'][alias].get('capabilities'),flush=True)
        for alias in args.models:
            print('proposals',alias,flush=True)
            run_model_proposals(client,alias,MODELS[alias],args.ctx,tasks,args.roles,think,results)
            run_triage(client,alias,MODELS[alias],args.ctx,tasks,results)
        if not args.skip_arbiter:
            for alias in args.arbiters:
                print('arbiter',alias,flush=True)
                run_arbiter(client,alias,MODELS[alias],args.ctx,tasks,think,results,args.seed)
        unload_all(client)
    results['ended']=time.time()
    path=out/'results.json';path.write_text(json.dumps(results,indent=2));path.chmod(0o600)
    print('\n'.join(summarize(results)));print('Private results:',path)

if __name__=='__main__':main()
