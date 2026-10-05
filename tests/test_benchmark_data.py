import sys
import uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'benchmarks'))
import run_board
from hub.models import JobRequest

def test_gold_spans_are_literal_and_ids_unique():
    assert len({t[0] for t in run_board.TASKS})==len(run_board.TASKS)
    assert [(t[0],g) for t in run_board.TASKS for g in t[4] if g not in t[2]]==[]

def test_every_arm_builds_a_valid_request():
    for arm,options in run_board.ARMS.items():
        request=JobRequest(role='personal',task='x',execution_preset='work',timeout=1200 if options.get('board') else 300,idempotency_key=uuid.uuid4().hex,**options)
        assert request.board==bool(options.get('board'))
