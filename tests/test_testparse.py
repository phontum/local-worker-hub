from pathlib import Path

from hub.testparse import parse

DATA = Path(__file__).parent / 'data' / 'testparse'

def sample(name):
    return (DATA / name).read_text()

def test_pytest_failures_carry_file_line_and_assertion():
    out = parse(sample('pytest-q.txt'))
    assert out['tool'] == 'pytest'
    assert out['counts'] == {'failed': 3, 'passed': 5}
    by_name = {f['test_id'].split('::')[-1]: f for f in out['failures']}
    assert set(by_name) == {'test_entry_expires_after_ttl', 'test_retry_passes_configured_timeout', 'test_client_uses_config_timeout'}
    cache = by_name['test_entry_expires_after_ttl']
    assert (cache['file'], cache['line']) == ('tests/test_cache.py', 9)
    assert 'assert 1 is None' in cache['message']
    assert by_name['test_retry_passes_configured_timeout']['line'] == 26

def test_pytest_pass_has_no_failures():
    out = parse(sample('pytest-pass.txt'))
    assert out['failures'] == [] and out['counts'] == {'passed': 2}

def test_node_tap_reports_failing_tests_not_passing_ones():
    out = parse(sample('node-test.txt'), root='/work/notify')
    assert out['tool'] == 'node:test'
    assert [f['test_id'] for f in out['failures']] == ['late evening is quiet', 'early morning is quiet']
    first = out['failures'][0]
    assert (first['file'], first['line']) == ('test/quiet.test.js', 5)
    assert 'false !== true' in first['message']
    assert out['counts']['fail'] == 2 and out['counts']['pass'] == 3

def test_vitest_failure_with_location_and_counts():
    out = parse(sample('vitest.txt'))
    assert out['tool'] == 'vitest'
    assert out['counts'] == {'failed': 1, 'passed': 1, 'total': 2}
    [failure] = out['failures']
    assert failure['test_id'] == 'sum.test.ts > sum > adds'
    assert (failure['file'], failure['line']) == ('sum.test.ts', 3)
    assert 'expected 2 to be 3' in failure['message']

def test_tsc_errors():
    out = parse(sample('tsc.txt'))
    assert out['tool'] == 'tsc' and out['counts'] == {'errors': 2}
    assert [(f['file'], f['line'], f['test_id']) for f in out['failures']] == [('bad.ts', 1, 'TS2322'), ('bad.ts', 2, 'TS2322')]

# The next three formats are written from the tools' documented output, not recorded: ruff, mypy and eslint are not installed here.
def test_ruff_mypy_eslint_documented_formats():
    ruff = parse("pkg/a.py:1:8: F401 [*] `os` imported but unused\nFound 1 error.\n")
    assert ruff['tool'] == 'ruff' and (ruff['failures'][0]['file'], ruff['failures'][0]['line'], ruff['failures'][0]['test_id']) == ('pkg/a.py', 1, 'F401')
    mypy = parse('pkg/a.py:3: error: Incompatible return value type (got "str", expected "int")  [return-value]\nFound 1 error in 1 file\n')
    assert mypy['tool'] == 'mypy' and mypy['failures'][0]['test_id'] == 'return-value' and mypy['failures'][0]['line'] == 3
    eslint = parse('/repo/src/a.ts\n  3:7  error  \'x\' is assigned a value but never used  @typescript-eslint/no-unused-vars\n\n1 problem\n')
    assert eslint['tool'] == 'eslint' and eslint['failures'][0]['line'] == 3 and eslint['failures'][0]['test_id'] == '@typescript-eslint/no-unused-vars'

def test_unknown_output_and_argv_hint_and_truncation():
    assert parse('hello world')['tool'] == 'unknown'
    assert parse('whatever', argv=['npx', 'tsc', '--noEmit'])['tool'] == 'tsc'
    many = ''.join(f'a.py:{i}:1: E501 line too long\n' for i in range(1, 80))
    out = parse(many)
    assert len(out['failures']) == 50 and out['truncated'] and out['counts'] == {'errors': 79}


def test_direct_report_lists_failing_tests_without_a_model():
    from hub.validation import analysis_evidence, direct_report, parsed_output
    output = sample('pytest-q.txt')
    counts, failures = parsed_output(output, ['python', '-m', 'pytest'])
    assert counts == {'total': 8, 'passed': 5, 'failed': 3, 'skipped': 0, 'format': 'pytest'}
    check = {'name': 'pytest', 'status': 'failed', 'exit_code': 1, 'counts': counts, 'failures': failures, 'artifact': 'check-0.log', 'output_tail': output}
    report = direct_report([check])
    assert report['status'] == 'PARTIAL'
    for name in ('test_entry_expires_after_ttl', 'test_retry_passes_configured_timeout', 'test_client_uses_config_timeout'):
        assert name in report['checks']
    assert 'tests/test_cache.py:9' in report['checks']
    evidence = analysis_evidence([check])
    assert 'parsed failures' in evidence and 'tests/test_retry.py:26' in evidence


def test_summary_and_brief_views_carry_a_bounded_failure_list():
    from hub.presentation import result_brief, result_summary
    output = sample('pytest-q.txt')
    from hub.validation import parsed_output
    counts, failures = parsed_output(output, ['pytest'])
    check = {'name': 'pytest', 'status': 'failed', 'exit_code': 1, 'counts': counts, 'failures': failures, 'artifact': 'check-0.log'}
    report = ('LOCAL_WORKER_REPORT\nStatus: PARTIAL\nFindings:\nx\nFiles:\nNone\nChecks:\nx\nRisks:\nx\nEND_LOCAL_WORKER_REPORT')
    job = {'id': 'a' * 32, 'state': 'completed', 'request': {'role': 'validator'}, 'result': {'report': report, 'worker_status': 'PARTIAL', 'checks': [check]}}
    assert len(result_summary(job)['checks'][0]['failures']) == 3
    brief = result_brief(job)
    assert any('test_entry_expires_after_ttl' in line for line in brief['checks'][0]['failures'])
    assert brief['response_bytes'] <= 2048
