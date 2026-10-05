"""Record and replay public web retrieval so evaluations vary only the model.

Off unless CONFIG/web-fixtures.json exists: {"mode": "record"|"replay", "dir": "<directory under the private state directory>",
"expires": <epoch seconds>}; an expired file is ignored so an interrupted run cannot leave replay switched on.
The benchmark harness writes it for the duration of a run and removes it afterwards; it is a file, not an environment variable,
because job processes deliberately start with a minimal environment. Recording stores search results and page text, so the
directory must stay inside STATE (never public source). Replay never touches the network: a missing fixture is reported as a
failure, not invented.
"""
import hashlib
import json
import re
import time
from pathlib import Path

from .settings import CONFIG, STATE

def directory():
    try:
        spec = json.loads((CONFIG / 'web-fixtures.json').read_text())
    except (OSError, ValueError):
        return None, None
    raw, mode = spec.get('dir') if isinstance(spec, dict) else None, spec.get('mode') if isinstance(spec, dict) else None
    expires = spec.get('expires') if isinstance(spec, dict) else None
    if not isinstance(raw, str) or mode not in ('record', 'replay') or not isinstance(expires, (int, float)) or expires < time.time():
        return None, None
    path = Path(raw).expanduser().resolve()
    if not path.is_relative_to(STATE.resolve()):
        raise ValueError('web fixture directory must be inside the private state directory')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path, mode

def slot(root, kind, key):
    return root / f"{kind}-{hashlib.sha256(' '.join(key.lower().split()).encode()).hexdigest()[:24]}.json"

def store(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    path.chmod(0o600)

NEAREST_MIN = 0.6

def words(text):
    return set(re.findall(r'\w+', text.lower()))

def nearest(root, query):
    """Recorded search whose query shares most words with this one (Jaccard >= NEAREST_MIN): a prompt change rewords queries slightly,
    and an exact-match replay would then compare prompts through luck rather than answers. Returns (saved, similarity) or (None, 0)."""
    wanted, best, score = words(query), None, 0.0
    for path in root.glob('search-*.json'):
        try:
            saved = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        other = words(saved.get('query', ''))
        similarity = len(wanted & other) / len(wanted | other) if wanted | other else 0.0
        if similarity > score:
            best, score = saved, similarity
    return (best, score) if score >= NEAREST_MIN else (None, 0.0)

def wrap_search(search):
    root, mode = directory()
    if not root:
        return search
    async def wrapped(query, config, audit):
        path = slot(root, 'search', query)
        if mode == 'replay':
            saved, similarity = (json.loads(path.read_text()), 1.0) if path.exists() else nearest(root, query)
            if saved is None:
                audit('web_search', {'query': query, 'provider': None, 'fallback_reason': 'fixture miss', 'results': [], 'replayed': True})
                return [], None, ['fixture miss']
            audit('web_search', {'query': query, 'provider': saved['provider'], 'fallback_reason': '; '.join(saved['failures']) or None,
                                 'results': [{'url': r['url'], 'title': r['title']} for r in saved['results']], 'replayed': True,
                                 **({'nearest_query': saved['query'], 'similarity': round(similarity, 2)} if similarity < 1 else {})})
            return saved['results'], saved['provider'], saved['failures']
        results, provider, failures = await search(query, config, audit)
        if results:
            store(path, {'query': query, 'results': results, 'provider': provider, 'failures': failures})
        return results, provider, failures
    return wrapped

def wrap_provider(provider):
    """Provider.fetch with the same record/replay as search, keyed by provider, arguments and units. Providers that need no network
    (the clock) are never frozen, because the current time is their data."""
    root, mode = directory()
    if not root or not getattr(provider, 'network', True):
        return provider.fetch
    from .providers.base import ProviderError, ProviderResult
    async def wrapped(args, prefs, task=''):
        path = slot(root, 'provider', f"{provider.name} {json.dumps(args.model_dump(), sort_keys=True)} {prefs.get('units')}")
        if mode == 'replay':
            if not path.exists():
                raise ProviderError('fixture miss')
            return ProviderResult(**json.loads(path.read_text()))
        result = await provider.fetch(args, prefs, task)
        store(path, {'title': result.title, 'url': result.url, 'text': result.text, 'evidence': result.evidence, 'records': result.records})
        return result
    return wrapped

def wrap_read_page(read_page):
    root, mode = directory()
    if not root:
        return read_page
    async def wrapped(url):
        path = slot(root, 'page', url)
        if mode == 'replay':
            return json.loads(path.read_text()) if path.exists() else {'url': url, 'error': 'fixture miss'}
        page = await read_page(url)
        if page.get('text'):
            store(path, page)
        return page
    return wrapped
