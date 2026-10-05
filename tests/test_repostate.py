import time
import uuid

from hub.codeindex import CodeIndex
from hub.models import JobRequest
from hub.repostate import RepoStates
from hub.scoped import ScopedFiles

def files_for(root):
    return ScopedFiles(JobRequest(role='investigator', repo=str(root), task='x', idempotency_key=uuid.uuid4().hex))

def make(tmp_path, name='r'):
    root = tmp_path / name
    root.mkdir()
    (root / 'a.py').write_text('def alpha():\n    return 1\n')
    (root / 'b.py').write_text('def beta():\n    return alpha()\n')
    return root

def test_a_second_call_is_a_hit_and_reparses_only_changed_files(tmp_path):
    root, states = make(tmp_path), RepoStates()
    first = states.index(files_for(root))
    assert states.stats == {'hits': 0, 'misses': 1} and first.stats['files'] == 2
    time.sleep(0.01)
    (root / 'b.py').write_text('def beta():\n    return alpha() + 1\n\ndef gamma():\n    pass\n')
    second = states.index(files_for(root))
    assert states.stats['hits'] == 1 and second.stats['parsed'] == 1 and second.stats['cached'] == 1
    assert [d['path'] for d in second.definitions('gamma')] == ['b.py'] and second is not first

def test_a_refresh_equals_a_cold_rebuild_after_edits_adds_and_deletes(tmp_path):
    root, states = make(tmp_path), RepoStates()
    states.index(files_for(root))
    (root / 'a.py').write_text('def alpha():\n    return 2\n\ndef delta():\n    pass\n')
    (root / 'c.py').write_text('def gamma():\n    return delta()\n')
    (root / 'b.py').unlink()
    warm = states.index(files_for(root))
    cold = CodeIndex.load(files_for(root))
    strip = lambda ix: {p: {k: v for k, v in info.items() if k != 'sig'} for p, info in ix.data.items()}  # noqa: E731
    assert strip(warm) == strip(cold) and 'b.py' not in warm.data

def test_the_cache_is_bounded_and_can_be_dropped(tmp_path):
    states = RepoStates(limit=2)
    roots = [make(tmp_path, n) for n in 'abc']
    for r in roots:
        states.index(files_for(r))
    assert len(states.states) == 2 and str(roots[0].resolve()) not in states.states
    states.forget(roots[1])
    assert len(states.states) == 1
    states.forget()
    assert not states.states
