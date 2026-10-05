import asyncio
import json
from datetime import datetime, timezone

import pytest

from hub import browser_page, public_page, retrieval, scoped

NOW = datetime.now(timezone.utc).isoformat()
LONG = 'Product description and specifications for the item. ' * 12
THIN = 'Loading…'
LD = ('<script type="application/ld+json">{"@type":"Product","name":"Pi 5 8GB","offers":{"price":"80","priceCurrency":"EUR","availability":"InStock"}}</script>')

class World:
    """Fake origin and fake browser: records which of them were used."""
    def __init__(self, monkeypatch, tmp_path, origin_text=LONG, origin_html='<html><body>ok</body></html>', origin_error=None, rendered=None, render_error=None):
        self.renders, self.active, self.overlap = [], 0, False
        monkeypatch.setattr(browser_page, 'CONFIG', tmp_path)
        async def fetch_origin(url, validate_url, html_sink=None):
            if html_sink is not None:
                html_sink.append(origin_html)
            if origin_error:
                raise ValueError(origin_error)
            return origin_text, {'title': 'T', 'observed_at': NOW, 'current_eligible': True}
        async def render(url, validate_url):
            self.renders.append(url); self.active += 1; self.overlap |= self.active > 1
            await asyncio.sleep(0.02); self.active -= 1
            if render_error:
                raise RuntimeError(render_error)
            text, html = rendered if rendered is not None else (LONG * 2, '<html>' + LD + '</html>')
            return text, html, {'title': 'Rendered', 'observed_at': NOW, 'current_eligible': True, 'escaped_connections': 0}
        async def public_url(url):
            if 'private' in url:
                raise ValueError('Private destinations are blocked')
            return url
        monkeypatch.setattr(public_page, 'fetch_origin', fetch_origin); monkeypatch.setattr(browser_page, 'render', render); monkeypatch.setattr(scoped, 'public_url', public_url)
        async def hosted(self_, name, args): return 'Title: hosted copy of the page ' + 'x' * 50
        monkeypatch.setattr(scoped.Research, 'call', hosted)

def mode(tmp_path, value):
    (tmp_path / 'browser.json').write_text(json.dumps({'mode': value}))

def read(url='https://shop.example/p'):
    return asyncio.run(retrieval.read_page(url))

def test_a_normal_page_never_starts_the_browser(monkeypatch, tmp_path):
    world = World(monkeypatch, tmp_path)
    assert read()['kind'] == 'page' and world.renders == []

def test_thin_text_is_rendered_and_products_come_from_the_rendered_html(monkeypatch, tmp_path):
    world = World(monkeypatch, tmp_path, origin_text=THIN)
    page = read()
    assert world.renders == ['https://shop.example/p'] and page['kind'] == 'browser' and page['rendered_because'] == 'thin text' and page['title'] == 'Rendered'
    assert page['products'][0]['name'] == 'Pi 5 8GB' and page['products'][0]['availability'] == 'in_stock'

def test_noscript_notice_and_empty_app_root_trigger_rendering(monkeypatch, tmp_path):
    for html, why in (('<noscript>Please enable JavaScript to view this shop.</noscript>', 'noscript notice'), ('<div id="root"></div>', 'empty app root')):
        world = World(monkeypatch, tmp_path, origin_text=LONG[:400], origin_html=html)
        assert read()['rendered_because'] == why and world.renders

def test_no_readable_text_error_with_html_is_rendered(monkeypatch, tmp_path):
    world = World(monkeypatch, tmp_path, origin_error='Origin returned no readable text', origin_html='<div id="app"></div>')
    assert read()['kind'] == 'browser' and world.renders

def test_blocked_origins_do_not_get_a_browser_because_it_cannot_change_how_the_site_sees_us(monkeypatch, tmp_path):
    world = World(monkeypatch, tmp_path, origin_error='Origin HTTP 403', origin_html='')
    page = read()
    assert world.renders == [] and page['kind'] == 'hosted' and page['origin_error'] == 'Origin HTTP 403'

def test_private_destinations_never_reach_the_browser(monkeypatch, tmp_path):
    world = World(monkeypatch, tmp_path)
    assert 'Private destinations' in read('https://private.example/p')['error'] and world.renders == []

def test_mode_off_and_always(monkeypatch, tmp_path):
    world = World(monkeypatch, tmp_path, origin_text=THIN)
    mode(tmp_path, 'off')
    assert read()['kind'] == 'page' and world.renders == []
    mode(tmp_path, 'always')
    world = World(monkeypatch, tmp_path)
    mode(tmp_path, 'always')
    assert read()['kind'] == 'browser' and read()['rendered_because'] == 'always'
    (tmp_path / 'browser.json').write_text('not json')
    assert browser_page.mode() == 'fallback'

def test_a_browser_failure_or_a_worse_render_keeps_the_origin_text_and_says_so(monkeypatch, tmp_path):
    World(monkeypatch, tmp_path, origin_text=THIN, render_error='Executable doesn\'t exist at /nowhere\nrun playwright install')
    page = read()
    assert page['kind'] == 'page' and page['text'] == THIN and "Executable doesn't exist" in page['browser_note'] and 'products' not in page
    World(monkeypatch, tmp_path, origin_text=LONG[:300], origin_html='<div id="root"></div>', rendered=('tiny', '<html></html>'))
    assert read()['kind'] == 'page' and 'no more text' in read()['browser_note']
    World(monkeypatch, tmp_path, origin_error='Origin returned no readable text', origin_html='<div id="app"></div>', render_error='timeout')
    assert read()['kind'] == 'hosted'

def test_concurrent_reads_use_one_browser_at_a_time(monkeypatch, tmp_path):
    world = World(monkeypatch, tmp_path, origin_text=THIN)
    async def three():
        return await asyncio.gather(*(retrieval.read_page(f'https://shop.example/p{i}') for i in range(3)))
    pages = asyncio.run(three())
    assert [p['kind'] for p in pages] == ['browser'] * 3 and len(world.renders) == 3 and world.overlap is False

def test_log_records_why_a_page_was_rendered(monkeypatch, tmp_path):
    World(monkeypatch, tmp_path, origin_text=THIN)
    async def search(query, config, audit): return [{'title': 'T', 'url': 'https://shop.example/p', 'snippet': 's'}], 'exa', []
    monkeypatch.setattr(retrieval, 'search', search)
    excerpts, log = asyncio.run(retrieval.gather('Pi 5?', ['pi 5'], lambda *a: None, config={'search_provider': 'exa'}))
    assert log['pages'][0]['kind'] == 'browser' and log['pages'][0]['rendered_because'] == 'thin text' and excerpts[0]['kind'] == 'product'
