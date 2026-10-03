import json
import os
import secrets
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
STATE = Path(os.environ.get('LOCAL_WORKER_STATE', str(Path.home() / '.local/state/opencode/local-worker')))
CONFIG = Path(os.environ.get('LOCAL_WORKER_CONFIG', str(Path.home() / '.config/local-worker')))
URL = 'http://127.0.0.1:8765'
MODEL = 'ollama/qwen3.5:9b'

def initialize():
    for p in (STATE, CONFIG, STATE / 'jobs'):
        p.mkdir(parents=True, exist_ok=True, mode=0o700)
        p.chmod(0o700)
    p = CONFIG / 'api-token'
    if not p.exists():
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'w') as f: f.write(secrets.token_urlsafe(32))
    p.chmod(0o600)
    rates = CONFIG / 'pricing.json'
    if not rates.exists():
        rates.write_text(json.dumps({'model': 'gpt-6.1-sol', 'as_of': None, 'source': None,
            'input_per_million': None, 'cached_input_per_million': None, 'output_per_million': None}, indent=2))
        rates.chmod(0o600)
    return p.read_text().strip()

def pricing():
    try: return json.loads((CONFIG / 'pricing.json').read_text())
    except (OSError, ValueError): return {}
