"""Host-computed drift checks between the original task, proposals and the arbiter's synthesis.

Everything here is deterministic string comparison; no model decides whether a requirement was dropped.
"""
import re

def norm(text):
    return re.sub(r'\s+', ' ', text).strip().lower()

def anchored(task, requirements):
    """Requirements whose task_quote is a verbatim substring of the original task."""
    return [r for r in requirements if r.task_quote and r.task_quote in task]

def task_spans(task):
    """Clauses of the task, split on sentence and conjunction boundaries."""
    pieces = re.split(r'(?<=[.!?])\s+|[,;:]\s+|\s+(?:and|but|or)\s+', task)
    return [p.strip() for p in pieces if len(p.strip()) >= 3]

def overlaps(a, b):
    a, b = norm(a), norm(b)
    return bool(a and b and (a in b or b in a))

def uncovered_spans(task, requirements):
    quotes = [r.task_quote for r in anchored(task, requirements)]
    return [span for span in task_spans(task) if not any(overlaps(span, q) for q in quotes)]

def proposal_stats(task, proposal):
    total = len(proposal.requirements)
    valid = anchored(task, proposal.requirements)
    return {'requirements': total, 'anchored': len(valid),
            'unanchored': [r.requirement[:200] for r in proposal.requirements if r not in valid],
            'derived': sum(1 for r in valid if r.kind == 'derived')}

def drift_report(task, proposals, synthesis):
    """`proposals` maps a candidate label to its BoardProposal."""
    arbiter = synthesis.requirements
    arbiter_valid = anchored(task, arbiter)
    arbiter_quotes = [r.task_quote for r in arbiter_valid]
    explicit_from_proposals = {}
    for label, proposal in proposals.items():
        for r in anchored(task, proposal.requirements):
            if r.kind == 'explicit':
                explicit_from_proposals.setdefault(norm(r.task_quote), (r.task_quote, []))[1].append(label)
    dropped = [{'quote': quote, 'proposed_by': labels} for key, (quote, labels) in explicit_from_proposals.items()
               if not any(overlaps(quote, q) for q in arbiter_quotes)]
    unanchored = [r.requirement[:200] for r in arbiter if r not in arbiter_valid]
    uncovered = uncovered_spans(task, arbiter)
    bad_labels = sorted({label for r in arbiter for label in r.supported_by if label not in proposals})
    report = {
        'proposals': {label: proposal_stats(task, p) for label, p in proposals.items()},
        'arbiter': {'requirements': len(arbiter), 'anchored': len(arbiter_valid), 'unanchored': unanchored,
                    'derived': sum(1 for r in arbiter_valid if r.kind == 'derived'),
                    'unknown_supporters': bad_labels},
        'dropped_explicit': dropped,
        'uncovered_task_spans': uncovered,
    }
    flags = [f'Arbiter dropped an explicit requirement a proposer anchored: "{d["quote"]}"' for d in dropped]
    flags += [f'Arbiter requirement has no verbatim task quote (advisory only): {u}' for u in unanchored]
    flags += [f'No arbiter requirement covers this part of the task: "{s}"' for s in uncovered]
    flags += [f'Arbiter cited unknown candidate {label}' for label in bad_labels]
    report['flags'] = flags
    return report
