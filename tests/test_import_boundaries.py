"""The layers of the hub, enforced from the directory layout.

    hub/*.py            core: job queue, runner, service, CLI, MCP adapter, models, scoped tools, report, store, engine and pipelines
    hub/skills/coding/  repository work, in five areas ordered low to high: execution, intelligence, validation, editing, delegation
    hub/skills/research public web research and structured data providers
    hub/skills/personal personal answers

Rules that hold today and must keep holding:
- coding and research never import each other (a repository worker has no web access and a web worker has no repository access, and the code says so);
- personal never imports coding;
- a skill never imports the job machinery above it (runner, service, cli, engine, mcp_adapter, install), so skills stay usable on their own;
- inside coding a module imports only its own area or a lower one;
- the old top-level names of moved modules are only compatibility aliases (hub/__init__.py): no real file is left behind at hub/<old name>.py."""
import ast
from pathlib import Path

import pytest

import hub

HUB = Path(__file__).resolve().parents[1] / 'hub'
CODING_ORDER = ['execution', 'intelligence', 'validation', 'editing', 'delegation']
JOB_MACHINERY = {'hub.runner', 'hub.service', 'hub.cli', 'hub.engine', 'hub.mcp_adapter', 'hub.install'}
KNOWN_DEBT = {('hub.skills.research.structured', 'hub.engine')}  # board phases reuse the engine's streaming; goes away when the Board is deleted

def dotted(path):
    parts = list(path.relative_to(HUB.parent).with_suffix('').parts)
    return parts[:-1] if parts[-1] == '__init__' else parts

def area(parts):
    """('core',) | ('coding', area) | ('research',) | ('personal',) for a dotted module path given as parts."""
    if parts[:2] == ['hub', 'skills'] and len(parts) > 2:
        if parts[2] == 'coding':
            return ('coding', parts[3] if len(parts) > 3 else None)
        return (parts[2],)
    return ('core',)

def imports_of(path):
    """Dotted modules (and imported names, which may be submodules) this file imports, resolved against the file's own package."""
    me = dotted(path)
    package = me if path.name == '__init__.py' else me[:-1]
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            base = package[:len(package) - (node.level - 1)]
            module = base + (node.module.split('.') if node.module else [])
        elif node.module and node.module.split('.')[0] == 'hub':
            module = node.module.split('.')
        else:
            continue
        found.add('.'.join(module))
        found.update('.'.join(module + [a.name]) for a in node.names)
    return {m for m in found if m.startswith('hub')}

def edges():
    out = []
    for path in sorted(HUB.rglob('*.py')):
        source = '.'.join(dotted(path))
        out += [(source, target) for target in imports_of(path) if target != source and not source.startswith(target + '.')]
    return out

def where(name):
    return area(name.split('.'))

def test_coding_and_research_do_not_import_each_other():
    crossing = [(a, b) for a, b in edges() if {where(a)[0], where(b)[0]} == {'coding', 'research'}]
    assert not crossing, crossing

def test_personal_does_not_depend_on_coding():
    assert not [(a, b) for a, b in edges() if where(a)[0] == 'personal' and where(b)[0] == 'coding']

def test_skills_do_not_import_the_job_machinery():
    offenders = [(a, b) for a, b in edges() if where(a)[0] in ('coding', 'research', 'personal') and any(b == m or b.startswith(m + '.') for m in JOB_MACHINERY)
                and not any(a == da and (b == db or b.startswith(db + '.')) for da, db in KNOWN_DEBT)]
    assert not offenders, offenders

def test_coding_areas_import_only_downward():
    rank = {name: i for i, name in enumerate(CODING_ORDER)}
    offenders = []
    for a, b in edges():
        wa, wb = where(a), where(b)
        if wa[0] == 'coding' and wb[0] == 'coding' and wa[1] and wb[1] and rank[wb[1]] > rank[wa[1]]:
            offenders.append((a, b))
    assert not offenders, offenders

def test_every_coding_module_lives_in_a_known_area():
    areas = {p.name for p in (HUB / 'skills' / 'coding').iterdir() if p.is_dir() and p.name != '__pycache__'}
    assert areas == set(CODING_ORDER), areas
    stray = [p.name for p in (HUB / 'skills' / 'coding').glob('*.py') if p.name != '__init__.py']
    assert not stray, stray

def test_moved_modules_are_not_left_behind_and_every_alias_resolves():
    leftovers = [name for name in hub.NEW_HOME if (HUB / f'{name}.py').exists() or (HUB / name).exists()]
    assert not leftovers, leftovers
    import importlib
    for name, package in hub.NEW_HOME.items():
        old, new = importlib.import_module(f'hub.{name}'), importlib.import_module(f'hub.{package}.{name}')
        assert old is new

def test_the_edge_scanner_sees_real_imports():
    pairs = set(edges())
    assert ('hub.skills.coding.intelligence.intel.lexical', 'hub.skills.coding.intelligence.repostate') in pairs
    assert any(a == 'hub.pipelines' and b.startswith('hub.skills.coding.execution.execctx') for a, b in pairs)
