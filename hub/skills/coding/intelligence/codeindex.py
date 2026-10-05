"""Deterministic code index: definitions, imports and identifier references, cached per file by mtime and size.

Python uses the stdlib `ast`; JavaScript, TypeScript, Go and Rust use tree-sitter; every text file gets a lexical word table.
Everything is built through ScopedFiles' inventory, so exclusions, secret-file and symlink guards still apply, and the cache
lives in the private state directory. `candidates()` ranks where to read for a task: definitions whose name words match the
task (rarer words weigh more), then the files that reference or import those definitions.
"""
import ast
import hashlib
import json
import math
import os
import re
from collections import defaultdict
from pathlib import Path
from .localize import CODE, identifiers
from hub.settings import STATE

VERSION = 4
MAX_CALLS = 3000
MAX_FILE = 300_000
JS = {'.js', '.jsx', '.mjs', '.cjs'}
TS = {'.ts', '.tsx'}
KEYWORDS = frozenset('''and are as assert async await break case catch class const continue def default del elif else enum except export
extends false finally for from func function global if implements import in interface is lambda let nil none not null or pass pub
raise return self static struct super switch this throw true try type typeof use var void while with yield string number boolean'''.split())
STOP = frozenset('''the and for with from that this what when where which how does did are was were has have had into all any each every
find code involved used uses use using explain return returns path line lines cite citation write read file files function functions
list report show give need make should must about there their than then them they you your out not but can will way also one two
piece pieces repository repo project implemented implementation implement exactly current currently whether answer say tell
get set via per its it's out over under after before between differences difference compare comparing summarize'''.split())
TESTISH = re.compile(r'(?:^|/)(?:tests?|__tests__|spec)/|(?:^|/)test_[^/]*$|[._]test\.\w+$|[._]spec\.\w+$')
WORD = re.compile(r'[A-Za-z_][A-Za-z0-9_]{2,}')
SUBWORD = re.compile(r'[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+')

def stem(word):
    word = word.lower()
    if len(word) > 4 and word.endswith('ies'):
        return word[:-3] + 'y'
    for suffix in ('ing', 'ed', 'es', 's'):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[:-len(suffix)]
    return word

def subwords(name):
    return {stem(w) for w in SUBWORD.findall(name.replace('_', ' ')) if len(w) > 1}

def task_terms(text):
    return {stem(w) for w in re.findall(r'[A-Za-z]{3,}', text) if w.lower() not in STOP} | {w for name in identifiers(text) for w in subwords(name)}

def language(suffix):
    try:
        if suffix in JS:
            import tree_sitter_javascript as mod
            return mod.language()
        if suffix == '.ts':
            import tree_sitter_typescript as mod
            return mod.language_typescript()
        if suffix == '.tsx':
            import tree_sitter_typescript as mod
            return mod.language_tsx()
        if suffix == '.go':
            import tree_sitter_go as mod
            return mod.language()
        if suffix == '.rs':
            import tree_sitter_rust as mod
            return mod.language()
    except ImportError:
        return None
    return None

def node_text(node):
    return node.text.decode('utf-8', 'replace')

def callee(node):
    """The simple name a call expression invokes (`f` for f(), `g` for a.g()), or None for anything computed."""
    if node.type in ('identifier', 'property_identifier', 'field_identifier', 'type_identifier'):
        return node_text(node)
    for field in ('property', 'field', 'name'):
        if node.type in ('member_expression', 'selector_expression', 'field_expression', 'scoped_identifier') and (child := node.child_by_field_name(field)) is not None:
            return node_text(child)
    return None

def python_facts(text):
    defs, imports, calls, inherits = [], [], [], []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return defs, imports, calls, inherits
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and len(calls) < MAX_CALLS:
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else None
            if name:
                calls.append([name, node.lineno])
        elif isinstance(node, ast.ClassDef):
            for base in node.bases:
                name = base.id if isinstance(base, ast.Name) else base.attr if isinstance(base, ast.Attribute) else None
                if name:
                    inherits.append([node.name, name, node.lineno])
    def visit(body, owner='', inner=False):
        """`inner` is a function body: nested functions and classes are definitions too (callers inside them need an owner), and imports there are real edges."""
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defs.append([node.name, 'method' if owner and not inner else 'function', node.lineno, node.end_lineno or node.lineno])
                visit(node.body, '', True)
            elif isinstance(node, ast.ClassDef):
                defs.append([node.name, 'class', node.lineno, node.end_lineno or node.lineno])
                visit(node.body, node.name, False)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and not owner and not inner:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                defs.extend([t.id, 'variable', node.lineno, node.end_lineno or node.lineno] for t in targets if isinstance(t, ast.Name))
            elif isinstance(node, ast.Import):
                imports.extend([alias.name, node.lineno] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(['.' * node.level + (node.module or ''), node.lineno])
            elif inner and isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While, ast.AsyncWith, ast.AsyncFor)):
                for field in ('body', 'orelse', 'finalbody'):
                    visit(getattr(node, field, []) or [], owner, inner)
                for handler in getattr(node, 'handlers', []):
                    visit(handler.body, owner, inner)
    visit(tree.body)
    return defs, imports, calls, inherits

def ts_facts(suffix, source):
    lang = language(suffix)
    if lang is None:
        return [], [], [], []
    from tree_sitter import Language, Parser
    root = Parser(Language(lang)).parse(source.encode('utf-8', 'replace')).root_node
    defs, imports, calls, inherits = [], [], [], []
    named = {'function_declaration': 'function', 'generator_function_declaration': 'function', 'class_declaration': 'class',
             'abstract_class_declaration': 'class', 'method_definition': 'method', 'interface_declaration': 'interface',
             'type_alias_declaration': 'type', 'enum_declaration': 'enum', 'method_declaration': 'method', 'type_spec': 'type',
             'function_item': 'function', 'struct_item': 'struct', 'enum_item': 'enum', 'trait_item': 'trait', 'type_item': 'type',
             'mod_item': 'module', 'const_item': 'const', 'const_spec': 'const', 'var_spec': 'variable'}
    stack = [(root, 0)]
    while stack:
        node, depth = stack.pop()
        kind = node.type
        line, end = node.start_point[0] + 1, node.end_point[0] + 1
        if kind in ('class_declaration', 'abstract_class_declaration') and (cname := node.child_by_field_name('name')) is not None:
            for child in node.children:
                if child.type == 'class_heritage':
                    inner = [child]
                    while inner:
                        part = inner.pop()
                        if part.type in ('identifier', 'type_identifier'):
                            inherits.append([node_text(cname), node_text(part), line])
                        inner.extend(part.children)
        if kind in named and (name := node.child_by_field_name('name')) is not None:
            defs.append([node_text(name), named[kind], line, end])
        elif kind == 'variable_declarator' and (name := node.child_by_field_name('name')) is not None and name.type == 'identifier':
            value = node.child_by_field_name('value')
            top = node.parent is not None and node.parent.parent is not None and node.parent.parent.type in ('program', 'export_statement')
            if top:
                defs.append([node_text(name), 'function' if value is not None and value.type in ('arrow_function', 'function_expression') else 'variable', line, end])
        elif kind == 'pair' and (key := node.child_by_field_name('key')) is not None and key.type in ('property_identifier', 'string') and node.parent is not None:
            owner, hops = node.parent, 0
            while owner is not None and owner.type in ('object', 'pair') and hops < 4:
                owner, hops = owner.parent, hops + 1
            if owner is not None and owner.type in ('assignment_expression', 'variable_declarator', 'export_statement') and hops <= 3:
                defs.append([node_text(key).strip('\'"'), 'property', line, end])
        elif kind == 'import_statement' or (kind == 'export_statement' and node.child_by_field_name('source') is not None):
            if (src := node.child_by_field_name('source')) is not None:
                imports.append([node_text(src).strip('\'"`'), line])
        elif kind == 'call_expression':
            fn, args = node.child_by_field_name('function'), node.child_by_field_name('arguments')
            if fn is not None and len(calls) < MAX_CALLS and (called := callee(fn)):
                calls.append([called, line])
            if fn is not None and node_text(fn) == 'require' and args is not None and args.named_child_count and args.named_children[0].type == 'string':
                imports.append([node_text(args.named_children[0]).strip('\'"`'), line])
        elif kind == 'impl_item' and (target := node.child_by_field_name('type')) is not None and (trait := node.child_by_field_name('trait')) is not None:
            inherits.append([node_text(target), node_text(trait).split('::')[-1].split('<')[0], line])
        elif kind == 'import_spec' and (path := node.child_by_field_name('path')) is not None:
            imports.append([node_text(path).strip('"'), line])
        elif kind == 'use_declaration':
            imports.append([node_text(node).removeprefix('use ').rstrip(';'), line])
        stack.extend((child, depth + 1) for child in reversed(node.children))
    return defs, imports, calls, inherits

def word_table(text):
    table = defaultdict(list)
    for number, line in enumerate(text.split('\n'), 1):
        for word in WORD.findall(line):
            if word.lower() not in KEYWORDS and len(table[word]) < 8 and (not table[word] or table[word][-1] != number):
                table[word].append(number)
    if len(table) > 600:
        table = dict(sorted(table.items(), key=lambda kv: -len(kv[1]))[:600])
    return dict(table)

def analyze(path, text):
    suffix = Path(path).suffix
    if suffix == '.py':
        defs, imports, calls, inherits = python_facts(text)
    elif suffix in JS | TS | {'.go', '.rs'}:
        defs, imports, calls, inherits = ts_facts(suffix, text)
    else:
        defs, imports, calls, inherits = [], [], [], []
    return {'defs': defs, 'imports': imports, 'calls': calls, 'inherits': inherits, 'words': word_table(text)}

class CodeIndex:
    def __init__(self, files):
        self.files = files
        self.data = {}
        self.stats = {'files': 0, 'parsed': 0, 'cached': 0}

    @staticmethod
    def cache_path(root):
        return STATE / 'index' / (hashlib.sha1(str(root).encode()).hexdigest()[:16] + '.json')

    @classmethod
    def load(cls, files, previous=None):
        """Build or refresh the index for the scoped repository; unchanged files come from `previous` (an index already in memory) or the private cache."""
        self = cls(files)
        cache = self.cache_path(files.root)
        if previous is not None:
            old = previous.data
        else:
            try:
                old = json.loads(cache.read_text()) if cache.exists() else {}
            except (OSError, ValueError):
                old = {}
            old = old.get('files', {}) if old.get('version') == VERSION else {}
        for path in files.inventory():
            if path.suffix not in CODE and path.suffix not in {'.md', '.txt', '.json', '.toml', '.yml', '.yaml', '.cfg', '.ini', '.rst'}:
                continue
            relative = str(path)
            try:
                target = files.path(relative)
                stat = target.stat()
                if stat.st_size > MAX_FILE:
                    continue
                signature = [stat.st_mtime_ns, stat.st_size]
                self.stats['files'] += 1
                if relative in old and old[relative]['sig'] == signature:
                    self.data[relative] = old[relative]
                    self.stats['cached'] += 1
                    continue
                self.data[relative] = {'sig': signature, **analyze(relative, target.read_text(errors='replace'))}
                self.stats['parsed'] += 1
            except (OSError, ValueError):
                continue
        if self.stats['parsed'] or len(old) != len(self.data):
            try:
                cache.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                temporary = cache.with_suffix('.tmp')
                temporary.write_text(json.dumps({'version': VERSION, 'files': self.data}))
                os.chmod(temporary, 0o600)
                temporary.replace(cache)
            except OSError:
                pass
        self._by_name = defaultdict(list)
        for path, info in self.data.items():
            for name, kind, start, end in info['defs']:
                self._by_name[name].append((path, kind, start, end))
        return self

    def definitions(self, name):
        return [{'path': p, 'kind': k, 'start': s, 'end': e} for p, k, s, e in self._by_name.get(name, [])]

    def references(self, name, limit=40):
        """Files and lines where the identifier occurs outside its own definition line."""
        own = {(d['path'], d['start']) for d in self.definitions(name)}
        found = []
        for path, info in sorted(self.data.items()):
            found += [{'path': path, 'line': n} for n in info['words'].get(name, []) if (path, n) not in own]
            if len(found) >= limit:
                break
        return found[:limit]

    def resolve(self, raw, source):
        suffix = Path(source).suffix
        base = None
        if suffix in JS | TS and raw.startswith('.'):
            base = (Path(source).parent / raw).as_posix()
            base = os.path.normpath(base)
            options = [base] + [base + ext for ext in sorted(JS | TS | {'.json'})] + [f'{base}/index{ext}' for ext in sorted(JS | TS)]
        elif suffix == '.py':
            dots = len(raw) - len(raw.lstrip('.'))
            module = raw.lstrip('.').replace('.', '/')
            anchor = Path(source).parent
            for _ in range(max(0, dots - 1)):
                anchor = anchor.parent
            base = os.path.normpath((anchor / module).as_posix()) if dots else module
            options = [base + '.py', base + '/__init__.py']
            if dots == 0 and Path(source).parent.as_posix() != '.':
                options.append(os.path.normpath((Path(source).parent / module).as_posix()) + '.py')
        else:
            return None
        return next((o for o in options if o in self.data), None)

    def imports_of(self, path):
        return [(self.resolve(raw, path), raw, line) for raw, line in self.data.get(path, {}).get('imports', [])]

    def importers(self, path):
        return [(other, line) for other, info in self.data.items() for raw, line in info['imports'] if self.resolve(raw, other) == path]

    def enclosing_def(self, path, line):
        """The smallest definition containing the line, as (name, kind, start, end), or None at module level."""
        spans = [(e - s, k in ('class', 'interface', 'struct', 'trait', 'enum', 'type', 'module'), n, k, s, e) for n, k, s, e in self.data.get(path, {}).get('defs', [])
                 if s <= line <= e and k not in ('variable', 'property', 'const')]  # on a tie a function or method beats the type that contains it
        return min(spans)[2:] if spans else None

    def callers_of(self, name, limit=40):
        """Call sites of a name with the definition each sits in. Name-based: two functions with the same name are not told apart."""
        found = []
        for path, info in sorted(self.data.items()):
            for called, line in info.get('calls', []):
                if called == name:
                    inside = self.enclosing_def(path, line)
                    found.append({'path': path, 'line': line, 'in': inside[0] if inside else None, 'kind': inside[1] if inside else 'module'})
        return found[:limit]

    def callees_of(self, path, start, end):
        """Names called inside a line range, with the definitions they resolve to by name (empty list for library or unresolved calls)."""
        names = {}
        for called, line in self.data.get(path, {}).get('calls', []):
            if start <= line <= end:
                names.setdefault(called, []).append(line)
        return [{'name': n, 'lines': lines[:5], 'definitions': self.definitions(n)[:3]} for n, lines in sorted(names.items())]

    def subclasses(self, name):
        """Types that extend or implement `name` (Python bases, TS extends/implements, Rust `impl Trait for Type`); Go interfaces are structural and not found."""
        return [{'path': path, 'name': child, 'line': line} for path, info in sorted(self.data.items()) for child, base, line in info.get('inherits', []) if base == name]

    def enclosing(self, path, line):
        """Smallest definition span containing the line, so a reference is read together with its function."""
        spans = [(e - s, s, e) for _, _, s, e in self.data.get(path, {}).get('defs', []) if s <= line <= e]
        return min(spans)[1:] if spans else None

    def seeds(self, task, extra_text=''):
        """Definitions ranked by how many rare task words their names (or file stems) contain, plus exact identifier hits."""
        exact = set(identifiers(task)) | set(identifiers(extra_text))
        terms = task_terms(task)
        weak = task_terms(extra_text) - terms
        scored = []
        document_frequency = defaultdict(int)
        rows = []
        for path, info in self.data.items():
            stem_words = subwords(Path(path).stem)
            for name, kind, start, end in info['defs']:
                words = subwords(name)
                rows.append((path, name, kind, start, end, words, stem_words))
                for w in words | stem_words:
                    document_frequency[w] += 1
        for path, name, kind, start, end, words, stem_words in rows:
            weight = lambda w: 1 / math.log(2 + document_frequency[w])
            score = sum(weight(w) for w in words & terms) + 0.5 * sum(weight(w) for w in stem_words & terms) + 0.7 * sum(weight(w) for w in words & weak)
            if name in exact:
                score += 3
            if TESTISH.search(path) and 'test' not in terms:
                score *= 0.5  # tests mirror the code's names; they should not crowd out the code under investigation
            if score >= (0.9 if kind == 'variable' else 0.45):
                scored.append((score, path, name, kind, start, end))
        scored.sort(key=lambda r: (-r[0], r[1], r[4]))
        return scored

    def candidates(self, task, extra_text='', limit=24):
        """Ranked read candidates: seed definitions, the places that use them and the files that import them."""
        out, seen = [], set()
        def add(path, start, end, why, score):
            key = (path, start)
            if key in seen or path not in self.data:
                return
            seen.add(key)
            out.append({'path': path, 'start': start, 'end': min(end, start + 59), 'why': why, 'score': round(score, 2)})
        seeds = self.seeds(task, extra_text)
        for score, path, name, kind, start, end in seeds[:10]:
            add(path, start, end, f'defines {kind} {name}', (5 if TESTISH.search(path) else 10) + score)
        exact = [n for n in dict.fromkeys(list(identifiers(task)) + list(identifiers(extra_text))) if n in self._by_name or any(n in i['words'] for i in self.data.values())]
        names = list(dict.fromkeys([s[2] for s in seeds[:4]] + exact[:8]))
        for name in identifiers(task):
            for hit in self.references(name, 8):
                add(hit['path'], max(1, hit['line'] - 1), hit['line'] + 4, f'finds {name}', 9)
        for name in names:
            for hit in self.references(name, 12):
                span = self.enclosing(hit['path'], hit['line']) or (max(1, hit['line'] - 4), hit['line'] + 8)
                add(hit['path'], span[0], span[1], f'uses {name}', 5)
        for score, path, name, kind, start, end in seeds[:4]:
            for other, line in self.importers(path)[:4]:
                add(other, max(1, line - 2), line + 10, f'imports {path}', 3)
        for path, line, word, score in self.mentions(task_terms(task) | task_terms(extra_text))[:10]:
            span = self.enclosing(path, line) or (max(1, line - 3), line + 6)
            add(path, span[0], span[1], f'mentions {word}', 8 + score)
        out.sort(key=lambda c: -c['score'])
        return out[:limit]

    def mentions(self, terms):
        """Identifiers in the code whose name words contain a task word (timeout_s for "timeout"), rarest words first."""
        if not terms:
            return []
        frequency = defaultdict(int)
        parts = {}
        for info in self.data.values():
            for word in info['words']:
                if word not in parts:
                    parts[word] = subwords(word)
                for w in parts[word] & terms:
                    frequency[w] += 1
        rows = []
        for path, info in self.data.items():
            if Path(path).suffix not in CODE:
                continue
            for word, lines in info['words'].items():
                hit = parts[word] & terms
                if hit and word not in self._by_name:
                    score = sum(1 / math.log(2 + frequency[w]) for w in hit)
                    rows += [(path, n, word, score) for n in lines[:2]]
        rows.sort(key=lambda r: (-r[3], r[0], r[1]))
        return rows

def mentioned_text(files, task, limit=6000):
    """Contents of files the task names by path (a requirements doc, a config file): their terms are extra seeds."""
    chunks = []
    for name in dict.fromkeys(re.findall(r'[\w./-]+\.(?:md|txt|rst|json|toml|ya?ml|cfg|ini|py|js|ts|tsx|go|rs)\b', task)):
        try:
            chunks.append(files.path(name).read_text(errors='replace')[:limit])
        except (OSError, ValueError, UnicodeError):
            continue
    return '\n'.join(chunks)

QUOTAS = {'defines': 4, 'finds': 5, 'mentions': 4, 'uses': 2, 'imports': 1}

def select_ranges(candidates, count=12):
    """The host's own picks, by quota per kind (definitions, mentions, uses, imports) so one noisy kind cannot crowd out the rest."""
    picked, taken = [], defaultdict(int)
    for c in candidates:
        kind = c['why'].split()[0]
        if taken[kind] < QUOTAS.get(kind, 1) and len(picked) < count:
            taken[kind] += 1
            picked.append(c)
    return merge_ranges([{'path': c['path'], 'start': c['start'], 'end': c['end'], 'why': c['why']} for c in picked])

def merge_ranges(ranges, gap=3):
    out = []
    for r in sorted(ranges, key=lambda r: (r['path'], r['start'])):
        if out and out[-1]['path'] == r['path'] and r['start'] <= out[-1]['end'] + gap:
            out[-1]['end'] = max(out[-1]['end'], r['end'])
        else:
            out.append(dict(r))
    return out

def format_candidates(candidates, budget=2800):
    lines, used = [], 0
    for c in candidates:
        line = f"{c['path']}:{c['start']}-{c['end']}  {c['why']}"
        if used + len(line) > budget:
            break
        lines.append(line)
        used += len(line) + 1
    return '\n'.join(lines)
