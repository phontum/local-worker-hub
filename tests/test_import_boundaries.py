"""Conceptual layers of the hub, enforced as a ratchet (see the architecture plan: core / skills.coding / skills.research / skills.personal).

Every module belongs to exactly one layer, so a new module forces a decision. Rules that already hold and must keep holding:
- coding and research never import each other (a repository worker has no web access and a web worker has no repository access, and the code should say so);
- the leaf modules of a skill never import the job machinery above them (runner, service, cli, engine, mcp_adapter, install), so they stay usable on their own;
- `core` does not import the personal layer's presentation of answers back into skills."""
import ast
from pathlib import Path

import pytest

HUB = Path(__file__).resolve().parents[1] / 'hub'

CODING = {'acceptance', 'codeindex', 'contextpack', 'editgate', 'execctx', 'incident', 'intel', 'localize', 'mappings', 'outcomes', 'profiles', 'repostate', 'router',
          'spec', 'testmap', 'testparse', 'textedit', 'validation', 'workspace'}
RESEARCH = {'answer_review', 'answers', 'ask', 'board', 'board_drift', 'board_prompts', 'browser_page', 'ledger', 'product_extract', 'providers', 'public_page', 'retrieval',
            'structured', 'web_fixtures', 'web_provider', 'web_verification'}
PERSONAL = {'plain', 'preferences'}
CORE = {'__init__', 'calls', 'cli', 'client', 'engine', 'evidence', 'history', 'install', 'mcp_adapter', 'model_registry', 'models', 'phases', 'pipelines', 'presentation',
        'report', 'runner', 'scoped', 'service', 'settings', 'store', 'trace'}
KNOWN_DEBT = {('structured', 'engine')}  # board phases reuse the engine's streaming; goes away when the Board moves to legacy/
JOB_MACHINERY = {'runner', 'service', 'cli', 'engine', 'mcp_adapter', 'install'}

def layer(name):
    return 'coding' if name in CODING else 'research' if name in RESEARCH else 'personal' if name in PERSONAL else 'core' if name in CORE else None

def modules():
    return sorted({p.stem if p.parent == HUB else p.relative_to(HUB).parts[0] for p in HUB.rglob('*.py')})

def imports_of(path):
    """Top-level hub modules this file imports (relative or absolute), at any nesting depth."""
    package_depth = len(path.relative_to(HUB).parts) - 1
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                up = node.level - 1 - package_depth  # levels above the hub package; <= 0 means inside it
                if up > 0:
                    continue
                parts = (node.module or '').split('.') if node.module else []
                if node.level - 1 == package_depth and parts and parts != ['']:
                    found.add(parts[0])
                elif node.level - 1 == package_depth and not parts:
                    found.update(a.name for a in node.names)
                elif node.level - 1 < package_depth:  # relative inside a subpackage: stays in that subpackage unless it climbs to hub
                    if package_depth - (node.level - 1) == 0 and parts:
                        found.add(parts[0])
            elif node.module and node.module.split('.')[0] == 'hub':
                parts = node.module.split('.')
                found.update([parts[1]] if len(parts) > 1 else [a.name for a in node.names])
    return found

def edges():
    out = []
    for path in sorted(HUB.rglob('*.py')):
        source = path.relative_to(HUB).parts[0].removesuffix('.py')
        out += [(source, target) for target in imports_of(path) if target != source]
    return out

def test_every_module_is_assigned_to_a_layer():
    unassigned = [m for m in modules() if layer(m) is None]
    assert not unassigned, f'classify new modules in tests/test_import_boundaries.py: {unassigned}'
    assert not (CODING & RESEARCH or CODING & CORE or RESEARCH & CORE or PERSONAL & (CODING | RESEARCH | CORE))

def test_coding_and_research_do_not_import_each_other():
    crossing = [(a, b) for a, b in edges() if {layer(a), layer(b)} == {'coding', 'research'}]
    assert not crossing, crossing

def test_skill_leaf_modules_do_not_import_the_job_machinery():
    offenders = [(a, b) for a, b in edges() if layer(a) in ('coding', 'research', 'personal') and b in JOB_MACHINERY and (a, b) not in KNOWN_DEBT]
    assert not offenders, offenders

def test_personal_does_not_depend_on_coding():
    assert not [(a, b) for a, b in edges() if layer(a) == 'personal' and layer(b) == 'coding']

def test_the_edge_scanner_sees_real_imports():
    pairs = set(edges())
    assert ('intel', 'codeindex') in pairs and ('spec', 'codeindex') in pairs and ('router', 'outcomes') in pairs and ('pipelines', 'execctx') in pairs
