import sys
import uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'benchmarks'))
import run_board
import eval_small
from hub.models import JobRequest

def test_gold_spans_are_literal_and_ids_unique():
    assert len({t[0] for t in run_board.TASKS})==len(run_board.TASKS)
    assert [(t[0],g) for t in run_board.TASKS for g in t[4] if g not in t[2]]==[]

def test_every_arm_builds_a_valid_request():
    for arm,options in run_board.ARMS.items():
        request=JobRequest(role='personal',task='x',execution_preset='work',timeout=1200 if options.get('board') else 300,idempotency_key=uuid.uuid4().hex,**options)
        assert request.board==bool(options.get('board'))

def test_personal_eval_cases_are_valid():
    import json
    import re
    import eval_small
    cases = eval_small.load_cases()
    assert len(cases) >= 50
    assert len({c['id'] for c in cases}) == len(cases)
    routes = {'direct', 'web'} | {f'provider:{n}' for n in ('weather', 'fx', 'clock')}
    categories = {'direct', 'web', 'weather', 'shopping', 'multilingual', 'units', 'clarify', 'fx'}
    keys = {'id', 'task', 'category', 'expect_route', 'must_match', 'must_not_match', 'requires_current', 'notes'}
    for case in cases:
        assert set(case) == keys, case['id']
        assert case['category'] in categories and case['expect_route'] in routes, case['id']
        assert isinstance(case['requires_current'], bool) and case['task'].strip()
        for pattern in case['must_match'] + case['must_not_match']:
            re.compile(pattern)
    assert {c['category'] for c in cases} == categories
    assert json.loads(json.dumps(cases)) == cases

def test_route_scoring():
    import eval_small
    assert eval_small.route_ok('web', 'web', False) and not eval_small.route_ok('web', 'direct', True)
    assert eval_small.route_ok('provider:weather', 'web', True) and not eval_small.route_ok('provider:weather', 'web', False)
    assert not eval_small.route_ok('direct', None, True)
