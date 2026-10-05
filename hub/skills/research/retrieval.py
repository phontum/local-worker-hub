"""Host-driven public retrieval for the ask pipeline: search, open the best pages, extract, rank passages.

The model never calls these as tools. Search providers are tried in configured order with explicit fallback
reporting; pages are read through the DNS-pinned origin reader (public_page.fetch_origin). Hosted extraction and
search snippets are only fallbacks and are labelled as such, so the answer can say how fresh its evidence is.
"""
import asyncio
import math
import re
from collections import Counter
from decimal import Decimal
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from . import web_fixtures
from .product_extract import Product, extract_products, format_products, product_rows
from .web_provider import ProviderUnavailable, check_exa_response, langsearch, langsearch_key, selection

SEARXNG_DEFAULT = 'http://127.0.0.1:8888'
MAX_RESULTS = 8
CHUNK = 900
BUDGET_CHARS = 18000

def now():
    return datetime.now(timezone.utc).isoformat()

def domain(url):
    return (urlsplit(url).hostname or '').removeprefix('www.')

async def searxng(query, base):
    async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=4), trust_env=False, follow_redirects=False) as client:
        response = await client.get(base.rstrip('/') + '/search', params={'q': query, 'format': 'json', 'safesearch': 0})
    if response.status_code != 200:
        raise ProviderUnavailable(f'SearXNG HTTP {response.status_code}')
    rows = response.json().get('results') or []
    return [{'title': str(r.get('title') or '')[:300], 'url': r['url'], 'snippet': str(r.get('content') or '')[:600]}
            for r in rows if isinstance(r, dict) and str(r.get('url', '')).startswith(('http://', 'https://'))][:MAX_RESULTS]

def parse_exa(text):
    """Exa returns text blocks starting with 'Title:' and 'URL:' lines."""
    results = []
    for block in re.split(r'\n(?=Title: )', text):
        url = re.search(r'(?m)^URL:\s*(https?://\S+)', block)
        if not url:
            continue
        title = re.search(r'(?m)^Title:\s*(.*)$', block)
        body = re.sub(r'(?m)^(Title|URL|Published|Author):.*$', '', block).strip()
        results.append({'title': (title.group(1) if title else '')[:300], 'url': url.group(1), 'snippet': body[:600]})
    return results[:MAX_RESULTS]

async def exa(query):
    from hub.scoped import Research
    research = Research(lambda *a: None)
    text = check_exa_response(await research.call('web_search_exa', {'query': query, 'objective': query, 'numResults': MAX_RESULTS}))
    return parse_exa(text)

async def langsearch_results(query):
    langsearch_key()
    pages = await langsearch(query)
    return [{'title': str(p.get('name') or '')[:300], 'url': p['url'], 'snippet': str(p.get('snippet') or p.get('summary') or '')[:600],
             'text': p.get('text') if isinstance(p.get('text'), str) else None} for p in pages if isinstance(p.get('url'), str)]

def provider_order(config):
    chosen = config.get('search_provider', 'exa')
    # With SearXNG chosen, a down container falls to the keyed LangSearch before the keyless Exa endpoint, which is the one that rate-limits.
    # LangSearch without a key fails at once and the order simply continues to Exa.
    order = {'searxng': ['searxng', 'langsearch', 'exa'], 'exa': ['exa', 'langsearch'], 'langsearch': ['langsearch', 'exa']}[chosen]
    return order

async def search(query, config, audit):
    """First provider that returns results wins; every failure is reported, never turned into a result."""
    failures = []
    for provider in provider_order(config):
        try:
            if provider == 'searxng':
                results = await searxng(query, config.get('searxng_url') or SEARXNG_DEFAULT)
            elif provider == 'exa':
                results = await exa(query)
            else:
                results = await langsearch_results(query)
        except Exception as error:  # a provider that fails in any way must not take the job down; the reason is reported and the next one is tried
            failures.append(f'{provider}: {str(error)[:120] or type(error).__name__}')
            continue
        if results:
            audit('web_search', {'query': query, 'provider': provider, 'fallback_reason': '; '.join(failures) or None,
                                 'results': [{'url': r['url'], 'title': r['title']} for r in results], 'retrieved_at': now()})
            return results, provider, failures
        failures.append(f'{provider}: no results')
    audit('web_search', {'query': query, 'provider': None, 'fallback_reason': '; '.join(failures), 'results': [], 'retrieved_at': now()})
    return [], None, failures

async def render_page(url):
    """Render a JavaScript-dependent page in the locked-down browser; (page dict, html) or (None, reason)."""
    from . import browser_page
    from hub.scoped import public_url
    try:
        async with browser_page.slot():
            text, html, metadata = await asyncio.wait_for(browser_page.render(url, public_url), browser_page.WALL_SECONDS + 5)
    except Exception as error:  # Playwright or Chromium missing, timeout, blocked page: the origin text stays as it was
        return None, (str(error).splitlines() or [type(error).__name__])[0][:120]
    return {'url': url, 'text': text, 'kind': 'browser', 'title': metadata.get('title') or '', 'observed_at': metadata['observed_at'],
            'current_eligible': metadata.get('current_eligible'), 'escaped_connections': metadata.get('escaped_connections', 0)}, html

async def read_page(url):
    """Origin read first; a page that needs JavaScript is rendered in the locked-down browser; hosted extraction is the labelled last resort.
    The browser runs the page's scripts but the network is still our public-only client, so it cannot help with sites that refuse that client."""
    from . import browser_page
    from .public_page import fetch_origin
    from hub.scoped import public_url, Research
    html, page, reason = [], None, ''
    try:
        await public_url(url)
        text, metadata = await asyncio.wait_for(fetch_origin(url, public_url, html), 20)
        page = {'url': url, 'text': text, 'kind': 'page', 'title': metadata.get('title') or '', 'observed_at': metadata['observed_at'],
                'current_eligible': metadata.get('current_eligible')}
    except Exception as error:  # blocked, JS-only, non-HTML, private destination, timeout
        reason = str(error)[:120] or type(error).__name__
    if 'Private destinations' in reason or 'Only public' in reason:
        return {'url': url, 'error': reason}
    wanted = None
    if browser_page.mode() != 'off':
        if page is None:
            wanted = 'no readable text' if html and 'no readable text' in reason else None
        else:
            wanted = 'always' if browser_page.mode() == 'always' else browser_page.needs_browser(page['text'], html[0] if html else '')
    if wanted:
        rendered, outcome = await render_page(url)
        if rendered and (page is None or len(rendered['text']) > len(page['text'])):
            page, html = rendered, [outcome]
            page['rendered_because'] = wanted
        elif page is not None:
            page['browser_note'] = f'rendering did not help ({wanted}): ' + (outcome if rendered is None else 'no more text')
        else:
            reason += f'; browser: {outcome}'
    if page is not None:
        products = extract_products(html[0]) if html else []
        if products:
            page['products'] = product_rows(products, url)
        return page
    try:
        hosted = check_exa_response(await Research(lambda *a: None).call('web_fetch_exa', {'url': url}))
        if hosted.strip():
            return {'url': url, 'text': hosted[:24000], 'kind': 'hosted', 'title': '', 'observed_at': now(), 'current_eligible': False, 'origin_error': reason}
    except Exception:
        pass
    return {'url': url, 'error': reason}

search = web_fixtures.wrap_search(search)
read_page = web_fixtures.wrap_read_page(read_page)

def pick_urls(results_by_query, limit):
    """Interleave queries, one page per domain first, so a single site cannot crowd out the rest."""
    chosen, domains = [], set()
    for rank in range(MAX_RESULTS):
        for results in results_by_query:
            if rank < len(results):
                url = results[rank]['url']
                if domain(url) not in domains and url not in chosen:
                    chosen.append(url)
                    domains.add(domain(url))
            if len(chosen) >= limit:
                return chosen
    return chosen

def chunks(text, size=CHUNK):
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n|\n', text) if p.strip()]
    out, current = [], ''
    for paragraph in paragraphs:
        if current and len(current) + len(paragraph) + 1 > size:
            out.append(current)
            current = ''
        while len(paragraph) > size:
            out.append(paragraph[:size])
            paragraph = paragraph[size:]
        current = (current + '\n' + paragraph).strip()
    if current:
        out.append(current)
    return out

def tokens(text):
    return re.findall(r'\w+', text.lower())

def bm25(query, documents, k1=1.5, b=0.75):
    terms = set(tokens(query))
    docs = [tokens(d) for d in documents]
    if not docs:
        return []
    average = sum(map(len, docs)) / len(docs) or 1
    frequency = Counter(t for d in docs for t in set(d))
    scores = []
    for words in docs:
        counts = Counter(words)
        score = 0.0
        for term in terms:
            if counts[term]:
                idf = math.log(1 + (len(docs) - frequency[term] + .5) / (frequency[term] + .5))
                score += idf * counts[term] * (k1 + 1) / (counts[term] + k1 * (1 - b + b * len(words) / average))
        scores.append(score)
    return scores

MAX_PRODUCT_PAGES = 3

def product_excerpts(pages):
    """Host-written PRODUCT DATA blocks from page markup, one per page, shown to the model before any page text."""
    out = []
    for page in pages:
        if page.get('products') and len(out) < MAX_PRODUCT_PAGES:
            rows = [Product(**{k: (Decimal(v) if k in ('price', 'price_high') and v is not None else v) for k, v in row.items() if k != 'url'}) for row in page['products']]
            out.append({'url': page['url'], 'title': (page.get('title') or domain(page['url'])) + ' (product data)', 'kind': 'product',
                        'observed_at': page['observed_at'], 'text': format_products(rows, page['url'])})
    return out

def select_excerpts(question, queries, pages, snippets, budget=BUDGET_CHARS):
    """Best page passages first (each readable page contributes its top passage), then fill by score; snippets last."""
    query = ' '.join([question, *queries])
    candidates = []
    for page in pages:
        for i, text in enumerate(chunks(page['text'])):
            candidates.append({'page': page, 'text': text, 'position': i})
    scores = bm25(query, [c['text'] + ' ' + c['page'].get('title', '') for c in candidates])
    for candidate, score in zip(candidates, scores):
        candidate['score'] = score + (0.5 if candidate['position'] == 0 else 0)
    chosen, used = [], 0
    for page in pages:
        best = max((c for c in candidates if c['page'] is page), key=lambda c: c['score'], default=None)
        if best and best['score'] > 0 and used + len(best['text']) <= budget:
            chosen.append(best)
            used += len(best['text'])
    for candidate in sorted(candidates, key=lambda c: -c['score']):
        if candidate in chosen or candidate['score'] <= 0 or used + len(candidate['text']) > budget:
            continue
        chosen.append(candidate)
        used += len(candidate['text'])
    excerpts = product_excerpts(pages) + [{'url': c['page']['url'], 'title': c['page'].get('title') or domain(c['page']['url']), 'kind': c['page']['kind'],
                 'observed_at': c['page']['observed_at'], 'text': c['text']} for c in chosen]
    for snippet in snippets:
        if used + len(snippet['text']) > budget:
            break
        excerpts.append(snippet)
        used += len(snippet['text'])
    for n, excerpt in enumerate(excerpts, 1):
        excerpt['n'] = n
    return excerpts

async def gather(question, queries, audit, config=None, pages_limit=3, ledger=None):
    """Run the searches, read the best pages and return numbered excerpts plus a retrieval log."""
    config = config or selection()
    results_by_query, providers, failures = [], [], []
    for query in queries[:2]:
        if ledger:
            ledger.charge('search')
        results, provider, failed = await search(query, config, audit)
        results_by_query.append(results)
        providers.append(provider)
        failures += failed
    urls = pick_urls(results_by_query, pages_limit)
    if ledger:
        for _ in urls:
            ledger.charge('fetch')
    pages = await asyncio.gather(*(read_page(u) for u in urls))
    readable = []
    for page in pages:
        audit('web_fetch', {'url': page['url'], 'method': page.get('kind') or 'failed', 'error': page.get('error'),
                            'current_eligible': page.get('current_eligible'), 'observed_at': page.get('observed_at')})
        if page.get('text'):
            readable.append(page)
    retrieved = now()
    snippets = []
    for results in results_by_query:
        for r in results[:4]:
            text = (r.get('text') or r.get('snippet') or '').strip()
            if text and r['url'] not in {p['url'] for p in readable}:
                snippets.append({'url': r['url'], 'title': r['title'] or domain(r['url']), 'kind': 'snippet', 'observed_at': retrieved, 'text': text[:600]})
    excerpts = select_excerpts(question, queries, readable, snippets)
    return excerpts, {'queries': queries[:2], 'providers': providers, 'failures': failures, 'pages': [
        {k: p.get(k) for k in ('url', 'kind', 'error', 'observed_at', 'rendered_because', 'browser_note') if p.get(k)} | {'error': p.get('error')} for p in pages],
        'products': [row for p in readable for row in p.get('products', [])][:8]}
