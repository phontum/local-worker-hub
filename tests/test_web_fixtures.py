import asyncio
import json
import time
import pytest
from hub.skills.research import web_fixtures
from hub.settings import STATE

@pytest.fixture(autouse=True)
def private_config(tmp_path, monkeypatch):
    monkeypatch.setattr(web_fixtures, 'CONFIG', tmp_path / 'config')
    (tmp_path / 'config').mkdir()
    return tmp_path / 'config'

def configure(config, mode, directory, expires=None):
    (config / 'web-fixtures.json').write_text(json.dumps({'mode': mode, 'dir': str(directory), 'expires': expires if expires is not None else time.time() + 3600}))

def audit_log():
    rows = []
    return rows, lambda kind, data: rows.append((kind, data))

def test_off_by_default_returns_the_original_functions(monkeypatch):
    async def search(query, config, audit): return [], None, []
    assert web_fixtures.wrap_search(search) is search

def test_record_then_replay_without_network(monkeypatch, private_config):
    root = STATE / 'benchmarks' / 'fixtures-test'
    calls = []
    async def search(query, config, audit):
        calls.append(query)
        return [{'title': 'T', 'url': 'https://example.org/a', 'snippet': 's'}], 'exa', []
    async def read_page(url):
        calls.append(url)
        return {'url': url, 'text': 'page body', 'kind': 'page', 'title': 'T', 'observed_at': '2026-01-01T00:00:00+00:00', 'current_eligible': True}
    configure(private_config, 'record', root)
    recorded = asyncio.run(web_fixtures.wrap_search(search)('  Weather  in Oslo ', {}, lambda *a: None))
    page = asyncio.run(web_fixtures.wrap_read_page(read_page)('https://example.org/a'))
    assert len(calls) == 2 and all(p.stat().st_mode & 0o777 == 0o600 for p in root.iterdir())
    configure(private_config, 'replay', root)
    rows, audit = audit_log()
    assert asyncio.run(web_fixtures.wrap_search(search)('weather in oslo', {}, audit)) == recorded
    assert asyncio.run(web_fixtures.wrap_read_page(read_page)('https://example.org/a')) == page
    assert len(calls) == 2 and rows[0][1]['replayed'] is True

def test_replay_miss_is_reported_not_invented(private_config):
    configure(private_config, 'replay', STATE / 'benchmarks' / 'fixtures-miss')
    async def never(*a): raise AssertionError('network used')
    rows, audit = audit_log()
    assert asyncio.run(web_fixtures.wrap_search(never)('unseen', {}, audit)) == ([], None, ['fixture miss'])
    assert asyncio.run(web_fixtures.wrap_read_page(never)('https://example.org/x')) == {'url': 'https://example.org/x', 'error': 'fixture miss'}

def test_fixture_directory_must_stay_in_private_state(private_config, tmp_path):
    configure(private_config, 'record', tmp_path / 'outside')
    with pytest.raises(ValueError):
        web_fixtures.directory()

def test_malformed_or_unknown_config_means_off(private_config):
    for text in ('not json', '[]', '{"mode":"record"}', '{"mode":"erase","dir":"x"}'):
        (private_config / 'web-fixtures.json').write_text(text)
        assert web_fixtures.directory() == (None, None)

def test_expired_config_is_ignored_so_an_interrupted_run_cannot_leave_replay_on(private_config):
    configure(private_config, 'replay', STATE / 'benchmarks' / 'fixtures-old', expires=time.time() - 1)
    assert web_fixtures.directory() == (None, None)
    (private_config / 'web-fixtures.json').write_text(json.dumps({'mode': 'replay', 'dir': str(STATE / 'benchmarks' / 'x')}))
    assert web_fixtures.directory() == (None, None)  # no expiry at all is treated as expired

def test_provider_record_replay_keys_on_arguments_and_units_and_skips_the_clock(monkeypatch, private_config):
    from hub.skills.research.providers.base import ProviderError, ProviderResult
    from hub.skills.research.providers.fx import Fx, FxArgs
    from hub.skills.research.providers.clock import Clock, ClockArgs
    root = STATE / 'benchmarks' / 'fixtures-providers'
    calls = []
    async def fetch(self, args, prefs, task=''):
        calls.append(args.amount)
        return ProviderResult('ECB', 'https://frankfurter.dev/', f'{args.amount} EUR', {'method': 'provider-api', 'observed_at': 'then'}, [{'n': 1}])
    monkeypatch.setattr(Fx, 'fetch', fetch)
    args, prefs = FxArgs(amount=5, from_currency='EUR', to_currency='USD'), {'units': 'metric'}
    configure(private_config, 'record', root)
    recorded = asyncio.run(web_fixtures.wrap_provider(Fx())(args, prefs))
    configure(private_config, 'replay', root)
    assert asyncio.run(web_fixtures.wrap_provider(Fx())(args, prefs)) == recorded and calls == [5]
    with pytest.raises(ProviderError, match='fixture miss'):
        asyncio.run(web_fixtures.wrap_provider(Fx())(FxArgs(amount=6, from_currency='EUR', to_currency='USD'), prefs))
    with pytest.raises(ProviderError, match='fixture miss'):
        asyncio.run(web_fixtures.wrap_provider(Fx())(args, {'units': 'imperial'}))
    clock = Clock()
    assert web_fixtures.wrap_provider(clock) == clock.fetch  # never frozen

def test_replay_serves_the_nearest_recorded_query_when_a_prompt_rewords_it(monkeypatch, private_config):
    root = STATE / 'benchmarks' / 'fixtures-nearest'
    async def search(query, config, audit):
        return [{'title': 'T', 'url': 'https://example.org/spec', 'snippet': 's'}], 'exa', []
    configure(private_config, 'record', root)
    asyncio.run(web_fixtures.wrap_search(search)('NVIDIA RTX 5070 memory bandwidth specifications', {}, lambda *a: None))
    configure(private_config, 'replay', root)
    async def never(*a): raise AssertionError('network used')
    rows, audit = audit_log()
    results, provider, failures = asyncio.run(web_fixtures.wrap_search(never)('NVIDIA RTX 5070 maximum memory bandwidth specs', {}, audit))
    assert results and provider == 'exa' and failures == [] and rows[0][1]['nearest_query'] == 'NVIDIA RTX 5070 memory bandwidth specifications' and 0.6 <= rows[0][1]['similarity'] < 1
    # An unrelated query is a miss, not a wrong answer.
    assert asyncio.run(web_fixtures.wrap_search(never)('cheapest SSD in Germany', {}, audit)) == ([], None, ['fixture miss'])
