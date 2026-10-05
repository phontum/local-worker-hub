"""Replays of the first real frontier failures (docs/KNOWN_ISSUES.md) through the real edit pipeline with a fake model.

Fixtures are synthetic: a large component-shaped file, its stylesheet and its test, never the private repository's source."""
import hashlib
import json
import uuid

import pytest

from hub import calls, pipelines, textedit
from hub.models import JobRequest
from hub.report import final_report
from hub.scoped import ScopedFiles

COMPONENT = ''.join(f'export function Part{i}() {{\n  return label({i}, "Take Backup {i}");\n}}\n' for i in range(1, 41))  # 120 lines
TEST = ''.join(f"it('case {i}', () => {{\n  expect(render({i})).toBe({i});\n}});\n" for i in range(1, 31))  # 90 lines
CSS = '.panel {\n  color: red;\n}\n'

def block(path, search, replace):
    return f'FILE: {path}\n<<<<<<< SEARCH\n{search}\n=======\n{replace}\n>>>>>>> REPLACE\n'

@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'proj'
    (root / 'src').mkdir(parents=True)
    (root / 'src' / 'Panel.tsx').write_text(COMPONENT)
    (root / 'src' / 'panel.css').write_text(CSS)
    (root / 'src' / 'panel.test.tsx').write_text(TEST)
    return root

ALLOWED = ['src/Panel.tsx', 'src/panel.css', 'src/panel.test.tsx']

async def run(tmp_path, project, replies, monkeypatch, allowed=ALLOWED, task='Rename labels', **request_kw):
    """Run the edit pipeline with scripted model replies, each (text, done_reason, output_tokens)."""
    directory = tmp_path / ('job' + uuid.uuid4().hex[:6])
    directory.mkdir()
    (directory / 'workspace').mkdir()
    (directory / 'request.json').write_text(JobRequest(role='editor', repo=str(project), task=task, allowed_paths=allowed, idempotency_key='inc' + str(len(replies)), **request_kw).model_dump_json())
    prompts = []
    async def call(client, d, label, name, step, body, event):
        text, reason, tokens = replies.pop(0)
        prompts.append(body['messages'][1]['content'])
        return {'message': {'content': text}, 'done_reason': reason, 'eval_count': tokens}
    monkeypatch.setattr(calls, 'call', call)
    await pipelines.run_edit(directory, 'edit', 'Task:\n' + task, 'edit')
    report = final_report(json.loads((directory / 'edit.session.json').read_text()))
    session = json.loads((directory / 'edit.session.json').read_text())
    meta = json.loads((directory / 'edit.turns.json').read_text())
    return report, session, meta, prompts, directory

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

@pytest.mark.asyncio
async def test_a_reply_cut_off_at_the_output_limit_applies_nothing_and_says_why(tmp_path, project, monkeypatch):
    # The first block is complete, the second is cut off; the limit was reached. Nothing may be applied, not even the complete block.
    reply = block('src/panel.css', 'color: red;', 'color: blue;') + 'FILE: src/Panel.tsx\n<<<<<<< SEARCH\nexport function Part1() {\n  return label(1, "Take Backup 1");\n=======\nexport function Part1() {\n  return label(1, "Create'
    before = {p: digest(project / p) for p in ALLOWED}
    report, session, meta, prompts, _ = await run(tmp_path, project, [(reply, 'length', 4096)], monkeypatch, continuation=False)
    assert {p: digest(project / p) for p in ALLOWED} == before
    assert report['status'] == 'PARTIAL' and 'cut off at 4096 of 4096 tokens' in report['risks'] and 'No edits were applied' in report['findings']
    assert session['info']['generations'][0]['done_reason'] == 'length' and session['info']['generations'][0]['truncated'] and meta['turns'][0]['truncated'] and len(prompts) == 1  # no pointless retry of the same oversized task

@pytest.mark.asyncio
async def test_hitting_the_cap_exactly_counts_as_truncated_even_without_a_length_reason(tmp_path, project, monkeypatch):
    reply = block('src/panel.css', 'color: red;', 'color: blue;') + 'END OF EDITS'
    report, _, meta, _, _ = await run(tmp_path, project, [(reply, 'stop', 4096)], monkeypatch, continuation=False)
    assert (project / 'src' / 'panel.css').read_text() == CSS and meta['turns'][0]['truncated'] and 'cut off' in report['risks']

@pytest.mark.asyncio
async def test_missing_terminator_applies_nothing_then_a_corrected_reply_applies(tmp_path, project, monkeypatch):
    broken = 'FILE: src/panel.css\n<<<<<<< SEARCH\ncolor: red;\n=======\ncolor: blue;\n'  # no >>>>>>> REPLACE
    fixed = block('src/panel.css', 'color: red;', 'color: blue;') + 'END OF EDITS\nSummary: recolored'
    report, _, meta, prompts, _ = await run(tmp_path, project, [(broken, 'stop', 60), (fixed, 'stop', 60)], monkeypatch, allowed=['src/panel.css'])
    assert 'missing >>>>>>> REPLACE' in prompts[1] and 'nothing was written' in prompts[1]
    assert report['status'] == 'COMPLETE' and (project / 'src' / 'panel.css').read_text() == '.panel {\n  color: blue;\n}\n'
    assert [t['truncated'] for t in meta['turns']] == [False, False] and meta['turns'][0]['errors']

@pytest.mark.asyncio
async def test_missing_separator_is_reported_not_guessed(tmp_path, project, monkeypatch):
    broken = 'FILE: src/panel.css\n<<<<<<< SEARCH\ncolor: red;\n>>>>>>> REPLACE\n'
    report, _, _, _, _ = await run(tmp_path, project, [(broken, 'stop', 40), (broken, 'stop', 40)], monkeypatch, allowed=['src/panel.css'])
    assert report['status'] == 'PARTIAL' and 'missing =======' in report['risks'] and (project / 'src' / 'panel.css').read_text() == CSS

@pytest.mark.asyncio
async def test_a_large_region_replaced_by_a_fragment_is_blocked(tmp_path, project, monkeypatch):
    region = '\n'.join(TEST.split('\n')[:60])  # 60 existing lines
    destructive = block('src/panel.test.tsx', region, 'toHaveBeenCalled();') + 'END OF EDITS'
    report, _, meta, prompts, _ = await run(tmp_path, project, [(destructive, 'stop', 300), (destructive, 'stop', 300)], monkeypatch, allowed=['src/panel.test.tsx'])
    assert (project / 'src' / 'panel.test.tsx').read_text() == TEST
    assert 'replaces 60 lines with 1' in prompts[1] and 'removes existing code' in report['risks'] and report['status'] == 'PARTIAL'

@pytest.mark.asyncio
async def test_an_ordinary_small_replacement_is_not_flagged(tmp_path, project, monkeypatch):
    region = '\n'.join(TEST.split('\n')[:30])
    smaller = '\n'.join(TEST.split('\n')[:30]).replace('case 1', 'case one')
    report, _, _, _, _ = await run(tmp_path, project, [(block('src/panel.test.tsx', region, smaller) + 'END OF EDITS', 'stop', 300)], monkeypatch, allowed=['src/panel.test.tsx'])
    assert report['status'] == 'COMPLETE' and "case one" in (project / 'src' / 'panel.test.tsx').read_text()

@pytest.mark.asyncio
async def test_one_bad_file_means_no_file_is_written_but_the_good_edit_is_kept_for_takeover(tmp_path, project, monkeypatch):
    first = block('src/panel.css', 'color: red;', 'color: blue;') + block('src/Panel.tsx', 'this text is not in the file', 'x') + 'END OF EDITS'
    report, _, meta, prompts, directory = await run(tmp_path, project, [(first, 'stop', 200), (block('src/Panel.tsx', 'still not there', 'x') + 'END OF EDITS', 'stop', 100)],
                                                    monkeypatch, allowed=['src/panel.css', 'src/Panel.tsx'])
    assert (project / 'src' / 'panel.css').read_text() == CSS and (project / 'src' / 'Panel.tsx').read_text() == COMPONENT
    assert report['status'] == 'PARTIAL' and 'src/Panel.tsx' in report['risks'] and 'staged.diff' in report['findings']
    assert '+  color: blue;' in (directory / 'staged.diff').read_text() and meta['staged_not_applied'] == ['src/panel.css']
    assert 'src/Panel.tsx' in prompts[1].split('Current content:')[1] and '.panel {' not in prompts[1].split('Current content:')[1]  # only the failing file is redone

@pytest.mark.asyncio
async def test_earlier_clean_edits_stay_staged_and_commit_together_with_the_corrected_file(tmp_path, project, monkeypatch):
    first = block('src/panel.css', 'color: red;', 'color: blue;') + block('src/Panel.tsx', 'not present', 'x') + 'END OF EDITS'
    second = block('src/Panel.tsx', 'export function Part1() {\n  return label(1, "Take Backup 1");', 'export function Part1() {\n  return label(1, "Create backup 1");') + 'END OF EDITS\nSummary: both done'
    report, _, _, _, _ = await run(tmp_path, project, [(first, 'stop', 200), (second, 'stop', 100)], monkeypatch, allowed=['src/panel.css', 'src/Panel.tsx'])
    assert report['status'] == 'COMPLETE' and '2 file(s)' in report['findings']
    assert 'color: blue' in (project / 'src' / 'panel.css').read_text() and 'Create backup 1' in (project / 'src' / 'Panel.tsx').read_text()

def test_a_whole_block_may_contain_separator_lines_such_as_markdown_underlines():
    reply = 'FILE: README.md\n<<<<<<< WHOLE\nTitle\n=======\nBody\n>>>>>>> WHOLE\nEND OF EDITS'
    parsed = textedit.parse(reply)
    assert not parsed.problems and parsed.edits[0].replace == 'Title\n=======\nBody\n'

def test_summary_is_read_only_from_outside_the_blocks():
    reply = block('a.py', 'x', 'Summary: not the summary') + 'END OF EDITS\nSummary: the real one'
    parsed = textedit.parse(reply)
    assert parsed.summary == 'the real one' and parsed.edits[0].replace == 'Summary: not the summary'
    assert textedit.parse(block('a.py', 'x', 'y')).summary == ''

def test_markers_must_be_exactly_seven_characters_and_blocks_cannot_nest():
    assert textedit.parse('FILE: a.py\n<<<<< SEARCH\nx\n=====\ny\n>>>>> REPLACE\n').edits == []
    nested = 'FILE: a.py\n<<<<<<< SEARCH\nx\n<<<<<<< SEARCH\ny\n=======\nz\n>>>>>>> REPLACE\n'
    assert textedit.parse(nested).problems and not textedit.parse(nested).edits[:0]
    assert textedit.parse('<<<<<<< SEARCH\nx\n=======\ny\n>>>>>>> REPLACE\n').problems == ['block 1 has no FILE line before it']

def test_the_sentinel_is_required_only_when_the_finish_state_is_unknown():
    reply = block('a.py', 'x', 'y')
    assert not textedit.parse(reply).problems
    assert textedit.parse(reply, require_sentinel=True).problems and not textedit.parse(reply + 'END OF EDITS', require_sentinel=True).problems

def test_commit_refuses_when_a_file_changed_after_it_was_read_and_rolls_back_a_failed_second_write(project, monkeypatch):
    files = ScopedFiles(JobRequest(role='editor', repo=str(project), task='t', allowed_paths=ALLOWED, idempotency_key='commit1'))
    snaps = {p: textedit.snapshot(files, p) for p in ('src/panel.css', 'src/Panel.tsx')}
    planned = textedit.plan([textedit.Edit('src/panel.css', 'color: red;', 'color: blue;'), textedit.Edit('src/Panel.tsx', 'Take Backup 1"', 'Create 1"')], snaps)
    assert not planned.errors
    (project / 'src' / 'Panel.tsx').write_text(COMPONENT + '// user edit\n')
    changed, errors = textedit.commit(files, planned.contents, snaps)
    assert changed == [] and 'changed since it was read' in errors[0] and (project / 'src' / 'panel.css').read_text() == CSS
    (project / 'src' / 'Panel.tsx').write_text(COMPONENT)
    original = files._replace_observed
    def failing(path, content, expected):
        if path == 'src/Panel.tsx':
            raise OSError('disk full')
        return original(path, content, expected)
    monkeypatch.setattr(files, '_replace_observed', failing)
    changed, errors = textedit.commit(files, planned.contents, snaps)
    assert changed == [] and 'nothing was applied' in errors[0] and (project / 'src' / 'panel.css').read_text() == CSS

def test_edit_load_of_file_shrink_and_definition_loss_are_flagged():
    big = '\n'.join(f'def f{i}():\n    pass' for i in range(30))
    assert textedit.suspicious('m.py', big, 'x = 1\n' * 5)  # file loses most of its lines
    assert textedit.suspicious('m.py', big, big.replace('def f1():', '#').replace('def f2():', '#'))
    assert not textedit.suspicious('m.py', big, big.replace('pass', 'return 1', 3))


@pytest.mark.asyncio
async def test_a_cut_off_reply_keeps_its_complete_blocks_staged_and_the_model_continues(tmp_path, project, monkeypatch):
    first = (block('src/panel.css', 'color: red;', 'color: blue;') +
             block('src/Panel.tsx', 'export function Part1() {\n  return label(1, "Take Backup 1");', 'export function Part1() {\n  return label(1, "Create backup 1");') +
             'FILE: src/Panel.tsx\n<<<<<<< SEARCH\nexport function Part2() {\n  return label(2, "Take Backup 2");\n=======\nexport function Part2() {\n  return label(2, "Cre')
    second = block('src/Panel.tsx', 'export function Part2() {\n  return label(2, "Take Backup 2");', 'export function Part2() {\n  return label(2, "Create backup 2");') + 'END OF EDITS\nSummary: done'
    report, session, meta, prompts, directory = await run(tmp_path, project, [(first, 'length', 4096), (second, 'stop', 300)], monkeypatch, allowed=['src/panel.css', 'src/Panel.tsx'])
    assert report['status'] == 'COMPLETE' and 'after 1 continuation turn(s)' in report['findings']
    text = (project / 'src' / 'Panel.tsx').read_text()
    assert 'Create backup 1' in text and 'Create backup 2' in text and 'Take Backup 3' in text and 'color: blue' in (project / 'src' / 'panel.css').read_text()
    assert 'cut off by the output limit after 2 complete edit block(s)' in prompts[1] and 'Create backup 1' in prompts[1]  # the model is shown the staged content, not the original
    assert [t['continuation'] for t in meta['turns']] == [False, True] and [t['blocks'] for t in meta['turns']] == [2, 1] and meta['continuations'] == 1
    assert session['info']['generations'][0]['truncated'] and not session['info']['generations'][1]['truncated']

@pytest.mark.asyncio
async def test_continuation_that_makes_no_progress_or_a_cut_before_any_block_applies_nothing(tmp_path, project, monkeypatch):
    nothing = 'FILE: src/panel.css\n<<<<<<< SEARCH\ncolor: red;\n=======\ncolor: bl'
    report, _, _, prompts, _ = await run(tmp_path, project, [(nothing, 'length', 4096)], monkeypatch, allowed=['src/panel.css'])
    assert report['status'] == 'PARTIAL' and 'nothing to continue from' in report['risks'] and (project / 'src' / 'panel.css').read_text() == CSS and len(prompts) == 1
    one = block('src/panel.css', 'color: red;', 'color: blue;') + 'FILE: src/panel.css\n<<<<<<< SEARCH\n.panel {\n=======\n.pa'
    report, _, meta, _, directory = await run(tmp_path, project, [(one, 'length', 4096), (one, 'length', 4096)], monkeypatch, allowed=['src/panel.css'])
    assert report['status'] == 'PARTIAL' and (project / 'src' / 'panel.css').read_text() == CSS  # no progress on the second turn: stop, write nothing
    assert 'color: blue' in (directory / 'staged.diff').read_text()

@pytest.mark.asyncio
async def test_a_complete_block_that_does_not_match_stops_the_continuation(tmp_path, project, monkeypatch):
    bad = block('src/panel.css', 'this line is not in the file', 'x') + 'FILE: src/panel.css\n<<<<<<< SEARCH\ncolor'
    report, _, _, prompts, _ = await run(tmp_path, project, [(bad, 'length', 4096)], monkeypatch, allowed=['src/panel.css'])
    assert report['status'] == 'PARTIAL' and 'does not match' in report['risks'] and len(prompts) == 1

@pytest.mark.asyncio
async def test_staged_artifacts_are_written_as_the_job_goes_so_a_killed_job_leaves_them(tmp_path, project, monkeypatch):
    first = block('src/panel.css', 'color: red;', 'color: blue;') + 'FILE: src/panel.css\n<<<<<<< SEARCH\n.panel {\n=======\n.pa'
    replies = [(first, 'length', 4096)]
    directory = tmp_path / 'job'
    directory.mkdir()
    (directory / 'workspace').mkdir()
    (directory / 'request.json').write_text(JobRequest(role='editor', repo=str(project), task='Recolor', allowed_paths=['src/panel.css'], idempotency_key='killed').model_dump_json())
    async def call(client, d, label, name, step, body, event):
        if not replies:
            assert (directory / 'edit.turns.json').exists()  # already flushed after the first continuation was staged
            raise RuntimeError('killed')
        text, reason, tokens = replies.pop(0)
        return {'message': {'content': text}, 'done_reason': reason, 'eval_count': tokens}
    monkeypatch.setattr(calls, 'call', call)
    with pytest.raises(RuntimeError, match='killed'):
        await pipelines.run_edit(directory, 'edit', 'Task:\nRecolor', 'edit')
    assert json.loads((directory / 'edit.turns.json').read_text())['continuations'] == 1
