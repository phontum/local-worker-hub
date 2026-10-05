"""Host-driven coding pipelines: investigate (map -> choose ranges -> read -> answer) and edit (show files ->
text edit blocks -> apply through ScopedFiles -> one corrective turn). No tool calling."""
import json
import re
import time
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field
from .calls import Caller, load_profiles, write_session
from .codeindex import CodeIndex, format_candidates, mentioned_text, select_ranges
from .localize import identifiers, read_ranges, repo_map, search_hits
from .models import JobRequest
from .preferences import prompt_lines
from .report import parse_report
from .scoped import ScopedFiles, ScopeError
from . import textedit

PROFILES = {'localize': {'thinking': False, 'output': 600, 'temperature': 0.0},
            'investigate-answer': {'thinking': False, 'output': 2048, 'temperature': 0.2}}
SHOW_WHOLE = 400

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

class Finding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    answer: str = Field(max_length=6000)
    refs: list[Ref] = Field(default_factory=list, max_length=24)
    complete: bool
    more: list[Range] = Field(max_length=4)

PICK = ('You are investigating a code repository to answer the question. Choose up to 6 file ranges to read (path exactly as listed, '
        'start and end line, at most 120 lines each). Candidate ranges come from the host code index (definitions, uses, imports, best first) '
        'and search hits show exact lines where named identifiers occur; prefer them. The host also reads its best candidates, so pick what else matters.')
ANSWER = ('Answer the question from the file excerpts. Put every supporting location in refs (the file path shown after the [E#] label, never the label itself; one line number shown in the '
          'excerpts; a short note); the host checks each against what was read. When asked to find all code involved, list every relevant location '
          'shown, including configuration, callers and importers. For a requirement or yes/no question start the answer with met, unmet or unknown. '
          'Say plainly what you could not find. Set complete=false when any requirement or part of the question is unknown or unverified, or when other ranges are needed (list them in more); '
          'otherwise complete=true and more empty.')
HOST_BUDGET = 26000
EDIT = ('You are a careful code editor. Make exactly the change the task asks for in the listed files, preserving unrelated code and '
        'behaviour. ' + textedit.FORMAT.format(limit=textedit.WHOLE_LIMIT))

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
    candidates, host_ranges = '', []
    try:
        found = CodeIndex.load(files).candidates(request.task, mentioned_text(files, request.task))
        candidates, host_ranges = format_candidates(found), select_ranges(found)
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
            content = envelope('PARTIAL', 'No usable answer was produced.', risks='The local model returned no answer.')
        else:
            good, bad = verified_refs(files, finding.refs, finding.answer)
            evidence = ('\n\nEvidence (host-verified against lines read):\n' + '\n'.join(f'- {p}:{n} — {note}' for p, n, note in good)) if good else ''
            risks = []
            if errors:
                risks.append('Unread: ' + '; '.join(errors))
            if bad:
                risks.append('Unverified references dropped (line not read): ' + ', '.join(f'{p}:{n}' for p, n, _ in bad[:6]))
            if not good:
                risks.append('No host-verified path:line reference supports this answer')
            content = envelope('COMPLETE' if finding.complete and good else 'PARTIAL', finding.answer.strip() + evidence,
                               risks='; '.join(risks) if risks else 'None identified.')
        write_session(directory, label, caller, prompt, content)

def file_blocks(snapshots, task):
    out = []
    for path, (relative, content, digest) in snapshots.items():
        if content is None:
            out.append(f'===== {path} (new file, does not exist yet) =====')
            continue
        rows = content.split('\n')
        if len(rows) <= SHOW_WHOLE:
            out.append(f'===== {path} ({len(rows)} lines) =====\n{content}\n===== end of {path} =====')
            continue
        # Large file: show the regions around identifiers named in the task, plus the top of the file.
        keep = set(range(0, 30))
        for name in identifiers(task):
            for i, row in enumerate(rows):
                if name in row:
                    keep.update(range(max(0, i - 40), min(len(rows), i + 41)))
        parts, previous = [], -2
        for i in sorted(keep):
            if i != previous + 1:
                parts.append(f'... (lines {i + 1}+)')
            parts.append(rows[i])
            previous = i
        out.append(f'===== {path} ({len(rows)} lines; only parts shown, SEARCH must use shown lines) =====\n' + '\n'.join(parts) + f'\n===== end of {path} =====')
    return '\n\n'.join(out)

async def run_edit(directory, label, prompt, phase='work'):
    request = JobRequest.model_validate_json((directory / 'request.json').read_text())
    files = ScopedFiles(request, audit_writer(directory, phase or label))
    async with Caller(directory, label, request, load_profiles(directory, {}), 'edit') as caller:
        try:
            snapshots = {r[0]: r for r in (textedit.snapshot(files, p) for p in request.allowed_paths)}
        except ScopeError as error:
            write_session(directory, label, caller, prompt, envelope('BLOCKED', f'Authorized files could not be read: {error}', risks=str(error)))
            return
        # Thinking only on explicit request: with thinking the model spent the whole output budget reasoning and
        # wrote no edit blocks (role profiles default editor thinking to on for the older tool loop).
        thinking = bool(request.model_thinking)
        reply = await caller.text(phase if phase in ('edit',) else 'work', 0, EDIT, prompt + '\n\nAuthorized files:\n' + file_blocks(snapshots, request.task), thinking)
        edits = textedit.parse(reply)
        changed, errors = textedit.apply_all(files, edits, snapshots) if edits else ([], ['No edit blocks were found in the reply'])
        if errors:
            # One corrective turn with the exact failures and the files as they are now.
            failed = {e.split(':', 1)[0] for e in errors}
            fresh = {r[0]: r for r in (textedit.snapshot(files, p) for p in request.allowed_paths) if r[0] in failed or not edits}
            reply2 = await caller.text(phase if phase in ('edit',) else 'work', 1, EDIT, prompt + '\n\nYour previous edit blocks could not be applied:\n- ' +
                                       '\n- '.join(errors) + '\n\nReply with corrected blocks for these files only. Current content:\n' + file_blocks(fresh or snapshots, request.task), thinking)
            edits2 = textedit.parse(reply2)
            more, errors = textedit.apply_all(files, edits2, fresh or snapshots) if edits2 else ([], ['No edit blocks were found in the corrected reply'])
            changed += [p for p in more if p not in changed]
            reply = reply2 if edits2 else reply
        note = textedit.summary(reply) or ('Edited ' + ', '.join(changed) if changed else 'No change was applied.')
        status = 'COMPLETE' if changed and not errors else 'PARTIAL'
        content = envelope(status, note, files='\n'.join(changed) or 'None', risks='; '.join(errors) or 'None identified.')
        if not parse_report(content):
            content = envelope(status, 'Edit summary unavailable.', files='\n'.join(changed) or 'None', risks='; '.join(errors) or 'None identified.')
        write_session(directory, label, caller, prompt, content)
