import json
import sys
from pathlib import Path

import pytest

from hub import calls, editgate, pipelines
from hub.models import Check, JobRequest
from hub.report import final_report, parse_report
from hub.runner import Runner

ROOT = Path(__file__).resolve().parents[1]

# Invented tasks with the shape of the two real failures: many renames plus mapped values plus behaviours, and a long spec-style brief.
INCIDENT_A = ('Make actual edits to these three files. Bounded slice: rename Alpha Panel -> Beta Panel, Gamma (Phase 3) -> Gamma, Delta (Phase 4) -> Delta data, Run Job -> Create job, '
              'Start Load -> Load from file, Admin only -> Administrator access required. Progress strings Scanning rows -> Checking records, Dumping set -> Creating job, Packing set -> Saving job. '
              'Rows header -> Records and display -> count records. In the preview map raw names to friendly labels via a fixed map known sessions_a:Work sessions, totals_b:Daily totals, '
              'visits_c:Activity, items_d:Work items, events_e:Ticket history, days_f:Historical days, members_g:Historical counts, periods_h:Historical periods; fallback Reporting data. '
              'Add a ref that blocks duplicate job starts and reset it in finally. Disable the Create job button while pending or starting. Add an effect that updates the status when jobs complete. '
              'Add an effect that updates the status when jobs fail. Update unit assertions for these exact labels. Add a duplicate-start test. Style the panel with the existing colours and readable mobile table overflow.')
INCIDENT_B = ' '.join(f'Add the {name} section showing {name} counts and handle the loading state. ' for name in ('quarterly', 'monthly', 'daily', 'weekly', 'yearly', 'archived', 'pending', 'failed', 'retained', 'expired', 'finalized')) + 'x' * 1900
SUCCESSES = {
    'incident-fixture all-in-one': ('Rename the labels and add small behaviours in the export panel, keeping everything else. In ExportPanel.tsx: Run Export -> Create export, Start Import -> Import from file, '
        'Manager role required -> Administrator access required; progress strings Scanning tables -> Scanning records, Writing archive -> Creating export, Packing bundle -> Saving export; '
        'table header Tables -> Records and show rows as "{rows} records"; add a ref called exportStarting that blocks a second takeExport call while one is starting and reset it in finally. '
        'In exportPanel.test.tsx update the assertions for these exact labels and add a test that clicking Create export twice starts one export. '
        'In exportPanel.css add a hover and focus-visible style for buttons using the existing colours. Do not change anything else.'),
}

def junior_editor_tasks():
    rows = [json.loads(l) for l in (ROOT / 'benchmarks' / 'evals' / 'junior.jsonl').read_text().splitlines() if l.strip()]
    return {r['id']: r['task'] for r in rows if r['role'] == 'editor'}

def test_every_task_that_succeeded_in_practice_passes_the_gate():
    for name, task in {**SUCCESSES, **junior_editor_tasks()}.items():
        assert not editgate.reasons(editgate.load(task)), (name, editgate.load(task))

def test_the_incident_shaped_tasks_are_refused_with_the_reason_counted():
    for task in (INCIDENT_A, INCIDENT_B):
        counts = editgate.load(task)
        assert editgate.reasons(counts), counts
    assert editgate.load(INCIDENT_A)['mappings'] >= 9 and editgate.load(INCIDENT_A)['colon_pairs'] == 8 and editgate.load(INCIDENT_A)['behaviours'] >= 8
    assert editgate.load(INCIDENT_B)['behaviours'] == 11 and editgate.load(INCIDENT_B)['chars'] >= 1800

SNAPS = {'src/Panel.tsx': ('src/Panel.tsx', 'Alpha Panel Gamma (Phase 3) Delta (Phase 4) Run Job Start Load Admin only\nScanning rows Dumping set Packing set\nRows header', 'a'),
         'src/panel.css': ('src/panel.css', '.panel { color: red; }\n', 'b'),
         'tests/panel.test.tsx': ('tests/panel.test.tsx', "it('shows Alpha Panel', () => {})\n", 'c')}

def test_the_proposed_split_covers_every_change_once_and_each_unit_passes_the_gate():
    units = editgate.propose_split(INCIDENT_A, SNAPS)
    covered = [c for u in units for c in u['clauses']]
    wanted = editgate.items_of(INCIDENT_A)
    assert sorted(covered) == sorted(wanted) and len(covered) == len(set(covered)) and sum('->' in c for c in covered) >= 9
    assert all(not editgate.reasons(editgate.load(u['task'])) for u in units), [editgate.load(u['task']) for u in units]
    by_path = {u['allowed_paths'][0] for u in units}
    assert by_path <= set(SNAPS) and 'src/Panel.tsx' in by_path and 'src/panel.css' in by_path and 'tests/panel.test.tsx' in by_path
    assert [u['allowed_paths'][0] for u in units][0] == 'src/Panel.tsx' and units[-1]['allowed_paths'][0] == 'src/panel.css'
    assert units[0]['task'].startswith(f'Part 1 of {len(units)} of a larger change to src/Panel.tsx')

def test_a_small_task_has_no_split_and_message_names_the_reasons():
    decision = editgate.decide('Rename alpha -> beta in the panel.', SNAPS)
    assert not decision['tripped'] and decision['units'] == []
    refused = editgate.decide(INCIDENT_A, SNAPS)
    assert refused['tripped'] and 'Proposed split' in editgate.message(refused) and 'separate changes' in editgate.message(refused)

def job(tmp_path, repo, task, allowed, **kw):
    directory = tmp_path / 'job'
    directory.mkdir()
    (directory / 'workspace').mkdir()
    (directory / 'request.json').write_text(JobRequest(role='editor', repo=str(repo), task=task, allowed_paths=allowed, idempotency_key='gate' + str(len(task)), **kw).model_dump_json())
    return directory

@pytest.mark.asyncio
async def test_run_edit_refuses_before_any_model_call_unless_the_gate_is_skipped(tmp_path, repo, monkeypatch):
    calls_made = []
    async def call(client, d, label, name, step, body, event):
        calls_made.append(name)
        return {'message': {'content': 'FILE: app.ts\n<<<<<<< SEARCH\nexport const answer = 41;\n=======\nexport const answer = 1;\n>>>>>>> REPLACE\nEND OF EDITS'}, 'done_reason': 'stop', 'eval_count': 20}
    monkeypatch.setattr(calls, 'call', call)
    directory = job(tmp_path, repo, INCIDENT_A, ['app.ts'], refuse_oversized=True)
    await pipelines.run_edit(directory, 'edit', 'Task:\n' + INCIDENT_A, 'edit')
    report = final_report(json.loads((directory / 'edit.session.json').read_text()))
    assert calls_made == [] and report['status'] == 'BLOCKED' and 'Proposed split' in report['findings']
    decision = json.loads((directory / 'edit.gate.json').read_text())
    assert decision['tripped'] and decision['units'] and decision['mode'] == 'refused'
    forced = tmp_path / 'forced'
    forced.mkdir()
    (forced / 'workspace').mkdir()
    (repo / 'app.ts').write_text('Alpha Panel and Run Job\nexport const answer = 41;\n')  # some mapped texts exist, so the early refusal does not apply
    (forced / 'request.json').write_text(JobRequest(role='editor', repo=str(repo), task=INCIDENT_A, allowed_paths=['app.ts'], idempotency_key='gate-skip', skip_gate=True).model_dump_json())
    await pipelines.run_edit(forced, 'edit', 'Task:\n' + INCIDENT_A, 'edit')
    assert calls_made == ['edit']

@pytest.mark.asyncio
async def test_a_refused_implement_job_returns_the_split_and_runs_no_checks(repo, store, tmp_path):
    marker = tmp_path / 'check-ran'
    check = Check(name='marker', argv=[sys.executable, '-c', f"open({str(marker)!r}, 'w').write('x')"])
    job_record = store.submit(JobRequest(role='editor', workflow='implement', repair_attempts=1, repo=str(repo), task=INCIDENT_A, allowed_paths=['app.ts'], checks=[check], idempotency_key='gate-impl', refuse_oversized=True))
    runner = Runner(store, 'token')
    async def model(_job, _request, directory, label, prompt, **kwargs):
        (directory / (label + '.gate.json')).write_text(json.dumps({**editgate.decide(INCIDENT_A, {'app.ts': ('app.ts', 'x', 'h')}), 'mode': 'refused'}))
        return {'info': {'tokens': {}}}, parse_report('LOCAL_WORKER_REPORT\nStatus: BLOCKED\nFindings:\nRefused before any model call.\nFiles:\nNone\nChecks:\nNot run.\nRisks:\nToo large.\nEND_LOCAL_WORKER_REPORT')
    runner.execute_model = model
    await runner.run(store.next())
    result = store.get(job_record['id'])['result']
    assert not marker.exists() and result['worker_status'] == 'BLOCKED' and result['proposed_split'] and result['gate']['tripped']
    assert result['completion']['next_action'] == 'split_and_resubmit' and result['completion']['blocking_category'] == 'oversized'


@pytest.mark.asyncio
async def test_by_default_a_flagged_task_still_runs_and_the_decision_is_only_advice(tmp_path, repo, monkeypatch):
    (repo / 'app.ts').write_text('Alpha Panel and Run Job\nexport const answer = 41;\n')
    async def call(client, d, label, name, step, body, event):
        return {'message': {'content': 'FILE: app.ts\n<<<<<<< SEARCH\nexport const answer = 41;\n=======\nexport const answer = 42;\n>>>>>>> REPLACE\nEND OF EDITS'}, 'done_reason': 'stop', 'eval_count': 20}
    monkeypatch.setattr(calls, 'call', call)
    directory = job(tmp_path, repo, INCIDENT_A, ['app.ts'])
    await pipelines.run_edit(directory, 'edit', 'Task:\n' + INCIDENT_A, 'edit')
    report = final_report(json.loads((directory / 'edit.session.json').read_text()))
    decision = json.loads((directory / 'edit.gate.json').read_text())
    assert report['status'] == 'COMPLETE' and decision['tripped'] and decision['mode'] == 'advised'
