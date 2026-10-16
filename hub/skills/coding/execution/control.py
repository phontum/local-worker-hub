"""Read-only master gate, usable without launcher or job orchestration imports."""
import json
from hub.settings import CONFIG


def enabled():
    try:
        value=json.loads((CONFIG/'integration-control.json').read_text())
        return isinstance(value,dict) and value.get('enabled') is True
    except FileNotFoundError:return True
    except (OSError,ValueError):return False


def require_enabled():
    if not enabled():raise RuntimeError('Local Worker is switched off. Run local-worker on; use a fresh frontier session.')


from contextlib import contextmanager
from functools import wraps
import fcntl
import os


@contextmanager
def switch_lock(config=None, exclusive=False, blocking=True):
    config=config or CONFIG
    config.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(config/'integration-switch.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        if os.fstat(fd).st_nlink!=1:raise RuntimeError('Refusing linked integration lock')
        fcntl.flock(fd,(fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)|(0 if blocking else fcntl.LOCK_NB))
        yield
    finally:os.close(fd)


def active_operation(fn):
    @wraps(fn)
    def guarded(*args,**kwargs):
        with switch_lock():
            require_enabled()
            return fn(*args,**kwargs)
    return guarded


def active_queue(fn):
    @wraps(fn)
    def guarded(*args,**kwargs):
        try:
            with switch_lock(blocking=False):
                if not enabled():return None
                return fn(*args,**kwargs)
        except BlockingIOError:return None
    return guarded
