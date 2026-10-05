import os
import tempfile
from pathlib import Path
import pytest

_sandbox=tempfile.TemporaryDirectory(prefix='worker-hub-tests-')
os.environ['LOCAL_WORKER_STATE']=str(Path(_sandbox.name)/'state')
os.environ['LOCAL_WORKER_CONFIG']=str(Path(_sandbox.name)/'config')
# Tests must not depend on the host's free RAM; the memory guard has its own explicit tests.
os.environ['LOCAL_WORKER_MIN_AVAILABLE_MB']='0'

@pytest.fixture
def repo(tmp_path):
    p=tmp_path/'repo';p.mkdir();(p/'app.ts').write_text('export const answer = 41;\n')
    return p

@pytest.fixture
def store(tmp_path):
    from hub.store import Store
    return Store(tmp_path/'test.sqlite3')
