import json

import pytest

from hub import calls, pipelines
from hub.skills.coding.editing import contextpack
from hub.skills.coding.intelligence import localize
from hub.models import JobRequest
from hub.report import final_report

def snap(path, text):
    return (path, text, 'sha')

def big_component(lines=2000):
    rows = [f'// filler line {i} with some text to take up space in the prompt' for i in range(lines)]
    rows[35], rows[109], rows[1500] = "  manifest: 'Scanning tables',", "  <p>Manager role required</p>", "  <button>Take Backup</button>"
    return '\n'.join(rows) + '\n'

def test_a_file_that_fits_the_budget_is_shown_whole_even_above_400_lines():
    content = '\n'.join(f'line {i} of a component that is not small' for i in range(463)) + '\n'
    packed = contextpack.pack({'a.tsx': snap('a.tsx', content)}, 'change something', contextpack.budget_chars(16384, 4096, 3000))
    assert packed.report['files']['a.tsx']['whole'] and 'not shown' not in packed.text and 'line 462 of' in packed.text

def test_every_text_the_task_names_is_visible_when_the_file_is_too_big_to_show_whole():
    content = big_component()
    task = 'Use SEARCH/REPLACE blocks: Scanning tables -> Scanning records; Manager role required -> Administrator access required; `Take Backup` -> Create backup'
    packed = contextpack.pack({'Panel.tsx': snap('Panel.tsx', content)}, task, 14000)
    info = packed.report['files']['Panel.tsx']
    assert not info['whole'] and info['shown'] < info['lines'] and packed.report['used_chars'] <= 14000 + 400
    for text in ("manifest: 'Scanning tables'", '<p>Manager role required</p>', '<button>Take Backup</button>'):
        assert text in packed.text
    assert 'not shown' in packed.text and packed.report['coverage']['hidden'] == [] and 'filler line 0 ' in packed.text  # the head is always there

def test_old_fixed_rule_would_have_missed_the_late_target_that_packing_finds():
    # Only identifiers (not quoted phrases) used to select regions; a phrase deep in the file was never shown.
    content = big_component()
    old_style = {i for name in localize.identifiers('Rename Take Backup to Create backup') for i, row in enumerate(content.split('\n')) if name in row}
    assert old_style == set()
    packed = contextpack.pack({'P.tsx': snap('P.tsx', content)}, 'Rename `Take Backup` to Create backup', 14000)
    assert '<button>Take Backup</button>' in packed.text

def test_protocol_words_are_not_treated_as_identifiers():
    names = localize.identifiers('Make edits using SEARCH/REPLACE blocks in `FILE` and update rowCount and agent_sessions')
    assert 'SEARCH' not in names and 'REPLACE' not in names and 'FILE' not in names and 'rowCount' in names and 'agent_sessions' in names

def test_missing_mappings_are_reported_not_hidden():
    packed = contextpack.pack({'a.py': snap('a.py', 'alpha = 1\n')}, 'Rename alpha -> beta; Zebra panel -> Giraffe panel', 20000)
    assert packed.report['coverage']['missing_mappings'] == ['Zebra panel']

def test_references_are_read_only_signatures_when_they_do_not_fit():
    api = ''.join(f'export function call{i}(x: number) {{\n  return x + {i};\n}}\n' for i in range(400))  # ~1200 lines
    small = 'export const THEME = { blue: "#1a73e8" };\n'
    packed = contextpack.pack({'a.tsx': snap('a.tsx', 'x = 1\n')}, 'change x',
                              20000, [('src/theme.ts', small, 'h1'), ('src/api.ts', api, 'h2')])
    assert 'REFERENCE (read-only, not editable): src/theme.ts' in packed.text and 'blue: "#1a73e8"' in packed.text
    assert 'signatures only' in packed.text and '1: export function call0' in packed.text
    assert [r['mode'] for r in packed.report['references']] == ['whole', 'signatures'] and packed.report['references'][1]['sha256'] == 'h2'

def test_budget_shrinks_with_a_larger_output_cap_and_a_longer_prompt():
    assert contextpack.budget_chars(16384, 8192, 3000) < contextpack.budget_chars(16384, 4096, 3000) < contextpack.budget_chars(32768, 4096, 3000)
    assert contextpack.budget_chars(16384, 4096, 20000) < contextpack.budget_chars(16384, 4096, 3000)

def test_reference_implementation_over_6000_chars_uses_available_budget():
    source = 'def scenario():\n    return (0, {}, {})\n' + '# padding\n' * 650
    packed = contextpack.pack({'test_demo.py': snap('test_demo.py', None)}, 'Test scenario return contract', 30000,
                              [('demo.py', source, 'h')])
    assert packed.report['references'][0]['mode'] == 'whole'
    assert 'return (0, {}, {})' in packed.text

def test_large_reference_selects_named_implementation_and_reports_omissions():
    source = '# filler\n' * 3000 + 'def scenario():\n    return (0, {}, {})\n'
    blocks, info = contextpack.pack_references([('demo.py', source, 'h')], 2000, 'Test scenario return contract')
    assert info[0]['mode'] == 'bodies' and 'return (0, {}, {})' in blocks[0]
    huge = 'def scenario():\n' + '    value = 123\n' * 400
    blocks, info = contextpack.pack_references([('demo.py', huge, 'h')], 500, 'Test scenario')
    assert blocks == [] and info[0]['omitted_symbols'] == ['scenario']

def job(tmp_path, repo, task, allowed, read=(), key='cp'):
    directory = tmp_path / 'job'
    directory.mkdir()
    (directory / 'workspace').mkdir()
    (directory / 'request.json').write_text(JobRequest(role='editor', repo=str(repo), task=task, allowed_paths=list(allowed), read_paths=list(read), idempotency_key=key).model_dump_json())
    return directory

@pytest.mark.asyncio
async def test_run_edit_shows_references_and_notes_texts_it_could_not_find(tmp_path, repo, monkeypatch):
    (repo / 'theme.ts').write_text('export const BLUE = "#1a73e8";\n')
    directory = job(tmp_path, repo, 'Rename answer -> reply; Ghost label -> Visible label', ['app.ts'], ['app.ts', 'theme.ts'])
    prompts = []
    async def call(client, d, label, name, step, body, event):
        prompts.append(body['messages'][1]['content'])
        return {'message': {'content': 'FILE: app.ts\n<<<<<<< SEARCH\nexport const answer = 41;\n=======\nexport const reply = 41;\n>>>>>>> REPLACE\nEND OF EDITS'}, 'done_reason': 'stop', 'eval_count': 30}
    monkeypatch.setattr(calls, 'call', call)
    await pipelines.run_edit(directory, 'edit', 'Task:\nRename', 'edit')
    assert 'REFERENCE (read-only, not editable): theme.ts' in prompts[0] and '"#1a73e8"' in prompts[0] and "'Ghost label'" in prompts[0]
    context = json.loads((directory / 'edit.context.json').read_text())
    assert context['coverage']['missing_mappings'] == ['Ghost label'] and context['references'][0]['path'] == 'theme.ts'
    assert (repo / 'app.ts').read_text() == 'export const reply = 41;\n'

@pytest.mark.asyncio
async def test_run_edit_refuses_without_a_model_call_when_no_mapped_text_exists_in_the_authorized_files(tmp_path, repo, monkeypatch):
    directory = job(tmp_path, repo, 'Panel: Zebra one -> Giraffe one; Zebra two -> Giraffe two', ['app.ts'], key='cp2')
    async def call(*a, **k):
        raise AssertionError('the model must not be called')
    monkeypatch.setattr(calls, 'call', call)
    await pipelines.run_edit(directory, 'edit', 'Task:\nRename', 'edit')
    report = final_report(json.loads((directory / 'edit.session.json').read_text()))
    assert report['status'] == 'BLOCKED' and 'None of the 2' in report['risks'] and 'No model call' in report['findings']
