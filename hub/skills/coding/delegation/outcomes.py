"""Delegation outcomes derived from what the hub already records: one feature row per job, the diff the frontier finally shipped, and acceptance statistics.

Nothing here is a second copy of state. A row is computed from the job record (request, result, review) and its private job directory, so it can be
rebuilt at any time, and `final_diff` is the only new artifact: it compares the authorized files as the worker's snapshot saw them with the user's tree
now, which is what the frontier actually left behind after accepting, rejecting or taking over."""
import difflib
import hashlib
import json
import time
from collections import defaultdict
from pathlib import Path
from hub.skills.coding.editing import workspace
from hub.settings import CONFIG, STATE

NOT_REAL = ('eval', 'benchmark', 'legacy-import')
LANGUAGES = {'.py': 'python', '.js': 'javascript', '.jsx': 'javascript', '.mjs': 'javascript', '.ts': 'typescript', '.tsx': 'typescript', '.go': 'go', '.rs': 'rust',
             '.java': 'java', '.rb': 'ruby', '.css': 'css', '.html': 'html', '.md': 'markdown', '.json': 'json', '.toml': 'toml', '.yml': 'yaml', '.yaml': 'yaml'}

EPOCH_FILE = 'stats-epoch.json'

def epoch():
    """Unix time from which acceptance statistics, routing estimates and the dataset count, or None for the whole history. Jobs are never deleted: the epoch only decides what is counted."""
    try:
        return float(json.loads((CONFIG / EPOCH_FILE).read_text())['since'])
    except (OSError, ValueError, KeyError, TypeError):
        return None

def reset_epoch(now=None):
    """Start counting from now. History stays on disk and in the job store and is still available with include_history."""
    since = now or time.time()
    CONFIG.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = CONFIG / EPOCH_FILE
    path.write_text(json.dumps({'since': since, 'note': 'jobs submitted before this time are not counted unless history is requested'}))
    path.chmod(0o600)
    return since

def counted(jobs, include_history=False):
    """The jobs that count: those submitted at or after the epoch (all of them when there is none or history is requested)."""
    since = None if include_history else epoch()
    return [j for j in jobs if since is None or (j.get('created') or 0) >= since]

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
    summary = {'available': True, 'files': files, 'changed_files': len(files), 'bytes': sum(len(c) for c in chunks), 'captured': time.time()}
    if directory.is_dir():
        out = directory / 'final-frontier.diff'
        out.write_text(''.join(chunks))
        out.chmod(0o600)
        (directory / 'final-frontier.json').write_text(json.dumps(summary))
        (directory / 'final-frontier.json').chmod(0o600)
    return summary

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
            'model': result.get('model') or request.get('model') or 'unrecorded', 'context_files': len(context.get('files', [])) if isinstance(context, dict) else None,
            'worker_status': result.get('worker_status'), 'diff': packet.get('diff'), 'scope_ok': packet.get('scope_ok'),
            'execution_seconds': metrics.get('execution_seconds'), 'local_tokens': (usage.get('input', 0) or 0) + (usage.get('output', 0) or 0) if isinstance(usage, dict) and usage else None,
            'decision': review.get('decision'), 'reason': review.get('reason'), 'review_effort_seconds': review.get('review_effort_seconds'),
            'task_outcome': review.get('task_outcome'), 'measurement_source': review.get('measurement_source'),
            'baseline_usage': review.get('baseline_usage'), 'delegated_usage': review.get('delegated_usage'),
            'baseline_frontier_tokens': review.get('baseline_frontier_tokens'), 'delegated_frontier_tokens': review.get('delegated_frontier_tokens')}

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


SCHEMA = 1

def read_text(path, limit=2_000_000):
    try:
        return Path(path).read_text(errors='replace')[:limit]
    except OSError:
        return None

def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None

def prompt_of(directory):
    """The user turn of the job's model session: the exact task-plus-context prompt the model saw (private source)."""
    for name in ('work.session.json', 'edit.session.json'):
        session = read_json(Path(directory) / name)
        for message in (session or {}).get('messages', []):
            if message.get('type') == 'user' and message.get('text'):
                return message['text']
    return None

def dataset_record(job, directory=None, include_source=False):
    """One training/evaluation record for a delegation: what was asked, what the worker was shown and produced, and what the frontier decided and finally shipped.

    Structure only by default (hashes, counts, paths, ranges, reason codes). `include_source` adds the task text, the exact prompt, the local patch, the final frontier diff and the
    review notes: private source and model output, written only on request so a later fine-tuning set stays possible without leaking by default."""
    directory = Path(directory or STATE / 'jobs' / job['id'])
    request, result, review = job['request'], job.get('result') or {}, job.get('review') or {}
    base = row(job, directory)
    context = read_json(directory / 'work.context.json') or {}
    turns = read_json(directory / 'work.turns.json') or {}
    gate = read_json(directory / 'work.gate.json') or {}
    shipped = read_json(directory / 'final-frontier.json')
    record = {'schema': SCHEMA, **base, 'created': job.get('created'), 'ended': job.get('ended'),
              'spec_sha256': hashlib.sha256(json.dumps(request.get('spec') or request.get('task', ''), sort_keys=True).encode()).hexdigest(),
              'allowed_paths': request.get('allowed_paths', []), 'read_paths': request.get('read_paths', []), 'handoff_id': request.get('handoff_id'),
              'context': {'budget_chars': context.get('budget_chars'), 'used_chars': context.get('used_chars'),
                          'files': {p: {k: v for k, v in info.items() if k in ('lines', 'shown', 'whole', 'ranges')} for p, info in (context.get('files') or {}).items()},
                          'focus': context.get('focus'), 'coverage': context.get('coverage')},
              'gate': {k: gate.get(k) for k in ('tripped', 'mode', 'counts')} if gate else None,
              'turns': [{k: t.get(k) for k in ('step', 'done_reason', 'output_tokens', 'truncated', 'continuation', 'blocks')} | {'errors': len(t.get('errors', []))} for t in turns.get('turns', [])],
              'local': {'status': result.get('worker_status'), 'changed_files': result.get('changed_files', []), 'checks': [{'name': c.get('name'), 'status': c.get('status'), 'failures': len(c.get('failures') or [])} for c in result.get('checks', [])],
                        'spec_verification': {k: v for k, v in (result.get('spec_verification') or {}).items() if k != 'results'} or None},
              'frontier': {'decision': review.get('decision'), 'reason': review.get('reason'), 'effort_seconds': review.get('review_effort_seconds'), 'task_outcome': review.get('task_outcome'),
                           'notes_chars': len(review.get('notes', '')), 'final_diff': shipped}}
    if include_source:
        record['source'] = {'task': request.get('task'), 'prompt': prompt_of(directory), 'local_patch': read_text(directory / 'job.diff') or read_text(directory / 'patch.diff'),
                            'final_frontier_diff': read_text(directory / 'final-frontier.diff'), 'review_notes': review.get('notes')}
    return record

def dataset(jobs, since_days=None, kinds=None, include_source=False, include_unreviewed=False, include_eval=False, jobs_dir=None, now=None):
    """Records for real delegations (benchmark jobs excluded) that the frontier reviewed, newest first."""
    cutoff = (now or time.time()) - since_days * 86400 if since_days else None
    out = []
    for job in jobs:
        if (not include_eval and not is_real(job)) or (not include_unreviewed and not job.get('review')):
            continue
        if kinds and (job['request'].get('kind') or '-') not in kinds:
            continue
        if cutoff and (job.get('created') or 0) < cutoff:
            continue
        out.append(dataset_record(job, Path(jobs_dir or STATE / 'jobs') / job['id'], include_source))
    return sorted(out, key=lambda r: -(r.get('created') or 0))
