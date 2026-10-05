"""Rendered public page reads for JavaScript-only pages, with the same network guarantees as public_page.fetch_origin.

Chromium never opens a socket or resolves a name itself:
- every request it makes is intercepted and re-issued by our own DNS-pinned, public-only httpx client (redirects are handed back to
  the browser, so each hop is intercepted and validated again), and the response is fulfilled from memory;
- the browser is launched with a proxy that is a local listener which only counts and closes connections, so anything that escapes
  interception (WebSocket, WebRTC, background services) fails closed and is reported as `escaped_connections`;
- only document, script, stylesheet, xhr and fetch GET/HEAD requests are served; downloads, popups, service workers, permissions and
  cookies are off, the profile is throwaway and request count, per-response bytes, total bytes and wall time are bounded.
Evidence uses method 'origin-browser' with javascript_rendered true, in the same shape as the plain origin read.
"""
import asyncio
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .public_page import PageText, main_text, transport
from hub.settings import CONFIG

DOC_BYTES = 2_000_000
SUBRESOURCE_BYTES = 1_500_000
TOTAL_BYTES = 6_000_000
MAX_REQUESTS = 60
WALL_SECONDS = 20
TEXT_LIMIT = 24000
SERVED_TYPES = {'document', 'script', 'stylesheet', 'xhr', 'fetch'}
FORWARDED_REQUEST_HEADERS = {'accept', 'accept-language', 'user-agent', 'content-type'}
DROPPED_RESPONSE_HEADERS = {'content-encoding', 'content-length', 'transfer-encoding', 'connection', 'keep-alive', 'set-cookie', 'set-cookie2', 'alt-svc'}
REDIRECTS = (301, 302, 303, 307, 308)
SPA_ROOT = re.compile(r'<div[^>]+id\s*=\s*["\'](?:root|app|__next|__nuxt|svelte)["\'][^>]*>\s*</div>', re.I)
NOSCRIPT_NOTICE = re.compile(r'<noscript[^>]*>[^<]*(?:enable\s+javascript|javascript\s+(?:is\s+)?(?:required|disabled|needed)|requires\s+javascript)', re.I)

def mode():
    """CONFIG/browser.json {"mode": "fallback"|"always"|"off"}; fallback (render only pages that need JavaScript) when missing or malformed."""
    try:
        value = json.loads((CONFIG / 'browser.json').read_text()).get('mode')
    except (OSError, ValueError, AttributeError):
        return 'fallback'
    return value if value in ('fallback', 'always', 'off') else 'fallback'

def status():
    """For `local-worker doctor`: the configured mode and whether Playwright and a Chromium build are present (nothing is launched)."""
    try:
        from importlib.metadata import version
        playwright = version('playwright')
    except Exception:
        playwright = None
    home = Path(os.environ.get('PLAYWRIGHT_BROWSERS_PATH') or Path.home() / '.cache/ms-playwright')
    builds = sorted(p.name for p in home.glob('chromium*')) if home.is_dir() else []
    custom = os.environ.get('CHROMIUM_PATH')
    return {'mode': mode(), 'playwright': playwright, 'chromium_builds': builds, 'chromium_path': custom,
            'ready': bool(playwright and (builds or (custom and Path(custom).exists())))}

_slot = None

def slot():
    """One browser at a time per job process: pages are read concurrently, Chromium instances are heavy."""
    global _slot
    if _slot is None:
        _slot = asyncio.Lock()
    return _slot

def needs_browser(text, html=''):
    """Why a plain origin read probably missed the page's real content, or None. Thin text alone is enough; markers only matter for short pages."""
    if len(text.strip()) < 200:
        return 'thin text'
    if len(text) < 2000 and html:
        if NOSCRIPT_NOTICE.search(html):
            return 'noscript notice'
        if SPA_ROOT.search(html):
            return 'empty app root'
    return None

class Budget:
    def __init__(self):
        self.requests = self.bytes = self.escaped = 0
        self.blocked, self.redirects = [], 0
        self.main = None

    def block(self, url, reason):
        if len(self.blocked) < 20:
            self.blocked.append({'url': url[:200], 'reason': reason})

async def blackhole(budget):
    async def accept(reader, writer):
        budget.escaped += 1
        writer.close()
    return await asyncio.start_server(accept, '127.0.0.1', 0)

async def serve(route, client, validate_url, budget):
    """Fulfil one browser request from our own client, or abort it. Never lets the browser use its network stack."""
    request = route.request
    budget.requests += 1
    if budget.requests > MAX_REQUESTS:
        budget.block(request.url, 'request limit')
        return await route.abort('blockedbyclient')
    if request.resource_type not in SERVED_TYPES or request.method not in ('GET', 'HEAD'):
        budget.block(request.url, f'{request.method} {request.resource_type} not served')
        return await route.abort('blockedbyclient')
    navigation = request.is_navigation_request() and request.frame.parent_frame is None
    limit = DOC_BYTES if request.resource_type == 'document' else SUBRESOURCE_BYTES
    try:
        await validate_url(request.url)
        headers = {k: v for k, v in request.headers.items() if k.lower() in FORWARDED_REQUEST_HEADERS}
        async with client.stream(request.method, request.url, headers=headers) as response:
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > limit or budget.bytes + len(body) > TOTAL_BYTES:
                    raise ValueError('size limit')
            budget.bytes += len(body)
            status, kept = response.status_code, {k: v for k, v in response.headers.items() if k.lower() not in DROPPED_RESPONSE_HEADERS}
            if navigation:
                if status in REDIRECTS:
                    budget.redirects += 1
                else:
                    budget.main = {'status': status, 'headers': dict(response.headers), 'sha256': hashlib.sha256(body).hexdigest(), 'url': str(response.url)}
    except Exception as error:  # private destination, TLS failure, timeout, oversize: the page simply does not get it
        budget.block(request.url, (str(error) or type(error).__name__)[:100])
        return await route.abort('failed')
    await route.fulfill(status=status, headers=kept, body=bytes(body))

def readable(html):
    parser = PageText()
    parser.feed(html)
    text = main_text(html) or '\n'.join(parser.main or parser.parts)
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text), ' '.join(parser.title)[:500]

async def render(url, validate_url):
    """Open `url` in a locked-down Chromium and return (text, html, metadata) for the rendered page."""
    from playwright.async_api import async_playwright
    started, budget = time.monotonic(), Budget()
    await validate_url(url)
    async with asyncio.timeout(WALL_SECONDS):
        async with async_playwright() as playwright:
            trap = await blackhole(budget)
            port = trap.sockets[0].getsockname()[1]
            browser = await playwright.chromium.launch(
                headless=True, executable_path=os.environ.get('CHROMIUM_PATH') or None, proxy={'server': f'http://127.0.0.1:{port}'},
                args=['--disable-dev-shm-usage', '--disable-background-networking', '--force-webrtc-ip-handling-policy=disable_non_proxied_udp'])
            try:
                context = await browser.new_context(accept_downloads=False, service_workers='block', permissions=[], ignore_https_errors=False, java_script_enabled=True)
                async with httpx.AsyncClient(transport=transport(), trust_env=False, follow_redirects=False, timeout=httpx.Timeout(10, connect=6)) as client:
                    await context.route('**/*', lambda route: serve(route, client, validate_url, budget))
                    page = await context.new_page()
                    context.on('page', lambda popup: asyncio.ensure_future(popup.close()) if popup is not page else None)
                    page.on('dialog', lambda dialog: asyncio.ensure_future(dialog.dismiss()))
                    await page.goto(url, wait_until='domcontentloaded', timeout=15000)
                    try:
                        await page.wait_for_load_state('networkidle', timeout=5000)
                    except Exception:
                        pass  # long-polling pages never go idle; whatever has rendered is what we read
                    html = (await page.content())[:3 * DOC_BYTES]
                    final_url = page.url
            finally:
                await browser.close()
                trap.close()
    main = budget.main
    if not main or main['status'] != 200:
        raise ValueError(f"Origin HTTP {main['status']}" if main else 'Browser received no page')
    text, title = readable(html)
    if not text.strip():
        raise ValueError('Origin returned no readable text even after rendering')
    headers = {k.lower(): v for k, v in main['headers'].items()}
    age = headers.get('age')
    age_seconds = int(age) if age is not None and age.isdigit() else None
    metadata = {'method': 'origin-browser', 'requested_url': url, 'final_url': final_url, 'title': title, 'observed_at': datetime.now(timezone.utc).isoformat(),
                'http_status': 200, 'server_date': headers.get('date'), 'cache_age_seconds': age_seconds, 'cache_control': headers.get('cache-control'),
                'redirects': budget.redirects, 'current_eligible': not (age is not None and age_seconds is None) and (age_seconds is None or age_seconds <= 900),
                'body_sha256': main['sha256'], 'rendered_sha256': hashlib.sha256(html.encode()).hexdigest(), 'seconds': round(time.monotonic() - started, 2),
                'truncated': len(text) > TEXT_LIMIT, 'javascript_rendered': True, 'subrequests': budget.requests, 'blocked': budget.blocked, 'escaped_connections': budget.escaped}
    return text[:TEXT_LIMIT], html, metadata
