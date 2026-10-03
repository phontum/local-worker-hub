"""Bounded projections; full private evidence stays in the store/artifacts."""
import json
import time
from .validation import failed
from .report import parse_report


def size(value):
    return len(json.dumps(value, ensure_ascii=True, indent=2).encode())


def bounded(value, budget):
    # Drop only optional narrative. Never drop check outcomes or fabricate success.
    value['truncated'] = False
    for key in ('report','notes','error','changed_files','recent_events'):
        while size(value) > budget and value.get(key):
            value['truncated'] = True
            v = value[key]
            value[key] = v[:len(v)//2] if isinstance(v,(str,list)) else None
    if size(value) > budget:
        value['truncated'] = True
        if isinstance(value.get('review'),dict): value['review'] = {'decision':value['review'].get('decision'), 'notes':'See full result'}
        if value.get('repo'):value['repo']=value['repo'][:120]
        for length in (60,30,15,8,4,2):
            if value.get('active_check'):value['active_check']=value['active_check'][:length]
            for c in [*value.get('checks', []), *value.get('check_results', [])]:
                c['name'] = c['name'][:length]
                if c.get('reason'):c['reason']=c['reason'][:length]
            if size(value)<=budget-32:break
    value['response_bytes'] = 0
    for _ in range(3): value['response_bytes'] = size(value)
    return value


def result_summary(job):
    r = job.get('result')
    if r is None: return {'ready':False, 'id':job['id']}
    checks = [{k:v for k,v in c.items() if k in ('name','status','exit_code','timed_out','seconds','artifact','reason','counts')} for c in r.get('checks',[])]
    refs = ['report.txt','before-status.txt','after-status.txt','before-diff.txt','after-diff.txt','before-staged.txt','after-staged.txt']
    if job['request'].get('role')=='editor':refs.append('scoped-diff.txt')
    if r.get('answer_review'):
        refs.append('draft-report.txt')
        if r['answer_review'].get('state')=='completed':refs.append('answer-review.json')
    web=r.get('web_verification')
    if web:refs.append(web['artifact'])
    plans=[name for name in r.get('research_plans',[]) if name in ('work.research-plan.json','answer-review.research-plan.json')]
    refs+=plans
    refs += [c['artifact'] for c in checks if c.get('artifact')]
    refs += [c['artifact'] for attempt in r.get('attempts',[]) for c in attempt.get('checks',[]) if c.get('artifact')]
    value = {k:r.get(k) for k in ('report','worker_status','report_valid','usage','model','workspace_verified','repo','error','metrics','report_origin','source_job_id','completion')}
    value.update(id=job['id'], ready=True, state=job['state'], checks=checks, checks_failed=any(failed(c) for c in checks),
                 changed_files=r.get('changed_files',[])[:20], changed_file_count=len(r.get('changed_files',[])),
                 review=job.get('review'), attempts=r.get('attempts',[])[:3], workflow=r.get('workflow'),
                 answer_review=r.get('answer_review'), artifacts=refs, full_result=f"/api/jobs/{job['id']}/result?view=full")
    if value.get('metrics'):
        value['metrics']=dict(value['metrics'],takeover=bool(job.get('review') and job['review']['decision']=='takeover'))
    if web:value['web_verification']={**web,'issues':[issue[:200] for issue in web.get('issues',[])[:3]]}
    if plans:value['research_plans']=plans
    if r.get('source_job_id'):value['evidence_job_id']=r['source_job_id']
    return bounded(value,8192)


def result_brief(job):
    result=job.get('result')
    if result is None:return {'ready':False,'id':job['id']}
    parsed=parse_report(result.get('report') or '')
    value={'id':job['id'],'ready':True,'state':job['state'],
           'status':result.get('worker_status'),'error':result.get('error'),
           'findings':parsed['findings'][:600] if parsed else '',
           'changed_files':result.get('changed_files',[])[:12],
           'checks':[{'name':c.get('name'),'status':c.get('status'),'exit_code':c.get('exit_code'),
                      'artifact':c.get('artifact'),'reason':c.get('reason')}
                     for c in result.get('checks',[])],
           'attempts':len(result.get('attempts',[])),
           'requirements_review':{k:(result.get('answer_review') or {}).get(k) for k in ('state','status','initial_status')} if result.get('answer_review') else None,
           'next_action':(result.get('completion') or {}).get('next_action'),
           'review':(job.get('review') or {}).get('decision'),
           'evidence':f"/api/jobs/{job['id']}/result?view=summary"}
    return bounded(value,2048)


def progress_summary(job, queue_position=None):
    p = job.get('progress') or {}
    value = {'id':job['id'],'state':job['state'],'role':job['request']['role'],
             'review':{'decision':job['review']['decision']} if job.get('review') else None,
             'queue_position':queue_position, 'phase':p.get('phase',job['state']),
             'active_check':str(p.get('active_check') or '')[:120] or None,
             'elapsed_seconds':round((job.get('ended') or time.time())-(job.get('started') or job['created']),1),
             'deadline':p.get('deadline'), 'heartbeat':p.get('heartbeat'), 'last_output_at':p.get('last_output_at'),
             'model_budget_remaining':p.get('model_budget_remaining'),
             'check_results':[{'name':str(c['name'])[:60],'status':c.get('status'), 'exit_code':c.get('exit_code')} for c in (job.get('result') or {}).get('checks',[])][:12]}
    return bounded(value,2048)


def history_item(job):
    return {k:job.get(k) for k in ('id','state','created','started','ended')} | {
        'request':{k:job['request'].get(k) for k in ('role','repo','caller','workflow')} | {'task':job['request']['task'][:160]},
        'review':{'decision':job['review']['decision'], 'notes':''} if job.get('review') else None,
        'result':{'worker_status':(job.get('result') or {}).get('worker_status'), 'usage':(job.get('result') or {}).get('usage')}}
