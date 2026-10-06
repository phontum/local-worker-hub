"""Bounded projections; full private evidence stays in the store/artifacts."""
import json
import time
from .skills.coding.validation.validation import failed, failure_lines
from .report import parse_report


def size(value):
    return len(json.dumps(value, ensure_ascii=True, indent=2).encode())


def bounded(value, budget):
    # Drop only optional narrative. Never drop check outcomes or fabricate success.
    value['truncated'] = False
    for key in ('ask','answer','report','notes','error','changed_files','recent_events'):
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


def board_artifacts(board):
    if not board or board.get('state')=='skipped':return []
    names=['board-state.json']
    if (board.get('scout') or {}).get('query'):names.append('board-scout.json')
    for item in board.get('phases',[]):
        if not item.get('ok'):continue
        if item['phase']=='triage':names.append('board-triage.json')
        elif item['phase'].startswith('proposal-'):names.append('board-'+item['phase']+'.json')
        elif item['phase']=='arbiter':names+=['board-synthesis.json','board-drift.json','board-identities.json']
    return names


def live_workspace(job):
    """The editor workspace as recorded in the result, with its current state (it may have been applied or discarded since)."""
    recorded=(job.get('result') or {}).get('workspace')
    if not recorded:return None
    try:
        from .skills.coding.editing import workspace
        record=workspace.read_record(job['id']);state=record['state']
        extra={'stale_dependencies':workspace.stale_dependencies(job['id']),'removed':record.get('removed',[])}
    except Exception:state=recorded.get('state');extra={}
    return {**recorded,'state':state,**extra}


def result_summary(job):
    r = job.get('result')
    if r is None: return {'ready':False, 'id':job['id']}
    checks = [{k:v for k,v in c.items() if k in ('name','status','exit_code','timed_out','seconds','artifact','reason','counts')} |
              ({'failures':[{**f,'message':f['message'][:160]} for f in c['failures'][:10]]} if c.get('failures') else {}) for c in r.get('checks',[])]
    refs = ['report.txt','before-status.txt','after-status.txt','before-diff.txt','after-diff.txt','before-staged.txt','after-staged.txt']
    if job['request'].get('role')=='editor':refs.append('scoped-diff.txt')
    if r.get('answer_review'):
        refs.append('draft-report.txt')
        if r['answer_review'].get('state')=='completed':refs.append('answer-review.json')
    web=r.get('web_verification')
    if web:refs.append(web['artifact'])
    plans=[name for name in r.get('research_plans',[]) if name in ('work.research-plan.json','answer-review.research-plan.json')]
    refs+=plans
    refs+=board_artifacts(r.get('board'))
    if r.get('ask'):refs.append('ask.json')
    refs += [c['artifact'] for c in checks if c.get('artifact')]
    refs += [c['artifact'] for attempt in r.get('attempts',[]) for c in attempt.get('checks',[]) if c.get('artifact')]
    if r.get('investigation'):refs.append('work.investigation.json')
    value = {k:r.get(k) for k in ('report','worker_status','report_valid','usage','model','workspace_verified','repo','error','metrics','report_origin','source_job_id','completion')}
    value.update(id=job['id'], ready=True, state=job['state'], checks=checks, checks_failed=any(failed(c) for c in checks),
                 changed_files=r.get('changed_files',[])[:20], changed_file_count=len(r.get('changed_files',[])),
                 review=job.get('review'), attempts=r.get('attempts',[])[:3], workflow=r.get('workflow'),
                 answer_review=r.get('answer_review'), board=r.get('board'), ask=r.get('ask'), artifacts=refs, full_result=f"/api/jobs/{job['id']}/result?view=full")
    if r.get('investigation'):
        packet=r['investigation']
        value['investigation']={'search_scope':packet['search_scope'],'unknowns':packet['unknowns'][:4],
                               'requirement_count':len(packet['requirements']),'semantic_verification':'frontier_required'}
    if r.get('workspace'):value.update(workspace=live_workspace(job),acceptance=r.get('acceptance'));value['artifacts']=[*refs,'patch.diff']
    elif r.get('acceptance'):value['acceptance']=r['acceptance']
    if r.get('spec_verification'):value['spec_verification']={k:v for k,v in r['spec_verification'].items() if k!='results'}|{'results':r['spec_verification']['results'][:20]}
    if r.get('proposed_split'):value['proposed_split']=r['proposed_split'];value['gate']=r.get('gate')
    parsed=parse_report(r.get('report') or '')
    if parsed:value['answer']=parsed['findings'][:3000]
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
                      'artifact':c.get('artifact'),'reason':c.get('reason')} |
                     ({'failures':[l.strip()[5:85] for l in failure_lines(c,limit=5,width=40)]} if c.get('failures') else {})
                     for c in result.get('checks',[])],
           'attempts':len(result.get('attempts',[])),
           'board':{'state':board.get('state'),'mode':board.get('mode'),'degraded':board.get('degraded'),'drift_flags':board.get('drift_flags'),
                    } if (board:=result.get('board')) else None,
           'requirements_review':{k:(result.get('answer_review') or {}).get(k) for k in ('state','status','initial_status')} if result.get('answer_review') else None,
           'next_action':(result.get('completion') or {}).get('next_action'),
           'split_units':len(result.get('proposed_split') or []) or None,
           'workspace':({'state':(live_workspace(job) or {}).get('state'),'origin_unchanged':result['workspace'].get('origin_unchanged'),'patch':'patch.diff'}
                        if result.get('workspace') else None),
           'acceptance':({'scope_ok':a['scope_ok'],'diff':a['diff'],'review_focus':a['review_focus'][:3],'repair_used':a['repair_used']}
                         if (a:=result.get('acceptance')) else None),
           'review':(job.get('review') or {}).get('decision'),
           'evidence':f"/api/jobs/{job['id']}/result?view=summary"}
    return bounded(value,2048)


def progress_summary(job, queue_position=None, queue_wait=None):
    p = job.get('progress') or {}
    value = {'id':job['id'],'state':job['state'],'role':job['request']['role'],
             'review':{'decision':job['review']['decision']} if job.get('review') else None,
             'queue_position':queue_position, 'queue_wait_upper_bound_seconds':queue_wait, 'eta_seconds':p.get('eta_seconds'), 'phase':p.get('phase',job['state']),
             'active_check':str(p.get('active_check') or '')[:120] or None,
             'elapsed_seconds':round((job.get('ended') or time.time())-(job.get('started') or job['created']),1),
             'deadline':p.get('deadline'), 'heartbeat':p.get('heartbeat'), 'last_output_at':p.get('last_output_at'),
             'model_budget_remaining':p.get('model_budget_remaining'),
             'check_results':[{'name':str(c['name'])[:60],'status':c.get('status'), 'exit_code':c.get('exit_code')} for c in (job.get('result') or {}).get('checks',[])][:12]}
    return bounded(value,2048)


def history_item(job):
    return {k:job.get(k) for k in ('id','state','created','started','ended')} | {
        'request':{k:job['request'].get(k) for k in ('role','repo','caller','workflow','board')} | {'task':job['request']['task'][:160]},
        'review':{'decision':job['review']['decision'], 'notes':''} if job.get('review') else None,
        'result':{'worker_status':(job.get('result') or {}).get('worker_status'), 'usage':(job.get('result') or {}).get('usage')}}
