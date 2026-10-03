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
