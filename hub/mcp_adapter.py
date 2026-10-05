from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from urllib.parse import quote, urlencode
import re
from .client import call
from .models import JobRequest, Review, default_timeout

READ = ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=True,openWorldHint=False)

def job_path(job_id):
    if not re.fullmatch(r'(?:[a-f0-9]{32}|legacy-[a-f0-9]{24})',job_id):raise ValueError('Invalid job ID')
    return '/api/jobs/'+job_id

def create_server():
    server=FastMCP('local-worker-hub')

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=True,idempotentHint=True,openWorldHint=True))
    def submit_job(role: str, task: str, idempotency_key: str, repo: str | None=None,
                   allowed_paths: list[str] | None=None, checks: list[dict] | None=None,
                   context: str='', caller: str='mcp', caller_session: str | None=None,
                   timeout: int | None=None, summary_mode: str='none', failure_policy: str='fail_fast',
                   profile_hash: str | None=None, profile_ref: str | None=None,
                   check_groups: list[str] | None=None, parameters: dict[str,str] | None=None,
                   workflow: str='single', repair_attempts: int=0, investigate_first: bool=False,
                   model_context: int | None=None, model_thinking: bool | None=None,
                   execution_preset: str | None=None, read_paths: list[str] | None=None,
                   evidence_job_ids: list[str] | None=None, handoff_id: str | None=None,
                   review_pass: bool | None=None, board: bool=False, board_mode: str | None=None, verify: bool=False, agent_loop: bool=False) -> dict:
        """Submit a bounded local task. Researcher accepts only a sanitized public brief; Editor requires exact file scope. Reuse the same key only for transport retries. Public web roles answer in plain language by default (about 30-60s). verify=true runs strict origin-proof research (slower, may end PARTIAL). board=true is a rarely useful anonymous multi-model deliberation (slower, FIFO queue); skip it when the task is already specified. The call returns immediately: continue other work, then poll get_job (it reports eta_seconds and queue wait) and read get_result."""
        request=JobRequest(role=role,task=task,repo=repo,allowed_paths=allowed_paths or [],checks=checks or [],
            context=context,caller=caller,caller_session=caller_session,idempotency_key=idempotency_key,
            timeout=timeout if timeout is not None else default_timeout(role,execution_preset,board,verify,bool(review_pass)),
            summary_mode=summary_mode,failure_policy=failure_policy,profile_hash=profile_hash,profile_ref=profile_ref,
            check_groups=check_groups or [],parameters=parameters or {},workflow=workflow,
            repair_attempts=repair_attempts,investigate_first=investigate_first,
            model_context=model_context,model_thinking=model_thinking,execution_preset=execution_preset,review_pass=review_pass,
            read_paths=read_paths or [],evidence_job_ids=evidence_job_ids or [],handoff_id=handoff_id,board=board,board_mode=board_mode,verify=verify,agent_loop=agent_loop)
        result=call('POST','/api/jobs',request.model_dump())
        return {'id':result['id'],'state':result['state'],'role':result['request']['role']}

    @server.tool(annotations=READ)
    def get_job(job_id: str) -> dict:
        """Get execution state and bounded progress. Completion is separate from frontier acceptance."""
        return call('GET',job_path(job_id)+'/progress')

    @server.tool(annotations=READ)
    def get_result(job_id: str, detail: str='brief') -> dict:
        """Get brief recorded outcomes (under 2 KiB). summary/full expose more evidence. Completion is not acceptance."""
        if detail not in ('brief','summary','full'):raise ValueError('detail must be brief, summary or full')
        result=call('GET',job_path(job_id)+'/result?view='+detail)
        return result or {'ready':False}

    @server.tool(annotations=READ)
    def read_artifact(job_id: str, artifact: str, offset: int=0, limit: int=4000) -> dict:
        """Read an authenticated report/diff/check-log page. Offsets and limits are bytes, maximum 16000. Follow next_offset when has_more."""
        if offset<0 or not 1<=limit<=16000:raise ValueError('Invalid page bounds')
        return call('GET',job_path(job_id)+'/artifact-page/'+quote(artifact,safe='')+'?'+urlencode({'offset':offset,'limit':limit}))

    @server.tool(annotations=READ)
    def read_trace(job_id: str, offset: int=0, limit: int=100) -> dict:
        """Inspect a private local-model trace page. Not needed for routine frontier acceptance; offset is bytes."""
        if offset<0 or not 1<=limit<=200:raise ValueError('Invalid trace page bounds')
        return call('GET',job_path(job_id)+'/trace?'+urlencode({'offset':offset,'limit':limit}))

    @server.tool(annotations=READ)
    def get_project_profile(repo: str) -> dict:
        """Inspect reviewed commands, then submit its short profile_ref and selected groups. Inspection executes nothing."""
        return call('GET','/api/project-profile?'+urlencode({'repo':repo}))

    @server.tool(annotations=READ)
    def wait_job(job_id: str, timeout_seconds: int=25) -> dict:
        """Wait up to 30 seconds for completion or meaningful phase/check progress."""
        if not 1<=timeout_seconds<=30:raise ValueError('timeout_seconds must be 1..30')
        return call('GET',job_path(job_id)+'/wait?'+urlencode({'timeout_seconds':timeout_seconds}))

    @server.tool(annotations=WRITE)
    def summarize_result(job_id: str, idempotency_key: str, timeout: int=120) -> dict:
        """Queue optional tools-disabled local analysis of saved check evidence. Creates a linked job; never reruns commands or replaces the original result."""
        return call('POST',job_path(job_id)+'/summarize',{'idempotency_key':idempotency_key,'timeout':timeout,'caller':'mcp'})

    @server.tool(annotations=WRITE)
    def cancel_job(job_id: str) -> dict:
        """Cancel queued or running local work. Changes already made are preserved for review."""
        job=call('POST',job_path(job_id)+'/cancel')
        return {'id':job['id'],'state':job['state']}

    @server.tool(annotations=WRITE)
    def record_review(job_id: str, decision: str, notes: str='', baseline_frontier_tokens: int | None=None,
                      delegated_frontier_tokens: int | None=None, baseline_frontier_cost: float | None=None,
                      delegated_frontier_cost: float | None=None, measurement_source: str='measured',
                      task_outcome: str | None=None, review_effort_seconds: int | None=None) -> dict:
        """Record frontier acceptance/rejection/takeover. Baselines must include orchestration, review, retries and takeover usage."""
        review=Review(decision=decision,notes=notes,baseline_frontier_tokens=baseline_frontier_tokens,
            delegated_frontier_tokens=delegated_frontier_tokens,baseline_frontier_cost=baseline_frontier_cost,
            delegated_frontier_cost=delegated_frontier_cost,measurement_source=measurement_source,
            task_outcome=task_outcome,review_effort_seconds=review_effort_seconds)
        job=call('POST',job_path(job_id)+'/review',review.model_dump())
        return {'id':job['id'],'review':job['review']}

    return server

def main():
    create_server().run(transport='stdio')
