from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from urllib.parse import quote, urlencode
import re
import uuid
from .client import call
from .models import Check, JobRequest, Review, default_timeout

READ = ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=True,openWorldHint=False)

def job_path(job_id):
    if not re.fullmatch(r'(?:[a-f0-9]{32}|legacy-[a-f0-9]{24})',job_id):raise ValueError('Invalid job ID')
    return '/api/jobs/'+job_id

INVESTIGATOR_KINDS=('find_code','explain','config_use','compare','check_requirement')
EDITOR_KINDS=('mechanical','guard')
NEXT_EDITOR='Poll get_job, then read get_result: check the acceptance packet and patch.diff, then apply_result (or discard_result). If the task was refused as too large, resubmit the units in proposed_split.'
NEXT_READ='Poll get_job (eta_seconds), then read get_result.'

def submit_request(request,next_step):
    result=call('POST','/api/jobs',request.model_dump())
    return {'id':result['id'],'state':result['state'],'role':result['request']['role'],'next':next_step}

def with_files(files,read_paths):
    """Read scope must contain every file the job may edit."""
    return list(dict.fromkeys([*(read_paths or []),*files])) if read_paths else []

def create_server():
    server=FastMCP('local-worker-hub')

    @server.tool(annotations=WRITE)
    def investigate_code(repo: str, question: str, read_paths: list[str] | None=None, kind: str='explain', idempotency_key: str | None=None) -> dict:
        """Ask the local worker to investigate code (read-only): find all code involved in X, explain how X works, where a config value is used, compare two implementations, or check whether a requirement is implemented. kind is one of find_code, explain, config_use, compare, check_requirement. The report gives host-verified path:line references; a COMPLETE report always has at least one. Returns a job id immediately."""
        if kind not in INVESTIGATOR_KINDS:raise ValueError('kind must be one of '+', '.join(INVESTIGATOR_KINDS))
        return submit_request(JobRequest(role='investigator',kind=kind,repo=repo,task=question,read_paths=read_paths or [],execution_preset='work',caller='mcp',
            timeout=default_timeout('investigator','work'),idempotency_key=idempotency_key or uuid.uuid4().hex),NEXT_READ)

    @server.tool(annotations=WRITE)
    def implement_change(repo: str, task: str, files: list[str], checks: list[dict] | None=None, read_paths: list[str] | None=None, delete_paths: list[str] | None=None, kind: str='mechanical', continue_from: str | None=None, idempotency_key: str | None=None) -> dict:
        """Ask the local worker to make a bounded, clearly specified change in exactly the listed files (a mechanical change, or a specified guard; kind is mechanical or guard). It works in a private copy of the repository; nothing changes in your tree until apply_result. With checks it runs them and gets one repair. Keep it to one concern or one file per job where you can: a long task still runs (a reply cut off by the output limit is continued from where it stopped), but the report is easier to review when the job is small. For a file over 400 lines, name the exact strings to change. read_paths adds read-only context. Returns a job id immediately."""
        if kind not in EDITOR_KINDS:raise ValueError('kind must be one of '+', '.join(EDITOR_KINDS))
        checks_=[Check.model_validate(c) for c in checks or []]
        return submit_request(JobRequest(role='editor',kind=kind,repo=repo,task=task,allowed_paths=files,delete_paths=delete_paths or [],read_paths=with_files(files,read_paths),checks=checks_,
            workflow='implement' if checks_ else 'single',repair_attempts=1 if checks_ else 0,execution_preset='work',caller='mcp',timeout=default_timeout('editor','work'),
            workspace_from=continue_from,idempotency_key=idempotency_key or uuid.uuid4().hex),NEXT_EDITOR)

    @server.tool(annotations=WRITE)
    def fix_failing_test(repo: str, test_command: list[str], files: list[str], test_id: str='', details: str='', read_paths: list[str] | None=None, idempotency_key: str | None=None) -> dict:
        """Ask the local worker to fix one exact failing test by changing the listed source files (not the test). test_command is the argv that runs the test (for example ["pytest","-q","tests/test_a.py::test_x"]); it is the approved check, re-run after the edit with one repair. details can carry the failure text. Works in a private copy; apply_result to take the change. Returns a job id immediately."""
        task='The test'+(f' {test_id}' if test_id else '')+' fails. Fix the source files listed (not the test) with the smallest change.'+(f'\nFailure details:\n{details}' if details else '')
        return submit_request(JobRequest(role='editor',kind='fix_test',repo=repo,task=task,allowed_paths=files,read_paths=with_files(files,read_paths),
            checks=[Check(name='failing test',argv=test_command)],execution_preset='work',caller='mcp',timeout=default_timeout('editor','work'),
            idempotency_key=idempotency_key or uuid.uuid4().hex),NEXT_EDITOR)

    @server.tool(annotations=WRITE)
    def add_regression_test(repo: str, bug: str, test_file: str, test_command: list[str], read_paths: list[str] | None=None, idempotency_key: str | None=None) -> dict:
        """Ask the local worker to add one regression test for a known bug to test_file. test_command is the argv that runs that test file. The host runs it before and after the edit: the new test must be defined by the job and must FAIL on the current code (a test that passes, an edited existing test or a file that does not collect is reported PARTIAL). It changes only test_file. Returns a job id immediately."""
        task=f'Known bug: {bug}\nAdd one regression test to {test_file} that fails on the current code because of this bug. Do not change any other file.'
        return submit_request(JobRequest(role='editor',kind='regression_test',repo=repo,task=task,allowed_paths=[test_file],read_paths=with_files([test_file],read_paths),
            checks=[Check(name='regression test',argv=test_command)],execution_preset='work',caller='mcp',timeout=default_timeout('editor','work'),
            idempotency_key=idempotency_key or uuid.uuid4().hex),NEXT_EDITOR)

    @server.tool(annotations=WRITE)
    def run_checks(repo: str, checks: list[dict], idempotency_key: str | None=None) -> dict:
        """Run explicitly approved checks (argv arrays) and get the failing tests with file:line and the assertion, parsed by the host at zero model tokens. Each check is {name, argv, cwd?, timeout?}. Never use it for installs, deployments or destructive commands. Returns a job id immediately."""
        return submit_request(JobRequest(role='validator',kind='run_tests',repo=repo,task='Run the approved checks and report which tests fail and why.',
            checks=[Check.model_validate(c) for c in checks],caller='mcp',timeout=default_timeout('validator'),idempotency_key=idempotency_key or uuid.uuid4().hex),NEXT_READ)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=True,idempotentHint=True,openWorldHint=True))
    def submit_job(role: str, task: str, idempotency_key: str, repo: str | None=None,
                   allowed_paths: list[str] | None=None, checks: list[dict] | None=None,
                   context: str='', caller: str='mcp', caller_session: str | None=None,
                   timeout: int | None=None, summary_mode: str='none', failure_policy: str='fail_fast',
                   profile_hash: str | None=None, profile_ref: str | None=None,
                   check_groups: list[str] | None=None, parameters: dict[str,str] | None=None,
                   workflow: str='single', repair_attempts: int=0, investigate_first: bool=False, kind: str | None=None,
                   model_context: int | None=None, model_thinking: bool | None=None,
                   execution_preset: str | None=None, read_paths: list[str] | None=None,
                   evidence_job_ids: list[str] | None=None, handoff_id: str | None=None,
                   review_pass: bool | None=None, board: bool=False, board_mode: str | None=None, verify: bool=False, agent_loop: bool=False, model: str | None=None) -> dict:
        """Generic job submission. For common work prefer investigate_code, implement_change, fix_failing_test, add_regression_test and run_checks, which fix the safe defaults. Submit a bounded local task. Researcher accepts only a sanitized public brief; Editor requires exact file scope and always works in a private copy of the repository: review the patch and acceptance packet, then call apply_result or discard_result. kind names the delegation shape (find_code, explain, config_use, compare, check_requirement, mechanical, guard, regression_test, run_tests, fix_test): it checks the role, tunes the answer format and sets defaults (fix_test uses the implement workflow with one repair; regression_test requires the check that runs the new test and verifies that the test fails on the current code). Reuse the same key only for transport retries. Public web roles answer in plain language by default (about 30-60s). verify=true runs strict origin-proof research (slower, may end PARTIAL). board=true is a rarely useful anonymous multi-model deliberation (slower, FIFO queue); skip it when the task is already specified. The call returns immediately: continue other work, then poll get_job (it reports eta_seconds and queue wait) and read get_result."""
        request=JobRequest(role=role,task=task,repo=repo,allowed_paths=allowed_paths or [],checks=checks or [],
            context=context,caller=caller,caller_session=caller_session,idempotency_key=idempotency_key,
            timeout=timeout if timeout is not None else default_timeout(role,execution_preset,board,verify,bool(review_pass)),
            summary_mode=summary_mode,failure_policy=failure_policy,profile_hash=profile_hash,profile_ref=profile_ref,
            check_groups=check_groups or [],parameters=parameters or {},workflow=workflow,
            repair_attempts=repair_attempts,investigate_first=investigate_first,kind=kind,
            model_context=model_context,model_thinking=model_thinking,execution_preset=execution_preset,review_pass=review_pass,
            read_paths=read_paths or [],evidence_job_ids=evidence_job_ids or [],handoff_id=handoff_id,board=board,board_mode=board_mode,verify=verify,agent_loop=agent_loop,model=model)
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
    def apply_result(job_id: str, accept_removals: bool=False, revalidate: bool=False, run_checks: bool=False) -> dict:
        """Apply a finished Editor job's patch to the repository. The worker edits a private copy; this writes only the authorized files, and refuses (listing conflicts) if any of them changed in the repository since the job started. Review patch.diff and the acceptance packet first. Files the job removed are applied only if it declared them in delete_paths and accept_removals is true. The result lists stale_dependencies (read_paths files that changed since the job started). revalidate=true first re-runs the job's approved checks on your tree as it is now plus the patch and writes nothing if they fail; run_checks=true runs them in your tree after the write. Both run the checks synchronously, so keep them short."""
        return call('POST',job_path(job_id)+'/apply',{'accept_removals':accept_removals,'revalidate':revalidate,'run_checks':run_checks})

    @server.tool(annotations=WRITE)
    def revert_result(job_id: str) -> dict:
        """Undo an applied Editor job: restore each written file to its snapshot content (delete files the job created). Refuses, listing conflicts, if a file changed again after the apply."""
        return call('POST',job_path(job_id)+'/revert')

    @server.tool(annotations=WRITE)
    def discard_result(job_id: str) -> dict:
        """Delete a finished Editor job's private workspace without applying it. The patch and report stay readable."""
        return call('POST',job_path(job_id)+'/discard')

    @server.tool(annotations=WRITE)
    def cancel_job(job_id: str) -> dict:
        """Cancel queued or running local work. Changes already made are preserved for review."""
        job=call('POST',job_path(job_id)+'/cancel')
        return {'id':job['id'],'state':job['state']}

    @server.tool(annotations=WRITE)
    def record_review(job_id: str, decision: str, notes: str='', baseline_frontier_tokens: int | None=None,
                      delegated_frontier_tokens: int | None=None, baseline_frontier_cost: float | None=None,
                      delegated_frontier_cost: float | None=None, measurement_source: str='measured',
                      task_outcome: str | None=None, review_effort_seconds: int | None=None, reason: str | None=None) -> dict:
        """Record frontier acceptance/rejection/takeover. A rejected or takeover review must say why in notes (at least 10 characters) and should give a reason (truncated, wrong_edit, oversized, no_change, check_failed, scope, other): real failures become regression cases. Baselines must include orchestration, review, retries and takeover usage."""
        review=Review(decision=decision,notes=notes,baseline_frontier_tokens=baseline_frontier_tokens,
            delegated_frontier_tokens=delegated_frontier_tokens,baseline_frontier_cost=baseline_frontier_cost,
            delegated_frontier_cost=delegated_frontier_cost,measurement_source=measurement_source,
            task_outcome=task_outcome,review_effort_seconds=review_effort_seconds,reason=reason)
        job=call('POST',job_path(job_id)+'/review',review.model_dump())
        return {'id':job['id'],'review':job['review']}

    return server

def main():
    create_server().run(transport='stdio')
