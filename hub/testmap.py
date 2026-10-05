"""Relevant-test selection: which test files are likely to exercise a change, from the host's own evidence, and a way to turn that into an approved check.

Signals, strongest first: test files that import the changed file (directly, or through one more module), test files named after it (test_x.py, x_test.go, x.test.ts),
test files that mention symbols the changed files define, and what earlier jobs showed (a change to this file made that test file fail). Everything is derived
from the index and recorded job results; no model is involved and nothing is executed here.

The model never writes a command. `fill_template` only substitutes validated test paths for the `{tests}` element of an argv that a reviewed project profile already
approves; a template left unfilled is refused by `refuse_unfilled` so the literal placeholder can never run."""
import re
from collections import defaultdict
from pathlib import Path
from .codeindex import TESTISH

PLACEHOLDER = '{tests}'
SAFE_PATH = re.compile(r'^[A-Za-z0-9_][\w./@+-]*$')
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

def valid_test_paths(paths, index):
    """Test paths that are safe to put on an argv: indexed test files, no option-like or shell-like text."""
    return [p for p in paths if SAFE_PATH.match(p) and p in index.data and is_test(p) and not p.startswith('-')]

def fill_template(check, tests, index):
    """The approved check with `{tests}` replaced by the validated test paths, or None when the check is not a template or no path survives."""
    argv = list(check['argv'] if isinstance(check, dict) else check.argv)
    if argv.count(PLACEHOLDER) != 1:
        return None
    safe = valid_test_paths(tests, index)
    if not safe:
        return None
    position = argv.index(PLACEHOLDER)
    data = dict(check if isinstance(check, dict) else check.model_dump())
    data['argv'] = argv[:position] + safe + argv[position + 1:]
    data['name'] = data['name'] + ' (selected)'
    return data

def refuse_unfilled(argv):
    """True when an argv still holds the template placeholder; such a command must never be executed."""
    return any(PLACEHOLDER in part for part in argv)

def recommend(index, changed, templates, history=None, limit=8):
    """Ranked tests plus, for each approved template check, the concrete check to run on them (never a model-written command)."""
    ranked = select(index, changed, history, limit)
    paths = [r['path'] for r in ranked]
    checks = [filled for t in templates if (filled := fill_template(t, paths, index))]
    return {'tests': ranked, 'checks': checks, 'note': 'derived from imports, names, mentioned symbols and recorded failures; run the full suite when unsure'}
