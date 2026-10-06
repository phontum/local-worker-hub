"""DelegationSpec: one structured description of a delegated task, shared by frontier callers (MCP `delegate`, `local-worker spec`) and standalone use.

A spec states the goal, the symbols and files involved, the required changes (with exact `old -> new` mappings), what must stay true, the evidence the
caller already holds, the approved checks and the acceptance criteria. `compile_spec` turns it into the JobRequest the hub already runs, with a task text whose
sections the pipelines read as before, so natural-language jobs keep working unchanged. Criteria that can be decided from the files and check results are verified
by the host after the edit; the rest are listed for the frontier as `frontier_judgement`, never silently assumed met."""
import re
import uuid
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from hub.skills.coding.editing import mappings
from hub.skills.coding.intelligence.codeindex import analyze
from hub.models import Check, JobRequest, default_timeout

MECHANICAL = ('file_unchanged', 'symbol_exists', 'symbol_absent', 'no_new_files', 'check_passes')
EDITOR_KINDS = ('mechanical', 'guard', 'regression_test', 'fix_test')
SINGLE_LINE = re.compile(r'^[^\n;]+$')

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Target(Strict):
    path: str = Field(min_length=1, max_length=300)
    symbol: str | None = Field(default=None, max_length=200)
    lines: tuple[int, int] | None = None

Mapping = mappings.LiteralMapping

class Change(Strict):
    description: str = Field(min_length=1, max_length=1500)
    mappings: list[Mapping] = Field(default_factory=list, max_length=40)

class Criterion(Strict):
    """A condition on the result. The mechanical kinds are verified by the host; `text` is for the frontier to judge."""
    kind: Literal['file_unchanged', 'symbol_exists', 'symbol_absent', 'no_new_files', 'check_passes', 'text'] = 'text'
    path: str | None = Field(default=None, max_length=300)
    symbol: str | None = Field(default=None, max_length=200)
    name: str | None = Field(default=None, max_length=120)
    text: str | None = Field(default=None, max_length=600)

    @model_validator(mode='after')
    def complete(self):
        need = {'file_unchanged': self.path, 'symbol_exists': self.symbol, 'symbol_absent': self.symbol, 'check_passes': self.name, 'text': self.text}
        if self.kind in need and not need[self.kind]:
            raise ValueError({'file_unchanged': 'path', 'symbol_exists': 'symbol', 'symbol_absent': 'symbol', 'check_passes': 'name', 'text': 'text'}[self.kind] + f' is required for {self.kind}')
        return self

    def label(self):
        return {'file_unchanged': f'{self.path} is unchanged', 'symbol_exists': f'{self.symbol} is defined' + (f' in {self.path}' if self.path else ''),
                'symbol_absent': f'{self.symbol} is not defined' + (f' in {self.path}' if self.path else ''), 'no_new_files': 'no new files are created',
                'check_passes': f'check "{self.name}" passes', 'text': self.text}[self.kind]

class Evidence(Strict):
    job_ids: list[str] = Field(default_factory=list, max_length=4)
    refs: list[str] = Field(default_factory=list, max_length=20)  # path:line the caller already verified

class Scope(Strict):
    read: list[str] = Field(default_factory=list, max_length=30)
    edit: list[str] = Field(default_factory=list, max_length=30)
    delete: list[str] = Field(default_factory=list, max_length=10)

class DelegationSpec(Strict):
    goal: str = Field(min_length=1, max_length=4000)
    kind: Literal['find_code', 'explain', 'config_use', 'compare', 'check_requirement', 'mechanical', 'guard', 'regression_test', 'run_tests', 'fix_test'] | None = None
    targets: list[Target] = Field(default_factory=list, max_length=30)
    changes: list[Change] = Field(default_factory=list, max_length=20)
    invariants: list[Criterion] = Field(default_factory=list, max_length=20)
    acceptance: list[Criterion] = Field(default_factory=list, max_length=20)
    evidence: Evidence = Field(default_factory=Evidence)
    scope: Scope = Field(default_factory=Scope)
    checks: list[Check] = Field(default_factory=list, max_length=10)
    profile_ref: str | None = Field(default=None, pattern=r'^[a-f0-9]{12}$')
    check_groups: list[str] = Field(default_factory=list, max_length=12)
    preset: Literal['small', 'work', 'extended'] = 'work'
    handoff_id: str | None = Field(default=None, max_length=200)
    execution_mode: Literal['model','literal'] = 'model'

    def all_mappings(self):
        return [(m.old, m.new) for change in self.changes for m in change.mappings]

    def criteria(self):
        return [('invariant', c) for c in self.invariants] + [('acceptance', c) for c in self.acceptance]

def role_of(spec):
    if spec.kind:
        return 'investigator' if spec.kind in ('find_code', 'explain', 'config_use', 'compare', 'check_requirement') else 'validator' if spec.kind == 'run_tests' else 'editor'
    return 'editor' if spec.scope.edit else 'investigator'

def task_text(spec):
    """The natural-language task the pipelines read: the same content as the spec, in sections, with mappings as `old -> new` lines."""
    parts = ['Goal: ' + spec.goal]
    if spec.targets:
        parts.append('Targets:\n' + '\n'.join('- ' + t.path + (f' ({t.symbol})' if t.symbol else '') + (f' lines {t.lines[0]}-{t.lines[1]}' if t.lines else '') for t in spec.targets))
    if spec.changes:
        blocks = []
        for i, change in enumerate(spec.changes, 1):
            lines = [f'{i}. {change.description}'] + [f'   {m.old} -> {m.new}' for m in change.mappings]
            blocks.append('\n'.join(lines))
        parts.append('Required changes:\n' + '\n'.join(blocks))
    if spec.invariants:
        parts.append('Must stay true:\n' + '\n'.join('- ' + c.label() for c in spec.invariants))
    if spec.acceptance:
        parts.append('Acceptance:\n' + '\n'.join('- ' + c.label() for c in spec.acceptance))
    if spec.evidence.refs:
        parts.append('Already verified by the caller (re-read before relying on it):\n' + '\n'.join('- ' + r for r in spec.evidence.refs))
    return '\n\n'.join(parts)[:12000]

def compile_spec(spec, repo, caller='mcp', idempotency_key=None):
    """JobRequest for the spec. Kind-specific rules (a fix_test or regression_test needs its check) are enforced by JobRequest itself."""
    role = role_of(spec)
    edit = list(dict.fromkeys(spec.scope.edit))
    read = list(dict.fromkeys(spec.scope.read + [t.path for t in spec.targets])) if role != 'editor' else list(dict.fromkeys(spec.scope.read + edit)) if spec.scope.read else []
    if role == 'editor' and not edit:
        raise ValueError('An editing spec needs scope.edit (the exact files it may change)')
    if role != 'editor' and (edit or spec.scope.delete):
        raise ValueError('Only editing specs may name scope.edit or scope.delete')
    kwargs = dict(role=role, kind=spec.kind, repo=repo, task=task_text(spec), read_paths=read, evidence_job_ids=spec.evidence.job_ids, handoff_id=spec.handoff_id,
                  checks=spec.checks, profile_ref=spec.profile_ref, check_groups=spec.check_groups, execution_preset=spec.preset, caller=caller,
                  timeout=default_timeout(role, spec.preset), idempotency_key=idempotency_key or uuid.uuid4().hex, spec=spec.model_dump(mode='json'),
                  literal_mappings=[m for change in spec.changes for m in change.mappings] if role=='editor' else [],execution_mode=spec.execution_mode)
    if role == 'editor':
        kwargs.update(allowed_paths=edit, delete_paths=spec.scope.delete)
        if spec.checks or spec.profile_ref:
            kwargs.update(workflow='implement', repair_attempts=1)
    return JobRequest(**kwargs)

def verify(spec, originals, after, checks):
    """Decide each mechanical criterion from the files before/after (`originals`: path -> text, '' for a file that did not exist) and the check results.

    Returns {'results': [{where, criterion, status}], 'unmet': n, 'unknown': n}; status is met, unmet, unknown (not enough evidence) or frontier_judgement."""
    results = []
    by_name = {c.get('name'): c for c in checks or []}
    for where, c in spec.criteria():
        status, detail = 'unknown', ''
        if c.kind == 'text':
            status = 'frontier_judgement'
        elif c.kind == 'file_unchanged':
            if c.path in after or c.path in originals:
                status = 'met' if after.get(c.path, '') == originals.get(c.path, after.get(c.path, '')) else 'unmet'
            else:
                status, detail = 'met', 'outside the editable files'
        elif c.kind == 'no_new_files':
            created = sorted(p for p, text in after.items() if originals.get(p, '') == '' and text)
            status, detail = ('unmet', ', '.join(created[:4])) if created else ('met', '')
        elif c.kind in ('symbol_exists', 'symbol_absent'):
            scope = [c.path] if c.path else list(after)
            if c.path and c.path not in after:
                status, detail = 'unknown', 'file is not among the edited files or is missing'
            elif scope:
                found = any(c.symbol == d[0] for p in scope for d in analyze(p, after.get(p, ''))['defs'])
                status = 'met' if found == (c.kind == 'symbol_exists') else 'unmet'
                if not c.path and c.kind == 'symbol_exists' and not found:
                    status, detail = 'unknown', 'only the edited files were searched'
        elif c.kind == 'check_passes':
            run = by_name.get(c.name)
            status = 'unknown' if run is None else 'met' if run.get('status') == 'passed' else 'unmet'
        results.append({'where': where, 'criterion': c.label(), 'status': status, 'detail': detail})
    return {'results': results, 'unmet': sum(r['status'] == 'unmet' for r in results), 'unknown': sum(r['status'] == 'unknown' for r in results),
            'for_frontier': sum(r['status'] == 'frontier_judgement' for r in results)}

def unmet_summary(verification):
    return 'Spec criteria not met: ' + '; '.join(r['criterion'] + (f" ({r['detail']})" if r['detail'] else '') for r in verification['results'] if r['status'] == 'unmet')[:400]

def draft_from_task(task, edit=(), read=(), kind=None, checks=(), index=None):
    """A deterministic draft spec of a natural-language task: the stated `old -> new` mappings, the named files and, when an index is given, the identifiers that
    resolve to definitions. No model is used; the caller (or standalone user) sees what the host understood and can correct it."""
    pairs = [Mapping(old=o, new=n) for o, n in mappings.extract(task) if SINGLE_LINE.match(o) and SINGLE_LINE.match(n)]
    targets = [Target(path=p) for p in dict.fromkeys(list(edit) + list(read))]
    if index is not None:
        from hub.skills.coding.intelligence.localize import identifiers
        for name in identifiers(task):
            for d in index.definitions(name)[:1]:
                if len(targets) < 30:
                    targets.append(Target(path=d['path'], symbol=name, lines=(d['start'], d['end'])))
    return DelegationSpec(goal=task[:4000], kind=kind, targets=targets, changes=[Change(description='Apply the stated replacements', mappings=pairs)] if pairs else [],
                          scope=Scope(read=list(read), edit=list(edit)), checks=[Check.model_validate(c) if not isinstance(c, Check) else c for c in checks])
