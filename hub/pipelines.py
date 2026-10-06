"""Host-driven coding pipelines: investigate (map -> choose ranges -> read -> answer) and edit (show files ->
text edit blocks -> apply through ScopedFiles -> one corrective turn). No tool calling."""
import difflib
import json
import re
import time
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field
from .calls import Caller, load_profiles, write_session
from .skills.coding.intelligence.codeindex import CodeIndex, format_candidates, mentioned_text, select_ranges
from .skills.coding.editing import contextpack, editgate, mappings
from .skills.coding.execution import execctx
from .phases import resolve_phase
from .skills.coding.intelligence.localize import identifiers, read_ranges, repo_map, search_hits
from .models import JobRequest
from .skills.personal.preferences import prompt_lines
from .report import parse_report
from .scoped import ScopedFiles, ScopeError
from .skills.coding.editing import textedit

PROFILES = {'localize': {'thinking': False, 'output': 600, 'temperature': 0.0},
            'investigate-answer': {'thinking': False, 'output': 2048, 'temperature': 0.2}}

class Range(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str = Field(max_length=300)
    start: int
    end: int

class Pick(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ranges: list[Range] = Field(max_length=6)

class Ref(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: str = Field(max_length=300)
    line: int
    note: str = Field(max_length=200)

class RequirementFinding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    task_quote: str = Field(min_length=1,max_length=600)
    status: str = Field(pattern=r'^(met|unmet|unknown)$')
    refs: list[Ref] = Field(default_factory=list,max_length=8)

class Finding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    answer: str = Field(max_length=6000)
    refs: list[Ref] = Field(default_factory=list, max_length=24)
    complete: bool
    more: list[Range] = Field(max_length=4)
    requirements: list[RequirementFinding] = Field(default_factory=list,max_length=20)
    unknowns: list[str] = Field(default_factory=list,max_length=12)

PICK = ('You are investigating a code repository to answer the question. Choose up to 6 file ranges to read (path exactly as listed, '
        'start and end line, at most 120 lines each). Candidate ranges come from the host code index (definitions, uses, imports, best first) '
        'and search hits show exact lines where named identifiers occur; prefer them. The host also reads its best candidates, so pick what else matters.')
ANSWER = ('Answer the question from the file excerpts. Put every supporting location in refs (the file path shown after the [E#] label, never the label itself; one line number shown in the '
          'excerpts; a short note); the host checks each against what was read. When asked to find all code involved, list every relevant location '
          'shown, including configuration, callers and importers. For a requirement or yes/no question start the answer with met, unmet or unknown. '
          'Say plainly what you could not find. Set complete=false when any requirement or part of the question is unknown or unverified, or when other ranges are needed (list them in more); '
          'otherwise complete=true and more empty. For explain, compare and check_requirement tasks, include requirements for every explicit question: '
          'task_quote is copied exactly from the original task, status is met/unmet/unknown, refs locate supporting implementation (not just names or test descriptions). '
          'Return unknowns explicitly. Separate observed definitions, callers and branch conditions from your interpretation. A repository-wide absence or all-callers claim '
          'requires the relevant search scope and callers to be inspected; excerpts alone do not prove absence. Do not infer return types or iteration behavior from names.')
HOST_BUDGET = 26000
EDIT_BASE = 'You are a careful code editor. Make exactly the change the task asks for in the listed files, preserving unrelated code and behaviour. '
EDIT = EDIT_BASE + textedit.format_for()

def audit_writer(directory, phase):
    def audit(kind, data):
        with (directory / 'tools.jsonl').open('a') as stream:
            stream.write(json.dumps({'time': time.time(), 'phase': phase, 'kind': kind, 'data': data}) + '\n')
    return audit

CITATION = re.compile(r'\b(E\d+|[\w./-]+\.\w{1,5}):(\d+)(?:\s*[-–]\s*(\d+))?')

WORDED = re.compile(r'([\w./-]+\.\w{1,5})\b[^.\n]{0,30}?\blines?\s+(\d+)(?:\s*[-–]\s*(\d+))?', re.I)

def prose_refs(text):
    """Citations the model wrote in the answer text (`[E2:19-21]`, `cache.py:19`, `in cache.py at line 19`), for when it leaves refs empty."""
    out = []
    for where, start, end in CITATION.findall(text) + WORDED.findall(text):
        for line in {int(start), int(end or start)}:
            out.append(Ref(path=where[:300], line=line, note='cited in the answer'))
    return out[:24]

def quoted_refs(files, text):
    """Code the answer quotes in backticks that appears verbatim at a line the host read: the host derives the path:line itself."""
    quotes = [q.strip() for q in text.split('`')[1::2] if 8 <= len(q.strip()) <= 120 and '\n' not in q]  # odd segments are inside backticks
    out = []
    for quote in dict.fromkeys(quotes):
        found = []
        for item in files.evidence:
            for row in (item.get('lines') or '').splitlines():
                number, _, content = row.partition(': ')
                if number.isdigit() and quote in content:
                    found.append((item['path'], int(number)))
        for path, line in list(dict.fromkeys(found))[:2]:
            out.append(Ref(path=path, line=line, note='quoted in the answer'))
    return out[:8]

def verified_refs(files, refs, text=''):
    """Split references (the model's own plus those cited in its prose) into lines the host actually returned from a read, and the rest."""
    good, bad, seen = [], [], set()
    by_id = {item['id']: item['path'] for item in files.evidence if item.get('path')}
    refs = list(refs) + prose_refs(text) + quoted_refs(files, text)
    for ref in refs:
        path = by_id.get(ref.path.strip().strip('[]'), str(Path(ref.path.strip().removeprefix('./'))))  # the model sometimes writes the [E3] label as the path
        coverage = files.observed_lines.get(path)
        if (path, ref.line) in seen:
            continue
        seen.add((path, ref.line))
        if coverage and ref.line in coverage['lines']:
            good.append((path, ref.line, ref.note.strip()))
        elif ref.note not in ('cited in the answer', 'quoted in the answer'):  # a prose citation that does not check out is noise, not a claim worth flagging
            bad.append((path, ref.line, ref.note.strip()))
    return good, bad

def envelope(status, findings, files='None', checks='Not run', risks='None'):
    return f'LOCAL_WORKER_REPORT\nStatus: {status}\nFindings:\n{findings}\nFiles:\n{files}\nChecks:\n{checks}\nRisks:\n{risks}\nEND_LOCAL_WORKER_REPORT'

async def run_investigate(directory, label, prompt, phase='work'):
    request = JobRequest.model_validate_json((directory / 'request.json').read_text())
    files = ScopedFiles(request, audit_writer(directory, phase or label))
    candidates, host_ranges, reference_hits = '', [], []
    exhaustive=bool(re.search(r'\b(all|every|exists?|absence|callers?)\b',request.task,re.I))
    try:
        index=CodeIndex.load(files)
        found = index.candidates(request.task, mentioned_text(files, request.task))
        candidates, host_ranges = format_candidates(found), select_ranges(found)
        if exhaustive:
            named=list(dict.fromkeys(seed[2] for seed in index.seeds(request.task)
                                    if re.search(r'\b'+re.escape(seed[2])+r'\b',request.task)))[:4]
            reference_hits=[{'name':name,**hit} for name in named for hit in index.references(name,41)]
            priority=[{'path':h['path'],'start':max(1,h['line']-3),'end':h['line']+8,'why':'reference coverage: '+h['name']} for h in reference_hits[:24]]
            host_ranges=priority+host_ranges
    except Exception as error:  # the index is an accelerator; the older map and search still work without it
        files.audit('index_error', {'error': str(error)[:250]})
    overview = (('Candidate ranges from the host code index (path:start-end  why), best first:\n' + candidates + '\n\n') if candidates else '') + \
        'Repository map (path: symbol@line):\n' + repo_map(files, request.task, budget=3500 if candidates else 7000) + '\n\nSearch hits:\n' + (search_hits(files, request.task) or '(none)')
    async with Caller(directory, label, request, load_profiles(directory, PROFILES), 'investigate') as caller:
        pick = await caller.json('localize', 0, PICK, prompt + '\n\n' + overview, Pick)
        ranges = [r.model_dump() for r in pick.ranges] if pick else []
        files.audit('host_ranges', {'ranges': [{k: r[k] for k in ('path', 'start', 'end', 'why')} for r in host_ranges]})
        excerpts, errors = read_ranges(files, ranges + host_ranges, limit=6 + len(host_ranges), budget=HOST_BUDGET)
        finding = await caller.json('investigate-answer', 1, ANSWER + '\n' + prompt_lines(), prompt + '\n\nFile excerpts:\n' + (excerpts or '(nothing could be read)') +
                                    ('\n\nRead failures:\n' + '\n'.join(errors) if errors else ''), Finding)
        if finding and not finding.complete and finding.more:
            more, more_errors = read_ranges(files, [r.model_dump() for r in finding.more], limit=4)
            errors += more_errors
            if more:
                excerpts += '\n\n' + more
                finding = await caller.json('investigate-answer', 2, ANSWER + '\n' + prompt_lines(), prompt + '\n\nFile excerpts:\n' + excerpts, Finding) or finding
        if not finding or not finding.answer.strip():
            cut = '; '.join(sorted({g.cut_off_message for g in caller.truncations}))
            content = envelope('PARTIAL', 'No usable answer was produced.' + (' The model output was cut off.' if cut else ''), risks=cut or 'The local model returned no answer.')
        else:
            refs=finding.refs+[ref for req in finding.requirements for ref in req.refs]
            good, bad = verified_refs(files, refs, finding.answer)
            # The host owns the quoted source; model notes are interpretations, not verified evidence.
            def observed(path,line):
                for item in files.evidence:
                    if item.get('path')==path:
                        for row in (item.get('lines') or '').splitlines():
                            number,_,code=row.partition(': ')
                            if number==str(line):return code[:240]
                return ''
            evidence = ('\n\nEvidence (locations host-verified against lines read; conclusions require frontier review):\n' +
                        '\n'.join(f'- {p}:{n} — {note}\n  Observed: {observed(p,n)}' for p,n,note in good[:16])) if good else ''
            risks = []
            strict=request.kind in ('explain','compare','check_requirement')
            unresolved=list(finding.unknowns)
            if strict and not finding.requirements:unresolved.append('No requirement evidence checklist was returned')
            for req in finding.requirements:
                locations,_=verified_refs(files,req.refs)
                if req.task_quote not in request.task or req.status=='unknown' or not locations:
                    unresolved.append(req.task_quote)
            if finding.more:unresolved.append('Additional requested ranges remain unresolved')
            if strict and (errors or bad):unresolved.append('Required reads or references remain unverified')
            missing_hits=[h for h in reference_hits if h['line'] not in files.observed_lines.get(h['path'],{}).get('lines',{})]
            if missing_hits:unresolved.append('Known reference sites unread: '+', '.join(f"{h['path']}:{h['line']}" for h in missing_hits[:6]))
            if any(sum(h['name']==name for h in reference_hits)>=41 for name in {h['name'] for h in reference_hits}):
                unresolved.append('Reference enumeration reached its bound; exhaustive coverage is unknown')
            if unresolved:risks.append('Unresolved: '+'; '.join(unresolved)[:1200])
            if errors:
                risks.append('Unread: ' + '; '.join(errors))
            if bad:
                risks.append('Unverified references dropped (line not read): ' + ', '.join(f'{p}:{n}' for p, n, _ in bad[:6]))
            if not good:
                risks.append('No host-verified path:line reference supports this answer')
            scope=', '.join(request.read_paths) if request.read_paths else 'repository (scoped guards apply)'
            packet={'search_scope':scope,'reference_precision':'name_based','reference_hits':reference_hits,'unread_reference_hits':missing_hits,
                    'read_ranges':[{'path':e['path'],'start':e.get('start'),'lines':e.get('lines','').count('\n')+1} for e in files.evidence if e.get('path') and e.get('lines')],
                    'requirements':[req.model_dump() for req in finding.requirements],'unknowns':unresolved,'semantic_verification':'frontier_required'}
            artifact=directory/(label+'.investigation.json');artifact.write_text(json.dumps(packet));artifact.chmod(0o600)
            content = envelope('COMPLETE' if finding.complete and good and not unresolved else 'PARTIAL', finding.answer.strip() + evidence + '\n\nSearch scope: '+scope,
                               risks='; '.join(risks) if risks else 'None identified.')
        write_session(directory, label, caller, prompt, content)

def read_references(files, request):
    """Read-only context the frontier named in read_paths that the job may not edit; unreadable or oversized ones are skipped."""
    allowed, out = set(request.allowed_paths), []
    for path in request.read_paths:
        if path in allowed:
            continue
        try:
            relative, content, digest = textedit.snapshot(files, path)
        except (ScopeError, OSError, UnicodeError, ValueError):
            continue
        if content is not None and len(content) <= 100_000:
            out.append((relative, content, digest))
    return out

def execution_focus(directory, files):
    """Failure evidence and focus ranges from the baseline test run the runner saved for a fix_test job, or ('', {}) when there is none."""
    path = directory / 'failure-evidence.json'
    if not path.is_file():
        return '', {}
    try:
        failures = json.loads(path.read_text()).get('failures', [])
        return execctx.build(failures, CodeIndex.load(files))
    except (OSError, ValueError, KeyError):
        return '', {}

def staged_diff(snapshots, contents):
    pieces = []
    for path, new in contents.items():
        before = snapshots[path][1] or ''
        text = '' if new is textedit.DELETED else new
        pieces.extend(difflib.unified_diff(before.splitlines(keepends=True), text.splitlines(keepends=True), fromfile='before/' + path, tofile='after/' + path))
    return ''.join(pieces)

MAX_CONTINUATIONS = 5
CONTINUE = ('\n\nYour previous reply was cut off by the output limit after {blocks} complete edit block(s). Those edits are already applied in the content below. '
            'Continue with only the changes that are still missing; do not repeat, undo or re-emit edits that are already there. '
            'If every requested change is already in the content, reply with END OF EDITS only.\n\nCurrent content:\n')

def continue_text(blocks):
    return CONTINUE.format(blocks=blocks)

def parse_reply(gen):
    return textedit.parse(gen.text, require_sentinel=gen.done_reason is None)

def evaluate(gen, view, allow_shrink=False, deletable=(), allow_empty=False, mode='substring'):
    """One complete reply planned in memory against `view`: (Plan, model summary). A cut-off or malformed reply plans nothing."""
    if gen.truncated:
        return textedit.Plan({}, [gen.cut_off_message], {}), ''
    parsed = parse_reply(gen)
    if parsed.problems:
        return textedit.Plan({}, parsed.problems, {}), parsed.summary
    if not parsed.edits:
        return (textedit.Plan({}, [], {}) if allow_empty else textedit.Plan({}, ['No edit blocks were found in the reply'], {})), parsed.summary
    return textedit.plan(parsed.edits, view, allow_shrink, deletable, mode), parsed.summary

def salvage(gen, view, deletable=(), mode='substring'):
    """The complete blocks of a reply that was cut off, planned in memory: (Plan, number of complete blocks). The cut-off block is dropped."""
    edits = textedit.parse(gen.text).edits  # parsing stops at the first unterminated block
    if not edits:
        return textedit.Plan({}, [], {}), 0
    return textedit.plan(edits, view, False, deletable, mode), len(edits)

def run_literal(directory, request):
    """Exact authorized substitutions with the same snapshot/commit guards as model edits."""
    files=ScopedFiles(request,audit_writer(directory,'literal'))
    try:
        snapshots={r[0]:r for r in (textedit.snapshot(files,p) for p in request.allowed_paths)}
        before={p:s[1] or '' for p,s in snapshots.items()}
        contents=mappings.literal_contents(request.literal_mappings,before)
        for p,new in contents.items():
            reasons=textedit.suspicious(p,before[p],new)
            if reasons:raise ValueError('; '.join(reasons))
        changed,errors=textedit.commit(files,contents,snapshots)
        if errors:raise ValueError('; '.join(errors))
        return parse_report(envelope('COMPLETE' if changed else 'PARTIAL',
                            'Applied exact literal mappings; no model inference.',files='\n'.join(changed) or 'None',risks='Frontier patch review required.'))
    except (ScopeError,ValueError,OSError) as error:
        return parse_report(envelope('BLOCKED','No literal edits applied.',risks=str(error)))

async def run_edit(directory, label, prompt, phase='work'):
    request = JobRequest.model_validate_json((directory / 'request.json').read_text())
    if request.literal_mappings:
        explicit='\nExplicit literal mappings (all are required):\n'+json.dumps([m.model_dump() for m in request.literal_mappings])
        request=request.model_copy(update={'task':request.task+explicit})
        prompt+=explicit
    files = ScopedFiles(request, audit_writer(directory, phase or label))
    async with Caller(directory, label, request, load_profiles(directory, {}), 'edit') as caller:
        try:
            snapshots = {r[0]: r for r in (textedit.snapshot(files, p) for p in request.allowed_paths)}
        except ScopeError as error:
            write_session(directory, label, caller, prompt, envelope('BLOCKED', f'Authorized files could not be read: {error}', risks=str(error)))
            return
        # Thinking only on explicit request: with thinking the model spent the whole output budget reasoning and
        # wrote no edit blocks (role profiles default editor thinking to on for the older tool loop).
        failure_text, focus = execution_focus(directory, files)
        if failure_text:
            prompt = prompt + '\n\n' + failure_text
        thinking = bool(request.model_thinking)
        name = phase if phase in ('edit',) else 'work'
        system = EDIT_BASE + textedit.format_for(request.delete_paths)
        deletable = frozenset(request.delete_paths)
        decision = editgate.decide(request.task, snapshots)
        if decision['tripped'] and not request.skip_gate:
            if request.refuse_oversized:
                decision['mode'] = 'refused'
            else:
                decision['mode'] = 'advised'  # a cut-off reply is continued, so the job runs; the decision is recorded for calibration
        (directory / (label + '.gate.json')).write_text(json.dumps(decision))
        (directory / (label + '.gate.json')).chmod(0o600)
        if decision.get('mode') == 'refused':
            write_session(directory, label, caller, prompt, envelope('BLOCKED', editgate.message(decision), risks='Too large for one generation; resubmit the proposed units as separate jobs'))
            return
        spec, _ = resolve_phase(request, caller.profiles, name)
        references = read_references(files, request)
        extra_targets=[('mapping',m.old) for m in request.literal_mappings]
        packed = contextpack.pack(snapshots, request.task, contextpack.budget_chars(spec.context, spec.output_limit, len(system) + len(prompt) + 400), references, focus,extra_targets)
        (directory / (label + '.context.json')).write_text(json.dumps(packed.report))
        (directory / (label + '.context.json')).chmod(0o600)
        pairs = mappings.extract(request.task)
        if len(pairs) >= 2 and len(packed.report['coverage']['missing_mappings']) == len(pairs):
            reason = f"None of the {len(pairs)} `old -> new` texts in the task exist in the authorized files ({', '.join(sorted(snapshots))}); the authorized paths are probably wrong"
            write_session(directory, label, caller, prompt, envelope('BLOCKED', reason + '. No model call was made.', risks=reason))
            return

        work, staged, turns, state = dict(snapshots), {}, [], {'step': 0, 'hint': '', 'continuations': 0, 'unit_log': []}
        def stage(contents):
            for path, new in contents.items():
                staged[path] = new
                work[path] = (snapshots[path][0], '' if new is textedit.DELETED else new, snapshots[path][2])
        def flush(errors, changed=()):
            (directory / (label + '.turns.json')).write_text(json.dumps({'turns': turns, 'changed': list(changed), 'errors': errors[:8], 'staged_not_applied': sorted(staged) if errors else [],
                                                                         'continuations': state['continuations'], 'units': state['unit_log']}))
            (directory / (label + '.turns.json')).chmod(0o600)
            if staged and errors:
                (directory / 'staged.diff').write_text(staged_diff(snapshots, staged))
                (directory / 'staged.diff').chmod(0o600)
        def log(gen, planned, unit, continuation=False, blocks=None):
            turns.append({'step': state['step'], 'unit': unit, 'done_reason': gen.done_reason, 'output_tokens': gen.output_tokens, 'limit': gen.limit, 'truncated': gen.truncated,
                          'continuation': continuation, 'blocks': blocks, 'errors': planned.errors[:8], 'planned_files': sorted(planned.contents), 'match': planned.stats or {}})
        async def generate(text):
            gen = await caller.generate(name, state['step'], system, text, thinking)
            state['step'] += 1
            return gen
        def pack_for(view, unit_task, header):
            budget = contextpack.budget_chars(spec.context, spec.output_limit, len(system) + len(header) + 400)
            return contextpack.pack(view, unit_task, budget, references, focus,extra_targets)

        async def run_unit(unit_task, scope, unit_prompt, index):
            """One bounded task over `scope`: generate, continue after a cut-off reply, one corrective turn. Clean results are staged; returns errors."""
            view = {p: work[p] for p in scope}
            cover = pack_for(view, unit_task, unit_prompt)
            missing = cover.report['coverage']['missing_mappings']
            notes = ('\n\nNote: the host could not find these texts in the authorized files: ' + '; '.join(f"'{t}'" for t in missing[:6]) +
                     '. Do not invent SEARCH text for them; they may belong to other files.') if missing else ''
            gen = await generate(unit_prompt + '\n\nAuthorized files:\n' + cover.text + notes)
            corrected, continuations = False, 0
            while True:
                view = {p: work[p] for p in scope}
                if gen.truncated:
                    if not request.continuation:
                        planned = textedit.Plan({}, [gen.cut_off_message], {})
                        log(gen, planned, index)
                        return planned.errors
                    planned, blocks = salvage(gen, view, deletable, request.match_mode)
                    log(gen, planned, index, continuation=continuations > 0, blocks=blocks)
                    if planned.errors:
                        stage(planned.contents)
                        return planned.errors
                    if not planned.contents:
                        return [gen.cut_off_message + '; no complete edit block came before the cut, so there is nothing to continue from']
                    stage(planned.contents)
                    continuations += 1
                    state['continuations'] += 1
                    flush([])
                    if continuations > MAX_CONTINUATIONS:
                        return [f'The model was still cut off after {MAX_CONTINUATIONS} continuation turns; split the task']
                    redo = pack_for({p: work[p] for p in scope}, unit_task, unit_prompt)
                    gen = await generate(unit_prompt + continue_text(blocks) + redo.text)
                    continue
                planned, hint = evaluate(gen, view, deletable=deletable, allow_empty=continuations > 0, mode=request.match_mode)
                log(gen, planned, index, continuation=continuations > 0, blocks=len(planned.contents))
                state['hint'] = hint or state['hint']
                stage(planned.contents)
                if not planned.errors or corrected:
                    return planned.errors
                corrected = True
                failing = {e.split(':', 1)[0] for e in planned.errors} & set(scope)
                fresh = {p: work[p] for p in (failing or scope)}
                correction = unit_prompt + '\n\nYour previous edit blocks could not be applied (nothing was written):\n- ' + '\n- '.join(planned.errors) + '\n\nReply with corrected blocks for these files only. Current content:\n'
                gen = await generate(correction + pack_for(fresh, unit_task, correction).text)

        errors = await run_unit(request.task, list(snapshots), prompt, 0)
        state['unit_log'].append({'index': 0, 'files': list(snapshots), 'ok': not errors})
        changed = []
        if staged and not errors and request.literal_mappings:
            verification=mappings.verify_literals(request.literal_mappings,{p:s[1] or '' for p,s in snapshots.items()},
                                                  {p:staged.get(p,s[1] or '') for p,s in snapshots.items()})
            errors += [f"{m.get('path') or 'mapping'}: {m['reason']}" for m in verification['unmet']]
        if staged and not errors:
            changed, errors = textedit.commit(files, staged, snapshots)
        flush(errors, changed)
        extra = (f' after {state["continuations"]} continuation turn(s)' if state['continuations'] else '')
        if changed:
            note = f'Applied {len(changed)} file(s){extra}: ' + ', '.join(changed) + '.' + (f' Model summary: {state["hint"]}' if state['hint'] else '')
        else:
            note = 'No edits were applied' + (f' ({errors[0]})' if errors else '') + '.' + (' Edits that planned cleanly for ' + ', '.join(sorted(staged)) + ' were not written; see staged.diff.' if staged and errors else '')
        status = 'COMPLETE' if changed and not errors else 'PARTIAL'
        content = envelope(status, note, files='\n'.join(changed) or 'None', risks='; '.join(errors) or 'None identified.')
        if not parse_report(content):
            content = envelope(status, 'Edit summary unavailable.', files='\n'.join(changed) or 'None', risks='; '.join(errors) or 'None identified.')
        write_session(directory, label, caller, prompt, content)
