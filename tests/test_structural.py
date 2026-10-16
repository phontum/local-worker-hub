from hub.skills.coding.intelligence.structural import compare,describe,syntax


def test_shared_baseline_missing_literal():
    base={'a.py':'x = 1\ny = 2\n'}
    result=compare(base,{'a.py':'x = 3\ny = 2\n'},{'a.py':'x = 3\ny = 4\n'})
    assert not result['exact_match'] and result['edits']['missing']
    assert result['semantic_verdict']=='unknown'


def test_formatting_and_identifier_hashes():
    assert describe('a.py','x=1\n','x = 1\n')['formatting_only']
    assert syntax('a.py','x=1')[0]!=syntax('a.py','y=1')[0]
    assert syntax('a.py','x=1')[0]!=syntax('a.py','x=2')[0]


def test_typescript_and_invalid_fallback():
    d=describe('a.ts','export const x = 1;','export const x = 2;')
    assert d['precision']=='syntactic' and d['literal_changes']['added']==['2']
    assert describe('a.py','x=1','def ???')['precision']=='lexical'


def test_equivalent_code_is_not_proof_and_file_deletion_detected():
    result=compare({'a.py':'x=1\n'},{'a.py':'x = 2\n'},{'a.py':'x=2\n'})
    assert result['ast_equivalent'] and result['semantic_verdict']=='unknown'
    assert not compare({'a.py':''},{},{'a.py':''})['ast_equivalent']
