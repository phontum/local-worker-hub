"""Small everyday evaluation: personal questions, repository lookups and the delegation fixtures, under ~15 min per model.

Mechanical checks only (expected strings, web use, units, fabricated links). Current facts still need a human or
frontier look at the saved answers and sources. Results go to private hub state, never into the repository.
Usage: .venv/bin/python benchmarks/eval_small.py [--model gemma|qwen] [--groups personal lookup fixtures] [--repeat N] [--category C ...]
Personal cases live in benchmarks/evals/personal.jsonl. For a repeatable web, run once with
--web record, then --web replay (same --fixture-set): search results and pages are stored under the state directory and served
back, so only the model varies (hub/web_fixtures.py). The harness writes CONFIG/web-fixtures.json for the run and restores it.
"""
import argparse
import json
import os
import re
import shutil
import signal
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hub.client import call
from hub.models import JobRequest
from hub.settings import CONFIG, STATE

REPO = str(Path(__file__).resolve().parents[1])
CASES = Path(__file__).parent / 'evals' / 'personal.jsonl'

def load_cases(categories=None):
    cases = [json.loads(line) for line in CASES.read_text().splitlines() if line.strip()]
    return [c for c in cases if not categories or c['category'] in categories]

LOOKUPS = [
    ('DEFAULT_ALIAS', 'Where is DEFAULT_ALIAS defined in this repository and what is its value? Return path:line.', ['hub/model_registry.py'], r'gemma'),
    ('LOW_MEMORY_MB', 'Where is the default low-memory threshold LOW_MEMORY_MB defined and what is the default in MB? Return path:line.', ['hub/service.py', 'hub/cli.py'], r'2500'),
    ('RETRY_CAP', 'Where is RETRY_CAP defined and what is its value? Return path:line.', ['hub/structured.py'], r'\b2\b'),
    ('MAX_TRACE_BYTES', 'What is the maximum trace size in bytes (MAX_TRACE_BYTES) and where is it set? Return path:line.', ['hub/trace.py'], r'16 \* 1024 \* 1024|16777216|16 ?MiB|16 ?MB'),
    ('clock24', 'Which function converts 12-hour clock times to 24-hour format? Return path:line.', ['hub/preferences.py'], r'clock24'),
    ('queue-limit', 'Where is the "Worker queue is full" limit enforced and how many queued jobs are allowed? Return path:line.', ['hub/store.py'], r'\b32\b'),
]
UNITS = re.compile(r'-?\d+(?:\.\d+)?\s?°\s?F\b|\b\d+(?:\.\d+)?\s?mph\b|\b\d+(?:\.\d+)?\s?miles?\b|\b(?:1[0-2]|0?[1-9])(?::[0-5]\d)?\s?[ap]\.?m\.?(?![\w-])', re.I)
OVERRIDE_KEYS = ('personal', 'researcher', 'investigator', 'editor', 'investigate', 'review', 'answer_review',
                 'ask-decide', 'ask-answer', 'localize', 'investigate-answer')

def wait(ident, limit):
    started = time.monotonic()
    while True:
        status = call('GET', f'/api/jobs/{ident}/wait?timeout_seconds=25')
        if status['state'] not in ('queued', 'running'):
            return status
        if time.monotonic() - started > limit:
            call('POST', f'/api/jobs/{ident}/cancel')
            return {'state': 'cancelled'}

def observed_text(ident):
    """Everything the tools actually returned for this job (search results, fetched pages, file reads)."""
    path = STATE / 'jobs' / ident / 'trace.jsonl'
    if not path.exists():
        return ''
    return ''.join(row['text'] for row in map(json.loads, path.read_text().splitlines()) if row['kind'] in ('tool_result', 'retrieval'))

def web_counts(ident):
    events = call('GET', f'/api/jobs/{ident}/events?limit=1000')
    return sum(e['kind'] == 'web_search' for e in events), sum(e['kind'] == 'web_fetch' for e in events)

def run_job(request, limit):
    started = time.monotonic()
    ident = call('POST', '/api/jobs', request.model_dump())['id']
    status = wait(ident, limit)
    return ident, status, call('GET', f'/api/jobs/{ident}/result?view=summary') or {}, round(time.monotonic() - started, 1)

def actual_route(ident):
    """What the ask pipeline decided, from the job's saved ask.json (older records only have needs_web)."""
    path = STATE / 'jobs' / ident / 'ask.json'
    if not path.exists():
        return None
    decision = json.loads(path.read_text()).get('decision') or {}
    if decision.get('route'):
        return decision['route'] if decision['route'] != 'provider' else 'provider:' + str(decision.get('provider'))
    return 'web' if decision.get('needs_web') else 'direct'

def fixture_misses(ident):
    """Searches in a replay run that found no recorded fixture (the model asked for a query that was never recorded)."""
    path = STATE / 'jobs' / ident / 'tools.jsonl'
    if not path.exists():
        return 0
    events = (json.loads(line) for line in path.read_text().splitlines() if line.strip())
    return sum(1 for e in events if e['kind'] == 'web_search' and e['data'].get('fallback_reason') == 'fixture miss')

def route_ok(expected, actual, coarse):
    """Exact match; coarse accepts web for a provider case, so the baseline before providers exist stays comparable."""
    if actual is None:
        return False
    if expected == actual:
        return True
    return coarse and expected.startswith('provider:') and actual == 'web'

def personal(rows, repeat, categories):
    for case in load_cases(categories):
        for attempt in range(repeat):
            request = JobRequest(role='personal', task=case['task'], execution_preset='work', timeout=120, idempotency_key=uuid.uuid4().hex, caller='eval')
            ident, status, result, seconds = run_job(request, 240)
            answer = result.get('answer') or result.get('report') or result.get('error') or ''
            searches, fetches = web_counts(ident)
            seen = observed_text(ident)
            links = re.findall(r'https?://[^\s)\]>,]+', answer)
            fabricated = [u for u in links if u.rstrip('.') not in seen]
            route = actual_route(ident)
            checks = [bool(re.search(p, answer, re.I)) for p in case['must_match']]
            forbidden = [p for p in case['must_not_match'] if re.search(p, answer, re.I)]
            rows.append({'group': 'personal', 'id': case['id'], 'category': case['category'], 'attempt': attempt, 'job': ident, 'seconds': seconds,
                         'state': status['state'], 'status': result.get('worker_status'), 'route': route, 'expect_route': case['expect_route'],
                         'route_exact': route_ok(case['expect_route'], route, False), 'route_coarse': route_ok(case['expect_route'], route, True),
                         'searches': searches, 'fetches': fetches, 'fixture_misses': fixture_misses(ident), 'requires_current': case['requires_current'],
                         'expected_ok': all(checks) if checks else None, 'forbidden_hits': forbidden,
                         'units_ok': not UNITS.search(answer) or case['category'] == 'units' or case['id'] == 'fact-boil-f', 'fabricated_links': fabricated,
                         'answer': answer[:1200]})
            print(json.dumps({k: rows[-1][k] for k in ('id', 'attempt', 'seconds', 'status', 'route', 'route_exact', 'expected_ok', 'units_ok', 'fabricated_links')}), flush=True)

def lookups(rows):
    for key, task, paths, value in LOOKUPS:
        request = JobRequest(role='investigator', repo=REPO, task=task, execution_preset='work', timeout=180, idempotency_key=uuid.uuid4().hex, caller='eval')
        ident, status, result, seconds = run_job(request, 300)
        report = result.get('report') or result.get('error') or ''
        rows.append({'group': 'lookup', 'id': key, 'job': ident, 'seconds': seconds, 'state': status['state'], 'status': result.get('worker_status'),
                     'path_ok': any(p in report for p in paths), 'value_ok': bool(re.search(value, report)), 'answer': report[:1200]})
        print(json.dumps({k: rows[-1][k] for k in ('id', 'seconds', 'status', 'path_ok', 'value_ok')}), flush=True)

def fixtures(rows, root):
    import run_delegation
    for name in run_delegation.CASES:
        row = run_delegation.run_case(root, name, 'work', 300)
        rows.append({'group': 'fixture', 'id': name, 'job': row['job_id'], 'seconds': row['seconds'], 'state': row['state'],
                     'status': row['worker_status'], 'correct': row['mechanically_correct']})
        print(json.dumps({k: rows[-1][k] for k in ('id', 'seconds', 'status', 'correct')}), flush=True)

def rate(values):
    values = list(values)
    return f"{sum(bool(v) for v in values)}/{len(values)}"

def summary(rows):
    out = {}
    p = [r for r in rows if r['group'] == 'personal']
    if p:
        times = sorted(r['seconds'] for r in p)
        graded = [r for r in p if r['expected_ok'] is not None]
        out['personal'] = {'runs': len(p), 'cases': len({r['id'] for r in p}), 'median_seconds': times[len(times) // 2],
                           'route_exact': rate(r['route_exact'] for r in p), 'route_coarse': rate(r['route_coarse'] for r in p),
                           'expected_ok': rate(r['expected_ok'] for r in graded), 'forbidden_free': rate(not r['forbidden_hits'] for r in p),
                           'units_ok': rate(r['units_ok'] for r in p), 'no_fabricated_links': rate(not r['fabricated_links'] for r in p),
                           'complete': rate(r['status'] == 'COMPLETE' for r in p), 'fixture_misses': sum(r['fixture_misses'] for r in p),
                           'by_category': {c: {'route_exact': rate(r['route_exact'] for r in p if r['category'] == c),
                                               'expected_ok': rate(r['expected_ok'] for r in graded if r['category'] == c)}
                                           for c in sorted({r['category'] for r in p})},
                           'flaky_cases': sorted(i for i in {r['id'] for r in p}
                                                 if len({(r['route'], r['expected_ok']) for r in p if r['id'] == i}) > 1)}
    lk = [r for r in rows if r['group'] == 'lookup']
    if lk:
        out['lookup'] = {'n': len(lk), 'path_ok': sum(r['path_ok'] for r in lk), 'value_ok': sum(r['value_ok'] for r in lk),
                         'median_seconds': sorted(r['seconds'] for r in lk)[len(lk) // 2]}
    fx = [r for r in rows if r['group'] == 'fixture']
    if fx:
        out['fixtures'] = {'n': len(fx), 'correct': sum(bool(r['correct']) for r in fx), 'seconds': round(sum(r['seconds'] for r in fx))}
    return out

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', choices=['gemma', 'qwen'], help='Temporarily select this model for every role (roles.json is restored afterwards)')
    parser.add_argument('--groups', nargs='+', choices=['personal', 'lookup', 'fixtures'], default=['personal', 'lookup', 'fixtures'])
    parser.add_argument('--label', default='')
    parser.add_argument('--web', choices=['record', 'replay'], help='Record live web results into a fixture set, or replay them without network')
    parser.add_argument('--fixture-set', default='baseline', help='Name of the fixture set under the state directory (default: baseline)')
    parser.add_argument('--repeat', type=int, default=3, help='Runs per personal case; pass rates, not single runs')
    parser.add_argument('--category', nargs='+', help='Only these personal categories')
    args = parser.parse_args()
    root = STATE / 'benchmarks' / ('eval-small-' + time.strftime('%Y%m%d-%H%M%S') + ('-' + args.label if args.label else ''))
    root.mkdir(parents=True, mode=0o700)
    roles = CONFIG / 'roles.json'
    backup = root / 'roles.json.bak'
    if args.model:
        # Jobs run one at a time and each waits to finish, so the override is in place for every job start.
        shutil.copyfile(roles, backup)
        profiles = json.loads(roles.read_text())
        for key in OVERRIDE_KEYS:
            profiles.setdefault(key, {})['model'] = args.model
        roles.write_text(json.dumps(profiles, indent=2))
    fixtures_file = CONFIG / 'web-fixtures.json'
    previous = fixtures_file.read_text() if fixtures_file.exists() else None
    if args.web:
        fixtures_file.write_text(json.dumps({'mode': args.web, 'dir': str(STATE / 'benchmarks' / ('web-fixtures-' + args.fixture_set)), 'expires': time.time() + 6 * 3600}))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # run the finally block so the fixture config and roles.json are restored
    rows = []
    try:
        if 'personal' in args.groups: personal(rows, args.repeat, args.category)
        if 'lookup' in args.groups: lookups(rows)
        if 'fixtures' in args.groups:
            sys.path.insert(0, str(Path(__file__).parent))
            fixtures(rows, root)
    finally:
        if args.model:
            shutil.copyfile(backup, roles)
        if args.web:
            fixtures_file.write_text(previous) if previous is not None else fixtures_file.unlink(missing_ok=True)
        (root / 'results.json').write_text(json.dumps({'model': args.model or 'default', 'web_fixtures': args.web and f'{args.web}:{args.fixture_set}', 'repeat': args.repeat, 'rows': rows, 'summary': summary(rows)}, indent=2, ensure_ascii=False))
    print(json.dumps(summary(rows), indent=2))
    print('Evidence:', root)

if __name__ == '__main__':
    main()
