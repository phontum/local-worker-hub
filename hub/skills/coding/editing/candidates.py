"""Sanitized snapshots and immutable experiment candidates."""
import hashlib
import json
from pathlib import Path
import shutil
import time

from hub.scoped import sensitive, EXCLUDED
from hub.settings import STATE
from .workspace import listing

MAX_BYTES = 300_000_000
TTL = 7 * 86400


def digest(contents):
    return hashlib.sha256(json.dumps(contents,sort_keys=True).encode()).hexdigest()


def capture(repo, destination, read_paths=(), deadline_seconds=5):
    repo=Path(repo).resolve(strict=True);destination=Path(destination)
    destination.mkdir(parents=True,exist_ok=False,mode=0o700)
    started=time.monotonic();total=0;content={};manifest={}
    try:
        initial=listing(repo)
        for name in initial:
            if time.monotonic()-started > deadline_seconds:
                raise TimeoutError('Snapshot capture exceeded its deadline')
            if sensitive(name) or any(p in EXCLUDED for p in Path(name).parts):continue
            if read_paths and not any(name==p or name.startswith(p.rstrip('/')+'/') for p in read_paths):continue
            source=repo/name
            if Path(name).is_absolute() or '..' in Path(name).parts or any((repo/Path(*Path(name).parts[:i])).is_symlink() for i in range(1,len(Path(name).parts)+1)):continue
            if not source.resolve().is_relative_to(repo):continue
            if source.is_symlink() or not source.is_file() or source.stat().st_nlink>1:continue
            raw=source.read_bytes();total+=len(raw)
            if total>MAX_BYTES:raise ValueError('Snapshot exceeds byte limit')
            if b'\0' in raw:continue
            text=raw.decode('utf-8')
            target=destination/name;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(raw);target.chmod(0o600)
            content[name]=text;manifest[name]=hashlib.sha256(raw).hexdigest()
        # Detect concurrent edits; exclude ambiguous snapshots instead of mixing revisions.
        for name,sha in manifest.items():
            source=repo/name
            if not source.is_file() or source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest()!=sha:
                raise ValueError('Source changed during capture: '+name)
        if listing(repo)!=initial:raise ValueError('Repository file set changed during capture')
        if time.monotonic()-started>deadline_seconds:raise TimeoutError('Snapshot capture exceeded its deadline')
        dependencies=[str(repo/p) for p in ('.venv','venv','node_modules') if (repo/p).is_dir() and not (repo/p).is_symlink()]
        return {'hash':digest(content),'files':manifest,'dependencies':dependencies,'captured':time.time(), 'bytes':total}
    except Exception:
        shutil.rmtree(destination,ignore_errors=True)
        raise


def contents(root, names=None):
    root=Path(root)
    paths = [root/p for p in names] if names is not None else sorted(root.rglob('*'))
    result={}
    for path in paths:
        if path.is_symlink() or not path.is_file():continue
        if not path.resolve().is_relative_to(root.resolve()):raise ValueError('Snapshot path escapes root')
        name=str(path.relative_to(root))
        if sensitive(name) or '.git' in path.parts:continue
        try:result[name]=path.read_text()
        except UnicodeError:pass
    return result


def materialize(values, root):
    root=Path(root);root.mkdir(parents=True,exist_ok=False,mode=0o700)
    for name,text in values.items():
        target=root/name
        if Path(name).is_absolute() or '..' in Path(name).parts or sensitive(name):raise ValueError('Unsafe candidate path')
        target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text);target.chmod(0o600)
    return root


def purge(root=None, now=None):
    root=Path(root or STATE/'tasks');now=now or time.time()
    for path in root.glob('*/snapshots'):
        if now-path.stat().st_mtime>TTL:shutil.rmtree(path)
