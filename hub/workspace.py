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

def create(origin, ident, allowed_paths):
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
    record = {'origin': str(origin), 'path': str(work), 'created': time.time(), 'state': 'ready', 'files': copied,
              'allowed': sorted(allowed_paths), 'base': {p: sha(origin / p) for p in allowed_paths}}
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
    """Unified diff of the authorized files against the baseline, new files included; also stores the finished hashes."""
    record = read_record(ident)
    work = Path(record['path'])
    paths = record['allowed']
    subprocess.run(GIT + ['-C', str(work), 'add', '-N', '--'] + paths, capture_output=True)
    done = subprocess.run(GIT + ['-C', str(work), 'diff', '--binary', '--no-ext-diff', 'HEAD', '--'] + paths, capture_output=True, text=True)
    record['result'] = {p: sha(work / p) for p in paths}
    record['changed'] = [p for p in paths if record['result'][p] is not None and record['result'][p] != record['base'][p]]
    record['removed'] = [p for p in paths if record['result'][p] is None and record['base'][p] is not None]
    write_record(ident, record)
    out = ROOT / ident / 'patch.diff'
    out.write_text(done.stdout)
    out.chmod(0o600)
    return done.stdout

def outside_scope(ident):
    """(tracked files changed outside the authorized paths, untracked files left behind); call after patch()."""
    record = read_record(ident)
    work, allowed = record['path'], set(record['allowed'])
    changed = subprocess.run(GIT + ['-C', work, 'diff', '--name-only', 'HEAD'], capture_output=True, text=True).stdout.split()
    extra = subprocess.run(GIT + ['-C', work, 'ls-files', '--others', '--exclude-standard'], capture_output=True, text=True).stdout.split()
    return [p for p in changed if p not in allowed], [p for p in extra if p not in allowed]

def apply(ident):
    """Write the changed authorized files into the user's tree, but only if none of them changed there since the snapshot."""
    record = read_record(ident)
    if record['state'] != 'ready':
        raise WorkspaceError('Workspace is ' + record['state'] + ', not ready to apply')
    origin, work = Path(record['origin']), Path(record['path'])
    if 'result' not in record:
        patch(ident)
        record = read_record(ident)
    conflicts = [p for p in record['changed'] if sha(origin / p) != record['base'][p]]
    if conflicts:
        return {'applied': [], 'conflicts': conflicts, 'state': 'ready'}
    drift = [p for p in record['changed'] if sha(work / p) != record['result'][p]]
    if drift:
        raise WorkspaceError('Workspace files changed after the patch was recorded: ' + ', '.join(drift))
    applied = []
    for name in record['changed']:
        target = origin / name
        for part in [target, *target.parents]:
            if part == origin:
                break
            if part.is_symlink():
                raise ScopeError('Refusing to write through a symlink: ' + name)
        if sensitive(name) or '..' in Path(name).parts:
            raise ScopeError('Refusing to write outside the authorized scope: ' + name)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.lw-apply')
        shutil.copy2(work / name, temporary)
        os.replace(temporary, target)
        applied.append(name)
    record['state'] = 'applied'
    record['applied'] = time.time()
    write_record(ident, record)
    shutil.rmtree(work, ignore_errors=True)
    return {'applied': applied, 'conflicts': [], 'state': 'applied', 'skipped_removals': record.get('removed', [])}

def discard(ident):
    record = read_record(ident)
    shutil.rmtree(record['path'], ignore_errors=True)
    if record['state'] == 'ready':
        record['state'] = 'discarded'
        write_record(ident, record)
    return {'state': record['state']}

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
