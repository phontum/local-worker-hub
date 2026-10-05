"""Metadata-only export of a finished job, so a real failure can become a regression case without copying private source.

Nothing from the task text, file contents, paths, test names or the model's output is included: files are aliased (file1, file2, ...),
the task is reduced to counts, and errors keep only their category. Review notes are left out unless asked for.
"""
import json
import re
from pathlib import Path

CATEGORIES = (('cut off', 'truncated'), ('not terminated', 'unterminated_block'), ('removes existing code', 'destructive_block'), ('would shrink', 'destructive_file'),
              ('definitions would be removed', 'destructive_definitions'), ('does not match', 'search_mismatch'), ('places; include more', 'search_ambiguous'),
              ('not one of the authorized', 'unauthorized_path'), ('changed since', 'stale_file'), ('did not change anything', 'no_change'),
              ('No edit blocks', 'no_blocks'), ('no FILE line', 'no_file_line'), ('WHOLE replacement', 'whole_limit'))

def category(error):
    return next((name for key, name in CATEGORIES if key in error), 'other')

def task_shape(task):
    """Counts that a complexity gate can be calibrated on, without the text itself."""
    return {'chars': len(task), 'words': len(task.split()), 'sentences': len(re.findall(r'[.!?;](?:\s|$)', task)), 'quoted_strings': len(re.findall(r'`[^`\n]+`|"[^"\n]+"|\'[^\'\n]+\'', task)),
            'mappings': len(re.findall(r'->|→|=>', task)), 'numbered_items': len(re.findall(r'(?m)^\s*\d+[.)]', task)), 'protocol_words': len(re.findall(r'SEARCH|REPLACE', task))}

def build(job, directory, include_notes=False):
    request, result, review = job['request'], job.get('result') or {}, job.get('review')
    directory = Path(directory)
    aliases = {path: f'file{i}' for i, path in enumerate(request.get('allowed_paths', []), 1)}
    def alias(text):
        for path, name in aliases.items():
            text = text.replace(path, name)
        return text
    turn_records = [json.loads(p.read_text()) for p in sorted(directory.glob('*.turns.json'))]
    turns = [{'step': t.get('step'), 'done_reason': t.get('done_reason'), 'output_tokens': t.get('output_tokens'), 'limit': t.get('limit'), 'truncated': t.get('truncated'),
              'error_categories': sorted({category(e) for e in t.get('errors', [])}), 'planned_files': len(t.get('planned_files', []))} for r in turn_records for t in r.get('turns', [])]
    packet = result.get('acceptance') or {}
    out = {
        'job_state': job['state'], 'worker_status': result.get('worker_status'),
        'request': {k: request.get(k) for k in ('role', 'kind', 'workflow', 'repair_attempts', 'in_place', 'execution_preset', 'timeout', 'model', 'investigate_first')} |
                   {'allowed_files': len(aliases), 'read_files': len(request.get('read_paths', [])), 'checks': len(request.get('checks', [])), 'task': task_shape(request.get('task', ''))},
        'files': {name: {'lines_at_export': sum(1 for _ in (Path(request['repo']) / path).open(errors='replace')) if request.get('repo') and (Path(request['repo']) / path).is_file() else None}
                  for path, name in aliases.items()},
        'turns': turns,
        'diff': packet.get('diff'), 'scope_ok': packet.get('scope_ok'), 'edits': packet.get('edits'), 'next_action': (result.get('completion') or {}).get('next_action'),
        'checks': [{'status': c.get('status'), 'exit_code': c.get('exit_code'), 'counts': c.get('counts'), 'failures': len(c.get('failures') or [])} for c in result.get('checks', [])],
        'metrics': {k: (result.get('metrics') or {}).get(k) for k in ('queue_seconds', 'analysis_seconds', 'check_seconds', 'execution_seconds')},
        'usage': result.get('usage'),
        'review': ({'decision': review['decision'], 'reason': review.get('reason'), 'notes_chars': len(review.get('notes', '')), 'review_effort_seconds': review.get('review_effort_seconds')} if review else None),
        'privacy': 'No task text, file names, file contents, test names or model output are included.',
    }
    if include_notes and review:
        out['review']['notes'] = alias(review.get('notes', ''))
    return out

def replies(directory):
    """The model's raw edit replies of a job, in order, rebuilt from its private trace plus the per-turn records (finish reason, output tokens)."""
    directory = Path(directory)
    text, order = {}, []
    trace = directory / 'trace.jsonl'
    for line in trace.read_text().splitlines() if trace.is_file() else []:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get('kind') == 'content' and str(row.get('phase', '')).split('-')[0] in ('edit', 'work', 'repair'):
            key = (row['phase'], row['step'])
            if key not in text:
                order.append(key)
            text[key] = text.get(key, '') + row.get('text', '')
    turns = [t for p in sorted(directory.glob('*.turns.json')) for t in json.loads(p.read_text()).get('turns', [])]
    by_step = {t.get('step'): t for t in turns}
    return [{'text': text[key], 'done_reason': (by_step.get(key[1]) or {}).get('done_reason') or 'stop',
             'output_tokens': (by_step.get(key[1]) or {}).get('output_tokens') or 0} for key in order]

def export_fixture(job, directory, base_dir, out):
    """Write a replayable regression fixture: the task, the authorized files as they were before the job, the model's raw replies and the
    metadata. This copies PRIVATE SOURCE and model output; unlike `build` it is for the user to review and trim before it is committed anywhere."""
    request, out, base_dir = job['request'], Path(out), Path(base_dir)
    if request.get('role') != 'editor':
        raise ValueError('Only Editor jobs can become replay fixtures')
    (out / 'files').mkdir(parents=True, exist_ok=True)
    files = []
    for path in request.get('allowed_paths', []):
        if '..' in Path(path).parts or Path(path).is_absolute():
            continue
        source = base_dir / path
        if source.is_file():
            (out / 'files' / path).parent.mkdir(parents=True, exist_ok=True)
            (out / 'files' / path).write_bytes(source.read_bytes())
            files.append(path)
    fixture = {'task': request.get('task', ''), 'allowed_paths': files, 'delete_paths': request.get('delete_paths', []),
               'replies': replies(directory), 'recorded': (lambda r: r and {'decision': r['decision'], 'reason': r.get('reason')})(job.get('review')),
               'recorded_status': (job.get('result') or {}).get('worker_status'),
               'expected': None,  # set to {"status": "COMPLETE"|"PARTIAL", "changed": [paths]} once the incident is fixed; None checks only the safety invariants
               'privacy': 'Contains private source and model output. Review before committing.'}
    (out / 'fixture.json').write_text(json.dumps(fixture, indent=2) + '\n')
    return fixture
