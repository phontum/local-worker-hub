"""Coherent file-level slices, with explicit fallback for unresolved imports."""
import ast
from pathlib import Path
import re
import shutil
from .candidates import materialize

CONFIG_NAMES=('pyproject.toml','pytest.ini','setup.cfg','conftest.py','package.json','tsconfig.json','vitest.config.ts','vite.config.ts')


def resolve(values,path,module):
    parent=Path(path).parent
    if Path(path).suffix=='.py':
        if module.startswith('.'):
            dots=len(module)-len(module.lstrip('.'))
            for _ in range(dots-1):parent=parent.parent
            stem=parent/module.lstrip('.').replace('.','/')
        else:stem=Path(module.replace('.','/'))
        candidates=[str(stem)+'.py',str(stem/'__init__.py')]
    elif module.startswith('.'):
        stem=parent/module
        import posixpath
        stem=Path(posixpath.normpath(str(stem)))
        candidates=[str(stem)+ext for ext in ('','.ts','.tsx','.js','.jsx','.json')]+[str(stem/'index')+ext for ext in ('.ts','.tsx','.js')]
    else:return None
    return next((p for p in candidates if p in values),None)


def imports(path,text):
    if Path(path).suffix=='.py':
        tree=ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):
                yield from (n.name for n in node.names)
            elif isinstance(node,ast.ImportFrom):
                if node.module:yield '.'*node.level+node.module
                else:yield from ('.'*node.level+n.name for n in node.names)
            elif isinstance(node,ast.Call) and (isinstance(node.func,ast.Name) and node.func.id=='__import__' or isinstance(node.func,ast.Attribute) and node.func.attr=='import_module'):
                raise ValueError('Dynamic Python imports require the full workspace')
    elif Path(path).suffix in ('.ts','.tsx','.js','.jsx'):
        if re.search(r'\b(?:import|require)\s*\(',text):raise ValueError('Dynamic JS imports require the full workspace')
        yield from re.findall(r'''\bfrom\s*['"]([^'"]+)['"]|\bimport\s*['"]([^'"]+)['"]''',text)


def build(values,targets,tests=(),stubs=None,external_modules=()):
    stubs=stubs or {};selected=set(targets)|set(tests)
    selected.update(p for p in values if Path(p).name in CONFIG_NAMES)
    selected.update(p for p in values if p.endswith('/__init__.py'))
    overlap=set(stubs)&set(values)
    if overlap:return {'available':False,'fallback':'Stub overlaps repository files: '+', '.join(sorted(overlap))}
    values={**values,**stubs}
    missing=[];todo=list(selected);visited=set()
    try:
        while todo:
            path=todo.pop()
            if path in visited:continue
            visited.add(path)
            if path not in values:raise ValueError('Missing slice file: '+path)
            for module in imports(path,values[path]):
                if isinstance(module,tuple):module=next(m for m in module if m)
                dependency=resolve(values,path,module)
                if dependency:
                    selected.add(dependency);todo.append(dependency)
                elif module.startswith('.') or module.split('.')[0] not in set(external_modules)|set(__import__('sys').stdlib_module_names):
                    missing.append(module)
            if len(selected)>100:raise ValueError('Slice exceeds 100 files')
        if missing:raise ValueError('Unresolved imports: '+', '.join(sorted(set(missing))))
        result={p:values[p] for p in selected}
        for path,text in stubs.items():
            if path in targets:raise ValueError('Stub overlaps edited file')
            result[path]=text
        if sum(len(v.encode()) for v in result.values())>10*1024*1024:raise ValueError('Slice exceeds 10 MiB')
        return {'available':True,'contents':result,'files':sorted(result),'stub_files':sorted(stubs),'fallback':None}
    except (ValueError,SyntaxError) as exc:
        return {'available':False,'fallback':str(exc)}
