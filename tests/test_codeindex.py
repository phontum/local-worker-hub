import os
from pathlib import Path

import pytest

from hub import codeindex
from hub.codeindex import CodeIndex, format_candidates, mentioned_text, select_ranges, stem, subwords
from hub.models import JobRequest
from hub.scoped import ScopedFiles


def scoped(root):
    return ScopedFiles(JobRequest(role='investigator', repo=str(root), task='x', idempotency_key='idx-' + root.name))


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'proj'
    (root / 'src').mkdir(parents=True)
    (root / 'src' / 'config.js').write_text("module.exports = {\n  leadMinutes: 10,\n  quiet: { start: 22, end: 7 },\n};\n")
    (root / 'src' / 'quiet.js').write_text("const config = require('./config');\nfunction isQuiet(hour) {\n  return hour >= config.quiet.start;\n}\nconst later = (x) => x + 1;\nmodule.exports = { isQuiet };\n")
    (root / 'src' / 'main.ts').write_text("import { isQuiet } from './quiet';\nexport interface Plan { id: string }\nexport class Runner {\n  run(hour: number) { return isQuiet(hour); }\n}\n")
    (root / 'app.py').write_text("import config\nfrom pkg.util import helper\n\nLIMIT = 3\n\n\ndef compute(x):\n    return helper(x) + LIMIT\n\n\nclass Box:\n    def open(self):\n        return compute(1)\n")
    (root / 'config.py').write_text("DEFAULTS = {'timeout_s': 5}\n")
    (root / 'pkg').mkdir()
    (root / 'pkg' / 'util.py').write_text("def helper(x):\n    return x\n")
    (root / 'main.go').write_text('package main\n\nimport "fmt"\n\nfunc Greet(name string) string {\n\treturn fmt.Sprint(name)\n}\n\ntype Server struct{}\n')
    (root / 'lib.rs').write_text('use std::fmt;\n\npub struct Point { x: i32 }\n\npub fn origin() -> Point { Point { x: 0 } }\n')
    return root


def names(index, path):
    return {(n, k) for n, k, _, _ in index.data[path]['defs']}


def test_definitions_and_imports_for_each_language(project):
    index = CodeIndex.load(scoped(project))
    assert {('leadMinutes', 'property'), ('quiet', 'property'), ('start', 'property')} <= names(index, 'src/config.js')
    assert {('isQuiet', 'function'), ('later', 'function')} <= names(index, 'src/quiet.js')
    assert {('Plan', 'interface'), ('Runner', 'class'), ('run', 'method')} <= names(index, 'src/main.ts')
    assert {('compute', 'function'), ('Box', 'class'), ('open', 'method'), ('LIMIT', 'variable')} <= names(index, 'app.py')
    assert {('Greet', 'function'), ('Server', 'type')} <= names(index, 'main.go')
    assert {('Point', 'struct'), ('origin', 'function')} <= names(index, 'lib.rs')
    assert ['./config', 1] in index.data['src/quiet.js']['imports'] and ['./quiet', 1] in index.data['src/main.ts']['imports']
    assert ['fmt', 3] in index.data['main.go']['imports']


def test_imports_resolve_to_repository_files(project):
    index = CodeIndex.load(scoped(project))
    assert index.resolve('./config', 'src/quiet.js') == 'src/config.js'
    assert index.resolve('config', 'app.py') == 'config.py'
    assert index.resolve('pkg.util', 'app.py') == 'pkg/util.py'
    assert index.resolve('fs', 'src/quiet.js') is None
    assert ('src/quiet.js', 1) in index.importers('src/config.js') and ('src/main.ts', 1) in index.importers('src/quiet.js')


def test_references_exclude_the_definition_line(project):
    index = CodeIndex.load(scoped(project))
    refs = {(r['path'], r['line']) for r in index.references('isQuiet')}
    assert ('src/main.ts', 4) in refs and ('src/quiet.js', 2) not in refs and ('src/quiet.js', 6) in refs
    assert index.definitions('compute')[0] == {'path': 'app.py', 'kind': 'function', 'start': 7, 'end': 8}


def test_cache_is_reused_and_refreshes_only_changed_files(project):
    first = CodeIndex.load(scoped(project))
    assert first.stats['parsed'] == first.stats['files'] > 0 and first.stats['cached'] == 0
    second = CodeIndex.load(scoped(project))
    assert second.stats['parsed'] == 0 and second.stats['cached'] == second.stats['files']
    path = project / 'pkg' / 'util.py'
    path.write_text("def helper(x):\n    return x\n\n\ndef extra():\n    pass\n")
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 5_000_000))
    third = CodeIndex.load(scoped(project))
    assert third.stats['parsed'] == 1 and ('extra', 'function') in names(third, 'pkg/util.py')
    path.unlink()
    assert 'pkg/util.py' not in CodeIndex.load(scoped(project)).data


def test_secret_files_symlinks_and_excluded_directories_are_never_indexed(project):
    (project / '.env').write_text('TOKEN=secret_value\n')
    (project / 'node_modules').mkdir()
    (project / 'node_modules' / 'dep.js').write_text('function leaked() {}\n')
    (project / 'link.py').symlink_to(project / 'app.py')
    index = CodeIndex.load(scoped(project))
    assert not {'.env', 'node_modules/dep.js', 'link.py'} & set(index.data)
    assert not any('secret_value' in w for info in index.data.values() for w in info['words'])


def test_candidates_rank_definitions_then_users_then_importers(project):
    index = CodeIndex.load(scoped(project))
    found = index.candidates('Find all code involved in the quiet hour check for notifications')
    top = [(c['path'], c['why']) for c in found[:4]]
    assert ('src/quiet.js', 'defines function isQuiet') in top
    assert any(c['path'] == 'src/main.ts' and c['why'].startswith('uses isQuiet') for c in found)
    assert any(c['path'] == 'src/main.ts' and c['start'] <= 1 <= c['end'] for c in found)  # the import line is covered, as a use or an import candidate
    assert format_candidates(found).splitlines()[0].startswith(found[0]['path'] + ':')


def test_identifier_words_inside_names_are_found_even_in_strings(project):
    index = CodeIndex.load(scoped(project))
    found = index.candidates('Where is the request timeout configured?')
    assert any(c['path'] == 'config.py' and c['why'] == 'mentions timeout_s' for c in found)


def test_terms_from_a_file_named_in_the_task_become_seeds(project):
    (project / 'docs').mkdir()
    (project / 'docs' / 'REQ.md').write_text('1. The helper must be used for every compute call.\n')
    files = scoped(project)
    extra = mentioned_text(files, 'Check each requirement in docs/REQ.md')
    assert 'helper' in extra
    found = CodeIndex.load(files).candidates('Check each requirement in docs/REQ.md', extra)
    assert any(c['why'].endswith('helper') for c in found) and any(c['path'] == 'app.py' for c in found)


def test_select_ranges_applies_quotas_and_merges_neighbours():
    cands = [{'path': 'a.py', 'start': i * 2, 'end': i * 2 + 1, 'why': 'defines function f', 'score': 20 - i} for i in range(1, 8)]
    cands += [{'path': 'b.py', 'start': 1, 'end': 5, 'why': 'mentions x', 'score': 9}]
    picked = select_ranges(cands)
    assert any(r['path'] == 'b.py' for r in picked)
    a = [r for r in picked if r['path'] == 'a.py']
    assert len(a) == 1 and a[0]['start'] == 2 and a[0]['end'] <= 9  # only four 'defines' picks, merged into one range


def test_naming_helpers():
    assert subwords('parseHTTPResponse_v2') >= {'parse', 'http', 'response'}
    assert stem('retries') == 'retry' and stem('timeouts') == 'timeout' and stem('go') == 'go'
    assert codeindex.TESTISH.search('tests/test_a.py') and codeindex.TESTISH.search('src/a.test.ts') and not codeindex.TESTISH.search('src/contest.py')

def test_nested_functions_and_lazy_imports_are_indexed(tmp_path):
    import uuid
    from hub.codeindex import CodeIndex
    from hub.models import JobRequest
    from hub.scoped import ScopedFiles
    root = tmp_path / 'p'
    root.mkdir()
    (root / 'util.py').write_text('def tool():\n    return 1\n')
    (root / 'app.py').write_text('def make():\n    def inner():\n        return tool()\n    class Local:\n        def method(self):\n            return inner()\n    try:\n        from util import tool\n    except ImportError:\n        tool = None\n    return inner\n')
    index = CodeIndex.load(ScopedFiles(JobRequest(role='investigator', repo=str(root), task='x', idempotency_key=uuid.uuid4().hex)))
    kinds = {n: k for n, k, _, _ in index.data['app.py']['defs']}
    assert kinds == {'make': 'function', 'inner': 'function', 'Local': 'class', 'method': 'method'}
    assert [c['in'] for c in index.callers_of('tool')] == ['inner']  # attributed to the nested function, not the outer one
    assert [c['in'] for c in index.callers_of('inner')] == ['method']
    assert index.importers('util.py') == [('app.py', 8)]  # the import inside the function body is an edge
