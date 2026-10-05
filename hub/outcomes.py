"""Delegation outcomes derived from what the hub already records: one feature row per job, the diff the frontier finally shipped, and acceptance statistics.

Nothing here is a second copy of state. A row is computed from the job record (request, result, review) and its private job directory, so it can be
rebuilt at any time, and `final_diff` is the only new artifact: it compares the authorized files as the worker's snapshot saw them with the user's tree
now, which is what the frontier actually left behind after accepting, rejecting or taking over."""
import difflib
import json
from collections import defaultdict
from pathlib import Path
from . import workspace
from .settings import STATE

NOT_REAL = ('eval', 'benchmark', 'legacy-import')
LANGUAGES = {'.py': 'python', '.js': 'javascript', '.jsx': 'javascript', '.mjs': 'javascript', '.ts': 'typescript', '.tsx': 'typescript', '.go': 'go', '.rs': 'rust',
             '.java': 'java', '.rb': 'ruby', '.css': 'css', '.html': 'html', '.md': 'markdown', '.json': 'json', '.toml': 'toml', '.yml': 'yaml', '.yaml': 'yaml'}

def is_real(job):
    """A job a frontier agent or a person delegated, as opposed to a benchmark or imported history."""
    return (job['request'].get('caller') or 'cli') not in NOT_REAL

def languages(paths):
    return sorted({LANGUAGES.get(Path(p).suffix.lower(), 'other') for p in paths})

def final_diff(job, jobs_dir=None):
    """Unified diff from the snapshot of the authorized files to the user's tree now; written to final-frontier.diff in the job directory.

    Only Editor jobs with a private workspace have a snapshot. Returns {'available': False, 'reason': ...} otherwise."""
    ident = job['id']
    try:
        record = workspace.read_record(ident)
    except workspace.WorkspaceError:
        return {'available': False, 'reason': 'no private workspace (in-place or non-editor job)'}
    origin = Path(record['origin'])
    bases = [workspace.ROOT / member / 'base' for member in record.get('chain', [ident])]
    chunks, files = [], []
    for name in record['allowed']:
        saved = next((b / name for b in bases if (b / name).is_file()), None)
        before = saved.read_text(errors='replace').splitlines(keepends=True) if saved else []
        target = origin / name
        after = target.read_text(errors='replace').splitlines(keepends=True) if target.is_file() and not target.is_symlink() else []
        delta = list(difflib.unified_diff(before, after, 'a/' + name, 'b/' + name))
        if delta:
            chunks.append(''.join(delta))
            files.append({'path': name, 'added': sum(1 for l in delta if l.startswith('+') and not l.startswith('+++')),
                          'removed': sum(1 for l in delta if l.startswith('-') and not l.startswith('---'))})
    directory = Path(jobs_dir or STATE / 'jobs') / ident
    if directory.is_dir():
        out = directory / 'final-frontier.diff'
        out.write_text(''.join(chunks))
        out.chmod(0o600)
    return {'available': True, 'files': files, 'changed_files': len(files), 'bytes': sum(len(c) for c in chunks)}

def row(job, directory=None):
    """The delegation feature row: task shape, scope, context actually shown, cost and outcome. Task text and source are not copied."""
    request, result, review = job['request'], job.get('result') or {}, job.get('review') or {}
    directory = Path(directory or STATE / 'jobs' / job['id'])
    context = {}
    try:
        context = json.loads((directory / 'work.context.json').read_text())
    except (OSError, ValueError):
        pass
    packet = result.get('acceptance') or {}
    paths = request.get('allowed_paths', []) + request.get('read_paths', [])
    metrics, usage = result.get('metrics') or {}, result.get('usage') or {}
    return {'job': job['id'], 'caller': request.get('caller'), 'real': is_real(job), 'role': request.get('role'), 'kind': request.get('kind'),
            'workflow': request.get('workflow'), 'languages': languages(paths), 'allowed_files': len(request.get('allowed_paths', [])),
            'read_files': len(request.get('read_paths', [])), 'checks': len(request.get('checks', [])), 'task_chars': len(request.get('task', '')),
            'model': request.get('model') or 'default', 'context_files': len(context.get('files', [])) if isinstance(context, dict) else None,
            'worker_status': result.get('worker_status'), 'diff': packet.get('diff'), 'scope_ok': packet.get('scope_ok'),
            'execution_seconds': metrics.get('execution_seconds'), 'local_tokens': usage.get('total_tokens') if isinstance(usage, dict) else None,
            'decision': review.get('decision'), 'reason': review.get('reason'), 'review_effort_seconds': review.get('review_effort_seconds')}

def stats(jobs, include_eval=False):
    """Acceptance by (role, kind) over reviewed jobs: how often the frontier accepted, rejected or took over."""
    table = defaultdict(lambda: {'reviewed': 0, 'accepted': 0, 'rejected': 0, 'takeover': 0, 'reasons': defaultdict(int)})
    for job in jobs:
        review = job.get('review')
        if not review or (not include_eval and not is_real(job)):
            continue
        entry = table[(job['request'].get('role'), job['request'].get('kind') or '-')]
        entry['reviewed'] += 1
        entry[review['decision']] += 1
        if review.get('reason'):
            entry['reasons'][review['reason']] += 1
    return [{'role': role, 'kind': kind, **{k: (dict(v) if k == 'reasons' else v) for k, v in entry.items()},
             'accept_rate': round(entry['accepted'] / entry['reviewed'], 3)} for (role, kind), entry in sorted(table.items(), key=lambda kv: (kv[0][0] or '', kv[0][1]))]
