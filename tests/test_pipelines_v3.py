import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from hub import answers, ask, calls, engine, localize, pipelines, retrieval, textedit
from hub.models import JobRequest
from hub.report import final_report, parse_report
from hub.scoped import ScopedFiles, ScopeError

PREFS = {'units': 'metric', 'clock': '24h', 'timezone': 'Europe/Belgrade'}
NOW = datetime.now(timezone.utc).isoformat()

# Retrieval ------------------------------------------------------------------------------------------------------------

def test_parse_exa_blocks():
    text = 'Title: A\nURL: https://a.example/x\nPublished: N/A\nBody A\n\nTitle: B\nURL: https://b.example/y\nBody B'
    assert [r['url'] for r in retrieval.parse_exa(text)] == ['https://a.example/x', 'https://b.example/y']

def test_pick_urls_prefers_one_page_per_domain_and_interleaves_queries():
    q1 = [{'url': 'https://a.example/1'}, {'url': 'https://a.example/2'}, {'url': 'https://b.example/1'}]
    q2 = [{'url': 'https://c.example/1'}, {'url': 'https://a.example/3'}]
    assert retrieval.pick_urls([q1, q2], 3) == ['https://a.example/1', 'https://c.example/1', 'https://b.example/1']

def test_bm25_and_excerpt_selection_puts_each_pages_best_passage_first_and_snippets_last():
    pages = [{'url': 'https://w.example', 'title': 'Weather', 'kind': 'page', 'observed_at': NOW,
              'text': 'Cookie banner text.\n' + 'Novi Sad forecast 15:00 23 °C sunny wind 3 m/s.\n' + 'Unrelated news about football.'},
             {'url': 'https://x.example', 'title': 'Other', 'kind': 'page', 'observed_at': NOW, 'text': 'Novi Sad evening forecast 20:00 17 °C.'}]
    snippets = [{'url': 'https://s.example', 'title': 'Snippet', 'kind': 'snippet', 'observed_at': NOW, 'text': 'Old snippet 4 °C'}]
    excerpts = retrieval.select_excerpts('weather Novi Sad forecast', ['Novi Sad forecast'], pages, snippets)
    assert [e['n'] for e in excerpts] == list(range(1, len(excerpts) + 1))
    assert excerpts[-1]['kind'] == 'snippet' and {e['url'] for e in excerpts[:2]} == {'https://w.example', 'https://x.example'}
    assert retrieval.bm25('forecast', ['a forecast here', 'nothing']) [0] > 0 == retrieval.bm25('forecast', ['a forecast here', 'nothing'])[1]

@pytest.mark.asyncio
async def test_search_falls_back_through_providers_and_reports_failures(monkeypatch):
    async def down(*a): raise retrieval.ProviderUnavailable('SearXNG HTTP 502')
    async def exa(q): return [{'title': 'T', 'url': 'https://e.example', 'snippet': 's'}]
    monkeypatch.setattr(retrieval, 'searxng', down); monkeypatch.setattr(retrieval, 'exa', exa)
    events = []
    results, provider, failures = await retrieval.search('q', {'search_provider': 'searxng'}, lambda k, d: events.append((k, d)))
    assert provider == 'exa' and results[0]['url'] == 'https://e.example' and 'searxng' in failures[0]
    assert events[0][0] == 'web_search' and 'SearXNG HTTP 502' in events[0][1]['fallback_reason']

@pytest.mark.asyncio
async def test_gather_reads_pages_and_keeps_failed_reads_out(monkeypatch):
    async def search(query, config, audit): return [{'title': 'A', 'url': 'https://a.example', 'snippet': 'snip a'}, {'title': 'B', 'url': 'https://b.example', 'snippet': 'snip b'}], 'exa', []
    async def read(url):
        return {'url': url, 'text': 'Page A says the RTX 5070 costs 76.999 RSD and is in stock.', 'kind': 'page', 'title': 'A', 'observed_at': NOW} if 'a.' in url else {'url': url, 'error': 'Origin HTTP 403'}
    monkeypatch.setattr(retrieval, 'search', search); monkeypatch.setattr(retrieval, 'read_page', read)
    events = []
    excerpts, log = await retrieval.gather('RTX 5070 price', ['RTX 5070 price Serbia'], lambda k, d: events.append(k), config={'search_provider': 'exa'})
    assert excerpts[0]['url'] == 'https://a.example' and excerpts[0]['kind'] == 'page'
    assert any(e['kind'] == 'snippet' and e['url'] == 'https://b.example' for e in excerpts)  # the failed page still contributes its snippet, labelled
    assert events.count('web_fetch') == 2 and log['pages'][1]['error'] == 'Origin HTTP 403'

# Answer finishing ------------------------------------------------------------------------------------------------------

EXCERPTS = [{'n': 1, 'url': 'https://a.example/page', 'title': 'Shop A', 'kind': 'page', 'observed_at': '2026-10-04T10:00:00+00:00', 'text': '...'},
            {'n': 2, 'url': 'https://s.example', 'title': 'Snippet', 'kind': 'snippet', 'observed_at': '2026-10-04T10:00:00+00:00', 'text': '...'}]

def test_model_written_sources_and_unfetched_links_are_removed_and_real_sources_added():
    text = 'It costs 76.999 RSD (see https://invented.example/x).\nSource: Yandex Weather, as of 13:22\nИсточник: Яндекс'
    report = parse_report(answers.render(text, EXCERPTS, [1], True, 'price?', prefs=PREFS))
    assert 'invented.example' not in report['findings'] and 'Yandex' not in report['findings'] and 'Яндекс' not in report['findings']
    assert '- Shop A (https://a.example/page), as of 12:00' in report['findings'] and report['status'] == 'COMPLETE'

def test_snippet_sources_are_labelled_and_unanswered_is_partial():
    report = parse_report(answers.render('Not found in the sources.', EXCERPTS, [2], False, 'q', prefs=PREFS))
    assert 'search summary, may be outdated' in report['findings'] and report['status'] == 'PARTIAL'

def test_units_follow_preferences_unless_the_user_asked_for_them():
    assert '100°C' in answers.render('Water boils at 212°F.', [], [], True, 'At what temperature does water boil?', prefs=PREFS)
    assert '212°F' in answers.render('Water boils at 212°F.', [], [], True, 'What is the boiling point in Fahrenheit?', prefs=PREFS)
    assert 'ветер 3,2 км/ч' in answers.render('ветер 0,9 м/с', [], [], True, 'погода', prefs=PREFS)
    assert 'wind 10.8 km/h' in answers.render('wind 3 m/s', [], [], True, 'weather', prefs=PREFS)

def test_source_titles_are_converted_to_the_preferred_units_too():
    excerpt = {'n': 1, 'url': 'https://w.example/today', 'title': 'Novi Sad Weather Today | 62°F, Partly cloudy', 'kind': 'page', 'observed_at': NOW, 'text': 't'}
    metric = answers.render('Mild.', [excerpt], [1], True, 'Weather in Novi Sad?', prefs=PREFS)
    assert '17°C, Partly cloudy' in metric and '62°F' not in metric and 'https://w.example/today' in metric
    assert '62°F' in answers.render('Mild.', [excerpt], [1], True, 'Weather in Novi Sad in Fahrenheit?', prefs=PREFS)

# Ask pipeline ----------------------------------------------------------------------------------------------------------

def ask_job(tmp_path, task, **kw):
    directory = tmp_path / 'job'; directory.mkdir(); (directory / 'workspace').mkdir()
    (directory / 'request.json').write_text(JobRequest(role='personal', task=task, idempotency_key='ask', **kw).model_dump_json())
    return directory

def fake_models(monkeypatch, decision, answers_):
    seen = []
    async def call(client, directory, label, name, step, body, event):
        seen.append((name, body))
        if 'format' not in body:
            return {'message': {'content': 'thinking about it'}}
        value = decision if name == 'ask-decide' else answers_.pop(0)
        return {'message': {'content': json.dumps(value)}, 'done_reason': 'stop'}
    monkeypatch.setattr(calls, 'call', call)
    monkeypatch.setattr(calls, 'model_support', lambda spec: asyncio.sleep(0, None))
    return seen

@pytest.mark.asyncio
async def test_ask_searches_answers_from_excerpts_and_cites_only_fetched_pages(tmp_path, monkeypatch):
    directory = ask_job(tmp_path, 'What is the cheapest RTX 5070 in Serbia?')
    seen = fake_models(monkeypatch, {'needs_web': True, 'queries': ['RTX 5070 cena Srbija'], 'reply_language': 'English'},
                       [{'answer': 'The cheapest is 76.999 RSD at Shop A.\nSource: madeup.example', 'used': [1], 'answered': True, 'follow_up_query': ''}])
    gathered = []
    async def gather(question, queries, audit, **kw):
        gathered.append(queries); return [dict(EXCERPTS[0])], {'queries': queries}
    monkeypatch.setattr(ask, 'gather', gather)
    monkeypatch.setattr(ask, 'load_preferences', lambda: PREFS)
    await ask.run_ask(directory, 'work', 'Task:\nWhat is the cheapest RTX 5070 in Serbia?')
    report = final_report(json.loads((directory / 'work.session.json').read_text()))
    assert gathered == [['RTX 5070 cena Srbija']] and [n for n, _ in seen] == ['ask-decide', 'ask-answer']
    assert all('tools' not in body for _, body in seen)  # no tool calling at all
    assert report['status'] == 'COMPLETE' and 'madeup' not in report['findings'] and 'https://a.example/page' in report['findings']
    assert 'Excerpts:' in seen[1][1]['messages'][1]['content'] and json.loads((directory / 'ask.json').read_text())['decision']['needs_web']

@pytest.mark.asyncio
async def test_ask_without_web_needs_no_retrieval(tmp_path, monkeypatch):
    directory = ask_job(tmp_path, 'Hello!')
    fake_models(monkeypatch, {'needs_web': False, 'queries': [], 'reply_language': 'English'}, [{'answer': 'Hi! How can I help?', 'used': [], 'answered': True, 'follow_up_query': ''}])
    async def gather(*a, **k): raise AssertionError('no retrieval expected')
    monkeypatch.setattr(ask, 'gather', gather)
    await ask.run_ask(directory, 'work', 'Hello!')
    report = final_report(json.loads((directory / 'work.session.json').read_text()))
    assert report['status'] == 'COMPLETE' and report['findings'] == 'Hi! How can I help?'

@pytest.mark.asyncio
async def test_ask_follow_up_round_and_unreachable_web_note(tmp_path, monkeypatch):
    directory = ask_job(tmp_path, 'Weather in Novi Sad?')
    fake_models(monkeypatch, {'needs_web': True, 'queries': ['Novi Sad weather'], 'reply_language': 'English'},
                [{'answer': 'Not in the excerpts.', 'used': [], 'answered': False, 'follow_up_query': 'Novi Sad hourly forecast'},
                 {'answer': '23 °C at 15:00.', 'used': [2], 'answered': True, 'follow_up_query': ''}])
    rounds = []
    async def gather(question, queries, audit, **kw):
        rounds.append(queries)
        return ([dict(EXCERPTS[0])] if len(rounds) == 1 else [dict(EXCERPTS[0], n=1, url='https://hourly.example', title='Hourly')]), {}
    monkeypatch.setattr(ask, 'gather', gather); monkeypatch.setattr(ask, 'load_preferences', lambda: PREFS)
    await ask.run_ask(directory, 'work', 'Weather in Novi Sad?')
    report = final_report(json.loads((directory / 'work.session.json').read_text()))
    assert rounds == [['Novi Sad weather'], ['Novi Sad hourly forecast']] and 'https://hourly.example' in report['findings'] and report['status'] == 'COMPLETE'

@pytest.mark.asyncio
async def test_ask_reports_when_the_web_gave_nothing(tmp_path, monkeypatch):
    directory = ask_job(tmp_path, 'Price of X?')
    fake_models(monkeypatch, {'needs_web': True, 'queries': ['X price'], 'reply_language': 'English'}, [{'answer': 'Typically around 100 EUR.', 'used': [], 'answered': True, 'follow_up_query': ''}])
    async def gather(*a, **k): return [], {'failures': ['exa: down']}
    monkeypatch.setattr(ask, 'gather', gather); monkeypatch.setattr(ask, 'load_preferences', lambda: PREFS)
    await ask.run_ask(directory, 'work', 'Price of X?')
    findings = final_report(json.loads((directory / 'work.session.json').read_text()))['findings']
    assert findings.startswith("I couldn't get anything useful from the web") and 'Sources' not in findings

# Text edits -----------------------------------------------------------------------------------------------------------

def test_parse_blocks_with_fences_and_path_lines():
    reply = 'FILE: a.py\n```python\n<<<<<<< SEARCH\nx = 1\n=======\nx = 2\n>>>>>>> REPLACE\n```\nb.py\n<<<<<<< WHOLE\nnew\n>>>>>>> WHOLE\nSummary: changed x'
    parsed = textedit.parse(reply)
    assert [(e.path, e.search, e.replace) for e in parsed.edits] == [('a.py', 'x = 1', 'x = 2'), ('b.py', None, 'new\n')] and not parsed.problems and parsed.summary == 'changed x'

def test_apply_exact_whitespace_tolerant_ambiguous_and_missing():
    source = 'def f():\n    a = 1\n    return a\n'
    assert textedit.apply(source, '    a = 1', '    a = 2') == 'def f():\n    a = 2\n    return a\n'
    assert textedit.apply(source, 'a = 1\nreturn a', 'a = 3\nreturn a + 1') == 'def f():\n    a = 3\n    return a + 1\n'
    with pytest.raises(ValueError, match='2 places'): textedit.apply('x\nx\n', 'x', 'y')
    with pytest.raises(ValueError, match='does not match'): textedit.apply(source, 'b = 2', 'c')
    with pytest.raises(ValueError, match='empty'): textedit.apply(source, '  ', 'c')

def editor(repo, paths=('app.ts',)):
    return ScopedFiles(JobRequest(role='editor', repo=str(repo), task='t', allowed_paths=list(paths), idempotency_key='te'))

def test_apply_all_writes_through_guards_and_respects_freshness(repo):
    files = editor(repo, ('app.ts', 'new.ts'))
    snaps = {r[0]: r for r in (textedit.snapshot(files, p) for p in ('app.ts', 'new.ts'))}
    changed, errors = textedit.apply_all(files, [textedit.Edit('app.ts', 'answer = 41', 'answer = 42'), textedit.Edit('new.ts', None, 'export {}\n'),
                                                 textedit.Edit('other.ts', None, 'x')], snaps)
    assert changed == [] and 'not one of the authorized' in errors[0] and (repo / 'app.ts').read_text() == 'export const answer = 41;\n' and not (repo / 'new.ts').exists()  # all or nothing
    changed, errors = textedit.apply_all(files, [textedit.Edit('app.ts', 'answer = 41', 'answer = 42'), textedit.Edit('new.ts', None, 'export {}\n')], snaps)
    assert changed == ['app.ts', 'new.ts'] and not errors and (repo / 'app.ts').read_text() == 'export const answer = 42;\n'
    snaps = {r[0]: r for r in [textedit.snapshot(files, 'app.ts')]}
    (repo / 'app.ts').write_text('export const answer = 7;\n')  # changed by someone else after the host read it
    changed, errors = textedit.apply_all(files, [textedit.Edit('app.ts', 'answer = 42', 'answer = 43')], snaps)
    assert changed == [] and (repo / 'app.ts').read_text() == 'export const answer = 7;\n'

def test_whole_replacement_is_limited_to_small_files(repo):
    (repo / 'app.ts').write_text('x\n' * 200)
    files = editor(repo)
    snaps = {r[0]: r for r in [textedit.snapshot(files, 'app.ts')]}
    changed, errors = textedit.apply_all(files, [textedit.Edit('app.ts', None, 'y\n')], snaps)
    assert not changed and 'only for files under' in errors[0]

# Localization and pipelines -----------------------------------------------------------------------------------------

def test_identifiers_and_symbols():
    assert localize.identifiers('Where is `DEFAULT_ALIAS` used by model_name() in model_registry.py and "Queue full"?') == ['DEFAULT_ALIAS', 'Queue full', 'model_name', 'model_registry', 'model_registry.py']
    names = [n for n, _ in localize.python_symbols("LIMIT = 3\nclass A:\n    def go(self): pass\ndef helper(): pass\n")]
    assert names == ['LIMIT', 'A', 'A.go()', 'helper()']

def test_repo_map_ranks_files_mentioning_the_task(repo):
    (repo / 'limits.py').write_text('QUEUE_LIMIT = 32\n'); (repo / '.env').write_text('SECRET=1')
    files = ScopedFiles(JobRequest(role='investigator', repo=str(repo), task='queue limit', idempotency_key='rm'))
    text = localize.repo_map(files, 'Where is QUEUE_LIMIT set?')
    assert text.splitlines()[0].startswith('limits.py: QUEUE_LIMIT@1') and '.env' not in text

def investigate_job(tmp_path, repo, task, **kw):
    directory = tmp_path / 'job'; directory.mkdir(); (directory / 'workspace').mkdir()
    (directory / 'request.json').write_text(JobRequest(role='investigator', repo=str(repo), task=task, idempotency_key='inv', **kw).model_dump_json())
    return directory

@pytest.mark.asyncio
async def test_investigate_reads_chosen_ranges_with_evidence_and_answers(tmp_path, repo, monkeypatch):
    directory = investigate_job(tmp_path, repo, 'What is `answer` in app.ts?')
    outputs = [{'ranges': [{'path': 'app.ts', 'start': 1, 'end': 5}, {'path': '../etc/passwd', 'start': 1, 'end': 3}]},
               {'answer': 'answer is 41 (app.ts:1).', 'refs': [{'path': 'app.ts', 'line': 1, 'note': 'the constant'}], 'complete': True, 'more': []}]
    prompts = []
    async def call(client, d, label, name, step, body, event):
        prompts.append(body['messages'][1]['content']); return {'message': {'content': json.dumps(outputs.pop(0))}}
    monkeypatch.setattr(calls, 'call', call)
    await pipelines.run_investigate(directory, 'work', 'Task:\nWhat is `answer` in app.ts?')
    report = final_report(json.loads((directory / 'work.session.json').read_text()))
    events = [json.loads(l) for l in (directory / 'tools.jsonl').read_text().splitlines()]
    assert report['status'] == 'COMPLETE' and 'app.ts:1' in report['findings'] and 'Unread: ../etc/passwd' in report['risks']
    assert any(e['kind'] == 'read' and e['data']['evidence_id'] for e in events) and any(e['kind'] == 'read_missing' for e in events)
    assert 'Search hits' in prompts[0] and 'export const answer = 41' in prompts[1]
    assert '- app.ts:1 — the constant' in report['findings'] and 'host-verified' in report['findings']

async def answer_with(tmp_path, repo, monkeypatch, finding):
    directory = investigate_job(tmp_path, repo, 'What is `answer` in app.ts?')
    outputs = [{'ranges': [{'path': 'app.ts', 'start': 1, 'end': 1}]}, finding]
    async def call(client, d, label, name, step, body, event):
        return {'message': {'content': json.dumps(outputs.pop(0))}}
    monkeypatch.setattr(calls, 'call', call)
    await pipelines.run_investigate(directory, 'work', 'Task:\nWhat is `answer` in app.ts?')
    return final_report(json.loads((directory / 'work.session.json').read_text()))

@pytest.mark.asyncio
async def test_investigate_drops_references_to_lines_never_read(tmp_path, repo, monkeypatch):
    report = await answer_with(tmp_path, repo, monkeypatch, {'answer': 'It is 41.', 'complete': True, 'more': [], 'refs': [
        {'path': 'app.ts', 'line': 1, 'note': 'read'}, {'path': 'app.ts', 'line': 99, 'note': 'invented'}, {'path': 'ghost.ts', 'line': 3, 'note': 'unread'}]})
    assert report['status'] == 'COMPLETE' and '- app.ts:1' in report['findings'] and 'app.ts:99' not in report['findings']
    assert 'Unverified references dropped' in report['risks'] and 'app.ts:99' in report['risks'] and 'ghost.ts:3' in report['risks']

@pytest.mark.asyncio
async def test_investigate_without_a_verified_reference_is_never_complete(tmp_path, repo, monkeypatch):
    report = await answer_with(tmp_path, repo, monkeypatch, {'answer': 'It is 41.', 'complete': True, 'more': [], 'refs': [{'path': 'app.ts', 'line': 99, 'note': 'invented'}]})
    assert report['status'] == 'PARTIAL' and 'No host-verified path:line reference' in report['risks']

@pytest.mark.asyncio
async def test_edit_pipeline_applies_blocks_and_retries_once_with_the_exact_failure(tmp_path, repo, monkeypatch):
    directory = tmp_path / 'job'; directory.mkdir(); (directory / 'workspace').mkdir()
    (directory / 'request.json').write_text(JobRequest(role='editor', repo=str(repo), task='Set answer to 42', allowed_paths=['app.ts'], idempotency_key='ed').model_dump_json())
    replies = ['FILE: app.ts\n<<<<<<< SEARCH\nanswer = 40\n=======\nanswer = 42\n>>>>>>> REPLACE',
               'FILE: app.ts\n<<<<<<< SEARCH\nexport const answer = 41;\n=======\nexport const answer = 42;\n>>>>>>> REPLACE\nSummary: set answer to 42']
    prompts = []
    async def call(client, d, label, name, step, body, event):
        prompts.append(body['messages'][1]['content']); return {'message': {'content': replies.pop(0)}, 'done_reason': 'stop', 'eval_count': 50}
    monkeypatch.setattr(calls, 'call', call)
    await pipelines.run_edit(directory, 'edit', 'Task:\nSet answer to 42', 'edit')
    report = final_report(json.loads((directory / 'edit.session.json').read_text()))
    assert (repo / 'app.ts').read_text() == 'export const answer = 42;\n' and report['status'] == 'COMPLETE' and report['findings'] == 'Applied 1 file(s): app.ts. Model summary: set answer to 42'
    assert 'could not be applied' in prompts[1] and 'does not match' in prompts[1]
    assert any(json.loads(l)['kind'] == 'edit' for l in (directory / 'tools.jsonl').read_text().splitlines())

def test_secret_files_cannot_be_authorized_for_text_edits(repo):
    (repo / '.env').write_text('TOKEN=1\n')
    with pytest.raises(ScopeError):
        editor(repo, ('.env',))

# Engine routing --------------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize('role,phase,target', [('personal', 'work', 'ask.run_ask'), ('investigator', 'work', 'pipelines.run_investigate'),
                                               ('editor', 'edit', 'pipelines.run_edit'), ('editor', 'investigate', 'pipelines.run_investigate')])
async def test_engine_routes_to_host_pipelines_unless_agent_loop(tmp_path, repo, monkeypatch, role, phase, target):
    directory = tmp_path / 'job'; directory.mkdir()
    kw = {} if role == 'personal' else {'repo': str(repo)}
    if role == 'editor': kw['allowed_paths'] = ['app.ts']
    (directory / 'request.json').write_text(JobRequest(role=role, task='t', idempotency_key='route', **kw).model_dump_json())
    module, name = target.split('.')
    called = []
    async def fake(*a, **k): called.append(name)
    monkeypatch.setattr({'ask': ask, 'pipelines': pipelines}[module], name, fake)
    await engine.run(directory, 'work', 't', phase=phase)
    assert called == [name]

@pytest.mark.asyncio
async def test_verify_and_agent_loop_keep_the_tool_loop(tmp_path, monkeypatch):
    for kw in ({'verify': True}, {'agent_loop': True}):
        directory = tmp_path / str(len(kw) + hash(str(kw)) % 1000); directory.mkdir()
        (directory / 'request.json').write_text(JobRequest(role='personal', task='Hello', idempotency_key='loop', **kw).model_dump_json())
        async def fake(*a, **k): raise AssertionError('ask pipeline must not run')
        monkeypatch.setattr(ask, 'run_ask', fake)
        seen = []
        async def response(client, url, body, headers, on_segment):
            seen.append(body); return {'message': {'role': 'assistant', 'content': json.dumps({'status': 'COMPLETE', 'findings': 'Hi', 'files': 'None', 'checks': 'Not run', 'risks': 'None'}) if not body['tools'] else 'Hi'}, 'done_reason': 'stop'}
        monkeypatch.setattr(engine, 'chat_response', response)
        await engine.run(directory, 'work', 'Hello')
        assert seen

@pytest.mark.asyncio
@pytest.mark.parametrize('model_thinking,expected', [(None, False), (True, True)])
async def test_edit_thinks_only_when_explicitly_asked_even_if_the_role_profile_says_so(tmp_path, repo, monkeypatch, model_thinking, expected):
    directory = tmp_path / 'job'; directory.mkdir(); (directory / 'workspace').mkdir()
    (directory / 'role-config.json').write_text(json.dumps({'editor': {'thinking': True}}))
    (directory / 'request.json').write_text(JobRequest(role='editor', repo=str(repo), task='Set answer to 42', allowed_paths=['app.ts'], idempotency_key='th', model_thinking=model_thinking).model_dump_json())
    seen = []
    async def call(client, d, label, name, step, body, event):
        seen.append(body['think']); return {'message': {'content': 'FILE: app.ts\n<<<<<<< SEARCH\nexport const answer = 41;\n=======\nexport const answer = 42;\n>>>>>>> REPLACE'}, 'done_reason': 'stop', 'eval_count': 40}
    monkeypatch.setattr(calls, 'call', call)
    await pipelines.run_edit(directory, 'edit', 'Set answer to 42', 'edit')
    assert seen == [expected]

@pytest.mark.asyncio
async def test_a_bare_boolean_answer_is_retried_once_and_then_reported_as_partial(tmp_path, monkeypatch):
    directory = ask_job(tmp_path, 'Thanks, that was helpful.')
    seen = fake_models(monkeypatch, {'needs_web': False, 'queries': [], 'reply_language': 'English'},
                       [{'answer': 'true', 'used': [], 'answered': True, 'follow_up_query': ''}, {'answer': "You're welcome!", 'used': [], 'answered': True, 'follow_up_query': ''}])
    await ask.run_ask(directory, 'work', 'Thanks, that was helpful.')
    report = final_report(json.loads((directory / 'work.session.json').read_text()))
    assert report['status'] == 'COMPLETE' and report['findings'] == "You're welcome!"
    assert [n for n, _ in seen] == ['ask-decide', 'ask-answer', 'ask-answer'] and 'never a bare true' in seen[2][1]['messages'][0]['content']
    (tmp_path / 'second').mkdir()
    directory = ask_job(tmp_path / 'second', 'Thanks!')
    fake_models(monkeypatch, {'needs_web': False, 'queries': [], 'reply_language': 'English'}, [{'answer': 'true', 'used': [], 'answered': True, 'follow_up_query': ''}] * 2)
    await ask.run_ask(directory, 'work', 'Thanks!')
    stuck = final_report(json.loads((directory / 'work.session.json').read_text()))
    assert stuck['status'] == 'PARTIAL' and 'true' not in stuck['findings'].lower().split('could not')[0]

@pytest.mark.asyncio
async def test_investigate_harvests_verifiable_citations_from_the_answer_text(tmp_path, repo, monkeypatch):
    report = await answer_with(tmp_path, repo, monkeypatch, {'answer': 'It is 41 [E3:1] and also app.ts:1, but not app.ts:77.', 'complete': True, 'more': [], 'refs': []})
    assert report['status'] == 'COMPLETE' and report['findings'].count('- app.ts:1 —') == 1 and 'app.ts:77' not in report['risks']

@pytest.mark.asyncio
async def test_investigate_accepts_an_evidence_label_written_as_the_path(tmp_path, repo, monkeypatch):
    report = await answer_with(tmp_path, repo, monkeypatch, {'answer': 'It is 41.', 'complete': True, 'more': [], 'refs': [{'path': 'E3', 'line': 1, 'note': 'label used as path'}]})  # E1 and E2 are the identifier searches
    assert report['status'] == 'COMPLETE' and '- app.ts:1 — label used as path' in report['findings']

@pytest.mark.asyncio
async def test_investigate_derives_the_line_of_code_the_answer_quotes(tmp_path, repo, monkeypatch):
    report = await answer_with(tmp_path, repo, monkeypatch, {'answer': 'The value is set by `export const answer = 41` and a made-up `return nonsense(42)`.', 'complete': True, 'more': [], 'refs': []})
    assert report['status'] == 'COMPLETE' and '- app.ts:1 — quoted in the answer' in report['findings'] and 'nonsense' not in report['risks']

@pytest.mark.asyncio
async def test_short_backtick_spans_do_not_misalign_the_quoted_code_pairing(tmp_path, repo, monkeypatch):
    answer = 'The `answer` constant is defined as `export const answer = 41` here, and a note: `x`.'
    report = await answer_with(tmp_path, repo, monkeypatch, {'answer': answer, 'complete': True, 'more': [], 'refs': []})
    assert report['status'] == 'COMPLETE' and '- app.ts:1 — quoted in the answer' in report['findings']

@pytest.mark.asyncio
async def test_investigate_harvests_citations_worded_as_a_file_and_line(tmp_path, repo, monkeypatch):
    report = await answer_with(tmp_path, repo, monkeypatch, {'answer': 'The constant is defined in app.ts at line 1 and nowhere else (app.ts line 90 is made up).', 'complete': True, 'more': [], 'refs': []})
    assert report['status'] == 'COMPLETE' and '- app.ts:1 —' in report['findings'] and 'app.ts:90' not in report['findings']
