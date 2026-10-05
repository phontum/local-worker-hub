"""The packet a frontier agent reviews before applying an editor's patch: what changed, what ran, and where to look.

Everything here is derived by the host from the patch, the recorded checks and the workspace; none of it comes from the model.
"""
import re
from .codeindex import TESTISH
from .validation import failed, failure_lines

DEFINITION = re.compile(r'^\+\s*(?:export\s+)?(?:async\s+)?(?:def|class|function|interface|type|struct|enum|func|fn|pub fn)\s+(\w+)|^\+\s*(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?(?:\(|function)')

def diff_stats(patch):
    """Per-file added and removed line counts from a unified diff."""
    stats, current = {}, None
    for line in patch.splitlines():
        if line.startswith('diff --git '):
            current = line.rsplit(' b/', 1)[-1]
            stats[current] = {'added': 0, 'removed': 0}
        elif current and line.startswith('+') and not line.startswith('+++'):
            stats[current]['added'] += 1
        elif current and line.startswith('-') and not line.startswith('---'):
            stats[current]['removed'] += 1
    return stats

def review_focus(patch, stats, checks, allowed):
    """Short, concrete things the reviewer should look at, from the diff and the recorded checks only."""
    notes = []
    code = [p for p in stats if not TESTISH.search(p)]
    tests = [p for p in stats if TESTISH.search(p)]
    if code and not tests:
        notes.append('Source changed without a test change: ' + ', '.join(code[:4]))
    if not checks:
        notes.append('No approved check ran; behaviour is unverified')
    elif any(failed(c) for c in checks):
        notes.append('Failing checks: ' + ', '.join(c['name'] for c in checks if failed(c)))
    added = [n for line in patch.splitlines() for groups in DEFINITION.findall(line) for n in groups if n]
    if added:
        notes.append('New definitions: ' + ', '.join(dict.fromkeys(added[:8])))
    big = [p for p, s in stats.items() if s['added'] + s['removed'] > 80]
    if big:
        notes.append('Large change: ' + ', '.join(big[:3]))
    untouched = [p for p in allowed if p not in stats]
    if untouched:
        notes.append('Authorized but unchanged: ' + ', '.join(untouched[:4]))
    return notes[:6]

def build(patch, checks, status, allowed, attempts, workspace_state, outside=None, extra_files=None, remaining=None, regression=None):
    stats = diff_stats(patch)
    outside = outside or []
    failing = [c for c in checks if failed(c)]
    if regression:
        # A regression test must fail on the unfixed code: the failing checks are the point, a passing or broken new test is the problem.
        clean = status == 'COMPLETE' and bool(stats) and regression['reproduces_bug'] and not outside
    else:
        clean = status == 'COMPLETE' and bool(stats) and not failing and not outside and bool(checks)
    return {
        'status': status,
        'workspace': workspace_state,
        'diff': {'files': len(stats), 'added': sum(s['added'] for s in stats.values()), 'removed': sum(s['removed'] for s in stats.values())},
        'changed_files': [{'path': p, **s} for p, s in stats.items()],
        'scope_ok': not outside,
        'outside_scope': outside[:10],
        'extra_files': (extra_files or [])[:10],
        'checks': [{'name': c['name'], 'status': c.get('status'), 'exit_code': c.get('exit_code'), 'counts': c.get('counts'),
                    'failures': [line.strip() for line in failure_lines(c, limit=5, width=120)]} for c in checks],
        'attempts': len(attempts),
        'repair_used': len(attempts) > 1,
        'remaining_issue': remaining,
        'regression': regression,
        'review_focus': (['New test fails as expected on the current code: ' + ', '.join(regression['new_failing'][:4])] if regression and regression['reproduces_bug'] else [])
                       + [n for n in review_focus(patch, stats, [] if regression else checks, allowed) if not (regression and n.startswith('Source changed without'))],
        'next_action': 'review_patch_then_apply_result' if clean else 'frontier_decision',
    }
