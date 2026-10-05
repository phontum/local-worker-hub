"""Provider contract and the shared public-only JSON fetch."""
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel

MAX_BYTES = 1_000_000

class ProviderError(Exception):
    """The provider could not produce a trustworthy result; the caller falls back to web search and reports why."""

@dataclass
class ProviderResult:
    title: str
    url: str
    text: str
    evidence: dict
    records: list = field(default_factory=list)

class Provider:
    name = ''
    description = ''
    network = True  # False for providers whose data is the local clock: never frozen by the eval fixtures
    Args: type[BaseModel] = BaseModel

    async def fetch(self, args, prefs, task=''):
        raise NotImplementedError

def now():
    return datetime.now(timezone.utc).isoformat()

def client_factory():
    from ..public_page import transport
    return httpx.AsyncClient(transport=transport(), timeout=httpx.Timeout(12, connect=6), follow_redirects=False,
                             headers={'User-Agent': 'local-worker-hub/1 (+public data lookup)'})

async def get_json(url, params):
    """GET a documented provider endpoint; returns (json, evidence). Anything but a small 200 JSON object is an error."""
    try:
        async with client_factory() as client:
            response = await client.get(url, params=params)
    except (httpx.HTTPError, ValueError, OSError) as error:
        raise ProviderError(f'{type(error).__name__}: {str(error)[:100]}') from error
    if response.status_code != 200:
        raise ProviderError(f'HTTP {response.status_code}')
    if len(response.content) > MAX_BYTES:
        raise ProviderError('response too large')
    try:
        data = response.json()
    except ValueError as error:
        raise ProviderError('invalid JSON') from error
    if not isinstance(data, dict):
        raise ProviderError('unexpected JSON shape')
    return data, {'method': 'provider-api', 'requested_url': str(response.request.url), 'observed_at': now(), 'http_status': 200, 'current_eligible': True}
