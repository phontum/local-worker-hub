"""Junior-task evaluation: the delegation shapes a frontier agent hands to the local worker.

Each case copies a fixture project (benchmarks/fixtures/junior/<project>) into a fresh git repository under the private
state directory, runs one job and grades it mechanically: gold path:line references for discovery, regexes for
explanations, hidden checks for edits (the worker never sees them), failing-then-passing tests for regression tests.
The model is chosen per request (JobRequest.model) and verified from the job's effective-config events, so a step that
silently stays on another model invalidates the row instead of skewing the comparison.
Results go to private hub state. Usage:
  .venv/bin/python benchmarks/eval_junior.py [--model gemma|qwen] [--thinking on|off] [--repeat N] [--shape S ...] [--id ID ...] [--label L]
  .venv/bin/python benchmarks/eval_junior.py --compare RESULTS_A RESULTS_B   # apply the pre-registered decision rule
Decision rule (docs in benchmarks/RESULTS.md): correct-and-honest rate first, then false-COMPLETE count, median seconds, tokens;
switch the default only if the challenger leads the primary metric by >= 10 points, or leads on false-COMPLETE without losing on correctness.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hub.client import call
from hub.model_registry import model_name
from hub.models import JobRequest
from hub.settings import STATE

HERE = Path(__file__).parent
CASES = HERE / 'evals' / 'junior.jsonl'
FIXTURES = HERE / 'fixtures' / 'junior'
CITATION = re.compile(r'(?<![\w./-])([\w./-]+\.(?:py|js|ts|tsx|md|json)):(\d+)(?:\s*[-–]\s*(\d+))?')
IGNORED = ('__pycache__', '.pytest_cache', '_hidden_')

def load_cases(shapes=None, ids=None):
    rows = [json.loads(line) for line in CASES.read_text().splitlines() if line.strip()]
    return [c for c in rows if (not shapes or c['shape'] in shapes) and (not ids or c['id'] in ids)]

def sub(argv):
    return [sys.executable if part == '{py}' else part for part in argv]

def make_repo(root, case):
    repo = root / case['id']
    shutil.copytree(FIXTURES / case['project'], repo, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
    (repo / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n_hidden_*\n')
    for step in (['init', '-q'], ['add', '-A'], ['-c', 'user.name=eval', '-c', 'user.email=eval@example.invalid', 'commit', '-qm', 'fixture']):
        subprocess.run(['git', '-C', str(repo)] + step, check=True, capture_output=True)
    return repo

def wait(ident, limit):
    started = time.monotonic()
    while True:
        status = call('GET', f'/api/jobs/{ident}/wait?timeout_seconds=25')
        if status['state'] not in ('queued', 'running'):
            return status
        if time.monotonic() - started > limit:
            call('POST', f'/api/jobs/{ident}/cancel')
            return {'state': 'cancelled'}

def effective_models(ident):
    events = call('GET', f'/api/jobs/{ident}/events?limit=1000')
    return sorted({(e.get('data') or {}).get('model') for e in events if e['kind'] == 'effective-config'} - {None})

def tokens(result):
    usage = result.get('usage') or {}
    return sum(int(usage.get(k) or 0) for k in ('input', 'output', 'reasoning'))

def request_for(case, repo, args):
    kwargs = dict(role=case['role'], repo=str(repo), task=case['task'], execution_preset='work', timeout=case.get('timeout', 240),
                  idempotency_key=uuid.uuid4().hex, caller='eval', model=args.model)
    if not args.no_kind:
        kwargs['kind'] = case['shape']
    if args.thinking:
        kwargs['model_thinking'] = args.thinking == 'on'
    if case['role'] == 'editor':
        kwargs.update(allowed_paths=case['allowed_paths'], workflow=case.get('workflow', 'single'))
    if case.get('checks'):
        kwargs['checks'] = [{'name': c['name'], 'argv': sub(c['argv'])} for c in case['checks']]
    if case.get('repair_attempts'):
        kwargs['repair_attempts'] = case['repair_attempts']
    for pair in getattr(args, 'request', None) or []:  # experiment options, e.g. match_mode=line; checked afterwards against the job's own records
        key, _, value = pair.partition('=')
        kwargs[key] = json.loads(value) if value[:1] in '0123456789tfn[{"' else value
    return JobRequest(**kwargs)

def changed_files(repo):
    out = subprocess.run(['git', '-C', str(repo), 'status', '--porcelain', '-uall'], capture_output=True, text=True).stdout
    return sorted(line[3:] for line in out.splitlines() if not any(i in line for i in IGNORED))

def run_hidden(repo, hidden):
    if 'argv' in hidden:
        proc = subprocess.run(sub(hidden['argv']), cwd=repo, capture_output=True, text=True, timeout=120)
    else:
        source = FIXTURES / 'hidden' / hidden['file']
        target = repo / ('_hidden_' + source.name)
        shutil.copyfile(source, target)
        try:
            runner = ['node'] if source.suffix == '.js' else [sys.executable]
            proc = subprocess.run(runner + [target.name], cwd=repo, capture_output=True, text=True, timeout=120)
        finally:
            target.unlink(missing_ok=True)
    return proc.returncode == 0, (proc.stdout + proc.stderr)[-400:]

def pytest_failures(repo, test_file):
    proc = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', test_file], cwd=repo, capture_output=True, text=True, timeout=120)
    failed = set(re.findall(r'FAILED \S+::(\w+)', proc.stdout))
    counts = {k: int(n) for n, k in re.findall(r'(\d+) (passed|failed|error)', proc.stdout)}
    return failed, sum(counts.values())

def grade_regression(repo, spec, tmp):
    failed, total = pytest_failures(repo, spec['test_file'])
    fixed = tmp / 'fixed'
    shutil.copytree(repo, fixed, ignore=shutil.ignore_patterns('.git'))
    fix = json.loads((FIXTURES / 'hidden' / spec['fix']).read_text())
    target = fixed / fix['path']
    text = target.read_text()
    if fix['search'] not in text:
        return False, 'reference fix did not apply'
    target.write_text(text.replace(fix['search'], fix['replace'], 1))
    failed_fixed, _ = pytest_failures(fixed, spec['test_file'])
    known = set(spec['known_failing'])
    new_fail_on_bug = bool(failed - known)
    passes_when_fixed = not (failed_fixed - known)
    more_tests = total > spec['baseline_tests']
    return new_fail_on_bug and passes_when_fixed and more_tests, f'fails_on_bug={new_fail_on_bug} passes_fixed={passes_when_fixed} new_tests={more_tests}'

def citations(text, repo):
    """Cited path:line references; a bad one names a file that does not exist or a line past its end."""
    good, bad = [], []
    for path, start, end in CITATION.findall(text):
        found = [p for p in repo.rglob(Path(path).name) if '.git' not in p.parts and p.is_file()] if not (repo / path).is_file() else [repo / path]
        found = [p for p in found if str(p.relative_to(repo)).endswith(path.lstrip('./'))]
        lines = len(found[0].read_text(errors='replace').splitlines()) if found else 0
        (good if found and int(start) <= lines else bad).append((str(found[0].relative_to(repo)) if found else path, int(start), int(end or start)))
    return good, bad

LOOSE_LINES = re.compile(r'(?:\blines?\s+|:|\bL)(\d+)(?:\s*[-–]\s*(\d+))?', re.I)

def gold_recall(case, repo, good, report=''):
    """A gold reference counts when its file is named and either a nearby line number is cited in any common form
    (path:N, line N, [E3:N]) or the gold line's own text is quoted. Strict path:line citations are checked separately."""
    gold = case.get('gold_refs', [])
    loose = [(int(a), int(b or a)) for a, b in LOOSE_LINES.findall(report)]
    hits = 0
    for ref in gold:
        file = repo / ref['path']
        if not file.is_file():
            continue
        line = next((i for i, text in enumerate(file.read_text().splitlines(), 1) if ref['contains'] in text), None)
        if line is None:
            continue
        strict = any(p == ref['path'] and s - 2 <= line <= e + 2 for p, s, e in good)
        named = Path(ref['path']).name in report
        near = any(s - 2 <= line <= e + 2 for s, e in loose)
        hits += strict or (named and (near or ref['contains'] in report))
    return hits, len(gold)

def requirement_statuses(text):
    parts = re.split(r'(?m)^\W*(?:requirement\s*)?(\d)\b[.):]?', text)
    out = {}
    for number, body in zip(parts[1::2], parts[2::2]):
        first = body[:400].lower()
        out.setdefault(number, 'unmet' if re.search(r'\bunmet\b|not met|not implemented|\bnot\b.{0,20}\b(?:enforced|implemented|honou?red)|\bno\b.{0,20}(?:guard|check|override|validation)|missing', first)
                       else 'met' if re.search(r'\bmet\b|implemented|satisfied', first) else 'unknown')
    return out

def single_status(text):
    low = text.lower()
    if re.search(r'\bunmet\b|not met|not implemented|\bnot\b.{0,30}(?:implemented|enforced)|no (?:validation|guard|check)', low):
        return 'unmet'
    return 'met' if re.search(r'\bmet\b|implemented|expires?', low) else 'unknown'

def grade(case, repo, result, ident, tmp):
    report = result.get('report') or result.get('answer') or result.get('error') or ''
    detail = {}
    if case['role'] == 'editor':
        changed = changed_files(repo)
        scope_ok = all(path in case['allowed_paths'] for path in changed)
        if case.get('regression'):
            ok, why = grade_regression(repo, case['regression'], tmp)
        else:
            ok, why = run_hidden(repo, case['hidden'])
        detail = {'changed': changed, 'scope_ok': scope_ok, 'why': why}
        return bool(changed) and scope_ok and ok, detail
    text = report
    if case['role'] == 'validator':
        # What the frontier sees without opening artifacts: the brief/summary report and check results, not raw logs.
        text = report + json.dumps(result.get('checks') or [])
    good, bad = citations(report, repo)
    hits, total = gold_recall(case, repo, good, report) if case.get('gold_refs') else (0, 0)
    ok = hits == total and not bad
    ok = ok and all(re.search(p, text, re.I | re.S) for p in case.get('must_match', []))
    ok = ok and not any(re.search(p, text, re.I) for p in case.get('must_not_match', []))
    if case.get('requirements'):
        expect = case['requirements']
        if '*' in expect:
            ok = ok and single_status(report) == expect['*']
        else:
            got = requirement_statuses(report)
            ok = ok and all(got.get(n) == s for n, s in expect.items())
            detail['requirements'] = got
    detail.update(gold=f'{hits}/{total}', bad_citations=[f'{p}:{s}' for p, s, _ in bad])
    if case['role'] == 'validator':
        logs = ''.join(p.read_text(errors='replace') for p in (STATE / 'jobs' / ident).glob('check-*.log'))
        detail['in_logs'] = all(re.search(p, logs, re.I) for p in case.get('must_match', []))
    return ok, detail

def run_case(case, attempt, args, root):
    repo = make_repo(root, {**case, 'id': f"{case['id']}-{attempt}"})
    started = time.monotonic()
    ident = call('POST', '/api/jobs', request_for(case, repo, args).model_dump())['id']
    status = wait(ident, case.get('timeout', 240) + 240)
    result = call('GET', f'/api/jobs/{ident}/result?view=summary') or {}
    seconds = round(time.monotonic() - started, 1)
    workspace_info = {}
    if case['role'] == 'editor' and result.get('workspace'):
        # Editors work in a private copy: the repository must be untouched until the frontier applies, so grade after a real apply.
        workspace_info = {'origin_unchanged': result['workspace'].get('origin_unchanged'), 'changed_before_apply': changed_files(repo)}
        try:
            applied = call('POST', f'/api/jobs/{ident}/apply')
            workspace_info.update(applied=applied.get('applied'), conflicts=applied.get('conflicts'))
        except Exception as exc:
            workspace_info['apply_error'] = repr(exc)[:200]
    models = effective_models(ident)
    expected = model_name(args.model) if args.model else None
    try:
        ok, detail = grade(case, repo, result, ident, root / f"tmp-{case['id']}-{attempt}")
    except Exception as exc:  # a grader bug must not end the run or count as a pass
        ok, detail = False, {'grader_error': repr(exc)}
    if (result.get('acceptance') or {}).get('regression') is not None:
        detail['host_reproduces_bug'] = result['acceptance']['regression'].get('reproduces_bug')  # the host's own red check, compared with the hidden reference grade
    if workspace_info:
        detail['workspace'] = workspace_info
        ok = ok and bool(workspace_info.get('origin_unchanged')) and not workspace_info['changed_before_apply']
    state = result.get('worker_status')
    return {'id': case['id'], 'shape': case['shape'], 'role': case['role'], 'attempt': attempt, 'job': ident, 'seconds': seconds,
            'state': status['state'], 'status': state, 'correct': ok, 'false_complete': state == 'COMPLETE' and not ok,
            'honest': ok or state != 'COMPLETE', 'tokens': tokens(result), 'models': models,
            'model_ok': not expected or case['role'] == 'validator' or models == [expected], 'detail': detail, 'answer': (result.get('report') or '')[:1500]}

def pct(part, whole):
    return round(100 * part / whole, 1) if whole else 0.0

def summarize(rows):
    valid = [r for r in rows if r['model_ok']]
    times = sorted(r['seconds'] for r in valid)
    out = {'runs': len(rows), 'invalid_model_rows': len(rows) - len(valid), 'correct': sum(r['correct'] for r in valid), 'valid': len(valid),
           'correct_pct': pct(sum(r['correct'] for r in valid), len(valid)), 'honest_pct': pct(sum(r['honest'] for r in valid), len(valid)),
           'false_complete': sum(r['false_complete'] for r in valid), 'median_seconds': times[len(times) // 2] if times else None,
           'tokens': sum(r['tokens'] for r in valid), 'by_shape': {}}
    for shape in sorted({r['shape'] for r in valid}):
        subset = [r for r in valid if r['shape'] == shape]
        out['by_shape'][shape] = f"{sum(r['correct'] for r in subset)}/{len(subset)}"
    return out

def compare(a_dir, b_dir):
    a, b = (json.loads((Path(d) / 'results.json').read_text()) for d in (a_dir, b_dir))
    sa, sb = a['summary'], b['summary']
    print(json.dumps({'incumbent': {'model': a['model'], **sa}, 'challenger': {'model': b['model'], **sb}}, indent=2))
    lead = sb['correct_pct'] - sa['correct_pct']
    fc_better = sb['false_complete'] < sa['false_complete'] and sb['correct_pct'] >= sa['correct_pct']
    verdict = 'SWITCH to challenger' if lead >= 10 or fc_better else 'KEEP incumbent'
    print(f"correct lead {lead:+.1f} points; false-COMPLETE {sa['false_complete']} vs {sb['false_complete']}: {verdict}")
    if sa['invalid_model_rows'] or sb['invalid_model_rows']:
        print('WARNING: rows with an unexpected effective model were excluded; rerun them before trusting this.')

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--model', help='Registered model alias sent with every request (default: the hub default)')
    parser.add_argument('--thinking', choices=['on', 'off'])
    parser.add_argument('--no-kind', action='store_true', help='Do not send the job kind (earlier runs did not)')
    parser.add_argument('--request', action='append', metavar='KEY=VALUE', help='Extra request field (repeatable), e.g. match_mode=line or edit_format=json')
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--shape', nargs='+')
    parser.add_argument('--id', nargs='+')
    parser.add_argument('--label', default='')
    parser.add_argument('--compare', nargs=2, metavar=('A', 'B'))
    args = parser.parse_args()
    if args.compare:
        return compare(*args.compare)
    root = STATE / 'benchmarks' / ('eval-junior-' + time.strftime('%Y%m%d-%H%M%S') + ('-' + args.label if args.label else ''))
    root.mkdir(parents=True, mode=0o700)
    rows = []
    try:
        for case in load_cases(args.shape, args.id):
            for attempt in range(args.repeat):
                rows.append(run_case(case, attempt, args, root))
                print(json.dumps({k: rows[-1][k] for k in ('id', 'attempt', 'seconds', 'status', 'correct', 'false_complete', 'model_ok')}), flush=True)
    finally:
        (root / 'results.json').write_text(json.dumps({'model': args.model or 'default', 'thinking': args.thinking, 'repeat': args.repeat,
                                                       'rows': rows, 'summary': summarize(rows)}, indent=2, ensure_ascii=False))
    print(json.dumps(summarize(rows), indent=2))
    print('Evidence:', root)

if __name__ == '__main__':
    main()
