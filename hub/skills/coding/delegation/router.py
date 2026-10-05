"""Advisory delegation routing: Tier 0 (host tools), Tier 1 (the local model) or Tier 2 (the frontier keeps the work).

The objective is accepted frontier work saved, net of the frontier's review effort, not local-model usage. Each (role, kind) has a Beta posterior of the frontier
accepting a job. Priors come from the junior-task evaluation (benchmarks/RESULTS.md) at half weight, because mechanical correctness is not acceptance; real reviews
(not benchmark jobs) then move it. Costs are constants in arbitrary effort units until recorded review effort replaces them. Hard rules decide first and never use
statistics: a lookup the host can answer is Tier 0, oversized, ambiguous or security-sensitive work is Tier 2, and a task smaller than its handoff is not delegated.

This is a starting estimator, not a trained model: with few reviews the prior dominates and the output says so (`basis`)."""
import re
from . import outcomes

# (correct runs, total runs) per kind from the 22-case, 3-repeat junior evaluation after the pipeline rework (Gemma 4 12B).
EVAL_PRIORS = {'find_code': (6, 6), 'explain': (6, 6), 'config_use': (6, 6), 'check_requirement': (6, 9), 'compare': (3, 6), 'mechanical': (6, 6), 'guard': (6, 6),
               'regression_test': (5, 6), 'fix_test': (9, 9), 'run_tests': (6, 6)}
DEFAULT_PRIOR = (1, 2)  # an unlabelled job: no evidence either way
PRIOR_WEIGHT = 0.5
FRONTIER_EFFORT = {'find_code': 3, 'explain': 3, 'config_use': 2, 'check_requirement': 4, 'compare': 5, 'mechanical': 4, 'guard': 4, 'regression_test': 6, 'fix_test': 8, 'run_tests': 1, None: 4}
LOOKUPS = ('find_code', 'config_use')
SENSITIVE = re.compile(r'\b(auth|authenticat\w*|authoriz\w*|credential\w*|password\w*|secret\w*|token\w*|crypto\w*|encrypt\w*|permission\w*|csrf|xss|injection|vulnerab\w*|sandbox)\b', re.I)
MIN_SAVING = 0.5
WASTE = 0.25  # a rejected or taken-over job costs the review plus this share of the task's effort (reading the failed patch, finding out why, redoing it)

def posterior(role, kind, jobs):
    """(alpha, beta, real reviews) for the bucket: the evaluation prior at half weight plus every real accepted/rejected/taken-over review of that role and kind."""
    correct, total = EVAL_PRIORS.get(kind, DEFAULT_PRIOR)
    alpha, beta = 1 + PRIOR_WEIGHT * correct, 1 + PRIOR_WEIGHT * (total - correct)
    real = 0
    for job in jobs:
        review = job.get('review')
        if not review or not outcomes.is_real(job) or job['request'].get('role') != role or (job['request'].get('kind') or None) != kind:
            continue
        real += 1
        if review['decision'] == 'accepted':
            alpha += 1
        else:
            beta += 1
    return alpha, beta, real

def review_effort(files):
    return 1 + 0.2 * max(files, 0)

def estimate(role, kind, task='', files=0, jobs=(), tier0_answerable=False, gate_tripped=False, ambiguous_cause=False):
    """Where the task should go and why. `jobs` are stored job records (reviews of real jobs update the estimate)."""
    rules = []
    effort, review = FRONTIER_EFFORT.get(kind, FRONTIER_EFFORT[None]), review_effort(files)
    alpha, beta, real = posterior(role, kind, jobs)
    p = alpha / (alpha + beta)
    saving = p * (effort - review) - (1 - p) * (review + WASTE * effort)
    if tier0_answerable and (kind in LOOKUPS or role == 'investigator'):
        tier, rules = 0, ['the host can answer this deterministically (find_symbol, find_references, symbol_context)']
    elif role == 'validator':
        tier, rules = 0, ['approved checks run at zero model tokens']
    elif gate_tripped:
        tier, rules = 2, ['the complexity gate flags the task as oversized: split it first']
    elif ambiguous_cause or kind in ('compare',) and p < 0.7:
        tier, rules = 2, ['ambiguous root cause or behavioural comparison: the frontier decides']
    elif SENSITIVE.search(task or ''):
        tier, rules = 2, ['security-sensitive wording: security decisions stay with the frontier']
    elif role == 'editor' and files <= 1 and len(task or '') < 160 and kind in ('mechanical', 'guard', None):
        tier, rules = 2, ['smaller than its own handoff: do it directly']
    elif saving < MIN_SAVING:
        tier, rules = 2, [f'expected net saving {saving:.1f} is below {MIN_SAVING}']
    else:
        tier = 1
    return {'tier': tier, 'p_accept': round(p, 3), 'expected_net_saving': round(saving, 2), 'rules': rules,
            'basis': {'prior_runs': EVAL_PRIORS.get(kind, DEFAULT_PRIOR)[1], 'real_reviews': real, 'frontier_effort': effort, 'review_effort': round(review, 2),
                      'note': 'prior-dominated' if real < 10 else 'updated by real reviews', 'units': 'relative effort, not dollars or tokens'}}

def calibration(jobs, bins=(0.0, 0.5, 0.75, 0.9, 1.01)):
    """Predicted p_accept (from the priors alone, as at submission) against the observed acceptance of real reviewed jobs, per probability bin."""
    rows = []
    for job in jobs:
        review = job.get('review')
        if not review or not outcomes.is_real(job):
            continue
        request = job['request']
        correct, total = EVAL_PRIORS.get(request.get('kind'), DEFAULT_PRIOR)
        rows.append(((1 + PRIOR_WEIGHT * correct) / (2 + PRIOR_WEIGHT * total), review['decision'] == 'accepted'))
    table = []
    for low, high in zip(bins, bins[1:]):
        inside = [accepted for p, accepted in rows if low <= p < high]
        if inside:
            table.append({'predicted': f'{low:.2f}-{min(high, 1):.2f}', 'jobs': len(inside), 'observed_accept_rate': round(sum(inside) / len(inside), 3)})
    return table
