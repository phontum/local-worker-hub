"""Small everyday evaluation: personal questions, repository lookups and the delegation fixtures, under ~15 min per model.

Mechanical checks only (expected strings, web use, units, fabricated links). Current facts still need a human or
frontier look at the saved answers and sources. Results go to private hub state, never into the repository.
Usage: .venv/bin/python benchmarks/eval_small.py [--model gemma|qwen] [--groups personal lookup fixtures]
"""
import argparse
import json
import os
import re
import shutil
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hub.client import call
from hub.models import JobRequest
from hub.settings import CONFIG, STATE

REPO = str(Path(__file__).resolve().parents[1])
# id, task, needs web (None = either), regexes that must match the answer (empty = graded by hand), case-insensitive
PERSONAL = [
    ('fact-wall', 'In which year did the Berlin Wall fall?', None, [r'1989']),
    ('fact-novel', "Who wrote the novel 'The Master and Margarita'?", None, [r'Bulgakov']),
    ('fact-fahrenheit', 'What is the boiling point of water at sea level in degrees Fahrenheit?', None, [r'212']),
    ('fact-capital', 'What is the capital of Australia?', None, [r'Canberra']),
    ('now-weather-ns', "What's the weather in Novi Sad today? I want to be outside from 15:00 to 20:00.", True, []),
    ('now-weather-msk', 'Какая сейчас погода в Москве?', True, []),
    ('now-price-gpu', 'What is the cheapest brand new RTX 5070 in Serbia right now? It must be in stock.', True, []),
    ('now-python', 'What is the latest stable Python 3 release?', True, []),
    ('lang-sr-time', 'Koliko je sada sati u Tokiju?', None, []),
    ('lang-de-fact', 'Wie hoch ist die Zugspitze?', None, [r'2[ .,]?96\d']),
    ('chat-hello', 'Hello! How are you today?', False, []),
    ('chat-clarify', "What's the weather like?", False, []),
]
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
                 'ask-decide', 'ask-answer', 'localize', 'investigate-answer', 'textedit')

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

def personal(rows):
    for key, task, web, expected in PERSONAL:
        request = JobRequest(role='personal', task=task, execution_preset='work', timeout=120, idempotency_key=uuid.uuid4().hex, caller='eval')
        ident, status, result, seconds = run_job(request, 240)
        answer = result.get('answer') or result.get('report') or result.get('error') or ''
        searches, fetches = web_counts(ident)
        seen = observed_text(ident)
        links = re.findall(r'https?://[^\s)\]>,]+', answer)
        fabricated = [u for u in links if u.rstrip('.') not in seen]
        rows.append({'group': 'personal', 'id': key, 'job': ident, 'seconds': seconds, 'state': status['state'], 'status': result.get('worker_status'),
                     'searches': searches, 'fetches': fetches, 'web_ok': web is None or (web == bool(searches)),
                     'expected_ok': all(re.search(p, answer, re.I) for p in expected) if expected else None,
                     'units_ok': not UNITS.search(answer) or key == 'fact-fahrenheit', 'fabricated_links': fabricated,
                     'answer': answer[:1200]})
        print(json.dumps({k: rows[-1][k] for k in ('id', 'seconds', 'status', 'searches', 'fetches', 'web_ok', 'expected_ok', 'units_ok', 'fabricated_links')}), flush=True)

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

def summary(rows):
    out = {}
    p = [r for r in rows if r['group'] == 'personal']
    if p:
        times = sorted(r['seconds'] for r in p)
        out['personal'] = {'n': len(p), 'median_seconds': times[len(times) // 2], 'web_ok': sum(r['web_ok'] for r in p),
                           'expected_ok': f"{sum(1 for r in p if r['expected_ok'])}/{sum(1 for r in p if r['expected_ok'] is not None)}",
                           'units_ok': sum(r['units_ok'] for r in p), 'fabricated': sum(bool(r['fabricated_links']) for r in p),
                           'complete': sum(r['status'] == 'COMPLETE' for r in p)}
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
    rows = []
    try:
        if 'personal' in args.groups: personal(rows)
        if 'lookup' in args.groups: lookups(rows)
        if 'fixtures' in args.groups:
            sys.path.insert(0, str(Path(__file__).parent))
            fixtures(rows, root)
    finally:
        if args.model:
            shutil.copyfile(backup, roles)
        (root / 'results.json').write_text(json.dumps({'model': args.model or 'default', 'rows': rows, 'summary': summary(rows)}, indent=2, ensure_ascii=False))
    print(json.dumps(summary(rows), indent=2))
    print('Evidence:', root)

if __name__ == '__main__':
    main()
