"""Deterministic complexity gate for Editor jobs, with a concrete proposed split when it refuses.

A job that asks for too many separate changes in one generation tends to overrun the output cap, so it is refused before any model call and
returned with units the frontier can resubmit as they are. The signals are plain counts from the task text, calibrated on observed jobs:
the two real frontier failures (about 17-21 concerns, 8-11 behavioural clauses, 1.7-2.9k characters) against every job that succeeded
(at most 12 concerns, 5 behavioural clauses, 800 characters). That is a small sample: thresholds are one table, `skip_gate` overrides, and
every decision is recorded so they can be recalibrated from `local-worker incident` records.
"""
import re

from . import mappings
from hub.skills.coding.intelligence.codeindex import TESTISH

THRESHOLDS = {'concerns': 14, 'behaviours': 8, 'chars': 1800, 'unit_concerns': 6}
VERBS = re.compile(r'\b(add|rename|replace|remove|delete|change|update|make|show|display|hide|handle|disable|enable|reset|block|style|fix|guard|wrap|extract|move|convert|implement|map|rewrite|sort|filter)\b', re.I)
ARROW = re.compile(r'->|→|=>')

def clauses(task):
    return [c.strip() for c in re.split(r'[;\n]|\.\s|\.$', task) if c.strip()]

def load(task):
    """Counts the gate decides on."""
    pairs = mappings.extract(task)
    colon = len(re.findall(r'\b\w+:[A-Z][\w ]+', task))
    behaviours = [c for c in clauses(task) if not ARROW.search(c) and VERBS.search(c) and len(c.split()) >= 3]
    return {'mappings': len(pairs), 'colon_pairs': colon, 'behaviours': len(behaviours), 'chars': len(task),
            'concerns': len(pairs) + len(behaviours) + colon // 2}

def reasons(counts):
    out = []
    if counts['concerns'] >= THRESHOLDS['concerns']:
        out.append(f"{counts['concerns']} separate changes ({counts['mappings']} renames, {counts['behaviours']} behaviours, {counts['colon_pairs']} mapped values)")
    if counts['behaviours'] >= THRESHOLDS['behaviours']:
        out.append(f"{counts['behaviours']} behavioural changes")
    if counts['chars'] >= THRESHOLDS['chars']:
        out.append(f"a {counts['chars']}-character task")
    return out

def attribute(clause, snapshots):
    """The authorized file a clause is about: its file name, the file holding the text it renames, a test or style keyword, a unique stem, else the main code file."""
    paths = list(snapshots)
    lowered = clause.lower()
    for path in paths:
        if path.rsplit('/', 1)[-1].lower() in lowered:
            return path
    for old, _ in mappings.extract(clause):
        for path, snap in snapshots.items():
            if snap[1] and mappings.literal(old, [snap[1]]):
                return path
    if re.search(r'\b(test|tests|assert|assertion|assertions|spec)\b', lowered):
        tests = [p for p in paths if TESTISH.search(p)]
        if tests:
            return tests[0]
    if re.search(r'\b(style|styles|styling|css|hover|focus|colou?rs?|border|margin|padding|overflow)\b', lowered):
        sheets = [p for p in paths if p.endswith(('.css', '.scss', '.less'))]
        if sheets:
            return sheets[0]
    words = set(re.findall(r'[\w.-]+', lowered))
    stems = {p: p.rsplit('/', 1)[-1].lower().rsplit('.', 1)[0] for p in paths}
    named = [p for p, stem in stems.items() if stem in words and list(stems.values()).count(stem) == 1]
    if len(named) == 1:
        return named[0]
    code = [p for p in paths if not TESTISH.search(p) and not p.endswith(('.css', '.scss', '.less'))]
    return (code or paths)[0]

def items_of(task):
    """Clauses that carry a change, one item per change: a clause listing several `a -> b` renames is split at its commas."""
    items = []
    for clause in clauses(task):
        if len(ARROW.findall(clause)) >= 2:
            items += [part.strip() for part in re.split(r',\s+', clause) if ARROW.search(part)]
        elif ARROW.search(clause) or (VERBS.search(clause) and len(clause.split()) >= 3):
            items.append(clause)
    return items

def weight(item):
    """How many separate changes an item stands for (a clause of mapped values counts for half its pairs)."""
    return max(1, len(re.findall(r'\b\w+:[A-Z][\w ]+', item)) // 2 + 1)

def propose_split(task, snapshots):
    """Units that each stay under the gate, grouped by file (code first, then tests, then styles); each carries a ready-to-submit task."""
    per_file = {}
    for item in items_of(task):
        per_file.setdefault(attribute(item, snapshots), []).append(item)
    order = sorted(per_file, key=lambda p: (2 if p.endswith(('.css', '.scss', '.less')) else 1 if TESTISH.search(p) else 0, p))
    units = []
    for path in order:
        current, load_now = [], 0
        for item in per_file[path]:
            if current and load_now + weight(item) > THRESHOLDS['unit_concerns']:
                units.append({'allowed_paths': [path], 'clauses': current})
                current, load_now = [], 0
            current.append(item)
            load_now += weight(item)
        if current:
            units.append({'allowed_paths': [path], 'clauses': current})
    for number, unit in enumerate(units, 1):
        unit['task'] = (f"Part {number} of {len(units)} of a larger change to {unit['allowed_paths'][0]}. Make only the changes listed here and leave everything else exactly as it is: "
                        + '; '.join(unit['clauses']) + '.')
    return units

def decide(task, snapshots):
    counts = load(task)
    why = reasons(counts)
    return {'counts': counts, 'tripped': bool(why), 'reasons': why, 'thresholds': dict(THRESHOLDS), 'units': propose_split(task, snapshots) if why else []}

def message(decision):
    units = decision['units']
    lines = [f"Refused before any model call: the task asks for {', '.join(decision['reasons'])}, more than one generation reliably fits (the output cap cut such jobs off in earlier use).",
             f"Proposed split ({len(units)} jobs, resubmit each as its own Editor job; the task text of each is in the result's proposed_split):"]
    lines += [f"{i}. {u['allowed_paths'][0]}: {len(u['clauses'])} change(s), e.g. {u['clauses'][0][:90]}" for i, u in enumerate(units, 1)]
    return '\n'.join(lines)
