"""SymbolProvider on the CodeIndex: name-based and syntactic, offline, no language server. Honest about it: results carry precision and caveats."""
import ast
from pathlib import Path
from ..codeindex import CODE, TESTISH, language
from ..repostate import STATES
from .provider import answer

BODY_LINES = 60

def syntax_errors(path, text):
    """(line, message) for each syntax error, from `ast` for Python and tree-sitter for the other parsed languages."""
    suffix = Path(path).suffix
    if suffix == '.py':
        try:
            ast.parse(text)
        except SyntaxError as error:
            return [(error.lineno or 1, error.msg)]
        return []
    lang = language(suffix)
    if lang is None:
        return []
    from tree_sitter import Language, Parser
    root = Parser(Language(lang)).parse(text.encode('utf-8', 'replace')).root_node
    if not root.has_error:
        return []
    found, stack = [], [root]
    while stack and len(found) < 10:
        node = stack.pop()
        if node.type == 'ERROR' or node.is_missing:
            found.append((node.start_point[0] + 1, 'missing ' + node.type if node.is_missing else 'syntax error'))
        elif node.has_error:
            stack.extend(reversed(node.children))
    return sorted(found)

class LexicalProvider:
    name = 'codeindex'

    def __init__(self, files):
        self.files = files
        self.index = STATES.index(files)  # warm per-repository index, refreshed incrementally

    def lines(self, path, start, end):
        try:
            text = self.files.path(path).read_text(errors='replace').split('\n')
        except (OSError, ValueError):
            return ''
        return '\n'.join(text[start - 1:end])

    def definitions(self, name, kind=None, limit=20):
        found = [d for d in self.index.definitions(name) if not kind or d['kind'] == kind]
        if found:
            return answer(self.name, 'syntactic', name=name, definitions=found[:limit], total=len(found))
        lowered = name.lower()
        near = [{'name': n, **d} for n, defs in self.index._by_name.items() if lowered in n.lower() for d in self.index.definitions(n)]
        near = [d for d in near if not kind or d['kind'] == kind]
        return answer(self.name, 'lexical', name=name, definitions=near[:limit], total=len(near), note='no exact definition; these names contain the query')

    def references(self, name, limit=40):
        found = self.index.references(name, limit)
        return answer(self.name, 'lexical', name=name, references=found, total=len(found),
                      note='name-based: same-named symbols are not told apart, and at most 8 lines per file are kept per word')

    def implementations(self, name, limit=40):
        subs = self.index.subclasses(name)
        return answer(self.name, 'syntactic', name=name, implementations=subs[:limit], total=len(subs),
                      note='classes that extend or implement the name (Python, TS/JS, Rust impl); Go interfaces are structural and not found')

    def callers(self, name, limit=40):
        found = self.index.callers_of(name, limit)
        return answer(self.name, 'syntactic', name=name, callers=found, total=len(found), note='call sites matched by callee name, not by resolved target')

    def callees(self, name, limit=40, path=None):
        out = []
        for d in [d for d in self.index.definitions(name) if not path or d['path'] == path][:5]:
            out.append({'path': d['path'], 'start': d['start'], 'end': d['end'], 'calls': self.index.callees_of(d['path'], d['start'], d['end'])[:limit]})
        return answer(self.name, 'syntactic', name=name, definitions=out, note='callee names resolved to definitions by name only')

    def diagnostics(self, paths=None, limit=60):
        found = []
        for path in paths or sorted(self.index.data):
            info = self.index.data.get(path)
            if info is None or Path(path).suffix not in CODE:
                continue
            try:
                text = self.files.path(path).read_text(errors='replace')
            except (OSError, ValueError):
                continue
            found += [{'path': path, 'line': line, 'severity': 'error', 'message': message} for line, message in syntax_errors(path, text)]
            for target, raw, line in self.index.imports_of(path):
                relative = raw.startswith('.') and (Path(raw).suffix in ('', '.js', '.ts', '.tsx', '.jsx', '.json') or Path(path).suffix == '.py')
                if relative and target is None:
                    found.append({'path': path, 'line': line, 'severity': 'warning', 'message': 'unresolved relative import ' + raw})
            if len(found) >= limit:
                break
        return answer(self.name, 'syntactic', diagnostics=found[:limit], total=len(found),
                      note='syntax errors and unresolved relative imports only; no type checking (that needs a language server or an approved check)')

    def symbol_context(self, name, budget=6000, path=None):
        defs = [d for d in self.index.definitions(name) if not path or d['path'] == path]
        if not defs:
            return answer(self.name, 'syntactic', name=name, found=False)
        d = defs[0]
        body = self.lines(d['path'], d['start'], min(d['end'], d['start'] + BODY_LINES - 1))
        callers = self.index.callers_of(name, 12)
        tests = sorted({c['path'] for c in callers if TESTISH.search(c['path'])} | {r['path'] for r in self.index.references(name, 60) if TESTISH.search(r['path'])})
        out = {'name': name, 'found': True, 'definition': d, 'other_definitions': defs[1:5], 'signature': body.split('\n')[0] if body else '', 'body': body,
               'body_truncated': d['end'] - d['start'] + 1 > BODY_LINES, 'callers': [c for c in callers if not TESTISH.search(c['path'])][:10],
               'callees': self.index.callees_of(d['path'], d['start'], d['end'])[:20], 'tests': tests[:10]}
        while len(str(out)) > budget and len(out['body']) > 200:
            out['body'] = out['body'][:len(out['body']) * 2 // 3]
            out['body_truncated'] = True
        return answer(self.name, 'syntactic', **out)

    def outline(self, path):
        info = self.index.data.get(path)
        if info is None:
            return answer(self.name, 'syntactic', path=path, found=False)
        return answer(self.name, 'syntactic', path=path, found=True,
                      symbols=[{'name': n, 'kind': k, 'start': s, 'end': e} for n, k, s, e in sorted(info['defs'], key=lambda d: d[2])],
                      imports=[{'module': raw, 'line': line, 'file': target} for target, raw, line in self.index.imports_of(path)])
