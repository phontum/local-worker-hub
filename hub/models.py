from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pathlib import Path
import re
from .model_registry import models as registered_models

Role = Literal['investigator', 'editor', 'validator', 'researcher', 'personal']

class Check(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    argv: list[str] = Field(min_length=1, max_length=30)
    cwd: str = '.'
    timeout: int = Field(default=300, ge=1, le=600)
    env_allowlist: list[str] = Field(default_factory=list, max_length=20)
    environment: dict[str, str] = Field(default_factory=dict, max_length=20)
    required_env: list[str] = Field(default_factory=list, max_length=20)
    depends_on: list[str] = Field(default_factory=list, max_length=12)
    requires_test_database: bool = False
    guard_next_dev: bool = False

def default_timeout(role, preset=None, board=False, verify=False, review=False):
    """Model-time budget when the caller gives none: plain public lookups are quick, everything else keeps 300s."""
    if preset == 'small':
        return 120
    if role in ('personal', 'researcher') and not (board or verify or review or preset == 'extended'):
        return 120
    return 300

class Turn(BaseModel):
    """One earlier chat message; follow-ups see a few of these so \"and tomorrow?\" can be understood."""
    model_config = ConfigDict(extra='forbid')
    role: Literal['user', 'assistant']
    text: str = Field(max_length=800)

class JobRequest(BaseModel):
    role: Role = 'investigator'
    workflow: Literal['single', 'implement'] = 'single'
    repair_attempts: int = Field(default=0, ge=0, le=2)
    investigate_first: bool = False
    in_place: bool = False
    skip_gate: bool = False
    auto_split: bool = False  # when the complexity gate trips, run the proposed units one after another inside this job instead of refusing
    continuation: bool = True  # after a reply cut off by the output limit, keep its complete blocks staged and ask the model to continue
    model_output: Literal[4096, 8192] | None = None  # editor output cap in tokens (4096 unless set; 8192 is an experiment)
    edit_format: Literal['text', 'json'] = 'text'
    workspace_from: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')  # continue an earlier Editor job's private workspace
    match_mode: Literal['substring', 'word', 'line'] = 'line'  # exact SEARCH matches must be whole lines; every recorded model SEARCH was
    refuse_oversized: bool = False  # refuse (rather than only advise on) a task the complexity gate flags; continuation makes refusing unnecessary by default
    kind: Literal['find_code', 'explain', 'config_use', 'compare', 'check_requirement', 'mechanical', 'guard', 'regression_test', 'run_tests', 'fix_test'] | None = None
    model_context: Literal[16384, 32768] | None = None
    model_thinking: bool | None = None
    review_pass: bool | None = None
    execution_preset: Literal['small', 'work', 'extended'] | None = None
    read_paths: list[str] = Field(default_factory=list, max_length=30)
    evidence_job_ids: list[str] = Field(default_factory=list, max_length=4)
    handoff_id: str | None = Field(default=None, max_length=200)
    task: str = Field(min_length=1, max_length=12000)
    repo: str | None = None
    allowed_paths: list[str] = Field(default_factory=list, max_length=30)
    delete_paths: list[str] = Field(default_factory=list, max_length=10)
    checks: list[Check] = Field(default_factory=list, max_length=12)
    context: str = Field(default='', max_length=16000)
    caller: str = Field(default='cli', max_length=80)
    caller_session: str | None = Field(default=None, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=200)
    timeout: int = Field(default=300, ge=1, le=1800)
    no_recovery: bool = False
    summary_mode: Literal['none', 'local'] = 'none'
    failure_policy: Literal['fail_fast', 'continue_independent'] = 'fail_fast'
    source_job_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    profile_hash: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    profile_ref: str | None = Field(default=None, pattern=r'^[a-f0-9]{12}$')
    check_groups: list[str] = Field(default_factory=list, max_length=12)
    parameters: dict[str, str] = Field(default_factory=dict, max_length=20)
    board: bool = False
    board_mode: Literal['full', 'lite'] | None = None
    verify: bool = False
    agent_loop: bool = False
    model: str | None = Field(default=None, pattern=r'^[a-z][a-z0-9_-]{0,30}$')
    history: list[Turn] = Field(default_factory=list, max_length=6)

    @model_validator(mode='after')
    def boundaries(self):
        if self.execution_preset == 'small' and 'timeout' not in self.model_fields_set:
            self.timeout = 120
        if self.board and 'timeout' not in self.model_fields_set:
            self.timeout = 300
        if self.verify and self.role not in ('personal', 'researcher'): raise ValueError('verify applies to public web roles only')
        if self.board_mode and not self.board: raise ValueError('board_mode requires board')
        if self.model and self.model not in registered_models(): raise ValueError('Unknown local model alias: ' + self.model)
        if self.model and self.board: raise ValueError('Board phases choose their own models; omit model')
        if self.board:
            if self.role not in ('personal', 'researcher'): raise ValueError('Board deliberation is available for public web roles only in this version')
            if self.execution_preset in ('small', 'extended') or self.model_context == 32768:
                raise ValueError('Board phases use fixed 16K contexts; omit small/extended presets and 32K context')
        if not self.task.strip(): raise ValueError('Task is empty')
        if self.role not in ('researcher', 'personal'):
            if not self.repo: raise ValueError('This role requires an explicit repository')
            p = Path(self.repo).expanduser().resolve(strict=True)
            if not p.is_dir() or p in (Path('/'), Path.home()):
                raise ValueError('Select a project directory, not the home directory or filesystem root')
            self.repo = str(p)
            if self.history: raise ValueError('Chat history applies to public web roles only')
        elif self.repo or self.context or self.allowed_paths or self.read_paths or self.evidence_job_ids or self.checks or self.source_job_id or self.profile_hash or self.profile_ref or self.check_groups or self.parameters:
            raise ValueError('Public web roles accept a public task brief only; no repository/context/checks')
        if any(not re.fullmatch(r'[a-f0-9]{32}', ident) for ident in self.evidence_job_ids):
            raise ValueError('Invalid evidence job ID')
        if self.read_paths and self.role not in ('investigator','editor'):
            raise ValueError('Read scope requires Investigator or Editor')
        if self.role == 'editor' and not self.allowed_paths:
            raise ValueError('Editor requires explicit allowed_paths')
        if self.workflow == 'implement' and self.role != 'editor':
            raise ValueError('Implement workflow requires Editor and exact allowed_paths')
        if self.workflow == 'implement' and not (self.checks or self.profile_hash or self.profile_ref):
            raise ValueError('Implement workflow requires supplied validation checks')
        if self.in_place and self.role != 'editor': raise ValueError('in_place applies to Editor only')
        if self.delete_paths and not set(self.delete_paths) <= set(self.allowed_paths): raise ValueError('delete_paths must be a subset of allowed_paths')
        if self.workspace_from and (self.role != 'editor' or self.in_place): raise ValueError('workspace_from continues a private workspace and applies to Editor jobs that are not in_place')
        if self.kind:
            wanted = {'find_code': 'investigator', 'explain': 'investigator', 'config_use': 'investigator', 'compare': 'investigator', 'check_requirement': 'investigator',
                      'mechanical': 'editor', 'guard': 'editor', 'regression_test': 'editor', 'fix_test': 'editor', 'run_tests': 'validator'}[self.kind]
            if self.role != wanted: raise ValueError(f'kind {self.kind} runs as {wanted}')
            if self.kind == 'regression_test' and not (self.checks or self.profile_hash or self.profile_ref): raise ValueError('regression_test requires the check that runs the new test')
            if self.kind == 'fix_test' and (self.checks or self.profile_hash or self.profile_ref):
                # A failing test is fixed in the implement workflow with one targeted repair unless the caller chose otherwise.
                if 'workflow' not in self.model_fields_set: self.workflow = 'implement'
                if 'repair_attempts' not in self.model_fields_set and self.workflow == 'implement': self.repair_attempts = 1
        if self.workflow == 'single' and (self.repair_attempts or self.investigate_first):
            raise ValueError('Repair and initial investigation require implement workflow')
        if self.allowed_paths and self.role != 'editor': raise ValueError('Only Editor accepts edit scope')
        if self.checks and self.role not in ('editor', 'validator'): raise ValueError('Checks require Editor or Validator')
        if self.source_job_id and (self.role != 'validator' or self.checks or self.profile_hash or self.profile_ref):
            raise ValueError('Saved-result analysis requires Validator without commands or a profile')
        if self.role == 'validator' and not (self.checks or self.source_job_id or self.profile_hash or self.profile_ref): raise ValueError('Validator requires checks')
        if self.profile_hash and self.profile_ref: raise ValueError('Use either profile hash or profile ref')
        if bool(self.profile_hash or self.profile_ref) != bool(self.check_groups): raise ValueError('Profile reference and check groups are required together')
        if (self.profile_hash or self.profile_ref) and self.role not in ('validator','editor'): raise ValueError('Profiles select Validator or Editor checks only')
        if self.parameters and not (self.profile_hash or self.profile_ref): raise ValueError('Parameters require a reviewed profile')
        names = [c.name for c in self.checks]
        if len(set(names)) != len(names): raise ValueError('Check names must be unique')
        pending = {c.name: set(c.depends_on) for c in self.checks}
        if any(deps - set(names) for deps in pending.values()): raise ValueError('Unknown check dependency')
        done = set()
        while pending:
            ready = [n for n, deps in pending.items() if deps <= done]
            if not ready: raise ValueError('Check dependencies contain a cycle')
            done.update(ready)
            for n in ready: del pending[n]
        for check in self.checks:
            reserved = {'HOME','PWD','LOCAL_WORKER_STATE','LOCAL_WORKER_CONFIG','OPENCODE_CONFIG_CONTENT'}
            if reserved.intersection([*check.env_allowlist, *check.environment, *check.required_env]):
                raise ValueError('Reserved environment variable requested')
            if any('\x00' in x for pair in check.environment.items() for x in pair): raise ValueError('Invalid environment value')
            if any('\x00' in x for x in check.argv): raise ValueError('Invalid command argument')
            if Path(check.cwd).is_absolute() or '..' in Path(check.cwd).parts:
                raise ValueError('Check cwd must be within the repository')
        return self

    def context_limit(self):
        return self.model_context or (32768 if self.execution_preset == 'extended' else 16384)

    def model_budget(self):
        return self.timeout

    def needs_answer_review(self):
        # Implementation already has an independent review. Direct validation
        # remains deterministic, including when extended context was requested.
        if self.workflow == 'implement' or (self.role == 'validator' and self.summary_mode == 'none' and not self.source_job_id):
            return False
        if self.review_pass is not None:
            return self.review_pass
        # Extended means more context and thinking; for public questions it no longer implies the strict reviewer.
        return self.execution_preset == 'extended' and (self.verify or self.role not in ('personal', 'researcher'))

class EvidenceReference(BaseModel):
    source: str = Field(min_length=1, max_length=30)
    quote: str = Field(min_length=1, max_length=2000)

class RequirementAssessment(BaseModel):
    requirement: str = Field(min_length=1, max_length=1000)
    status: Literal['met', 'unmet', 'unknown']
    evidence: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[EvidenceReference] = Field(max_length=8)

class WebClaim(BaseModel):
    url: str = Field(min_length=1,max_length=2048)
    source: str = Field(min_length=1,max_length=30)
    quote: str = Field(min_length=1,max_length=2000)

class WebRequirement(BaseModel):
    id: str = Field(min_length=1,max_length=30,description='Requirement ID such as Q1; distinct from tool-evidence IDs R0, R1, etc.')
    task_quote: str = Field(min_length=1,max_length=2000)
    requirement: str = Field(min_length=1,max_length=1000)
    acceptance: str = Field(min_length=1,max_length=1500)

class WebResearchPlan(BaseModel):
    objective: str = Field(min_length=1,max_length=1500)
    needs_current_evidence: bool
    requirements: list[WebRequirement] = Field(min_length=1,max_length=15)
    strategy: list[str] = Field(min_length=1,max_length=8)
    result_format: str = Field(min_length=1,max_length=1000)
    stop_when: str = Field(min_length=1,max_length=1000)

class AnswerReview(BaseModel):
    status: Literal['COMPLETE', 'PARTIAL', 'BLOCKED']
    findings: str = Field(min_length=1)
    files: str = Field(min_length=1)
    checks: str = Field(min_length=1)
    risks: str = Field(min_length=1)
    requirements: list[RequirementAssessment] = Field(min_length=1, max_length=50)
    web_claims: list[WebClaim] = Field(default_factory=list,max_length=16)

    @model_validator(mode='after')
    def conservative_completion(self):
        # The assessed original requirements determine completion. Protocol
        # finalization (e.g. tools being disabled) is not an extra user task.
        if all(r.status == 'met' for r in self.requirements):self.status = 'COMPLETE'
        elif self.status == 'COMPLETE':self.status = 'PARTIAL'
        return self

REVIEW_REASONS = ('truncated', 'wrong_edit', 'oversized', 'no_change', 'check_failed', 'scope', 'other')

class Review(BaseModel):
    decision: Literal['accepted', 'rejected', 'takeover']
    notes: str = Field(default='', max_length=4000)
    reason: Literal['truncated', 'wrong_edit', 'oversized', 'no_change', 'check_failed', 'scope', 'other'] | None = None
    baseline_frontier_tokens: int | None = Field(default=None, ge=0)
    delegated_frontier_tokens: int | None = Field(default=None, ge=0)
    baseline_frontier_cost: float | None = Field(default=None, ge=0)
    delegated_frontier_cost: float | None = Field(default=None, ge=0)
    measurement_source: Literal['measured', 'manual_estimate'] = 'measured'
    task_outcome: Literal['completed', 'partial', 'blocked'] | None = None
    review_effort_seconds: int | None = Field(default=None, ge=0)

    @model_validator(mode='after')
    def say_why(self):
        # A rejection or takeover with no reason cannot become a regression case; require it on input (stored reviews are untouched).
        if self.decision in ('rejected', 'takeover') and len(self.notes.strip()) < 10:
            raise ValueError('A rejected or takeover review needs notes (at least 10 characters) saying what went wrong, and ideally a reason: ' + ', '.join(REVIEW_REASONS))
        return self


class BoardModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class BoardRequirement(BoardModel):
    id: str = Field(min_length=1, max_length=30)
    kind: Literal['explicit', 'derived']
    task_quote: str = Field(min_length=1, max_length=2000, description='Copied EXACTLY from the task')
    requirement: str = Field(min_length=1, max_length=1000)
    acceptance: str = Field(min_length=1, max_length=1500)

class BoardAssumption(BoardModel):
    text: str = Field(min_length=1, max_length=600)
    risk_if_wrong: str = Field(max_length=600)
    how_to_check: str = Field(max_length=600)

class BoardPlanStep(BoardModel):
    step: str = Field(min_length=1, max_length=600)
    requirement_ids: list[str] = Field(max_length=10)

class BoardProposal(BoardModel):
    """An independent proposal. There is deliberately no field for facts: only hypotheses to verify."""
    requirements: list[BoardRequirement] = Field(min_length=1, max_length=12)
    assumptions: list[BoardAssumption] = Field(max_length=8)
    plan: list[BoardPlanStep] = Field(min_length=1, max_length=10)
    risks: list[str] = Field(max_length=8)
    hypotheses_to_verify: list[str] = Field(max_length=8)
    open_questions: list[str] = Field(max_length=6)
    rationale: str = Field(max_length=600)

class BoardTriage(BoardModel):
    skip_board: bool
    needs_external_info: bool
    ambiguity: Literal['low', 'medium', 'high']
    scout_query: str = Field(default='', max_length=200, description='Short neutral public search query, or empty')

class SynthRequirement(BoardRequirement):
    supported_by: list[Literal['A', 'B', 'C', 'D']] = Field(max_length=4)

class BoardDecision(BoardModel):
    topic: str = Field(min_length=1, max_length=300)
    chosen: str = Field(min_length=1, max_length=600)
    basis: Literal['requirement', 'evidence', 'reasoning']

class BoardDissent(BoardModel):
    candidate: Literal['A', 'B', 'C', 'D']
    point: str = Field(min_length=1, max_length=600)
    why_not_adopted: str = Field(min_length=1, max_length=600)

class BoardSynthesis(BoardModel):
    objective: str = Field(min_length=1, max_length=1500)
    requirements: list[SynthRequirement] = Field(min_length=1, max_length=15)
    decisions: list[BoardDecision] = Field(max_length=10)
    dissent: list[BoardDissent] = Field(max_length=6)
    plan: list[str] = Field(min_length=1, max_length=8)
    result_format: str = Field(min_length=1, max_length=1000)
    stop_when: str = Field(min_length=1, max_length=1000)
    needs_web: bool
    needs_current_evidence: bool
    open_questions: list[str] = Field(max_length=6)
