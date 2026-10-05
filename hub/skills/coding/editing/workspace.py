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
from hub.scoped import EXCLUDED, ScopeError, sensitive
from hub.settings import STATE

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

MAX_DEPENDENCIES = 300

def expand_dependencies(origin, names):
    """Files behind read_paths: a directory root contributes its files (guarded, bounded), a missing name nothing."""
    origin, found = Path(origin), []
    for name in names:
        target = origin / name
        if '..' in Path(name).parts or Path(name).is_absolute() or sensitive(name):
            continue
        if target.is_file() and not target.is_symlink():
            found.append(name)
        elif target.is_dir() and not target.is_symlink():
            for base, dirs, files in os.walk(target, followlinks=False):
                dirs[:] = sorted(d for d in dirs if d not in EXCLUDED and not (Path(base) / d).is_symlink())
                for f in sorted(files):
                    relative = str((Path(base) / f).relative_to(origin))
                    if not sensitive(relative) and not (Path(base) / f).is_symlink():
                        found.append(relative)
                if len(found) >= MAX_DEPENDENCIES:
                    break
    return list(dict.fromkeys(found))[:MAX_DEPENDENCIES]

def _copy_tree(origin, work):
    """Copy the files `listing` names, skipping secrets, symlinks and oversized files; the fallback for non-git trees."""
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
            raise WorkspaceError(f'Repository is larger than {MAX_TOTAL // 1_000_000} MB; name a narrower repository (a git work tree has no such limit)')
        target = work / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1
    return copied

def _is_git_root(origin):
    probe = subprocess.run(['git', '-C', str(origin), 'rev-parse', '--show-toplevel'], capture_output=True, text=True)
    return probe.returncode == 0 and Path(probe.stdout.strip()).resolve() == origin

def _clone_tree(origin, work):
    """Fast snapshot of a git work tree: a shared clone of HEAD (objects are borrowed, nothing is written to the user's .git), then the
    uncommitted state laid over it: modified, staged and untracked files are copied, files deleted in the user's tree are removed.
    Secrets, symlinks and oversized files are dropped like in the copy path. Returns the file count, or None to fall back to copying."""
    if not _is_git_root(origin) or subprocess.run(['git', '-C', str(origin), 'rev-parse', '--verify', '-q', 'HEAD'], capture_output=True).returncode:
        return None
    shutil.rmtree(work, ignore_errors=True)
    done = subprocess.run(['git', 'clone', '-q', '--shared', '--no-checkout', '--no-hardlinks', str(origin), str(work)], capture_output=True, text=True)
    if done.returncode:
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True, mode=0o700)
        return None
    # The clone keeps an `origin` remote and branch refs; the workspace needs a private history only.
    for step in (['remote', 'remove', 'origin'], ['checkout', '-q', '--detach', 'HEAD']):
        if subprocess.run(GIT + ['-C', str(work)] + step, capture_output=True, text=True).returncode:
            shutil.rmtree(work, ignore_errors=True)
            work.mkdir(parents=True, exist_ok=True, mode=0o700)
            return None
    changed = subprocess.run(['git', '-C', str(origin), 'diff', '--name-only', '-z', 'HEAD'], capture_output=True, text=True).stdout.split('\0')
    untracked = subprocess.run(['git', '-C', str(origin), 'ls-files', '-z', '--others', '--exclude-standard'], capture_output=True, text=True).stdout.split('\0')
    for name in dict.fromkeys(n for n in changed + untracked if n):
        source, target = origin / name, work / name
        if source.is_file() and not source.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink() or target.is_dir():
                target.unlink() if target.is_symlink() else shutil.rmtree(target)
            shutil.copy2(source, target)
        elif target.is_file() or target.is_symlink():
            target.unlink()
    copied = 0
    for name in subprocess.run(['git', '-C', str(work), 'ls-files', '-z'], capture_output=True, text=True).stdout.split('\0'):
        if not name:
            continue
        target = work / name
        if (sensitive(name) or target.is_symlink() or not target.is_file() or target.stat().st_size > MAX_FILE) and (target.is_symlink() or target.exists()):
            target.unlink()
        elif target.is_file():
            copied += 1
    for name in subprocess.run(['git', '-C', str(work), 'ls-files', '-z', '--others', '--exclude-standard'], capture_output=True, text=True).stdout.split('\0'):
        if name and (sensitive(name) or (work / name).is_symlink() or (work / name).stat().st_size > MAX_FILE):
            (work / name).unlink()
        elif name:
            copied += 1
    shutil.rmtree(work / '.git' / 'refs' / 'remotes', ignore_errors=True)
    return copied

def create(origin, ident, allowed_paths, dependencies=(), delete_paths=(), checks=()):
    """Copy the tree, commit a baseline and record the hash of each authorized file; returns the workspace record."""
    origin = Path(origin).resolve()
    work = ROOT / ident / 'tree'
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(work.parent, 0o700)
    copied = _clone_tree(origin, work)
    if copied is None:
        copied = _copy_tree(origin, work)
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
              'dependencies': {p: sha(origin / p) for p in expand_dependencies(origin, dependencies) if p not in allowed_paths}, 'checks': list(checks),
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
    deps.update({p: sha(origin_path / p) for p in expand_dependencies(origin_path, dependencies) if p not in base})
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
    from hub.models import Check
    from hub.skills.coding.validation.validation import run_check_sync
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

def _guard_target(origin, name):
    target = origin / name
    for part in [target, *target.parents]:
        if part == origin:
            break
        if part.is_symlink():
            raise ScopeError('Refusing to write through a symlink: ' + name)
    if sensitive(name) or '..' in Path(name).parts or Path(name).is_absolute():
        raise ScopeError('Refusing to write outside the authorized scope: ' + name)
    return target

def journal_path(ident):
    return ROOT / ident / 'apply.journal'

def _restore(origin, journal, ident):
    """Put every file named in the journal back to its backed-up pre-image (or remove a file that did not exist)."""
    backup = ROOT / ident / 'apply-backup'
    for entry in journal['files']:
        target = origin / entry['name']
        saved = backup / entry['name']
        if entry['existed']:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + '.lw-restore')
            shutil.copy2(saved, temporary)
            os.replace(temporary, target)
        else:
            target.unlink(missing_ok=True)

def _commit(origin, ident, writes, removals):
    """All-or-nothing write into the user's tree.

    `writes` maps a relative name to the source file holding the new bytes; `removals` lists names to delete. Every target is validated before
    anything is written, new bytes are staged next to their targets, the current bytes are backed up and a journal is fsynced, then the files are
    replaced one by one. Any failure (including a file that changed since it was backed up) restores every file already replaced and re-raises,
    so the tree is either fully before or fully after. A journal that survives a crash is rolled back by `recover`."""
    names = list(writes) + list(removals)
    targets = {name: _guard_target(origin, name) for name in names}
    backup = ROOT / ident / 'apply-backup'
    shutil.rmtree(backup, ignore_errors=True)
    staged, journal = [], {'time': time.time(), 'origin': str(origin), 'files': []}
    try:
        for name in names:
            target = targets[name]
            existed = target.is_file()
            digest = sha(target)
            if existed:
                (backup / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup / name)
            journal['files'].append({'name': name, 'existed': existed, 'pre': digest, 'post': sha(writes[name]) if name in writes else None})
        for name in writes:
            target = targets[name]
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + '.lw-apply')
            staged.append(temporary)
            shutil.copy2(writes[name], temporary)
        journal_file = journal_path(ident)
        journal_file.parent.mkdir(parents=True, exist_ok=True)
        with open(journal_file, 'w') as handle:
            json.dump(journal, handle)
            handle.flush()
            os.fsync(handle.fileno())
        replaced = []
        try:
            for entry in journal['files']:
                name, target = entry['name'], targets[entry['name']]
                if sha(target) != entry['pre']:
                    raise WorkspaceError('File changed while applying: ' + name)
                replaced.append(entry)
                if name in writes:
                    os.replace(target.with_name(target.name + '.lw-apply'), target)
                else:
                    target.unlink(missing_ok=True)
        except BaseException:
            _restore(origin, {'files': replaced}, ident)
            raise
    except BaseException:
        for temporary in staged:
            temporary.unlink(missing_ok=True)
        journal_path(ident).unlink(missing_ok=True)
        raise
    journal_path(ident).unlink(missing_ok=True)
    shutil.rmtree(backup, ignore_errors=True)
    return [entry['name'] for entry in journal['files']]

def recover(ident=None):
    """Roll back any apply interrupted by a crash (a journal left behind); returns the job ids that were rolled back."""
    rolled = []
    for journal_file in ([journal_path(ident)] if ident else ROOT.glob('*/apply.journal') if ROOT.is_dir() else []):
        if not journal_file.is_file():
            continue
        job = journal_file.parent.name
        try:
            journal = json.loads(journal_file.read_text())
            _restore(Path(journal['origin']), journal, job)
        except (OSError, ValueError, KeyError):
            continue
        journal_file.unlink(missing_ok=True)
        shutil.rmtree(ROOT / job / 'apply-backup', ignore_errors=True)
        rolled.append(job)
    return rolled

def apply(ident, accept_removals=False, revalidate_first=False, run_checks=False, accept_stale=False):
    """Write the changed authorized files into the user's tree, but only if none of them changed there since the snapshot.

    Removals (a file missing from the workspace) are applied only when the job declared them in delete_paths and `accept_removals` is set.
    `revalidate_first` re-runs the job's approved checks on your tree as it is now plus the job's changes before writing anything;
    Files the job only read (read_paths, evidence) that changed since the snapshot make the patch unverified against current code: apply refuses unless
    `revalidate_first` passes or `accept_stale` is set.
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
    if stale and not accept_stale and not revalidate_first:
        return {'applied': [], 'conflicts': [], 'stale_dependencies': stale, 'refused': 'stale_dependencies', 'state': 'ready',
                'hint': 'Files the job read changed since its snapshot. Re-run with revalidate (approved checks must pass) or accept_stale after reviewing them.'}
    drift = [p for p in record['changed'] if sha(work / p) != record['result'][p]]
    if drift:
        raise WorkspaceError('Workspace files changed after the patch was recorded: ' + ', '.join(drift))
    if revalidate_first:
        results = revalidate(record, ident)
        if not results or any(r['status'] != 'passed' for r in results):
            return {'applied': [], 'conflicts': [], 'stale_dependencies': stale, 'revalidation': compact(results), 'state': 'ready'}
    try:
        applied = _commit(origin, ident, {name: work / name for name in record['changed']}, removed)
    except ScopeError:
        raise
    except (OSError, WorkspaceError) as error:
        record['apply_error'] = str(error)[:300]
        write_record(ident, record)
        raise WorkspaceError('Apply failed and was rolled back; your tree is unchanged: ' + str(error)[:200]) from error
    record['state'] = 'applied'
    record['applied'] = time.time()
    record['applied_sha'] = {name: sha(origin / name) for name in applied}
    out = {'applied': applied, 'conflicts': [], 'state': 'applied', 'removed': removed, 'stale_dependencies': stale}
    if run_checks:
        from hub.models import Check
        from hub.skills.coding.validation.validation import run_check_sync
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
    writes, deletes = {}, []
    for name in record.get('applied_sha', {}):
        saved = next((b / name for b in bases if (b / name).is_file()), None)
        if saved is not None:
            writes[name] = saved
        else:
            deletes.append(name)
    try:
        restored = _commit(origin, ident, writes, deletes)
    except ScopeError:
        raise
    except (OSError, WorkspaceError) as error:
        raise WorkspaceError('Revert failed and was rolled back; your tree is unchanged: ' + str(error)[:200]) from error
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
