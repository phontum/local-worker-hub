"""Host finishing for ask answers: real citations from fetched excerpts, unit/time normalizing, report envelope.

The model never writes source lines or links; anything that looks like one is removed and replaced by the
excerpts it reported using, with the host's own fetch time.
"""
import re
from datetime import datetime
from .preferences import load, normalize

SOURCE_LINE = re.compile(r'(?im)^\s*[-*]?\s*(sources?|source\s+used|references?|izvori?|извор[и]?|источник[и]?|quellen?|fuentes?|fontes?)\s*[:：].*$\n?')
LINK = re.compile(r'https?://[^\s)\]>]+')
HEADERS = re.compile(r'(?m)^(\s*)(LOCAL_WORKER_REPORT|END_LOCAL_WORKER_REPORT|Status|Findings|Files|Checks|Risks)(:?)')
KIND_NOTE = {'snippet': ' (search summary, may be outdated)', 'hosted': ' (copy via search provider)', 'page': '', 'provider': ' (structured data)', 'product': ' (product data from page markup)', 'browser': ''}

def local_time(iso, prefs):
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromisoformat(iso).astimezone(ZoneInfo(prefs['timezone'])).strftime('%H:%M')
    except Exception:
        return ''

def clean(text, allowed_urls):
    """Drop model-written source lines and any link that was not actually fetched."""
    text = SOURCE_LINE.sub('', text)
    text = LINK.sub(lambda m: m.group(0) if m.group(0).rstrip('.,;') in allowed_urls else '', text)
    text = re.sub(r'\(\s*\)', '', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()

def sources(excerpts, used, prefs):
    chosen = [e for e in excerpts if e['n'] in set(used)] or [e for e in excerpts if e['kind'] in ('page', 'browser', 'provider')][:3]
    lines, seen = [], set()
    for e in chosen:
        if e['url'] in seen:
            continue
        seen.add(e['url'])
        when = local_time(e['observed_at'], prefs)
        lines.append(f"- {e['title'][:90]} ({e['url']}){KIND_NOTE.get(e['kind'], '')}{', as of ' + when if when else ''}")
    return lines

def envelope(status, findings, risks='None'):
    findings = HEADERS.sub(lambda m: m.group(1) + '⁠' + m.group(2) + m.group(3), findings)
    return ('LOCAL_WORKER_REPORT\nStatus: ' + status + '\nFindings:\n' + findings +
            '\nFiles:\nNone\nChecks:\nNot run\nRisks:\n' + risks + '\nEND_LOCAL_WORKER_REPORT')

def render(answer, excerpts, used, answered, task, note='', prefs=None):
    prefs = prefs or load()
    allowed = {e['url'] for e in excerpts}
    body = normalize(clean(answer, allowed), prefs, task)
    if note:
        body = note + '\n\n' + body
    cited = sources(excerpts, used, prefs) if excerpts else []
    if cited:
        # Page titles are third-party text ("Weather today | 62°F"), so they get the same unit and clock rewrite as the answer.
        body += '\n\nSources:\n' + normalize('\n'.join(cited), prefs, task)
    if answered:
        return envelope('COMPLETE', body)
    return envelope('PARTIAL', body, 'The sources found did not clearly contain the answer.')
