"""Local Worker Hub. Core (job queue, runner, service, CLI, MCP adapter, models, scoped tools, report) lives in this package; capabilities live in `hub.skills`.

Skill modules used to be top-level (`hub.workspace`, `hub.codeindex`, ...). Those names still import: `_Aliases` maps each old dotted name to the module's new location and
returns the very same module object, so `monkeypatch`, `import hub.workspace as w` and string targets keep working. New code should import the canonical path."""
import importlib
import importlib.abc
import importlib.util
import sys

MOVED = {
    'skills.coding.intelligence': ('codeindex', 'localize', 'repostate', 'testmap', 'intel'),
    'skills.coding.execution': ('execctx', 'testparse'),
    'skills.coding.validation': ('validation', 'profiles', 'acceptance'),
    'skills.coding.editing': ('textedit', 'contextpack', 'editgate', 'mappings', 'workspace'),
    'skills.coding.delegation': ('spec', 'router', 'outcomes', 'incident'),
    'skills.research': ('answer_review', 'answers', 'ask', 'board', 'board_drift', 'board_prompts', 'browser_page', 'ledger', 'product_extract', 'providers',
                        'public_page', 'retrieval', 'structured', 'web_fixtures', 'web_provider', 'web_verification'),
    'skills.personal': ('plain', 'preferences'),
}
NEW_HOME = {name: package for package, names in MOVED.items() for name in names}

class _AliasLoader(importlib.abc.Loader):
    def __init__(self, real):
        self.real = real
        self.saved = None

    def create_module(self, spec):
        module = importlib.import_module(self.real)  # the same module object under both names
        self.saved = (module.__spec__, module.__loader__)
        return module

    def exec_module(self, module):
        module.__spec__, module.__loader__ = self.saved  # importing sets the alias spec on the shared module; its own spec must stay (relative imports use it)

    def get_code(self, fullname):  # `python -m hub.<old name>` needs the code object
        return importlib.util.find_spec(self.real).loader.get_code(self.real)

class _Aliases(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        parts = fullname.split('.')
        if len(parts) < 2 or parts[0] != 'hub' or parts[1] not in NEW_HOME:
            return None
        real = '.'.join(['hub', NEW_HOME[parts[1]], parts[1]] + parts[2:])
        spec = importlib.util.spec_from_loader(fullname, _AliasLoader(real), is_package=importlib.util.find_spec(real) is not None and importlib.util.find_spec(real).submodule_search_locations is not None)
        return spec

if not any(isinstance(finder, _Aliases) for finder in sys.meta_path):
    sys.meta_path.insert(0, _Aliases())
