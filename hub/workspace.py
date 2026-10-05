"""Private editor workspaces: a copy of the user's current tree, a patch against it, and a guarded apply step.

The worker edits and checks inside STATE/workspaces/<job>, never in the user's tree. The copy is the working tree as it is now
(tracked and untracked-but-not-ignored files, uncommitted changes included, secret files left out), so checks see what the user
sees. A private git repository with one baseline commit makes the patch exact without touching the user's .git. Applying
re-checks every authorized file in the user's tree against the hash recorded at snapshot time and refuses on any difference.
"""
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from .scoped import EXCLUDED, ScopeError, sensitive
from .settings import STATE

ROOT = STATE / 'workspaces'
MAX_TOTAL = 300_000_000
MAX_FILE = 20_000_000
LINK_DIRS = ('node_modules', '.venv', 'venv')
TTL_SECONDS = 7 * 24 * 3600
GIT = ['git', '-c', 'user.name=local-worker', '-c', 'user.email=local-worker@invalid', '-c', 'commit.gpgsign=false',
       '-c', 'core.autocrlf=false', '-c', 'core.hooksPath=/dev/null']

class WorkspaceError(RuntimeError):
    pass

def sha(path):
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

def listing(origin):
    """Files to copy, relative to origin: git's view when it is a work tree, otherwise a guarded walk."""
    probe = subprocess.run(['git', '-C', str(origin), 'rev-parse', '--show-toplevel'], capture_output=True, text=True)
    if probe.returncode == 0 and Path(probe.stdout.strip()).resolve() == origin:
        out = subprocess.run(['git', '-C', str(origin), 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                             capture_output=True, text=True, check=True).stdout
        return sorted({name for name in out.split('\0') if name})
    names = []
    for base, dirs, files in os.walk(origin, followlinks=False):
        dirs[:] = [d for d in dirs if d not in EXCLUDED and not (Path(base) / d).is_symlink()]
        names += [str((Path(base) / f).relative_to(origin)) for f in files]
    return sorted(names)

def create(origin, ident, allowed_paths, dependencies=(), delete_paths=(), checks=()):
    """Copy the tree, commit a baseline and record the hash of each authorized file; returns the workspace record."""
    origin = Path(origin).resolve()
    work = ROOT / ident / 'tree'
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(work.parent, 0o700)
    total, copied = 0, 0
    for name in listing(origin):
        source = origin / name
        if sensitive(name) or source.is_symlink() or not source.is_file():
            continue
        size = source.stat().st_size
        if size > MAX_FILE:
            continue
        total += size
        if total > MAX_TOTAL:
            shutil.rmtree(work.parent, ignore_errors=True)
            raise WorkspaceError(f'Repository is larger than {MAX_TOTAL // 1_000_000} MB; use in_place or narrow the repository')
        target = work / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1
    for name in LINK_DIRS:
        if (origin / name).is_dir() and not (origin / name).is_symlink() and not (work / name).exists():
            (work / name).symlink_to(origin / name, target_is_directory=True)
    for step in (['init', '-q'], None, ['add', '-A', '--', '.'], ['commit', '-q', '--allow-empty', '-m', 'baseline']):
        if step is None:  # linked dependency directories and check byproducts must not enter the baseline
            (work / '.git' / 'info').mkdir(parents=True, exist_ok=True)
            (work / '.git' / 'info' / 'exclude').write_text('\n'.join(LINK_DIRS) + '\n__pycache__/\n.pytest_cache/\n')
            continue
        done = subprocess.run(GIT + ['-C', str(work)] + step, capture_output=True, text=True)
        if done.returncode:
            shutil.rmtree(work.parent, ignore_errors=True)
            raise WorkspaceError('Could not snapshot the repository: ' + done.stderr.strip()[:200])
    root_commit = subprocess.run(GIT + ['-C', str(work), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
    base_dir = ROOT / ident / 'base'
    for p in allowed_paths:  # the files as they were, so an applied job can be reverted
        if (origin / p).is_file() and not (origin / p).is_symlink() and not sensitive(p):
            (base_dir / p).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin / p, base_dir / p)
    record = {'origin': str(origin), 'path': str(work), 'created': time.time(), 'state': 'ready', 'files': copied,
              'allowed': sorted(allowed_paths), 'base': {p: sha(origin / p) for p in allowed_paths}, 'delete': sorted(delete_paths),
              'dependencies': {p: sha(origin / p) for p in dependencies if p not in allowed_paths and (origin / p).is_file()}, 'checks': list(checks),
              'chain': [ident], 'root_commit': root_commit, 'baseline_commit': root_commit, 'job_allowed': sorted(allowed_paths)}
    write_record(ident, record)
    return record

def record_path(ident):
    return ROOT / ident / 'workspace.json'

def write_record(ident, record):
    path = record_path(ident)
    path.write_text(json.dumps(record, indent=2))
    path.chmod(0o600)

def read_record(ident):
    path = record_path(ident)
    if not path.is_file():
        raise WorkspaceError('No private workspace for this job')
    return json.loads(path.read_text())

def patch(ident):
    """Unified diff of the authorized files against the first snapshot (cumulative across a chain), new files included; also stores the finished
    hashes and writes `job.diff`, the changes of this job alone."""
    record = read_record(ident)
    work = Path(record['path'])
    paths = record['allowed']
    subprocess.run(GIT + ['-C', str(work), 'add', '-N', '--'] + paths, capture_output=True)
    cumulative = subprocess.run(GIT + ['-C', str(work), 'diff', '--binary', '--no-ext-diff', record.get('root_commit', 'HEAD'), '--'] + paths, capture_output=True, text=True)
    own = subprocess.run(GIT + ['-C', str(work), 'diff', '--binary', '--no-ext-diff', record.get('baseline_commit', 'HEAD'), '--'] + record.get('job_allowed', paths), capture_output=True, text=True)
    record['result'] = {p: sha(work / p) for p in paths}
    record['changed'] = [p for p in paths if record['result'][p] is not None and record['result'][p] != record['base'][p]]
    record['removed'] = [p for p in paths if record['result'][p] is None and record['base'][p] is not None]
    write_record(ident, record)
    for name, text in (('patch.diff', cumulative.stdout), ('job.diff', own.stdout)):
        out = ROOT / ident / name
        out.write_text(text)
        out.chmod(0o600)
    return cumulative.stdout

def job_patch(ident):
    """The diff of this job alone (the same as `patch` unless the job continued an earlier workspace)."""
    patch(ident)
    return (ROOT / ident / 'job.diff').read_text()

def chain(origin, ident, parent, allowed_paths, dependencies=(), delete_paths=(), checks=()):
    """Start a job inside an earlier job's workspace, so several bounded jobs build on each other and one apply writes them all.
    The earlier job's changes become the new baseline for this job's own diff; conflicts are still judged against your tree."""
    previous = read_record(parent)
    if previous['state'] != 'ready':
        raise WorkspaceError(f"Cannot continue from a workspace that is {previous['state']}")
    if Path(origin).resolve() != Path(previous['origin']):
        raise WorkspaceError('A continued job must use the same repository as the job it continues')
    work = Path(previous['path'])
    if not work.exists():
        raise WorkspaceError('The workspace of the job being continued has expired or was removed')
    for step in (['add', '-A', '--', '.'], ['commit', '-q', '--allow-empty', '-m', 'before ' + ident]):
        subprocess.run(GIT + ['-C', str(work)] + step, capture_output=True, text=True)
    baseline = subprocess.run(GIT + ['-C', str(work), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
    base = dict(previous['base'])
    for p in allowed_paths:
        if p not in base:  # not touched by an earlier job, so the tree still holds the snapshot content
            base[p] = sha(work / p)
            if (work / p).is_file():
                (ROOT / ident / 'base' / p).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(work / p, ROOT / ident / 'base' / p)
    deps = dict(previous.get('dependencies', {}))
    origin_path = Path(previous['origin'])
    deps.update({p: sha(origin_path / p) for p in dependencies if p not in base and (origin_path / p).is_file()})
    merged_checks = list(previous.get('checks', []))
    merged_checks += [c for c in checks if c not in merged_checks]
    record = {**previous, 'created': time.time(), 'state': 'ready', 'chain': previous.get('chain', [parent]) + [ident], 'parent': parent, 'baseline_commit': baseline,
              'allowed': sorted(set(previous['allowed']) | set(allowed_paths)), 'job_allowed': sorted(allowed_paths), 'base': base,
              'delete': sorted(set(previous.get('delete', [])) | set(delete_paths)), 'dependencies': deps, 'checks': merged_checks}
    for stale in ('result', 'changed', 'removed', 'applied', 'applied_sha', 'post_checks'):
        record.pop(stale, None)
    previous['state'], previous['chained_into'] = 'chained', ident
    write_record(parent, previous)
    write_record(ident, record)
    return record

def chain_ids(ident):
    return read_record(ident).get('chain', [ident])

def outside_scope(ident):
    """(tracked files changed outside the authorized paths, untracked files left behind); call after patch()."""
    record = read_record(ident)
    work, allowed = record['path'], set(record['allowed'])
    changed = subprocess.run(GIT + ['-C', work, 'diff', '--name-only', record.get('root_commit', 'HEAD')], capture_output=True, text=True).stdout.split()
    extra = subprocess.run(GIT + ['-C', work, 'ls-files', '--others', '--exclude-standard'], capture_output=True, text=True).stdout.split()
    return [p for p in changed if p not in allowed], [p for p in extra if p not in allowed]

def stale_dependencies(ident):
    """Read-only context files (read_paths) that changed in the user's tree since the job's snapshot."""
    record = read_record(ident)
    origin = Path(record['origin'])
    return sorted(p for p, digest in record.get('dependencies', {}).items() if sha(origin / p) != digest)

def revalidate(record, ident):
    """Re-run the job's approved checks on the user's tree as it is now plus the job's changes, in a throwaway copy."""
    from .models import Check
    from .validation import run_check_sync
    scratch = ident + '-revalidate'
    shutil.rmtree(ROOT / scratch, ignore_errors=True)
    copy = create(record['origin'], scratch, record['allowed'])
    try:
        tree, work = Path(copy['path']), Path(record['path'])
        for name in record['changed']:
            (tree / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(work / name, tree / name)
        for name in record.get('removed', []):
            (tree / name).unlink(missing_ok=True)
        return [run_check_sync(Check.model_validate(c), tree) for c in record.get('checks', [])]
    finally:
        shutil.rmtree(ROOT / scratch, ignore_errors=True)

def compact(results):
    return [{k: r.get(k) for k in ('name', 'status', 'exit_code', 'reason', 'counts')} | {'failures': [f"{f['test_id']} ({f.get('file')}:{f.get('line')})" for f in r.get('failures', [])[:5]]} for r in results]

def apply(ident, accept_removals=False, revalidate_first=False, run_checks=False):
    """Write the changed authorized files into the user's tree, but only if none of them changed there since the snapshot.

    Removals (a file missing from the workspace) are applied only when the job declared them in delete_paths and `accept_removals` is set.
    `revalidate_first` re-runs the job's approved checks on your tree as it is now plus the job's changes before writing anything;
    `run_checks` runs them in your tree after the write (no automatic revert; use `revert`)."""
    record = read_record(ident)
    if record['state'] == 'chained':
        raise WorkspaceError('This workspace was continued by job ' + str(record.get('chained_into')) + '; apply that job, which includes these changes')
    if record['state'] != 'ready':
        raise WorkspaceError('Workspace is ' + record['state'] + ', not ready to apply')
    origin, work = Path(record['origin']), Path(record['path'])
    if 'result' not in record:
        patch(ident)
        record = read_record(ident)
    stale = stale_dependencies(ident)
    touched = record['changed'] + record.get('removed', [])
    conflicts = [p for p in touched if sha(origin / p) != record['base'][p]]
    if conflicts:
        return {'applied': [], 'conflicts': conflicts, 'stale_dependencies': stale, 'state': 'ready'}
    removed = record.get('removed', [])
    undeclared = [p for p in removed if p not in record.get('delete', [])]
    if removed and (undeclared or not accept_removals):
        return {'applied': [], 'conflicts': [], 'pending_removals': removed, 'undeclared_removals': undeclared, 'stale_dependencies': stale, 'state': 'ready'}
    drift = [p for p in record['changed'] if sha(work / p) != record['result'][p]]
    if drift:
        raise WorkspaceError('Workspace files changed after the patch was recorded: ' + ', '.join(drift))
    if revalidate_first:
        results = revalidate(record, ident)
        if not results or any(r['status'] != 'passed' for r in results):
            return {'applied': [], 'conflicts': [], 'stale_dependencies': stale, 'revalidation': compact(results), 'state': 'ready'}
    applied = []
    for name in record['changed'] + removed:
        target = origin / name
        for part in [target, *target.parents]:
            if part == origin:
                break
            if part.is_symlink():
                raise ScopeError('Refusing to write through a symlink: ' + name)
        if sensitive(name) or '..' in Path(name).parts:
            raise ScopeError('Refusing to write outside the authorized scope: ' + name)
        if name in removed:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + '.lw-apply')
            shutil.copy2(work / name, temporary)
            os.replace(temporary, target)
        applied.append(name)
    record['state'] = 'applied'
    record['applied'] = time.time()
    record['applied_sha'] = {name: sha(origin / name) for name in applied}
    out = {'applied': applied, 'conflicts': [], 'state': 'applied', 'removed': removed, 'stale_dependencies': stale}
    if run_checks:
        from .models import Check
        from .validation import run_check_sync
        results = [run_check_sync(Check.model_validate(c), origin) for c in record.get('checks', [])]
        record['post_checks'] = compact(results)
        out['post_checks'] = record['post_checks']
        out['post_checks_ok'] = bool(results) and all(r['status'] == 'passed' for r in results)
    write_record(ident, record)
    for earlier in record.get('chain', [ident])[:-1]:
        try:
            before = read_record(earlier)
            before['state'], before['applied_via'] = 'applied', ident
            write_record(earlier, before)
        except WorkspaceError:
            pass
    shutil.rmtree(work, ignore_errors=True)
    return out

def revert(ident):
    """Undo an applied job: restore each written file to its snapshot content (or delete a file the job created), unless it changed again since."""
    record = read_record(ident)
    if record['state'] != 'applied':
        raise WorkspaceError('Only an applied job can be reverted (state: ' + record['state'] + ')')
    origin = Path(record['origin'])
    bases = [ROOT / earlier / 'base' for earlier in record.get('chain', [ident])]  # the earliest copy of a file is its snapshot state
    conflicts = [p for p, digest in record.get('applied_sha', {}).items() if sha(origin / p) != digest]
    if conflicts:
        return {'reverted': [], 'conflicts': conflicts, 'state': 'applied'}
    restored = []
    for name in record.get('applied_sha', {}):
        target = origin / name
        saved = next((b / name for b in bases if (b / name).is_file()), None)
        if saved is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(saved, target)
        else:
            target.unlink(missing_ok=True)
        restored.append(name)
    record['state'] = 'reverted'
    record['reverted'] = time.time()
    write_record(ident, record)
    return {'reverted': restored, 'conflicts': [], 'state': 'reverted'}

def discard(ident):
    record = read_record(ident)
    shutil.rmtree(record['path'], ignore_errors=True)
    for member in record.get('chain', [ident]):  # a chain shares one tree: discarding the last job discards them all
        try:
            member_record = read_record(member)
        except WorkspaceError:
            continue
        if member_record['state'] in ('ready', 'chained'):
            member_record['state'] = 'discarded'
            write_record(member, member_record)
    return {'state': read_record(ident)['state']}

def purge_old(now=None, ttl=TTL_SECONDS):
    """Remove workspace trees older than the TTL (the record and patch stay so a result can still be read)."""
    now = now or time.time()
    removed = []
    for record_file in ROOT.glob('*/workspace.json') if ROOT.is_dir() else []:
        try:
            record = json.loads(record_file.read_text())
            if Path(record['path']).exists() and now - record['created'] > ttl:
                shutil.rmtree(record['path'], ignore_errors=True)
                if record['state'] == 'ready':
                    record['state'] = 'expired'
                    write_record(record_file.parent.name, record)
                removed.append(record_file.parent.name)
        except (OSError, ValueError, KeyError):
            continue
    return removed
