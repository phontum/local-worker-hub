import json

from hub import jsonedit, textedit

def reply(*edits, summary='done'):
    return json.dumps({'edits': list(edits), 'summary': summary})

def test_a_valid_reply_becomes_the_same_edits_as_the_text_protocol():
    parsed = jsonedit.parse(reply({'path': 'a.py', 'op': 'replace', 'search': 'x = 1', 'replace': 'x = 2'}, {'path': 'b.py', 'op': 'whole', 'replace': 'new'}, {'path': 'c.py', 'op': 'delete'}, summary='changed x'))
    assert not parsed.problems and parsed.summary == 'changed x'
    assert [(e.path, e.search, e.replace, e.delete) for e in parsed.edits] == [('a.py', 'x = 1', 'x = 2', False), ('b.py', None, 'new\n', False), ('c.py', None, None, True)]
    assert isinstance(parsed.edits[0], textedit.Edit)

def test_code_with_quotes_backslashes_newlines_and_unicode_round_trips():
    search = 'const re = /\\d+"/g;\n  label("Zażółć \'x\'");'
    parsed = jsonedit.parse(reply({'path': 'a.ts', 'op': 'replace', 'search': search, 'replace': search.replace('Zażółć', 'ok')}))
    assert not parsed.problems and parsed.edits[0].search == search and 'ok' in parsed.edits[0].replace

def test_invalid_incomplete_or_extra_fields_void_the_whole_reply():
    for text in ('not json', '{"edits": [{"path": "a.py", "op": "replace", "search": "x", "replace": "y"}', '{"edits": [{"path": "a.py", "op": "move"}]}',
                 '{"edits": [], "oops": 1}', ''):
        parsed = jsonedit.parse(text)
        assert parsed.edits == [] and parsed.problems and 'valid JSON edit set' in parsed.problems[0]
    missing = jsonedit.parse(reply({'path': 'a.py', 'op': 'replace', 'search': 'x'}))
    assert missing.edits == [] and 'needs both' in missing.problems[0]
    assert 'needs "replace"' in jsonedit.parse(reply({'path': 'a.py', 'op': 'whole'})).problems[0]

def test_salvage_returns_the_complete_edit_objects_of_a_cut_off_reply():
    full = reply({'path': 'a.py', 'op': 'replace', 'search': 'one', 'replace': 'two'}, {'path': 'b.py', 'op': 'replace', 'search': 'three {"x": 1}', 'replace': 'four'},
                 {'path': 'c.py', 'op': 'replace', 'search': 'five', 'replace': 'six'})
    cut = full[:full.index('"c.py"') + 40]  # the third edit is cut off mid-string
    saved = jsonedit.salvage(cut)
    assert [(e.path, e.search) for e in saved] == [('a.py', 'one'), ('b.py', 'three {"x": 1}')]
    assert jsonedit.salvage('{"edits": [') == [] and jsonedit.salvage('no json here') == []
    assert len(jsonedit.salvage(full)) == 3

def test_salvage_stops_at_the_first_invalid_edit():
    text = '{"edits": [{"path": "a.py", "op": "replace", "search": "x", "replace": "y"}, {"path": "b.py", "op": "replace"}, {"path": "c.py", "op": "delete"}]}'
    assert [e.path for e in jsonedit.salvage(text)] == ['a.py']

def test_format_text_names_deletable_files_only_when_there_are_some():
    assert 'Deletable' not in jsonedit.format_for() and 'Deletable: a.py, b.py.' in jsonedit.format_for(['a.py', 'b.py'])
    assert '"op": "replace"' in jsonedit.format_for() and 'under 150 lines' in jsonedit.format_for()
