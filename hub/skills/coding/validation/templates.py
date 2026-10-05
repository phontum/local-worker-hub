"""Approved test-selection checks: the `{tests}` template of a reviewed project profile filled with validated test paths, and the guard that keeps an unfilled template from running.

The model never writes a command. `fill_template` only substitutes validated test paths for the `{tests}` element of an argv that a reviewed project profile already
approves; a template left unfilled is refused by `refuse_unfilled` so the literal placeholder can never run. Which tests to run comes from
`hub.skills.coding.intelligence.testmap`."""
import re
from hub.skills.coding.intelligence.testmap import is_test, select

PLACEHOLDER = '{tests}'
SAFE_PATH = re.compile(r'^[A-Za-z0-9_][\w./@+-]*$')

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
