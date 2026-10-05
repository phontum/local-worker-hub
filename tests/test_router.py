from hub import router

def reviewed(role, kind, decision, caller='mcp'):
    return {'request': {'role': role, 'kind': kind, 'caller': caller}, 'review': {'decision': decision}}

def test_hard_rules_decide_before_statistics():
    assert router.estimate('investigator', 'find_code', 'where is x used', 1, tier0_answerable=True)['tier'] == 0
    assert router.estimate('validator', 'run_tests')['tier'] == 0
    assert router.estimate('editor', 'mechanical', 'rename a to b everywhere in these files please, ' * 5, 3, gate_tripped=True)['tier'] == 2
    sensitive = router.estimate('editor', 'guard', 'Add a guard that rejects an expired auth token in the login handler and logs it properly for the audit trail', 2)
    assert sensitive['tier'] == 2 and 'security' in sensitive['rules'][0]
    tiny = router.estimate('editor', 'mechanical', 'rename x to y', 1)
    assert tiny['tier'] == 2 and 'smaller than its own handoff' in tiny['rules'][0]
    assert router.estimate('investigator', 'compare', 'differences between A and B', 2)['tier'] == 2  # prior 0.5 on behavioural comparison

def test_a_well_measured_kind_is_delegated_with_a_positive_expected_saving():
    out = router.estimate('editor', 'fix_test', 'The test test_cache_expiry fails with a KeyError in cache.get; fix the source file with the smallest change.', 1)
    assert out['tier'] == 1 and out['expected_net_saving'] > 0.5 and out['basis']['note'] == 'prior-dominated' and out['p_accept'] > 0.8

def test_real_reviews_move_the_estimate_and_benchmark_jobs_do_not():
    base = router.estimate('editor', 'fix_test', 'x' * 200, 2)['p_accept']
    bad = [reviewed('editor', 'fix_test', 'takeover') for _ in range(12)]
    worse = router.estimate('editor', 'fix_test', 'x' * 200, 2, jobs=bad)
    assert worse['p_accept'] < base - 0.3 and worse['basis']['real_reviews'] == 12 and worse['basis']['note'] == 'updated by real reviews'
    assert worse['tier'] == 2  # the saving turned negative
    ignored = router.estimate('editor', 'fix_test', 'x' * 200, 2, jobs=[reviewed('editor', 'fix_test', 'takeover', caller='eval')] * 12)
    assert ignored['p_accept'] == base and ignored['basis']['real_reviews'] == 0
    other_kind = router.estimate('editor', 'fix_test', 'x' * 200, 2, jobs=[reviewed('editor', 'guard', 'takeover')] * 12)
    assert other_kind['p_accept'] == base

def test_unlabelled_kinds_start_uncertain_and_calibration_compares_prediction_with_outcome():
    assert abs(router.estimate('editor', None, 'x' * 300, 3)['p_accept'] - 0.5) < 0.1
    jobs = [reviewed('editor', 'fix_test', 'accepted')] * 3 + [reviewed('editor', 'fix_test', 'takeover')] + [reviewed('editor', 'compare', 'takeover')] * 2
    table = router.calibration(jobs)
    assert sum(row['jobs'] for row in table) == 6 and any(row['observed_accept_rate'] == 0.75 for row in table)
