"""Selectable public search; credentials stay outside prompts and job snapshots."""
import json
import re
import os
import stat
from pathlib import Path

import httpx

from hub.settings import CONFIG


class ProviderUnavailable(ValueError):
    """Sanitized transient failure; never pass provider credentials to the model."""


def check_exa_response(text):
    import re
    if re.search(r"(?:hit Exa's free MCP rate limit|too many requests|quota exceeded|rate.?limit exceeded)",text,re.I):
        raise ProviderUnavailable('Exa free endpoint is rate-limited')
    return text


def selection(directory=None):
    path=Path(directory)/'web-config.json' if directory is not None else CONFIG/'web.json'
    if directory is not None and not path.exists():path=CONFIG/'web.json'
    value=json.loads(path.read_text()) if path.exists() else {'search_provider':'exa'}
    if not set(value)<={'search_provider','searxng_url'} or value.get('search_provider') not in ('exa','langsearch','searxng'):
        raise ValueError('web.json must contain search_provider (exa, langsearch or searxng) and optionally searxng_url')
    if 'searxng_url' in value and not re.fullmatch(r'http://(127\.0\.0\.1|localhost):\d{2,5}/?',str(value['searxng_url'])):
        raise ValueError('searxng_url must be a local http://127.0.0.1:PORT address')
    return value


def searxng_health(url):
    """True when the local SearXNG answers JSON searches."""
    try:
        response=httpx.get(url.rstrip('/')+'/search',params={'q':'weather','format':'json'},timeout=10,trust_env=False)
        return response.status_code==200 and isinstance(response.json().get('results'),list)
    except (httpx.HTTPError,ValueError):
        return False


def langsearch_key():
    path=CONFIG/'langsearch-api-key'
    try:
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd) as source:
            st=os.fstat(source.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_mode & 0o077:
                raise ValueError('LangSearch key file must be owned by you and private (chmod 600)')
            key=source.read(4097).strip()
        if not key or len(key)>4096 or '\n' in key or '\r' in key:raise ValueError('Invalid LangSearch key file')
        return key
    except FileNotFoundError:raise ValueError('LangSearch needs a free API key; run local-worker web configure langsearch') from None


def configure(provider,searxng_url='http://127.0.0.1:8888'):
    if provider not in ('exa','langsearch','searxng'):raise ValueError('Unknown public search provider')
    if provider=='langsearch':langsearch_key() # Do not activate a broken provider.
    if provider=='searxng' and not searxng_health(searxng_url):
        raise ValueError('SearXNG is not answering JSON searches at '+searxng_url+'; run local-worker web setup-searxng')
    CONFIG.mkdir(mode=0o700,parents=True,exist_ok=True)
    target=CONFIG/'web.json'
    temporary=CONFIG/'web.json.tmp'
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as out:json.dump({'search_provider':provider,**({'searxng_url':searxng_url} if provider=='searxng' else {})},out)
    temporary.chmod(0o600);temporary.replace(target)
    return selection()


async def langsearch(query):
    key=langsearch_key()
    async with httpx.AsyncClient(timeout=30,trust_env=False,follow_redirects=False) as client:
        async with client.stream('POST','https://api.langsearch.com/v1/web-search',
            headers={'Authorization':'Bearer '+key},
            json={'query':query,'count':5,'contents':{'text':{'maxCharacters':24000}}}) as response:
            if response.status_code!=200:raise ValueError(f'LangSearch HTTP {response.status_code}; no fallback provider used')
            data=bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data)>1_000_000:raise ValueError('LangSearch response exceeds the public evidence limit')
    payload=json.loads(data)
    if str(payload.get('code'))!='200':raise ValueError('LangSearch reported a failed request; no fallback provider used')
    pages=((payload.get('data') or {}).get('webPages') or {}).get('value') or []
    if not isinstance(pages,list):raise ValueError('LangSearch response schema changed')
    return pages[:5]
