"""Recorded command evidence, independent of model availability."""
import json
import os
import re
import subprocess
import time
from pathlib import Path
from hub.report import parse_report
from hub.skills.coding.execution.testparse import parse as parse_output
from .templates import refuse_unfilled


def tail(path, limit=10000):
    with path.open('rb') as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - limit))
        return f.read().decode('utf-8', errors='replace')


def test_counts(text):
    # Only recognized final summaries; an exit code remains authoritative.
    match = re.search(r'Ran (\d+) tests? in [\d.]+s\s*\n\s*(OK(?:\s*\(skipped=(\d+)\))?|FAILED\s*\(([^)]*)\))', text)
    if match:
        total = int(match[1]); details = dict(re.findall(r'(failures|errors|skipped)=(\d+)', match[4] or ''))
        failed = int(details.get('failures', 0)) + int(details.get('errors', 0))
        skipped = int(match[3] or details.get('skipped', 0))
        return {'total': total, 'passed': total - failed - skipped, 'failed': failed, 'skipped': skipped, 'format': 'unittest'}
    total = re.search(r'^# tests (\d+)\s*$', text, re.M)
    if total:
        values = {k: re.search(r'^# '+k+r' (\d+)\s*$', text, re.M) for k in ('pass','fail','skipped','cancelled','todo')}
        if all(values.values()):
            return {'total':int(total[1]), 'passed':int(values['pass'][1]), 'failed':int(values['fail'][1]),
                    'skipped':int(values['skipped'][1]), 'cancelled':int(values['cancelled'][1]), 'todo':int(values['todo'][1]), 'format':'node-test'}
    match = re.search(r'^\s*Tests\s+([^\n]+)', text, re.M)
    if match:
        values = dict((name,int(n)) for n,name in re.findall(r'(\d+)\s+(passed|failed|skipped|todo)', match[1]))
        total = re.search(r'\((\d+)\)', match[1])
        if total and values:
            return {'total':int(total[1]), **{name:values.get(name,0) for name in ('passed','failed','skipped','todo')}, 'format':'vitest'}
    return None


def parsed_output(output, argv=None, root=None):
    """Counts (only when the older summary parsers do not know the format) and structured failures from check output."""
    found = parse_output(output, argv, root)
    counts = test_counts(output)
    if counts is None and found['tool'] == 'pytest' and found['counts']:
        c = found['counts']; failed_n = c.get('failed', 0) + c.get('error', 0)
        counts = {'total': sum(c.values()), 'passed': c.get('passed', 0), 'failed': failed_n, 'skipped': c.get('skipped', 0), 'format': 'pytest'}
    return counts, found['failures']

def parsed_log(path, argv, root):
    """Read bounded head and tail so an early failure survives a huge assertion dump."""
    with Path(path).open('rb') as stream:
        data=stream.read(2_000_001)
        if len(data)>2_000_000:
            stream.seek(-1_000_000,2)
            data=data[:1_000_000]+b'\n[Middle of log omitted]\n'+stream.read(1_000_000)
    return parsed_output(data.decode('utf-8','replace'),argv,root)

def expected_files(check, root):
    root=Path(root).resolve()
    for name in check.expected_test_files:
        target=(root/name).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return 'Expected test file missing or outside repository: '+name
    return None

def verify_check_coverage(check, result):
    if result.get('status')!='passed' or check.minimum_tests is None:return result
    counts=result.get('counts') or {}
    if counts.get('total',0)<check.minimum_tests:
        result.update(status='failed',reason=f"Expected at least {check.minimum_tests} tests; observed {counts.get('total', 'no recognized test count')}")
    return result


def failure_lines(check, limit=10, width=160):
    """One line per parsed failure: test id, file:line, first assertion text."""
    rows = check.get('failures') or []
    lines = []
    for f in rows[:limit]:
        where = f"{f['file']}:{f['line']}" if f.get('file') and f.get('line') else (f.get('file') or '')
        lines.append(f"  FAIL {f['test_id']}" + (f' ({where})' if where else '') + (' — ' + f['message'][:width] if f.get('message') else ''))
    if len(rows) > limit:
        lines.append(f'  ... {len(rows) - limit} more failures in the artifact')
    return lines


def failed(check):
    return check.get('status') in ('failed','blocked','skipped','cancelled') or check.get('exit_code') != 0 or check.get('timed_out', False)


def direct_report(checks):
    status = 'PARTIAL' if not checks or any(failed(c) for c in checks) else 'COMPLETE'
    descriptions = []
    for c in checks:
        line = f"{c['name']}: {c.get('status', 'passed' if c.get('exit_code') == 0 else 'failed')}"
        if c.get('reason'): line += ' — ' + c['reason'][:500]
        if c.get('counts'): line += ' ' + json.dumps(c['counts'])
        line += f"; exit={c.get('exit_code')}; artifact={c.get('artifact') or 'None'}"
        descriptions.append(line)
        descriptions.extend(failure_lines(c))
    report = ('LOCAL_WORKER_REPORT\nStatus: '+status+'\nFindings:\nRecorded command outcomes; no model inference.\n'
              'Files:\nNone edited by the helper. Trusted checks may produce artifacts.\nChecks:\n'+
              ('\n'.join(descriptions) or 'Not run.')+'\nRisks:\n'+
              ('Failed, blocked or skipped checks require frontier review.' if status != 'COMPLETE' else 'Frontier acceptance is still required.')+
              '\nEND_LOCAL_WORKER_REPORT')
    return parse_report(report)


def analysis_evidence(checks, budget=12000):
    compact = [{k:v for k,v in c.items() if k not in ('output_tail','argv','cwd','failures')} for c in checks]
    text = json.dumps(compact, ensure_ascii=True)
    for c in checks:
        if failed(c):
            parsed = failure_lines(c, limit=20, width=300)
            # Parsed failures say what broke; keep only a short raw tail beside them.
            text += '\n'+c['name']+(' parsed failures:\n'+'\n'.join(parsed)+'\n' if parsed else '')+' recorded log excerpt:\n'+c.get('output_tail','')[-(1500 if parsed else 3000):]
    return text[:budget] + '\n[Evidence excerpt is bounded; saved logs are authoritative. Omitted logs were not inspected.]'


def run_check_sync(check, root, cap=120):
    """Run one approved check synchronously in `root` (used when a finished job's checks are re-run, which the job runner is not involved in).
    Same argv, working-directory boundary and environment rules as the job runner; checks that need its extra machinery are reported as blocked."""
    result = {'name': check.name, 'exit_code': None, 'timed_out': False, 'status': 'blocked', 'seconds': 0, 'output_tail': '', 'counts': None, 'failures': []}
    if refuse_unfilled(check.argv):
        return result | {'reason': 'This check is a test-selection template ({tests}); fill it with recommend_checks first'}
    root = Path(root).resolve()
    cwd = (root / check.cwd).resolve()
    if not cwd.is_dir() or not (cwd == root or root in cwd.parents):
        return result | {'reason': 'Check cwd is not a directory inside the repository'}
    missing=expected_files(check,root)
    if missing:return result | {'reason':missing}
    if check.depends_on or check.requires_test_database or check.guard_next_dev or check.required_env:
        return result | {'reason': 'This check needs the job runner (dependencies, test database, dev-server guard or required environment); run it yourself'}
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'USER', 'LANG', 'LC_ALL') or k in check.env_allowlist}
    env.update(check.environment)
    env.setdefault('PYTHONDONTWRITEBYTECODE', '1')
    started = time.monotonic()
    try:
        done = subprocess.run(check.argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=min(check.timeout, cap))
        output = (done.stdout + done.stderr).decode('utf-8', errors='replace')
        counts, failures = parsed_output(output[:1_000_000]+'\n'+output[-1_000_000:] if len(output)>2_000_000 else output, check.argv, str(cwd))
        result.update(exit_code=done.returncode, status='passed' if done.returncode == 0 else 'failed', output_tail=output[-10000:], counts=counts, failures=failures if done.returncode else [])
        verify_check_coverage(check,result)
    except subprocess.TimeoutExpired:
        result.update(timed_out=True, status='failed', reason=f'Timed out after {min(check.timeout, cap)}s')
    except OSError as error:
        result.update(reason=str(error)[:300])
    result['seconds'] = round(time.monotonic() - started, 2)
    return result
