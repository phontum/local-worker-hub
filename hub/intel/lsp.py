"""A thin Language Server Protocol client (stdio, JSON-RPC) and a provider that asks a server for semantic answers, falling back to the lexical provider.

Why not adopt a library: the useful part of SolidLSP is its per-server launch quirks, and it also brings Serena's dependency tree and runtime downloads of language
servers, which conflicts with an offline setup. The protocol itself is small. Servers are never installed or started implicitly: `approved_servers` reads the project
profile, and a missing program, a crash or a timeout returns the lexical answer with `precision` unchanged and a note saying why.

LSP locates things by position, so a name is first resolved to a position with the CodeIndex (its definition, or a reference), then the server answers the semantic
question (references, implementations, call hierarchy) at that position."""
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
from .lexical import LexicalProvider
from .provider import answer

TIMEOUT = 15
LANGUAGE_IDS = {'.py': 'python', '.js': 'javascript', '.jsx': 'javascriptreact', '.ts': 'typescript', '.tsx': 'typescriptreact', '.go': 'go', '.rs': 'rust'}

class LspError(RuntimeError):
    pass

def path_uri(path):
    return 'file://' + quote(str(path))

def uri_path(uri):
    return unquote(urlparse(uri).path)

class LspClient:
    """One server process. Requests are synchronous with a timeout; a reader thread matches responses to ids and keeps published diagnostics."""

    def __init__(self, argv, root, env=None, timeout=TIMEOUT):
        self.argv, self.root, self.timeout = list(argv), Path(root).resolve(), timeout
        self.process = None
        self.lock = threading.Lock()
        self.pending, self.responses, self.diagnostics, self.opened = {}, {}, {}, set()
        self.counter = 0
        self.env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'USER', 'LANG', 'LC_ALL')} | (env or {})
        self.last_used = time.monotonic()

    def start(self):
        try:
            self.process = subprocess.Popen(self.argv, cwd=self.root, env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError as error:
            raise LspError(f'could not start {self.argv[0]}: {error}') from error
        threading.Thread(target=self.read_loop, daemon=True).start()
        self.request('initialize', {'processId': os.getpid(), 'rootUri': path_uri(self.root), 'capabilities': {'textDocument': {'publishDiagnostics': {}}}, 'workspaceFolders': None})
        self.notify('initialized', {})
        return self

    def send(self, message):
        body = json.dumps(message).encode()
        try:
            self.process.stdin.write(b'Content-Length: %d\r\n\r\n' % len(body) + body)
            self.process.stdin.flush()
        except (OSError, ValueError, AttributeError) as error:
            raise LspError('language server is not running') from error

    def read_loop(self):
        stream = self.process.stdout
        while True:
            length = None
            while True:
                line = stream.readline()
                if not line:
                    self.fail_all('language server exited')
                    return
                line = line.strip()
                if not line:
                    break
                if line.lower().startswith(b'content-length:'):
                    length = int(line.split(b':')[1])
            if length is None:
                continue
            try:
                message = json.loads(stream.read(length))
            except ValueError:
                continue
            self.dispatch(message)

    def dispatch(self, message):
        if 'id' in message and ('result' in message or 'error' in message):
            with self.lock:
                self.responses[message['id']] = message
                event = self.pending.pop(message['id'], None)
            if event:
                event.set()
        elif message.get('method') == 'textDocument/publishDiagnostics':
            params = message.get('params', {})
            self.diagnostics[uri_path(params.get('uri', ''))] = params.get('diagnostics', [])
        elif 'id' in message and 'method' in message:  # a server-to-client request we do not implement: answer so it does not wait
            self.send({'jsonrpc': '2.0', 'id': message['id'], 'result': None})

    def fail_all(self, reason):
        with self.lock:
            for ident, event in list(self.pending.items()):
                self.responses[ident] = {'error': {'message': reason}}
                event.set()
            self.pending.clear()

    def request(self, method, params):
        with self.lock:
            self.counter += 1
            ident, event = self.counter, threading.Event()
            self.pending[ident] = event
        self.send({'jsonrpc': '2.0', 'id': ident, 'method': method, 'params': params})
        if not event.wait(self.timeout):
            with self.lock:
                self.pending.pop(ident, None)
            raise LspError(f'{method} timed out after {self.timeout}s')
        self.last_used = time.monotonic()
        message = self.responses.pop(ident)
        if 'error' in message:
            raise LspError(f"{method}: {message['error'].get('message', 'error')}")
        return message.get('result')

    def notify(self, method, params):
        self.send({'jsonrpc': '2.0', 'method': method, 'params': params})

    def open(self, path, text):
        """Tell the server the current content of a file (always the version on disk, so answers match what the host sees)."""
        uri = path_uri(path)
        if uri in self.opened:
            self.notify('textDocument/didClose', {'textDocument': {'uri': uri}})
        self.notify('textDocument/didOpen', {'textDocument': {'uri': uri, 'languageId': LANGUAGE_IDS.get(Path(path).suffix, 'plaintext'), 'version': 1, 'text': text}})
        self.opened.add(uri)

    def close(self):
        if self.process and self.process.poll() is None:
            try:
                self.request('shutdown', None)
                self.notify('exit', None)
            except LspError:
                pass
            try:
                self.process.wait(3)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.fail_all('closed')

def approved_servers(repo):
    """{language id: argv} the reviewed project profile allows for this repository; empty when it names none (the default: nothing is started)."""
    try:
        from ..profiles import load_profile
        profile, _ = load_profile(repo)
    except (ValueError, OSError):
        return {}
    return {name: list(argv) for name, argv in (getattr(profile, 'lsp', None) or {}).items()}

SUFFIXES = {'python': ('.py',), 'typescript': ('.ts', '.tsx', '.js', '.jsx', '.mjs'), 'go': ('.go',), 'rust': ('.rs',)}
MAX_LIVE = 2
IDLE_SECONDS = 600

class Pool:
    """At most MAX_LIVE warm servers across repositories; the least recently used is stopped to make room and idle ones are stopped on the next access."""

    def __init__(self):
        self.clients, self.lock = {}, threading.Lock()

    def get(self, root, language, argv):
        key = (str(root), language)
        with self.lock:
            now = time.monotonic()
            for other, client in list(self.clients.items()):
                if now - client.last_used > IDLE_SECONDS or (client.process and client.process.poll() is not None):
                    self.clients.pop(other).close()
            if key in self.clients:
                return self.clients[key]
            while len(self.clients) >= MAX_LIVE:
                oldest = min(self.clients, key=lambda k: self.clients[k].last_used)
                self.clients.pop(oldest).close()
            self.clients[key] = LspClient(argv, root).start()
            return self.clients[key]

    def close_all(self):
        with self.lock:
            for client in self.clients.values():
                client.close()
            self.clients.clear()

POOL = Pool()

class LspProvider(LexicalProvider):
    """Semantic answers from a server where it can give them; everything else (and every failure) falls through to the lexical provider."""
    name = 'lsp'

    def __init__(self, files, servers, pool=POOL):
        super().__init__(files)
        self.servers, self.pool = servers, pool  # servers: {language: argv}, approved by the project profile

    def client_for(self, path):
        suffix = Path(path).suffix
        language = next((lang for lang, suffixes in SUFFIXES.items() if suffix in suffixes and lang in self.servers), None)
        if language is None:
            return None
        return self.pool.get(self.files.root, language, self.servers[language])

    @staticmethod
    def lexical(result, error):
        """A lexical answer used because the server could not answer: it says so and keeps its own provider name."""
        return result | {'provider': 'codeindex', 'fallback': str(error)}

    def position(self, name, path=None):
        """(path, line0, col0) of the first definition of the name, found with the index."""
        for d in self.index.definitions(name):
            if path and d['path'] != path:
                continue
            text = self.files.path(d['path']).read_text(errors='replace').split('\n')
            line = text[d['start'] - 1] if d['start'] - 1 < len(text) else ''
            column = line.find(name)
            if column >= 0:
                return d['path'], d['start'] - 1, column
        return None

    def semantic(self, name, method, extra=None, path=None):
        spot = self.position(name, path)
        if spot is None:
            raise LspError('no definition to anchor the question on')
        rel, line, column = spot
        client = self.client_for(rel)
        if client is None:
            raise LspError('no approved language server for ' + (Path(rel).suffix or 'this file type'))
        client.open(self.files.path(rel), self.files.path(rel).read_text(errors='replace'))
        params = {'textDocument': {'uri': path_uri(self.files.path(rel))}, 'position': {'line': line, 'character': column}} | (extra or {})
        return rel, client.request(method, params)

    def relative(self, location):
        uri = location.get('uri') or location.get('targetUri') or ''
        try:
            return str(Path(uri_path(uri)).resolve().relative_to(self.files.root))
        except ValueError:
            return None

    def references(self, name, limit=40):
        try:
            _, result = self.semantic(name, 'textDocument/references', {'context': {'includeDeclaration': False}})
            found = [{'path': p, 'line': loc['range']['start']['line'] + 1} for loc in result or [] if (p := self.relative(loc))]
            return answer(self.name, 'semantic', name=name, references=found[:limit], total=len(found))
        except LspError as error:
            return self.lexical(super().references(name, limit), error)

    def implementations(self, name, limit=40):
        try:
            _, result = self.semantic(name, 'textDocument/implementation')
            found = [{'path': p, 'line': (loc.get('range') or loc.get('targetRange'))['start']['line'] + 1} for loc in result or [] if (p := self.relative(loc))]
            return answer(self.name, 'semantic', name=name, implementations=found[:limit], total=len(found))
        except LspError as error:
            return self.lexical(super().implementations(name, limit), error)

    def callers(self, name, limit=40):
        try:
            rel, items = self.semantic(name, 'textDocument/prepareCallHierarchy')
            client, out = self.client_for(rel), []
            for item in items or []:
                for call in client.request('callHierarchy/incomingCalls', {'item': item}) or []:
                    caller = call['from']
                    out.append({'path': self.relative(caller), 'in': caller['name'], 'line': caller['selectionRange']['start']['line'] + 1})
            return answer(self.name, 'semantic', name=name, callers=out[:limit], total=len(out))
        except LspError as error:
            return self.lexical(super().callers(name, limit), error)

    def diagnostics(self, paths=None, limit=60):
        base = super().diagnostics(paths, limit) | {'provider': 'codeindex'}
        found = []
        for path in paths or []:
            client = self.client_for(path)
            if client is None:
                continue
            try:
                client.open(self.files.path(path), self.files.path(path).read_text(errors='replace'))
                deadline = time.monotonic() + 3
                key = str(self.files.path(path))
                while key not in client.diagnostics and time.monotonic() < deadline:
                    time.sleep(0.05)
                found += [{'path': path, 'line': d['range']['start']['line'] + 1, 'severity': {1: 'error', 2: 'warning'}.get(d.get('severity'), 'info'), 'message': d['message'][:300],
                           'source': d.get('source')} for d in client.diagnostics.get(key, [])]
            except LspError as error:
                base = self.lexical(base, error)
        if found:
            return answer(self.name, 'semantic', diagnostics=found[:limit], total=len(found), note='from the language server; includes type errors')
        return base
