"""Editor experiments on a workload that really overflows the output cap.

The incident benchmark's one-job task finishes in about 1,000 tokens, so it cannot tell 4K from 8K output, decomposition from none, or one edit
format from another. This script adds "heavy" jobs: a synthetic file of N handlers (about 25 lines each) and a task that asks for the same
structural change in every one of them, which makes the model re-emit large regions (N=8 needs roughly the 4K cap, N=16 roughly twice that).
Variants are chosen with flags and verified from the job's own records (effective output cap, edit format, turn counts), because the request
model ignores unknown fields silently.
Usage: .venv/bin/python benchmarks/eval_editor_experiments.py --handlers 8 [--output 4096|8192] [--no-continue]
       [--match substring|line] [--repeat N] [--label L]
Measured per run: correct (every handler changed, nothing else damaged), COMPLETE-but-wrong, truncations, continuation turns, generations, output tokens, seconds.
"""
import argparse
import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).parent))
from eval_junior import call, tokens, wait
from hub.models import JobRequest
from hub.settings import STATE

def handler(i):
    return f'''export async function handleAction{i}(ctx: Context, input: Input{i}) {{
  const started = Date.now();
  const record = await ctx.store.load('table{i}', input.id);
  if (!record) {{
    return {{ ok: false, reason: 'missing' }};
  }}
  const merged = {{ ...record, ...input.changes, updatedAt: started }};
  if (merged.locked) {{
    ctx.metrics.increment('action{i}.locked');
    return {{ ok: false, reason: 'locked' }};
  }}
  await ctx.store.save('table{i}', merged);
  ctx.metrics.increment('action{i}.saved');
  const audit = {{
    action: 'action{i}',
    id: input.id,
    at: started,
    by: ctx.user,
  }};
  await ctx.audit.write(audit);
  ctx.metrics.timing('action{i}.ms', Date.now() - started);
  return {{ ok: true, record: merged }};
}}
'''

def component(n):
    rows = ["import type { Context } from './context';", '']
    rows += [f'export interface Input{i} {{\n  id: string;\n  changes: Record<string, unknown>;\n}}\n' for i in range(1, n + 1)]
    rows += [handler(i) for i in range(1, n + 1)]
    return '\n'.join(rows)

def task(n):
    return (f'In every one of the {n} handlers handleAction1 to handleAction{n} in handlers.ts, wrap the whole body in try/catch: on error call ctx.report(error, "actionN") '
            f'(with that handler\'s own number) and return {{ ok: false, reason: \'error\' }}. Keep every handler\'s existing code and behaviour unchanged inside the try block. Change nothing else in the file.')

def make_repo(root, name, n):
    repo = root / name
    (repo / 'src').mkdir(parents=True)
    (repo / 'src' / 'handlers.ts').write_text(component(n))
    for step in (['init', '-q'], ['add', '-A'], ['-c', 'user.name=eval', '-c', 'user.email=eval@example.invalid', 'commit', '-qm', 'fixture']):
        subprocess.run(['git', '-C', str(repo)] + step, check=True, capture_output=True)
    return repo

def judge(repo, n):
    """All handlers wrapped with their own number and everything else intact."""
    text = (repo / 'src' / 'handlers.ts').read_text()
    original = component(n)
    wrapped = sum(bool(re.search(rf"handleAction{i}\(ctx: Context, input: Input{i}\) \{{\s*try \{{", text)) and f"ctx.report(error, 'action{i}')" in text.replace('"', "'") for i in range(1, n + 1))
    intact = all(f"ctx.metrics.increment('action{i}.saved')" in text and f"await ctx.audit.write(audit)" in text for i in range(1, n + 1)) and text.count('export async function') == n and len(text.splitlines()) >= len(original.splitlines())
    return wrapped == n and intact, {'wrapped': wrapped, 'intact': intact}

def big_run(args, root):
    """A gate-tripping task over the incident fixture: many renames plus behaviours across three files."""
    import eval_incidents as inc
    repo = inc.make_repo(root, 'big-' + uuid.uuid4().hex[:6])
    task = (inc.TASK_ALL + ' In ExportPanel.tsx also add a data-testid attribute to each button. Add an aria-label attribute to the table. Add a title attribute to the heading.')
    extra = {}
    for pair in args.request or []:
        key, _, value = pair.partition('=')
        extra[key] = json.loads(value) if value[:1] in '0123456789tfn[{\"' else value
    request = JobRequest(role='editor', repo=str(repo), task=task, allowed_paths=inc.ALLOWED, workflow='single', execution_preset='work', timeout=args.timeout,
                         idempotency_key=uuid.uuid4().hex, caller='eval', model=args.model, skip_gate=args.no_gate, **extra)
    started = time.monotonic()
    ident = call('POST', '/api/jobs', request.model_dump())['id']
    status = wait(ident, args.timeout + 300)
    result = call('GET', f'/api/jobs/{ident}/result?view=summary') or {}
    seconds = round(time.monotonic() - started, 1)
    if result.get('workspace'):
        try:
            call('POST', f'/api/jobs/{ident}/apply')
        except Exception:
            pass
    ok, detail = inc.hidden('oversized', repo)
    panel = (repo / inc.ALLOWED[0]).read_text()
    extras = {'testid': 'data-testid' in panel, 'aria': 'aria-label' in panel, 'title': 'title=' in panel}
    ok = ok and all(extras.values())
    directory = STATE / 'jobs' / ident
    turns = [t for p in sorted(directory.glob('*.turns.json')) for t in json.loads(p.read_text()).get('turns', [])]
    state = result.get('worker_status')
    gate = (result.get('gate') or {})
    return {'job': ident, 'handlers': 'big', 'status': state, 'correct': ok, 'false_complete': state == 'COMPLETE' and not ok, 'honest': ok or state != 'COMPLETE', 'detail': {**detail, **extras},
            'seconds': seconds, 'tokens': tokens(result), 'generations': len(turns), 'truncated_turns': sum(bool(t.get('truncated')) for t in turns), 'output_tokens': [t.get('output_tokens') or 0 for t in turns],
            'output_limits': [], 'continuations': sum(bool(t.get('continuation')) for t in turns),
            'units': len(result.get('proposed_split') or []) or None, 'applied': None, 'report_head': (result.get('report') or '')[:300], 'gate_tripped': gate.get('tripped')}

def run_once(args, root):
    if args.handlers == 0:
        return big_run(args, root)
    n = args.handlers
    repo = make_repo(root, f'heavy{n}-{uuid.uuid4().hex[:6]}', n)
    extra = {}
    for pair in args.request or []:
        key, _, value = pair.partition('=')
        extra[key] = json.loads(value) if value[:1] in '0123456789tfn[{\"' else value
    for flag, field in (('output', 'model_output'), ('match', 'match_mode')):
        if getattr(args, flag):
            extra[field] = getattr(args, flag)
    if args.no_continue:
        extra['continuation'] = False
    request = JobRequest(role='editor', repo=str(repo), task=task(n), allowed_paths=['src/handlers.ts'], workflow='single', execution_preset='work', timeout=args.timeout,
                         idempotency_key=uuid.uuid4().hex, caller='eval', model=args.model, skip_gate=not args.gate, **extra)
    started = time.monotonic()
    ident = call('POST', '/api/jobs', request.model_dump())['id']
    status = wait(ident, args.timeout + 300)
    result = call('GET', f'/api/jobs/{ident}/result?view=summary') or {}
    seconds = round(time.monotonic() - started, 1)
    applied = None
    if result.get('workspace'):
        try:
            applied = call('POST', f'/api/jobs/{ident}/apply').get('applied')
        except Exception:
            applied = []
    ok, detail = judge(repo, n)
    directory = STATE / 'jobs' / ident
    turns = [t for p in sorted(directory.glob('*.turns.json')) for t in json.loads(p.read_text()).get('turns', [])]
    events = call('GET', f'/api/jobs/{ident}/events?limit=1000')
    limits = sorted({(e.get('data') or {}).get('output') for e in events if e['kind'] == 'effective-config'} - {None})
    outputs = [t.get('output_tokens') or 0 for t in turns]
    state = result.get('worker_status')
    return {'job': ident, 'handlers': n, 'status': state, 'correct': ok, 'false_complete': state == 'COMPLETE' and not ok, 'honest': ok or state != 'COMPLETE', 'detail': detail,
            'seconds': seconds, 'tokens': tokens(result), 'generations': len(turns), 'truncated_turns': sum(bool(t.get('truncated')) for t in turns), 'output_tokens': outputs,
            'output_limits': limits, 'continuations': sum(bool(t.get('continuation')) for t in turns),
            'units': (result.get('gate') or {}).get('units') if result.get('gate') else None, 'applied': applied, 'report_head': (result.get('report') or '')[:300]}

def summarize(rows):
    n = len(rows)
    times = sorted(r['seconds'] for r in rows)
    return {'runs': n, 'correct': sum(r['correct'] for r in rows), 'honest': sum(r['honest'] for r in rows), 'false_complete': sum(r['false_complete'] for r in rows),
            'runs_with_truncation': sum(r['truncated_turns'] > 0 for r in rows), 'generations': sum(r['generations'] for r in rows), 'continuations': sum(r['continuations'] for r in rows),
            'median_seconds': times[n // 2] if n else None, 'output_tokens': sum(sum(r['output_tokens']) for r in rows), 'tokens': sum(r['tokens'] for r in rows),
            'output_limits': sorted({l for r in rows for l in r['output_limits']}), 'formats': sorted({f for r in rows for f in r['formats']})}

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--model')
    parser.add_argument('--handlers', type=int, default=8, help='Heavy handlers in the file; 0 runs the gate-tripping "big" case on the incident fixture')
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--output', type=int, choices=[4096, 8192])
    parser.add_argument('--match', choices=['substring', 'line'])
    parser.add_argument('--no-continue', action='store_true')
    parser.add_argument('--gate', action='store_true', help='Leave the complexity gate on (default: skipped so the experiment measures generation, not refusal)')
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('--request', action='append', metavar='KEY=VALUE', help='Extra request field, e.g. match_mode=line')
    parser.add_argument('--no-gate', action='store_true', help='(big case) skip the complexity gate and run the task in one shot')
    parser.add_argument('--label', default='')
    args = parser.parse_args()
    root = STATE / 'benchmarks' / ('eval-editor-exp-' + time.strftime('%Y%m%d-%H%M%S') + ('-' + args.label if args.label else ''))
    root.mkdir(parents=True, mode=0o700)
    rows = []
    try:
        for _ in range(args.repeat):
            rows.append(run_once(args, root))
            print(json.dumps({k: rows[-1][k] for k in ('status', 'correct', 'false_complete', 'generations', 'truncated_turns', 'output_tokens', 'seconds', 'detail')}), flush=True)
    finally:
        (root / 'results.json').write_text(json.dumps({'args': vars(args), 'rows': rows, 'summary': summarize(rows)}, indent=2))
    print(json.dumps(summarize(rows), indent=2))
    print('Evidence:', root)

if __name__ == '__main__':
    main()
