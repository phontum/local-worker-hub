"""Board evaluation harness: public-web tasks across arms. Mechanical outcomes are not frontier acceptance.

Arms: fast (single pass), review (answer review), board-lite, board-full, and review-matched (answer review
given the board's time budget, so the board is not credited for simply having more time).
Ablation arms (for example a board without the second model) need phase `model` overrides in roles.json;
this script does not edit your configuration.
Grade every job with `local-worker review <id> accepted|rejected|takeover` and compare recall against `gold`.
"""
import argparse
import json
import statistics
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from hub.client import call
from hub.models import JobRequest
from hub.settings import STATE

# gold: literal spans of the task a careful answer must respect. web: whether live public sources are needed.
TASKS=[
    ('shop-gpu-ns','shopping',"Find the cheapest RTX 5070 in Novi Sad today. It should be in stock.",True,['cheapest','RTX 5070','Novi Sad','today','in stock']),
    ('shop-cpu-bg','shopping',"Which shop in Belgrade sells the Ryzen 7 9700X for the lowest price right now? I want to pick it up this week.",True,['Belgrade','Ryzen 7 9700X','lowest price','right now','pick it up this week']),
    ('shop-ssd','shopping',"I need a 2 TB NVMe SSD with PCIe 4.0 delivered to Novi Sad within three days. What is the cheapest option that is actually available?",True,['2 TB','NVMe','PCIe 4.0','Novi Sad','within three days','cheapest','available']),
    ('shop-monitor','shopping',"Find a 27 inch 1440p monitor under 300 EUR that is in stock at a Serbian retailer. Give me the price in RSD.",True,['27 inch','1440p','under 300 EUR','in stock','Serbian retailer','price in RSD']),
    ('shop-laptop','shopping',"Is the Lenovo ThinkPad E14 Gen 6 available in Serbia right now, and what is the lowest listed price?",True,['ThinkPad E14 Gen 6','available in Serbia','right now','lowest listed price']),
    ('shop-gpu-variant','shopping',"Find the cheapest desktop RTX 5070 Ti, not the laptop version and not the plain 5070, available in Serbia today.",True,['cheapest','RTX 5070 Ti','not the laptop version','not the plain 5070','Serbia','today']),
    ('shop-ram','shopping',"Compare current prices for a 32 GB DDR5-6000 CL30 kit at three Serbian shops and tell me which one has it in stock.",True,['32 GB DDR5-6000 CL30','three Serbian shops','in stock']),
    ('shop-psu','shopping',"Which 850 W 80 Plus Gold fully modular power supply is cheapest in Novi Sad today? Only count items that can be picked up the same day.",True,['850 W','80 Plus Gold','fully modular','cheapest','Novi Sad','today','picked up the same day']),
    ('docs-fastapi','docs',"According to the official FastAPI documentation, how do you declare a dependency with yield? Answer in exactly two bullets and cite the source URL.",True,['official FastAPI documentation','dependency with yield','exactly two bullets','source URL']),
    ('docs-python','docs',"What changed for free-threading in Python 3.13 compared with 3.12? Use only python.org pages. Do not use blogs.",True,['free-threading','Python 3.13','3.12','only python.org pages','Do not use blogs']),
    ('docs-react','docs',"Per the official React documentation, when should I use useEffectEvent? Give one short paragraph and one source URL.",True,['official React documentation','useEffectEvent','one short paragraph','one source URL']),
    ('docs-postgres','docs',"What does the official PostgreSQL documentation say about the default isolation level? Quote the sentence and cite the page.",True,['official PostgreSQL documentation','default isolation level','Quote the sentence','cite the page']),
    ('docs-ollama','docs',"Using only the official Ollama documentation, how do I keep a model loaded indefinitely? Answer in three bullets.",True,['only the official Ollama documentation','keep a model loaded indefinitely','three bullets']),
    ('trick-count','trick',"List the three largest cities in Serbia by population as exactly three bullets, with the population figure in each bullet and the source named once at the end.",True,['three largest cities in Serbia','exactly three bullets','population figure','source named once']),
    ('trick-negation','trick',"Which Serbian city is NOT in Vojvodina: Novi Sad, Subotica, Nis or Zrenjanin? Answer with one word and do not search the web.",False,['NOT in Vojvodina','one word','do not search the web']),
    ('trick-official','trick',"Give the official name of the Serbian national railway company from its own website only, not from Wikipedia or news.",True,['official name','own website only','not from Wikipedia or news']),
    ('trick-train','trick',"Is there a direct train from Novi Sad to Subotica this Saturday morning, and what does a second-class ticket cost?",True,['direct train','Novi Sad to Subotica','this Saturday morning','second-class ticket']),
    ('trick-weather','trick',"Will it rain in Novi Sad tomorrow afternoon? Answer yes or no first, then the chance if available.",True,['rain in Novi Sad','tomorrow afternoon','yes or no first','chance if available']),
    ('noweb-mutex','noweb',"Explain the difference between a mutex and a semaphore. Use exactly three bullets and do not search the web.",False,['mutex','semaphore','exactly three bullets','do not search the web']),
    ('noweb-sql','noweb',"What is the difference between INNER JOIN and LEFT JOIN? Two short sentences, no web search.",False,['INNER JOIN','LEFT JOIN','Two short sentences','no web search']),
    ('noweb-greet','noweb',"Hello! Thanks for your help earlier.",False,[]),
    ('noweb-math','noweb',"What is 17 times 23? Just the number.",False,['Just the number']),
]
ARMS={
    'fast':dict(review_pass=False),
    'review':dict(review_pass=True),
    'board-lite':dict(board=True,board_mode='lite'),
    'board-full':dict(board=True,board_mode='full'),
    'review-matched':dict(review_pass=True),
}

def wait(ident,limit):
    started=time.monotonic()
    while True:
        status=call('GET',f'/api/jobs/{ident}/wait?timeout_seconds=25')
        if status['state'] not in ('queued','running'):return status
        if time.monotonic()-started>limit:
            call('POST',f'/api/jobs/{ident}/cancel');return {'state':'cancelled'}

def run_one(task,arm,repeat,timeout,matched):
    task_id,category,text,web,gold=task
    options=dict(ARMS[arm]);budget=matched if arm=='review-matched' else (1200 if options.get('board') else timeout)
    request=JobRequest(role='personal',task=text,execution_preset='work',timeout=budget,idempotency_key=uuid.uuid4().hex,
                       handoff_id='board-eval-'+task_id,caller='benchmark',**options)
    started=time.monotonic();ident=call('POST','/api/jobs',request.model_dump())['id'];status=wait(ident,budget+300)
    result=call('GET',f'/api/jobs/{ident}/result?view=summary') or {}
    events=call('GET',f'/api/jobs/{ident}/events?limit=1000')
    searches=sum(e['kind']=='web_search' for e in events);fetches=sum(e['kind']=='web_fetch' for e in events)
    verification=result.get('web_verification') or {}
    board=result.get('board') or {}
    return {'task':task_id,'category':category,'arm':arm,'repeat':repeat,'job_id':ident,'seconds':round(time.monotonic()-started,1),
        'state':status['state'],'worker_status':result.get('worker_status'),'review_status':(result.get('answer_review') or {}).get('status'),
        'expects_web':web,'searches':searches,'fetches':fetches,'unnecessary_web':bool(not web and (searches or fetches)),
        'verified_observations':verification.get('verified_observations'),'provenance_issues':len(verification.get('issues',[])),
        'board_state':board.get('state'),'drift_flags':board.get('drift_flags'),'swaps':board.get('swaps'),
        'repair':bool(board.get('repair') and board['repair'].get('round2_status')),'degraded':board.get('degraded'),
        'gold':gold,'usage':result.get('usage'),'metrics':result.get('metrics'),
        'report':(result.get('report') or '')[:1500],'frontier_review':'pending'}

def summarize(rows):
    for arm in ARMS:
        mine=[r for r in rows if r['arm']==arm]
        if not mine:continue
        times=sorted(r['seconds'] for r in mine)
        print(f"{arm:15} n={len(mine):3} completed={sum(r['state']=='completed' for r in mine):3} COMPLETE={sum(r['worker_status']=='COMPLETE' for r in mine):3} "
              f"median={statistics.median(times):7.1f}s p90={times[int(.9*(len(times)-1))]:7.1f}s unnecessary_web={sum(r['unnecessary_web'] for r in mine)} "
              f"provenance_issues={sum(r['provenance_issues'] for r in mine)} degraded={sum(bool(r['degraded']) for r in mine)} repaired={sum(r['repair'] for r in mine)}",flush=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--arms',nargs='+',choices=list(ARMS),default=['review','board-lite'])
    parser.add_argument('--categories',nargs='+',choices=sorted({t[1] for t in TASKS}))
    parser.add_argument('--tasks',nargs='+',help='Task ids')
    parser.add_argument('--repeats',type=int,default=1,help='Use at least 3 before drawing conclusions')
    parser.add_argument('--timeout',type=int,default=300,help='Model budget for non-board arms')
    parser.add_argument('--matched-timeout',type=int,default=1200,help='Budget for review-matched; set to the board median')
    parser.add_argument('--max-seconds',type=int,default=7200)
    args=parser.parse_args()
    tasks=[t for t in TASKS if (not args.categories or t[1] in args.categories) and (not args.tasks or t[0] in args.tasks)]
    root=STATE/'benchmarks'/('board-'+uuid.uuid4().hex[:12]);root.mkdir(parents=True,mode=0o700)
    rows=[];started=time.monotonic()
    for repeat in range(args.repeats):
        for task in tasks:
            for arm in args.arms:
                if time.monotonic()-started+1500>args.max_seconds:break
                row=run_one(task,arm,repeat,args.timeout,args.matched_timeout);rows.append(row)
                (root/'results.json').write_text(json.dumps({'rows':rows,'note':'Mechanical outcomes only. Frontier acceptance, requirement recall against gold and net savings need independent review and matched baselines.'},indent=2))
                print(json.dumps({k:row[k] for k in ('task','arm','repeat','seconds','state','worker_status','board_state','drift_flags','job_id')}),flush=True)
    summarize(rows);print('Evidence: '+str(root),flush=True)

if __name__=='__main__':main()
