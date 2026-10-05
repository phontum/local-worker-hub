import asyncio
import ipaddress
from urllib.parse import urlsplit

import httpx
import pytest

pytest.importorskip('playwright')
from hub import browser_page

SPA = ('<!doctype html><html><head><title>Shop</title></head><body><div id="app"></div><noscript>Please enable JavaScript to view this shop.</noscript>'
       '<script src="/app.js"></script></body></html>')
APP_JS = ('document.getElementById("app").innerHTML = "<main><h1>RTX 5070 12GB</h1><p>Price 599.00 EUR. In stock at the warehouse, ships tomorrow. '
          + 'Quiet cooling and fast memory for modern games. ' * 6 + '</p></main>";')

class Site:
    """An in-memory public site behind httpx.MockTransport; records every request that reaches 'the network'."""
    def __init__(self, routes):
        self.routes, self.seen = routes, []
    def transport(self):
        return httpx.MockTransport(self.handle)
    def handle(self, request):
        self.seen.append(f'{request.method} {request.url}')
        route = self.routes.get(request.url.path)
        if callable(route):
            return route(request)
        if route is None:
            return httpx.Response(404, text='not found', headers={'content-type': 'text/html'})
        body, kind = route if isinstance(route, tuple) else (route, 'text/html')
        return httpx.Response(200, text=body, headers={'content-type': kind})

async def validate(url):
    """Stand-in for scoped.public_url without DNS: refuses literal private addresses and localhost."""
    host = urlsplit(url).hostname or ''
    try:
        if not ipaddress.ip_address(host).is_global:
            raise ValueError('Private destinations are blocked')
    except ValueError as error:
        if 'Private' in str(error):
            raise
    if host in ('localhost',) or host.endswith('.internal'):
        raise ValueError('Private destinations are blocked')

def render(monkeypatch, site, url='https://shop.example/'):
    monkeypatch.setattr(browser_page, 'transport', site.transport)
    try:
        return asyncio.run(browser_page.render(url, validate))
    except Exception as error:
        if 'Executable doesn' in str(error) or 'playwright install' in str(error):
            pytest.skip('Chromium is not installed for Playwright')
        raise

def test_javascript_rendered_content_is_read_with_browser_evidence(monkeypatch):
    site = Site({'/': SPA, '/app.js': (APP_JS, 'application/javascript')})
    text, html, meta = render(monkeypatch, site)
    assert 'RTX 5070 12GB' in text and 'Price 599.00 EUR' in text and 'RTX 5070' in html
    assert meta['method'] == 'origin-browser' and meta['javascript_rendered'] is True and meta['http_status'] == 200
    assert meta['requested_url'] == 'https://shop.example/' and meta['final_url'].startswith('https://shop.example')
    assert meta['title'] == 'Shop' and meta['current_eligible'] is True and meta['subrequests'] >= 2 and len(meta['body_sha256']) == 64
    assert meta['blocked'] == [] and set(site.seen) >= {'GET https://shop.example/', 'GET https://shop.example/app.js'}

def test_private_subrequests_are_refused_and_never_leave_the_host(monkeypatch):
    script = 'fetch("http://127.0.0.1:9/secret").catch(()=>{}); fetch("http://169.254.169.254/latest/meta-data/").catch(()=>{});' + APP_JS
    site = Site({'/': SPA, '/app.js': (script, 'application/javascript')})
    _, _, meta = render(monkeypatch, site)
    refused = {b['url'] for b in meta['blocked']}
    assert 'http://127.0.0.1:9/secret' in refused and 'http://169.254.169.254/latest/meta-data/' in refused
    assert not [s for s in site.seen if '127.0.0.1' in s or '169.254' in s]

def test_a_redirect_to_a_private_address_is_validated_on_the_hop(monkeypatch):
    site = Site({'/': lambda r: httpx.Response(302, headers={'location': 'http://127.0.0.1:9/admin'})})
    with pytest.raises(Exception):
        render(monkeypatch, site)
    assert not [s for s in site.seen if '127.0.0.1' in s]

def test_traffic_that_bypasses_interception_fails_closed_and_is_counted(monkeypatch):
    script = 'try { new WebSocket("wss://tracker.example/socket"); } catch (e) {}' + APP_JS
    site = Site({'/': SPA, '/app.js': (script, 'application/javascript')})
    _, _, meta = render(monkeypatch, site)
    assert meta['escaped_connections'] >= 1  # the browser's own socket went to the local trap, not to the network
    assert not [s for s in site.seen if 'tracker.example' in s]

def test_only_get_and_head_are_served(monkeypatch):
    script = 'fetch("/api/save", {method: "POST", body: "x"}).catch(()=>{});' + APP_JS
    site = Site({'/': SPA, '/app.js': (script, 'application/javascript')})
    _, _, meta = render(monkeypatch, site)
    assert any('POST' in b['reason'] for b in meta['blocked']) and not [s for s in site.seen if s.startswith('POST')]

def test_oversized_subresources_and_request_floods_are_cut_off(monkeypatch):
    flood = ''.join(f'fetch("/data/{i}").catch(()=>{{}});' for i in range(browser_page.MAX_REQUESTS + 15))
    site = Site({'/': SPA, '/app.js': (flood + APP_JS, 'application/javascript'), '/big.js': ('x' * (browser_page.SUBRESOURCE_BYTES + 10), 'application/javascript')})
    _, _, meta = render(monkeypatch, site)
    assert any(b['reason'] == 'request limit' for b in meta['blocked']) and meta['subrequests'] > browser_page.MAX_REQUESTS
    assert len([s for s in site.seen if '/data/' in s]) < browser_page.MAX_REQUESTS
    oversized = Site({'/': SPA.replace('/app.js', '/big.js'), '/big.js': ('x' * (browser_page.SUBRESOURCE_BYTES + 10), 'application/javascript')})
    text, _, meta = render(monkeypatch, oversized)
    assert any(b['reason'] == 'size limit' for b in meta['blocked']) and 'RTX 5070' not in text

def test_a_non_200_page_is_an_error_not_evidence(monkeypatch):
    with pytest.raises(ValueError, match='Origin HTTP 404'):
        render(monkeypatch, Site({}))

def test_stale_cdn_copies_are_not_current_eligible(monkeypatch):
    stale = lambda request: httpx.Response(200, text=SPA, headers={'content-type': 'text/html', 'age': '7200', 'cache-control': 'max-age=86400'})
    _, _, meta = render(monkeypatch, Site({'/': stale, '/app.js': (APP_JS, 'application/javascript')}))
    assert meta['cache_age_seconds'] == 7200 and meta['current_eligible'] is False

def test_needs_browser_heuristic():
    assert browser_page.needs_browser('short') == 'thin text'
    long = 'Product details and description. ' * 20
    assert browser_page.needs_browser(long, '<html><body><p>fine</p></body></html>') is None
    assert browser_page.needs_browser(long[:400], '<noscript>Please enable JavaScript to continue</noscript>') == 'noscript notice'
    assert browser_page.needs_browser(long[:400], '<div id="root"></div>') == 'empty app root'
    assert browser_page.needs_browser(long * 5, '<noscript>Please enable JavaScript</noscript><div id="root"></div>') is None  # plenty of text already
