"""Recorded command evidence, independent of model availability."""
import json
import re
from .report import parse_report


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
    report = ('LOCAL_WORKER_REPORT\nStatus: '+status+'\nFindings:\nRecorded command outcomes; no model inference.\n'
              'Files:\nNone edited by the helper. Trusted checks may produce artifacts.\nChecks:\n'+
              ('\n'.join(descriptions) or 'Not run.')+'\nRisks:\n'+
              ('Failed, blocked or skipped checks require frontier review.' if status != 'COMPLETE' else 'Frontier acceptance is still required.')+
              '\nEND_LOCAL_WORKER_REPORT')
    return parse_report(report)


def analysis_evidence(checks, budget=12000):
    compact = [{k:v for k,v in c.items() if k not in ('output_tail','argv','cwd')} for c in checks]
    text = json.dumps(compact, ensure_ascii=True)
    for c in checks:
        if failed(c): text += '\n'+c['name']+' recorded log excerpt:\n'+c.get('output_tail','')[:3000]
    return text[:budget] + '\n[Evidence excerpt is bounded; saved logs are authoritative. Omitted logs were not inspected.]'
