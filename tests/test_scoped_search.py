import json
import pytest
from hub.models import JobRequest
from hub.scoped import ScopedFiles, glob_match

@pytest.fixture
def tree(tmp_path):
    root=tmp_path/'proj';(root/'hub'/'sub').mkdir(parents=True);(root/'src').mkdir()
    (root/'hub'/'registry.py').write_text("DEFAULT_ALIAS = 'gemma'\n");(root/'hub'/'sub'/'deep.py').write_text('x = 1\n');(root/'src'/'app.ts').write_text("export const DEFAULT_ALIAS = 1\n")
    return ScopedFiles(JobRequest(role='investigator',repo=str(root),task='find',idempotency_key='scoped'))

@pytest.mark.parametrize('path,pattern,expected',[
    ('hub/registry.py','hub/**/*',True),('hub/sub/deep.py','hub/**/*',True),('hub/registry.py','hub/',True),('src/app.ts','hub/',False),
    ('hub/registry.py','hub/*.py',True),('hub/registry.py','*.py',True),('hub/registry.py','**/*.py',True),('src/app.ts','**/*.py',False),('hub/registry.py','other/**/*',False)])
def test_glob_match_is_forgiving_but_not_loose(path,pattern,expected):
    assert glob_match(path,pattern) is expected

def test_glob_and_search_use_the_forgiving_matcher(tree):
    assert set(json.loads(tree.glob_files('hub/**/*'))['paths'])=={'hub/registry.py','hub/sub/deep.py'}
    found=json.loads(tree.search_text('DEFAULT_ALIAS','hub/'))
    assert [m['path'] for m in found['matches']]==['hub/registry.py'] and 'hint' not in found

def test_swapped_arguments_return_a_corrective_hint_not_a_silent_empty_result(tree):
    result=json.loads(tree.search_text('hub/','DEFAULT_ALIAS'))
    assert result['matches']==[] and "'text' is the literal string" in result['hint']
