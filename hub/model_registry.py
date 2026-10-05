"""Local model registry: names and fixed contexts. Capabilities are probed, never assumed.

Gemma is the default for single-model flows; Qwen is used where a phase or role profile selects its alias
(for example board triage). Neither default is a measured accuracy claim; see benchmarks/RESULTS.md. ~/.config/local-worker/models.json may override names/contexts.
"""
import json
import re
import httpx
from .settings import CONFIG

DEFAULT_ALIAS = 'gemma'
DEFAULTS = {
    'qwen': {'name': 'qwen3.5:9b', 'num_ctx': 16384},
    'gemma': {'name': 'gemma4:12b-it-qat', 'num_ctx': 16384},
}
CONTEXTS = (16384, 32768)
OLLAMA = 'http://127.0.0.1:11434'

def models():
    result = {alias: dict(value) for alias, value in DEFAULTS.items()}
    path = CONFIG / 'models.json'
    if path.is_file():
        try:
            overrides = json.loads(path.read_text())
        except (OSError, ValueError):
            overrides = {}
        for alias, value in (overrides.items() if isinstance(overrides, dict) else []):
            if not isinstance(value, dict) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,30}', str(alias)):
                continue
            name = value.get('name', result.get(alias, {}).get('name'))
            ctx = value.get('num_ctx', result.get(alias, {}).get('num_ctx', 16384))
            if isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9._:/-]{1,100}', name) and ctx in CONTEXTS:
                result[alias] = {'name': name, 'num_ctx': ctx}
    return result

def resolve(alias=DEFAULT_ALIAS):
    try:
        return models()[alias]
    except KeyError:
        raise ValueError('Unknown local model alias: ' + str(alias)) from None

def model_name(alias=DEFAULT_ALIAS):
    return resolve(alias)['name']

def allowed_names():
    return {value['name'] for value in models().values()}

def probe(name, timeout=5):
    """Ask Ollama what the installed model supports. Never raises."""
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.post(OLLAMA + '/api/show', json={'model': name})
        if response.status_code != 200:
            return {'installed': False, 'status': response.status_code}
        data = response.json()
        return {'installed': True, 'capabilities': data.get('capabilities') or [],
                'parameter_size': (data.get('details') or {}).get('parameter_size'),
                'quantization': (data.get('details') or {}).get('quantization_level')}
    except (httpx.HTTPError, ValueError):
        return {'installed': None, 'error': 'Ollama unavailable'}

def residency(timeout=5):
    """Loaded models and the share of each resident on the GPU, from Ollama's /api/ps."""
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            rows = client.get(OLLAMA + '/api/ps').json().get('models', [])
    except (httpx.HTTPError, ValueError):
        return None
    return [{'name': row.get('name'), 'size': row.get('size'), 'size_vram': row.get('size_vram'),
             'on_gpu_fraction': round(row['size_vram'] / row['size'], 3) if row.get('size') else None,
             'context': row.get('context_length')} for row in rows]
