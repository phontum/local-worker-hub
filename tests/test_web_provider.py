import json
from unittest.mock import AsyncMock

import httpx
import pytest

from hub import web_provider
from hub.scoped import Research


def key_config(tmp_path,monkeypatch):
    monkeypatch.setattr(web_provider,'CONFIG',tmp_path)
    key=tmp_path/'langsearch-api-key';key.write_text('synthetic-test-secret');key.chmod(0o600)
    return key


def test_provider_config_and_private_key(tmp_path,monkeypatch):
    key=key_config(tmp_path,monkeypatch)
    assert web_provider.selection()['search_provider']=='exa'
    assert web_provider.configure('langsearch')=={'search_provider':'langsearch'}
    assert 'synthetic' not in (tmp_path/'web.json').read_text()
    key.chmod(0o644)
    with pytest.raises(ValueError,match='private'):web_provider.langsearch_key()
    key.unlink();key.symlink_to(tmp_path/'web.json')
    with pytest.raises(OSError):web_provider.langsearch_key()
    (tmp_path/'web.json').write_text('{"search_provider":"paid-unknown"}')
    with pytest.raises(ValueError):web_provider.selection()


@pytest.mark.asyncio
@pytest.mark.parametrize('http_status,code',[(200,200),(429,None),(302,None),(200,401)])
async def test_langsearch_request_errors_without_secret_or_redirect(tmp_path,monkeypatch,http_status,code):
    key_config(tmp_path,monkeypatch);seen=[]
    async def handle(request):
        seen.append(request)
        assert request.url==httpx.URL('https://api.langsearch.com/v1/web-search')
        assert request.headers['Authorization']=='Bearer synthetic-test-secret'
        assert json.loads(request.content)['contents']['text']=={'maxCharacters':24000}
        return httpx.Response(http_status,headers={'location':'https://evil.example'},json={'code':code,'message':'synthetic-test-secret',
            'data':{'webPages':{'value':[{'name':'Official','url':'https://example.com','text':'Source text'}]}}})
    original=httpx.AsyncClient
    monkeypatch.setattr(web_provider.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handle),**kw))
    if http_status==200 and code==200:
        assert (await web_provider.langsearch('public query'))[0]['text']=='Source text'
    else:
        with pytest.raises(ValueError) as error:await web_provider.langsearch('public query')
        assert 'synthetic-test-secret' not in str(error.value)
    assert len(seen)==1


@pytest.mark.asyncio
async def test_langsearch_full_text_reuse_not_snippet_promotion(monkeypatch):
    monkeypatch.setattr(web_provider,'langsearch',AsyncMock(return_value=[
        {'name':'Full','url':'https://example.com/full','text':'Verified page detail'},
        {'name':'Snippet','url':'https://example.com/snippet','snippet':'Discovery only'}]))
    monkeypatch.setattr('hub.scoped.public_url',AsyncMock(side_effect=lambda url:url))
    events=[];research=Research(lambda k,v:events.append((k,v)),provider='langsearch')
    research.call=AsyncMock(return_value='Fetched full page separately')
    await research.search_web('public query',objective='Verify these specific public facts')
    web_provider.langsearch.assert_awaited_once_with('public query')
    assert 'Verified page detail' in await research.fetch_web('https://example.com/full',mode='cached')
    research.call.assert_not_awaited()
    assert events[-1][1]['provider']=='langsearch-text'
    assert 'Fetched full page separately' in await research.fetch_web('https://example.com/snippet',mode='cached')
    assert events[-1][1]['provider']=='exa-keyless' and research.call.await_count==1

# A rate limit from the MCP client arrives wrapped in an ExceptionGroup; it must still mean "try the next provider".
def test_exa_rate_limit_inside_a_task_group_becomes_provider_unavailable(monkeypatch):
    import asyncio
    from hub.scoped import Research
    from hub.web_provider import ProviderUnavailable
    async def exa_call(self, name, args):
        raise ExceptionGroup('unhandled errors in a TaskGroup', [ProviderUnavailable('Exa free endpoint is rate-limited')])
    monkeypatch.setattr(Research, 'exa_call', exa_call)
    with pytest.raises(ProviderUnavailable, match='rate-limited'):
        asyncio.run(Research(lambda *a: None).call('web_search_exa', {}))

def test_nested_http_429_and_cancellation_in_a_group(monkeypatch):
    import asyncio
    import httpx
    from hub.scoped import Research
    from hub.web_provider import ProviderUnavailable
    response = httpx.Response(429, request=httpx.Request('POST', 'https://mcp.exa.ai/mcp'))
    async def limited(self, name, args):
        raise ExceptionGroup('g', [ExceptionGroup('inner', [httpx.HTTPStatusError('too many', request=response.request, response=response)])])
    monkeypatch.setattr(Research, 'exa_call', limited)
    with pytest.raises(ProviderUnavailable, match='HTTP 429'):
        asyncio.run(Research(lambda *a: None).call('web_search_exa', {}))
    async def cancelled(self, name, args):
        raise BaseExceptionGroup('g', [asyncio.CancelledError()])
    monkeypatch.setattr(Research, 'exa_call', cancelled)
    with pytest.raises(BaseExceptionGroup):
        asyncio.run(Research(lambda *a: None).call('web_search_exa', {}))

def test_search_falls_back_to_langsearch_when_exa_fails_in_any_way(monkeypatch):
    import asyncio
    from hub import retrieval
    async def exa(query): raise ExceptionGroup('g', [ValueError('mcp client broke')])
    async def langsearch_results(query): return [{'title': 'T', 'url': 'https://a.example/', 'snippet': 's', 'text': None}]
    monkeypatch.setattr(retrieval, 'exa', exa); monkeypatch.setattr(retrieval, 'langsearch_results', langsearch_results)
    rows = []
    results, provider, failures = asyncio.run(retrieval.search('q', {'search_provider': 'exa'}, lambda kind, data: rows.append(data)))
    assert provider == 'langsearch' and results and failures and failures[0].startswith('exa:') and rows[0]['fallback_reason'].startswith('exa:')

def test_provider_order_puts_the_keyed_provider_before_keyless_exa_when_searxng_is_down():
    from hub import retrieval
    assert retrieval.provider_order({'search_provider': 'searxng'}) == ['searxng', 'langsearch', 'exa']
    assert retrieval.provider_order({'search_provider': 'exa'}) == ['exa', 'langsearch']
    assert retrieval.provider_order({'search_provider': 'langsearch'}) == ['langsearch', 'exa']
