"""The Tier-0 tools: each is a direct, zero-token call on a SymbolProvider, with bounded output. The service exposes them; MCP wrappers forward to it."""
import json
import uuid
from hub.models import JobRequest
from hub.scoped import ScopedFiles, ScopeError
from .lexical import LexicalProvider
from .lsp import LspProvider

MAX_CHARS = 24_000
TOOLS = {'find_symbol': ('definitions', ('name', 'kind', 'limit')), 'find_references': ('references', ('name', 'limit')),
         'find_implementations': ('implementations', ('name', 'limit')), 'callers': ('callers', ('name', 'limit', 'path')), 'callees': ('callees', ('name', 'limit', 'path')),
         'diagnostics': ('diagnostics', ('paths', 'limit')), 'symbol_context': ('symbol_context', ('name', 'budget', 'path')), 'outline': ('outline', ('path',))}

def provider_for(repo, servers=None):
    """A read-only scoped view of the repository (the same secret, symlink and exclusion guards as the workers) and the provider over it."""
    request = JobRequest(role='investigator', repo=repo, task='code intelligence', idempotency_key=uuid.uuid4().hex)
    files = ScopedFiles(request)
    return LspProvider(files, servers) if servers else LexicalProvider(files)  # a language server runs only when the reviewed project profile names it (the caller passes those)

def run(tool, repo, arguments, servers=None):
    if tool not in TOOLS:
        raise ValueError('Unknown tool: ' + tool)
    method, allowed = TOOLS[tool]
    kwargs = {k: v for k, v in (arguments or {}).items() if k in allowed and v is not None}
    for key in ('limit', 'budget'):
        if key in kwargs:
            kwargs[key] = max(1, min(int(kwargs[key]), 200 if key == 'limit' else 12_000))
    if tool in ('find_symbol', 'find_references', 'find_implementations', 'callers', 'callees', 'symbol_context') and not str(kwargs.get('name', '')).strip():
        raise ValueError('name is required')
    try:
        result = getattr(provider_for(repo, servers), method)(**kwargs)
    except ScopeError as error:
        raise ValueError(str(error)) from error
    text = json.dumps(result)
    if len(text) > MAX_CHARS:
        result = {'provider': result.get('provider'), 'precision': result.get('precision'), 'truncated': True, 'partial': text[:MAX_CHARS]}
    return result
