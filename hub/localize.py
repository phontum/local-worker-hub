"""Repository localization for the Investigator and the editor's context, Agentless/aider-style.

The host builds a compact repository map (files plus top-level symbols) and runs literal searches for identifiers
named in the task, so the model only has to choose what to read and then answer. Reads go through ScopedFiles,
which keeps read scope, secret-file and symlink guards and records evidence IDs for the report.
"""
import ast
import json
import re
from pathlib import Path

CODE = {'.py', '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.go', '.rs', '.java', '.kt', '.rb', '.php', '.cs', '.sh'}
TEXT = CODE | {'.md', '.json', '.toml', '.yml', '.yaml', '.ini', '.cfg', '.txt', '.html', '.css', '.sql'}
JS_SYMBOL = re.compile(r'^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function\s*\*?\s*(\w+)|class\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=|interface\s+(\w+)|type\s+(\w+)\s*=)')
GO_SYMBOL = re.compile(r'^\s*(?:func\s+(?:\([^)]*\)\s*)?(\w+)|type\s+(\w+)|const\s+(\w+)|var\s+(\w+))')
GENERIC = re.compile(r'^\s*(?:pub\s+)?(?:fn|def|class|struct|enum|interface|trait|module)\s+(\w+)')

def python_symbols(text):
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node.name + '()', node.lineno))
        elif isinstance(node, ast.ClassDef):
            out.append((node.name, node.lineno))
            out += [(f'{node.name}.{item.name}()', item.lineno) for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))][:12]
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            out += [(t.id, node.lineno) for t in targets if isinstance(t, ast.Name) and (t.id.isupper() or t.id[0].isupper())]
    return out

def regex_symbols(text, pattern):
    out = []
    for number, line in enumerate(text.split('\n'), 1):
        match = pattern.match(line)
        if match:
            name = next((g for g in match.groups() if g), None)
            if name:
                out.append((name, number))
    return out[:40]

def symbols(path, text):
    suffix = Path(path).suffix
    if suffix == '.py':
        return python_symbols(text)
    if suffix in ('.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs'):
        return regex_symbols(text, JS_SYMBOL)
    if suffix == '.go':
        return regex_symbols(text, GO_SYMBOL)
    if suffix in CODE:
        return regex_symbols(text, GENERIC)
    return []

def identifiers(task):
    """Names worth a literal search: backticked or quoted text, snake_case, CamelCase, UPPER_CASE, dotted paths."""
    found = re.findall(r'`([^`\n]{2,80})`', task) + re.findall(r'"([^"\n]{3,60})"', task) + re.findall(r"'([^'\n]{3,60})'", task)
    found += re.findall(r'\b([A-Za-z]+_[A-Za-z0-9_]+|[a-z]+[A-Z]\w+|[A-Z][a-z0-9]+[A-Z]\w+|[A-Z]{3,}[A-Z0-9_]*)\b', task)
    found += re.findall(r'\b([\w-]+\.(?:py|ts|tsx|js|go|rs|json|toml|md))\b', task)
    out = []
    for item in found:
        item = item.strip()
        if item and item not in out and len(out) < 6:
            out.append(item)
    return out

def repo_map(files, task='', budget=7000):
    """Files with their top-level symbols, the ones mentioning task words first, within a character budget."""
    words = {w.lower() for w in re.findall(r'\w{4,}', task)}
    rows = []
    for path in files.inventory():
        if path.suffix not in TEXT:
            continue
        line = str(path)
        if path.suffix in CODE:
            try:
                target = files.path(str(path))
                if target.stat().st_size <= 300_000:
                    names = symbols(str(path), target.read_text(errors='replace'))
                    if names:
                        line += ': ' + ', '.join(f'{n}@{l}' for n, l in names[:25])
            except (OSError, ValueError):
                pass
        relevance = sum(1 for w in words if w in line.lower())
        rows.append((-relevance, str(path), line))
    rows.sort()
    out, used = [], 0
    for _, _, line in rows:
        if used + len(line) > budget:
            out.append('… (more files not shown)')
            break
        out.append(line)
        used += len(line) + 1
    return '\n'.join(out)

def search_hits(files, task, limit=40):
    lines = []
    for name in identifiers(task):
        try:
            result = json.loads(files.search_text(name, '*'))
        except Exception:
            continue
        for match in result.get('matches', [])[:10]:
            lines.append(f"{match['path']}:{match['line']}: {match['text'].strip()[:160]}  [{result.get('evidence_id')}]")
            if len(lines) >= limit:
                return '\n'.join(lines)
    return '\n'.join(lines)

def read_ranges(files, ranges, limit=6):
    """Read the chosen ranges through the scoped reader; returns prompt text and failures."""
    blocks, errors = [], []
    for item in ranges[:limit]:
        path = str(item.get('path', '')).strip()
        start = max(1, int(item.get('start') or 1))
        end = max(start, int(item.get('end') or start + 79))
        try:
            value = json.loads(files.read_file(path, start=start, lines=min(120, end - start + 1)))
            blocks.append(f"[{value['evidence_id']}] {value['path']} lines {start}-{start + len(value['content'].splitlines()) - 1} of {value['total_lines']}\n{value['content']}")
        except Exception as error:
            files.audit('read_missing', {'path': path, 'error': str(error)[:250]})
            errors.append(f'{path}: {str(error)[:160]}')
    return '\n\n'.join(blocks), errors
