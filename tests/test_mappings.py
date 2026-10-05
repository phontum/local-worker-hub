from pathlib import Path

import pytest

from hub import mappings
from hub.models import JobRequest
from hub.report import parse_report
from hub.runner import Runner

TASK = ('In Panel.tsx: Run Export -> Create export, Start Import -> Import from file, Manager role required -> Administrator access required; '
        'progress strings Scanning tables -> Scanning records, table header Tables -> Records. Change nothing else.')
BEFORE = {'Panel.tsx': '<h3>Run Export</h3><button>Start Import</button><p>Manager role required</p>\nscan: Scanning tables\n<th>Tables</th>\n'}

def test_extracts_pairs_per_clause_and_skips_chains():
    pairs = mappings.extract(TASK)
    assert ('In Panel.tsx: Run Export', 'Create export') in pairs and mappings.literal('In Panel.tsx: Run Export', list(BEFORE.values())) == 'Run Export'
    assert ('Manager role required', 'Administrator access required') in pairs
    assert ('table header Tables', 'Records') in pairs and ('progress strings Scanning tables', 'Scanning records') in pairs
    assert mappings.extract('a -> b -> c, x -> y') == [('x', 'y')]

def test_leading_sentence_words_are_trimmed_until_the_text_is_found():
    assert mappings.literal('progress strings Scanning tables', list(BEFORE.values())) == 'Scanning tables'
    assert mappings.literal('table header Tables', list(BEFORE.values())) == 'Tables'
    assert mappings.literal('nothing like this', list(BEFORE.values())) is None

def test_complete_renames_are_met_and_incomplete_ones_are_named():
    done = {'Panel.tsx': '<h3>Create export</h3><button>Import from file</button><p>Administrator access required</p>\nscan: Scanning records\n<th>Records</th>\n'}
    assert mappings.verify(TASK, BEFORE, done)['unmet'] == []
    partial = {'Panel.tsx': '<h3>Create export</h3><button>Start Import</button><p>Administrator access required</p>\nscan: Scanning tables\n<th>Records</th>\n'}
    result = mappings.verify(TASK, BEFORE, partial)
    assert [m['old'] for m in result['unmet']] == ['Start Import', 'Scanning tables'] and result['checked'] == 5 and result['unverifiable'] == 0

def test_a_single_word_old_text_that_still_appears_elsewhere_is_not_flagged_once_something_changed():
    before = {'a.tsx': '<th>Tables</th> and a Tables helper and Tables again'}
    after = {'a.tsx': '<th>Records</th> and a Tables helper and Tables again'}
    assert mappings.verify('header Tables -> Records', before, after)['unmet'] == []
    assert mappings.verify('header Tables -> Records', before, before)['unmet'][0]['reason'] == 'the old text is still present'

def test_unfindable_old_text_is_unverifiable_not_a_failure():
    result = mappings.verify('Foo bar -> Baz', {'a.py': 'nothing'}, {'a.py': 'nothing'})
    assert result == {'checked': 0, 'unverifiable': 1, 'unmet': []}

@pytest.mark.asyncio
async def test_a_job_that_claims_complete_with_renames_undone_ends_partial(store, tmp_path):
    repo = tmp_path / 'proj'
    repo.mkdir()
    (repo / 'Panel.tsx').write_text(BEFORE['Panel.tsx'])
    job = store.submit(JobRequest(role='editor', repo=str(repo), task=TASK, allowed_paths=['Panel.tsx'], idempotency_key='mapping-partial'))
    runner = Runner(store, 'token')
    async def model(_job, _request, directory, label, prompt, **kwargs):
        target = Path(_request.repo) / 'Panel.tsx'
        target.write_text(target.read_text().replace('Run Export', 'Create export'))  # one of five renames
        return {'info': {'tokens': {}}}, parse_report('LOCAL_WORKER_REPORT\nStatus: COMPLETE\nFindings:\nApplied 1 file(s): Panel.tsx.\nFiles:\nPanel.tsx\nChecks:\nNot run.\nRisks:\nNone identified.\nEND_LOCAL_WORKER_REPORT')
    runner.execute_model = model
    await runner.run(store.next())
    result = store.get(job['id'])['result']
    assert result['worker_status'] == 'PARTIAL' and 'Task mappings not fully applied' in result['completion']['remaining_issue']
    assert "'Start Import' -> 'Import from file'" in result['report'] and result['mappings']['checked'] == 5
    assert result['acceptance']['next_action'] == 'frontier_decision'
