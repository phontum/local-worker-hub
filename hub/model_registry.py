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
    'coder': {'name': 'qwen2.5-coder:14b', 'num_ctx': 16384},  # benchmark candidate for Editor phases; not a default until it wins benchmarks/eval_junior.py
    'coder7': {'name': 'qwen2.5-coder:7b', 'num_ctx': 16384},
}
CONTEXTS = (16384, 32768)
from .llama_backend import BASE_URL

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
    """Probe the models actually served by llama.cpp; never infer residency."""
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.get(BASE_URL + '/v1/models')
            response.raise_for_status()
            rows = response.json().get('data', [])
        return {'installed': any(row.get('id') == name for row in rows), 'capabilities': ['completion']}
    except (httpx.HTTPError, ValueError):
        return {'installed': None, 'error': 'llama.cpp unavailable'}

def residency(timeout=5):
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.get(BASE_URL + '/v1/models')
            response.raise_for_status()
            rows = response.json().get('data', [])
        return [{'name': row.get('id'), 'size': None, 'size_vram': None,
                 'on_gpu_fraction': None, 'context': None} for row in rows]
    except (httpx.HTTPError, ValueError):
        return None
