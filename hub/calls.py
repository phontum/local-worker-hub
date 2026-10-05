"""One model-call helper for the host-driven pipelines (ask, investigate, edit): tracing, retry, usage events,
capability gating and the think-then-format pattern, with no tool calling."""
import hashlib
import json
import uuid
import httpx
from .phases import resolve_phase, model_support
from .settings import CONFIG
from .structured import call

def load_profiles(directory, defaults):
    path = directory / 'role-config.json'
    if not path.exists():
        path = CONFIG / 'roles.json'
    user = json.loads(path.read_text()) if path.exists() else {}
    return {**user, **{phase: {**values, **user.get(phase, {})} for phase, values in defaults.items()}}

class Caller:
    def __init__(self, directory, label, request, profiles, prefix='direct'):
        self.directory, self.label, self.request, self.profiles = directory, label, request, profiles
        self.sid = prefix + '-' + uuid.uuid4().hex
        self.client = None

    def event(self, part):
        print(json.dumps({'sessionID': self.sid, 'part': part}), flush=True)

    async def __aenter__(self):
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(900, connect=5), trust_env=False)
        return self

    async def __aexit__(self, *exc):
        await self.client.aclose()

    async def _setup(self, name, system, thinking):
        spec, profile = resolve_phase(self.request, self.profiles, name)
        think = bool(thinking if thinking is not None else spec.thinking)
        supports = await model_support(spec)
        if supports is not None and 'thinking' not in supports:
            think = False
        self.event({'type': 'effective-config', 'phase': name, 'model': spec.model, 'unsupported': [], 'context': spec.context,
                    'thinking': think, 'output': spec.output_limit, 'system_hash': hashlib.sha256(system.encode()).hexdigest()})
        return spec, think, {'num_ctx': spec.context, 'num_predict': spec.output_limit, 'temperature': spec.temperature}

    async def text(self, name, step, system, user, thinking=None):
        spec, think, options = await self._setup(name, system, thinking)
        value = await call(self.client, self.directory, self.label, name, step, {'model': spec.model, 'stream': True, 'think': think, 'options': options,
            'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]}, self.event)
        return (value.get('message') or {}).get('content') or ''

    async def json(self, name, step, system, user, schema, thinking=None):
        """Schema-constrained output. With thinking, reason first and format in a second thinking-off turn."""
        spec, think, options = await self._setup(name, system, thinking)
        messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]
        if think:
            first = await call(self.client, self.directory, self.label, name, step, {'model': spec.model, 'messages': messages, 'stream': True, 'think': True, 'options': options}, self.event)
            messages += [{'role': 'assistant', 'content': (first.get('message') or {}).get('content') or ''},
                         {'role': 'user', 'content': 'Now give the final result as JSON matching the schema.'}]
            step += 1
        value = await call(self.client, self.directory, self.label, name, step, {'model': spec.model, 'messages': messages, 'stream': True, 'think': False,
                                                                                 'format': schema.model_json_schema(), 'options': options}, self.event)
        try:
            return schema.model_validate_json((value.get('message') or {}).get('content') or '')
        except ValueError:
            return None

def write_session(directory, label, caller, prompt, content, blocks=()):
    saved = [{'type': 'user', 'text': prompt}, {'type': 'assistant', 'finish': 'stop', 'content': [*blocks, {'type': 'text', 'text': content}]}]
    session = {'info': {'id': caller.sid, 'outcome': 'succeeded', 'engine': 'ollama-direct', 'location': {'directory': str(directory / 'workspace')}}, 'messages': saved}
    path = directory / (label + '.session.json')
    path.write_text(json.dumps(session))
    path.chmod(0o600)
