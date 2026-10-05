"""Relevant-test selection: which test files are likely to exercise a change, from the host's own evidence, and a way to turn that into an approved check.

Signals, strongest first: test files that import the changed file (directly, or through one more module), test files named after it (test_x.py, x_test.go, x.test.ts),
test files that mention symbols the changed files define, and what earlier jobs showed (a change to this file made that test file fail). Everything is derived
from the index and recorded job results; no model is involved and nothing is executed here.

`hub.skills.coding.validation.templates` turns the ranking into an approved check."""
from collections import defaultdict
from pathlib import Path
from .codeindex import TESTISH

GENERIC = frozenset({'main', 'init', 'run', 'get', 'set', 'test', 'setup', 'name', 'data', 'value', 'item', 'result', 'config', 'handler'})

def is_test(path):
    return bool(TESTISH.search(path))

def normalized_stem(path):
    stem = Path(path).name.rsplit('.', 1)[0]
    for prefix in ('test_', 'tests_'):
        stem = stem.removeprefix(prefix)
    for suffix in ('_test', '_tests', '.test', '.spec', '_spec'):
        stem = stem.removesuffix(suffix)
    return stem.lower()

def tests_importing(index, path, depth=2):
    """{test path: hops} for test files that import `path` directly or through up to depth-1 intermediate modules."""
    found, frontier, seen = {}, {path}, {path}
    for hop in range(1, depth + 1):
        following = set()
        for target in frontier:
            for other, _ in index.importers(target):
                if other in seen:
                    continue
                seen.add(other)
                if is_test(other):
                    found.setdefault(other, hop)
                else:
                    following.add(other)
        frontier = following
    return found

def history_from_jobs(jobs):
    """{changed file: {test file: times a check failed in that test file after a job changed the file}} from recorded, non-benchmark jobs."""
    seen = defaultdict(lambda: defaultdict(int))
    for job in jobs:
        result = job.get('result') or {}
        if (job['request'].get('caller') or '') in ('eval', 'benchmark', 'legacy-import'):
            continue
        failing = {f['file'] for c in result.get('checks', []) for f in c.get('failures') or [] if f.get('file') and is_test(f['file'])}
        for changed in result.get('changed_files', []):
            for test in failing:
                seen[changed][test] += 1
    return {k: dict(v) for k, v in seen.items()}

def select(index, changed, history=None, limit=8):
    """Ranked [{path, score, reasons}] of test files for the changed paths."""
    scores, reasons = defaultdict(float), defaultdict(list)
    def add(test, amount, why):
        scores[test] += amount
        reasons[test].append(why)
    tests = [p for p in index.data if is_test(p)]
    owners = defaultdict(set)
    for path, info in index.data.items():
        for name, kind, _, _ in info['defs']:
            owners[name].add(path)
    for path in changed:
        stem = normalized_stem(path)
        for test, hops in tests_importing(index, path).items():
            add(test, 3 / hops, f'imports {path}' + (' via another module' if hops > 1 else ''))
        for test in tests:
            if stem and normalized_stem(test) == stem and not is_test(path):
                add(test, 2, f'named after {Path(path).name}')
        names = [n for n, kind, _, _ in index.data.get(path, {}).get('defs', []) if kind in ('function', 'class', 'method') and len(n) >= 5 and n.lower() not in GENERIC and len(owners[n]) <= 2]
        for test in tests:
            hit = [n for n in names if n in index.data[test]['words']]
            if hit:
                add(test, min(len(hit), 3), 'mentions ' + ', '.join(hit[:3]))
        for test, count in (history or {}).get(path, {}).items():
            if test in index.data:
                add(test, 2 * min(count, 3), f'failed {count}x after earlier changes to {path}')
    ranked = sorted(scores, key=lambda t: (-scores[t], t))
    return [{'path': t, 'score': round(scores[t], 2), 'reasons': reasons[t]} for t in ranked[:limit]]
