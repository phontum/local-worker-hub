import subprocess
import sys
from pathlib import Path

import pytest

from hub import workspace
from hub.models import Check, JobRequest
from hub.report import parse_report
from hub.runner import Runner

CALC = 'def add(a, b):\n    return a + b\n\n\ndef total(items):\n    return sum(items) + 1  # known bug: off by one\n'
BASE_TEST = 'from calc import add\n\n\ndef test_add():\n    assert add(1, 1) == 2\n'


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'proj'
    (root / 'tests').mkdir(parents=True)
    (root / 'calc.py').write_text(CALC)
    (root / 'conftest.py').write_text('import sys\nfrom pathlib import Path\n\nsys.path.insert(0, str(Path(__file__).parent))\n')
    (root / 'tests' / 'test_calc.py').write_text(BASE_TEST)
    for step in (['init', '-q'], ['add', '-A'], ['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'x']):
        subprocess.run(['git', '-C', str(root), *step], check=True, capture_output=True)
    return root


def request(root, key, **kw):
    check = Check(name='pytest', argv=[sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'tests/test_calc.py'])
    return JobRequest(role='editor', kind='regression_test', repo=str(root), task='Add a regression test for the off-by-one in total()',
                      allowed_paths=['tests/test_calc.py'], checks=[check], idempotency_key=key, **kw)


async def run_with(store, root, key, appended):
    job = store.submit(request(root, key))
    runner = Runner(store, 'token')
    async def model(_job, _request, directory, label, prompt, **kwargs):
        target = Path(_request.repo) / 'tests' / 'test_calc.py'
        target.write_text(BASE_TEST + appended)
        return {'info': {'tokens': {}}}, parse_report('LOCAL_WORKER_REPORT\nStatus: COMPLETE\nFindings:\nAdded a test.\nFiles:\ntests/test_calc.py\nChecks:\nNot run.\nRisks:\nNone.\nEND_LOCAL_WORKER_REPORT')
    runner.execute_model = model
    await runner.run(store.next())
    return store.get(job['id'])['result']


@pytest.mark.asyncio
async def test_a_new_test_that_fails_on_the_unfixed_code_is_accepted(store, project):
    result = await run_with(store, project, 'rt-good', '\n\ndef test_total_is_exact():\n    from calc import total\n    assert total([1, 2]) == 3\n')
    assert result['regression']['reproduces_bug'] and result['regression']['new_failing'] == ['tests/test_calc.py::test_total_is_exact']
    assert result['worker_status'] == 'COMPLETE' and not result['checks_failed']
    packet = result['acceptance']
    assert packet['next_action'] == 'review_patch_then_apply_result' and packet['regression']['reproduces_bug']
    assert any('fails as expected' in note for note in packet['review_focus'])
    assert (project / 'tests' / 'test_calc.py').read_text() == BASE_TEST  # still only in the private workspace


@pytest.mark.asyncio
async def test_a_new_test_that_passes_on_the_unfixed_code_does_not_reproduce_the_bug(store, project):
    result = await run_with(store, project, 'rt-pass', '\n\ndef test_add_again():\n    from calc import add\n    assert add(2, 2) == 4\n')
    assert result['regression']['reproduces_bug'] is False and result['regression']['new_failing'] == []
    assert result['worker_status'] == 'PARTIAL' and 'does not reproduce the bug' in result['completion']['remaining_issue']
    assert result['acceptance']['next_action'] == 'frontier_decision'


@pytest.mark.asyncio
async def test_a_new_test_file_that_does_not_collect_is_not_a_reproduction(store, project):
    result = await run_with(store, project, 'rt-broken', '\n\ndef test_syntax(:\n    pass\n')
    assert result['regression']['reproduces_bug'] is False
    assert result['worker_status'] == 'PARTIAL' and 'does not collect or run' in result['completion']['remaining_issue']


def test_kind_must_match_the_role_and_fix_test_defaults_to_one_repair(project):
    with pytest.raises(ValueError, match='runs as investigator'):
        JobRequest(role='editor', kind='find_code', repo=str(project), task='x', allowed_paths=['calc.py'], idempotency_key='k1')
    with pytest.raises(ValueError, match='requires the check'):
        JobRequest(role='editor', kind='regression_test', repo=str(project), task='x', allowed_paths=['calc.py'], idempotency_key='k2')
    check = Check(name='t', argv=[sys.executable, '-c', 'pass'])
    fix = JobRequest(role='editor', kind='fix_test', repo=str(project), task='x', allowed_paths=['calc.py'], checks=[check], idempotency_key='k3')
    assert fix.workflow == 'implement' and fix.repair_attempts == 1
    explicit = JobRequest(role='editor', kind='fix_test', repo=str(project), task='x', allowed_paths=['calc.py'], checks=[check], workflow='single', idempotency_key='k4')
    assert explicit.workflow == 'single' and explicit.repair_attempts == 0
