"""Turn test and lint output into structured failures a frontier agent can read without opening a log.

Deterministic and model-free. Supported: pytest, vitest, node:test (TAP), tsc, ruff (concise), mypy and eslint (stylish).
Anything else is reported as tool 'unknown' with no failures; callers keep the raw log tail as the fallback.
Each failure is {tool, test_id, file, line, message}; counts holds whatever totals the tool prints.
"""
import re

ANSI = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')
MAX_FAILURES = 50
MAX_MESSAGE = 300

def failure(tool, test_id, file=None, line=None, message=''):
    return {'tool': tool, 'test_id': test_id, 'file': file, 'line': line, 'message': ' '.join(message.split())[:MAX_MESSAGE]}

def pytest_counts(text):
    found = re.findall(r'(?m)^=*\s*((?:\d+ \w+(?:, )?)+)(?: \([^)]*\))? in [\d.]+s', text)
    if not found:
        return {}
    return {('error' if k.startswith('error') else k): int(n) for n, k in re.findall(r'(\d+) (\w+)', found[-1])}

def parse_pytest(text):
    sections = {}
    for header, body in re.findall(r'(?ms)^_{3,} (.+?) _{3,}\n(.*?)(?=^_{3,} |^={3,} |\Z)', text):
        name = header.split('.')[-1]
        at = re.findall(r'(?m)^(\S+?\.py):(\d+): ', body)
        message = ' '.join(m.strip() for m in re.findall(r'(?m)^E\s+(.*)$', body) if m.strip())
        sections[name] = (at[-1] if at else (None, None), message)
    out = []
    for kind, ident, brief in re.findall(r'(?m)^(FAILED|ERROR) (\S+?)(?: - (.*))?$', text):
        file, line = sections.get(ident.split('::')[-1], ((None, None), ''))[0]
        message = sections.get(ident.split('::')[-1], (None, ''))[1] or brief
        out.append(failure('pytest', ident, file or ident.split('::')[0], int(line) if line else None, message))
    if not out:
        out = [failure('pytest', name, f, int(l) if l else None, m) for name, ((f, l), m) in sections.items()]
    return out, pytest_counts(text)

def parse_vitest(text):
    out = []
    for block in re.split(r'(?m)^ FAIL  ', text)[1:]:
        lines = block.splitlines()
        where = re.search(r'(?m)^ ❯ (\S+?):(\d+):\d+', block)
        message = next((l for l in lines[1:] if l.strip()), '')
        out.append(failure('vitest', lines[0].strip(), where.group(1) if where else lines[0].split(' > ')[0].strip(), int(where.group(2)) if where else None, message))
    counts = {}
    summary = re.search(r'(?m)^\s*Tests\s+(.*?)\s*\((\d+)\)', text)
    if summary:
        counts = {k: int(n) for n, k in re.findall(r'(\d+) (\w+)', summary.group(1))}
        counts['total'] = int(summary.group(2))
    return out, counts

def parse_node_tap(text):
    out = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        match = re.match(r'^\s*not ok \d+ - (.*?)(?: # .*)?$', line)
        if not match:
            continue
        block = []
        for follow in lines[i + 1:]:
            if re.match(r'^\s*(?:not )?ok \d+ - |^\s*# Subtest', follow):
                break
            block.append(follow)
        body = '\n'.join(block)
        if "failureType: 'subtestsFailed'" in body:
            continue  # a describe() parent; its failing children are listed on their own
        location = re.search(r"location: '(.*?):(\d+):\d+'", body)
        error = re.search(r'error: \|-?\n((?:\s{4,}.*\n?)+)', body) or re.search(r"error: '?(.*?)'?\n", body)
        out.append(failure('node:test', match.group(1), location.group(1) if location else None, int(location.group(2)) if location else None,
                           error.group(1) if error else ''))
    counts = {k: int(n) for k, n in re.findall(r'(?m)^# (pass|fail|skipped|cancelled|todo) (\d+)$', text)}
    return out, counts

def parse_tsc(text):
    out = [failure('tsc', m.group(4), m.group(1), int(m.group(2) or m.group(3)), m.group(5))
           for m in re.finditer(r'(?m)^(\S+?)(?:\((\d+),\d+\)|:(\d+):\d+ -): error (TS\d+): (.*)$', text)]
    return out, {'errors': len(out)}

def parse_ruff(text):
    out = [failure('ruff', code, file, int(line), message) for file, line, code, message in
           re.findall(r'(?m)^(\S+?\.py):(\d+):\d+: ([A-Z]+\d+) (.*)$', text)]
    return out, {'errors': len(out)}

def parse_mypy(text):
    out = [failure('mypy', code or 'error', file, int(line), message) for file, line, message, code in
           re.findall(r'(?m)^(\S+?\.pyi?):(\d+): error: (.*?)(?:  \[([\w-]+)\])?$', text)]
    return out, {'errors': len(out)}

def parse_eslint(text):
    out, file = [], None
    for line in text.splitlines():
        if re.match(r'^/?[\w./@-]+\.[jt]sx?$', line.strip()) and not line.startswith(' '):
            file = line.strip()
        match = re.match(r'^\s+(\d+):\d+\s+error\s+(.*?)\s{2,}(\S+)$', line)
        if match and file:
            out.append(failure('eslint', match.group(3), file, int(match.group(1)), match.group(2)))
    return out, {'errors': len(out)}

DETECT = (
    ('pytest', re.compile(r'(?m)^(?:=+ test session starts|=+ FAILURES =+|FAILED \S+::|\d+ (?:passed|failed)\b.* in [\d.]+s)')),
    ('vitest', re.compile(r'(?m)^ (?:RUN|FAIL)  |^\s*Test Files\s')),
    ('node:test', re.compile(r'(?m)^TAP version|^# (?:pass|fail) \d+$')),
    ('tsc', re.compile(r'error TS\d+:')),
    ('mypy', re.compile(r'(?m)^\S+\.pyi?:\d+: error:')),
    ('ruff', re.compile(r'(?m)^\S+\.py:\d+:\d+: [A-Z]+\d+ ')),
    ('eslint', re.compile(r'(?m)^\s+\d+:\d+\s+error\s+')),
)
PARSERS = {'pytest': parse_pytest, 'vitest': parse_vitest, 'node:test': parse_node_tap, 'tsc': parse_tsc,
           'mypy': parse_mypy, 'ruff': parse_ruff, 'eslint': parse_eslint}

def detect(text, argv=None):
    joined = ' '.join(argv or [])
    for name, hint in (('pytest', 'pytest'), ('vitest', 'vitest'), ('tsc', 'tsc'), ('mypy', 'mypy'), ('ruff', 'ruff'), ('eslint', 'eslint')):
        if hint in joined:
            return name
    if re.search(r'\bnode\b.*--test', joined):
        return 'node:test'
    return next((name for name, pattern in DETECT if pattern.search(text)), None)

def parse(text, argv=None, root=None):
    """Structured view of test or lint output; `root` is stripped from absolute file paths."""
    text = ANSI.sub('', text or '')
    tool = detect(text, argv)
    if tool is None:
        return {'tool': 'unknown', 'counts': {}, 'failures': [], 'truncated': False}
    failures, counts = PARSERS[tool](text)
    if root:
        prefix = root.rstrip('/') + '/'
        for item in failures:
            if item['file'] and item['file'].startswith(prefix):
                item['file'] = item['file'][len(prefix):]
    return {'tool': tool, 'counts': counts, 'failures': failures[:MAX_FAILURES], 'truncated': len(failures) > MAX_FAILURES}
