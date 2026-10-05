import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from hub import ask, public_page, retrieval, scoped
from hub.report import final_report
from test_pipelines_v3 import PREFS, ask_job, fake_models

NOW = datetime.now(timezone.utc).isoformat()
PAGE = ('<html><head><title>RTX 5070 | Shop A</title><script type="application/ld+json">{"@type":"Product","name":"RTX 5070 12GB","brand":"Acme",'
        '"offers":{"@type":"Offer","price":"599.00","priceCurrency":"EUR","availability":"https://schema.org/InStock","seller":"Shop A"}}</script></head>'
        '<body><main><h1>RTX 5070 12GB</h1><p>' + 'Graphics card with fast memory and quiet cooling. ' * 8 + '</p><p class="stock">Currently sold out online.</p></main></body></html>')

def serve_page(monkeypatch, html=PAGE):
    async def fetch_origin(url, validate_url, html_sink=None):
        if html_sink is not None:
            html_sink.append(html)
        return 'RTX 5070 12GB graphics card. Currently sold out online.', {'title': 'RTX 5070 | Shop A', 'observed_at': NOW, 'current_eligible': True}
    async def public_url(url): return url
    monkeypatch.setattr(public_page, 'fetch_origin', fetch_origin)
    monkeypatch.setattr(scoped, 'public_url', public_url)

def test_read_page_extracts_products_from_the_markup(monkeypatch):
    serve_page(monkeypatch)
    page = asyncio.run(retrieval.read_page('https://shop.example/p'))
    assert page['kind'] == 'page' and page['text'].startswith('RTX 5070')
    [row] = page['products']
    assert (row['name'], row['price'], row['currency'], row['availability'], row['seller'], row['source'], row['url']) == (
        'RTX 5070 12GB', '599.00', 'EUR', 'in_stock', 'Shop A', 'jsonld', 'https://shop.example/p')
    json.dumps(page)  # rows must be JSON-safe: they are stored in fixtures and ask.json

def test_pages_without_markup_have_no_products(monkeypatch):
    serve_page(monkeypatch, '<html><body><p>Price: 5 EUR</p></body></html>')
    assert 'products' not in asyncio.run(retrieval.read_page('https://shop.example/p'))

def test_product_block_comes_first_and_keeps_the_contradicting_page_text(monkeypatch):
    serve_page(monkeypatch)
    page = asyncio.run(retrieval.read_page('https://shop.example/p'))
    page['text'] = 'RTX 5070 12GB graphics card.\n\nCurrently sold out online.'
    excerpts = retrieval.select_excerpts('RTX 5070 in stock?', [], [page], [])
    assert [e['kind'] for e in excerpts] == ['product', 'page'] and [e['n'] for e in excerpts] == [1, 2]
    assert excerpts[0]['text'].startswith('PRODUCT DATA') and 'price 599.00 EUR' in excerpts[0]['text'] and 'availability in_stock (markup: InStock)' in excerpts[0]['text']
    assert 'sold out' in excerpts[1]['text']  # the conflict stays visible to the model

def test_gather_logs_product_rows_for_the_dashboard(monkeypatch):
    serve_page(monkeypatch)
    async def search(query, config, audit): return [{'title': 'T', 'url': 'https://shop.example/p', 'snippet': 's'}], 'exa', []
    monkeypatch.setattr(retrieval, 'search', search)
    excerpts, log = asyncio.run(retrieval.gather('RTX 5070?', ['rtx 5070'], lambda *a: None, config={'search_provider': 'exa'}))
    assert excerpts[0]['kind'] == 'product' and log['products'][0]['price'] == '599.00' and log['products'][0]['url'] == 'https://shop.example/p'

def test_product_excerpts_round_trip_through_stored_rows():
    rows = [{'name': 'A', 'brand': '', 'sku': '', 'gtin': '', 'variant': 'size 8GB', 'price': '80', 'price_high': '95', 'currency': 'EUR', 'availability': 'preorder',
             'availability_raw': 'PreOrder', 'condition': 'new', 'seller': '', 'price_valid_until': '', 'source': 'jsonld', 'url': 'https://s.example/'}]
    [excerpt] = retrieval.product_excerpts([{'url': 'https://s.example/', 'title': 'Shop', 'observed_at': NOW, 'products': rows}])
    assert 'price 80-95 EUR' in excerpt['text'] and 'variant size 8GB' in excerpt['text'] and Decimal('80') == Decimal(rows[0]['price'])

@pytest.mark.asyncio
async def test_ask_cites_the_page_once_and_shows_the_model_the_product_block(tmp_path, monkeypatch):
    directory = ask_job(tmp_path, 'Is the RTX 5070 in stock at Shop A?')
    seen = fake_models(monkeypatch, {'needs_web': True, 'queries': ['rtx 5070 shop a'], 'reply_language': 'English'},
                       [{'answer': 'The markup says in stock at 599.00 EUR, but the page text says sold out online.', 'used': [1, 2], 'answered': True, 'follow_up_query': ''}])
    serve_page(monkeypatch)
    async def search(query, config, audit): return [{'title': 'T', 'url': 'https://shop.example/p', 'snippet': 's'}], 'exa', []
    monkeypatch.setattr(retrieval, 'search', search); monkeypatch.setattr(ask, 'load_preferences', lambda: PREFS)
    await ask.run_ask(directory, 'work', 'Is the RTX 5070 in stock at Shop A?')
    findings = final_report(json.loads((directory / 'work.session.json').read_text()))['findings']
    prompt = seen[1][1]['messages'][1]['content']
    assert '| product |' in prompt and 'PRODUCT DATA' in prompt and 'sold out' in prompt
    assert findings.count('https://shop.example/p') == 1 and 'sold out' in findings
    record = json.loads((directory / 'ask.json').read_text())
    assert record['retrieval']['products'][0]['availability'] == 'in_stock' and record['excerpts'][0]['kind'] == 'product'
