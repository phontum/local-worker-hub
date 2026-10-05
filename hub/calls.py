"""One model-call helper for the host-driven pipelines (ask, investigate, edit): tracing, retry, usage events,
capability gating and the think-then-format pattern, with no tool calling."""
import hashlib
import json
import uuid
from dataclasses import dataclass
import httpx
from .phases import resolve_phase, model_support
from .settings import CONFIG
from .skills.research.structured import call

def load_profiles(directory, defaults):
    path = directory / 'role-config.json'
    if not path.exists():
        path = CONFIG / 'roles.json'
    user = json.loads(path.read_text()) if path.exists() else {}
    return {**user, **{phase: {**values, **user.get(phase, {})} for phase, values in defaults.items()}}

@dataclass
class Generation:
    """One model reply with how it ended. `truncated` means the output limit cut it off, so it must not be trusted as complete."""
    text: str
    done_reason: str | None
    output_tokens: int | None
    limit: int
    name: str = ''
    step: int = 0

    @property
    def truncated(self):
        return self.done_reason == 'length' or (self.output_tokens is not None and self.output_tokens >= self.limit)

    @property
    def cut_off_message(self):
        return f'The model output was cut off at {self.output_tokens or self.limit} of {self.limit} tokens; split the task into fewer or smaller changes'

def generation_of(value, name, step, limit):
    return Generation((value.get('message') or {}).get('content') or '', value.get('done_reason'), value.get('eval_count'), limit, name, step)

class Caller:
    def __init__(self, directory, label, request, profiles, prefix='direct'):
        self.directory, self.label, self.request, self.profiles = directory, label, request, profiles
        self.sid = prefix + '-' + uuid.uuid4().hex
        self.client = None
        self.generations = []

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

    def record(self, value, name, step, options):
        gen = generation_of(value, name, step, options['num_predict'])
        self.generations.append(gen)
        self.event({'type': 'generation', 'phase': name, 'step': step, 'done_reason': gen.done_reason, 'output_tokens': gen.output_tokens,
                    'limit': gen.limit, 'truncated': gen.truncated})
        return gen

    @property
    def truncations(self):
        return [g for g in self.generations if g.truncated]

    async def generate(self, name, step, system, user, thinking=None, schema=None):
        """Free-text reply with its finish state; with `schema` the reply is constrained to that JSON schema (no thinking). Callers decide what a truncated reply means."""
        spec, think, options = await self._setup(name, system, thinking)
        body = {'model': spec.model, 'stream': True, 'think': False if schema else think, 'options': options,
                'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]}
        if schema:
            body['format'] = schema.model_json_schema()
        value = await call(self.client, self.directory, self.label, name, step, body, self.event)
        return self.record(value, name, step, options)

    async def text(self, name, step, system, user, thinking=None):
        return (await self.generate(name, step, system, user, thinking)).text

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
        gen = self.record(value, name, step, options)
        try:
            return schema.model_validate_json(gen.text)
        except ValueError:
            return None  # a truncated reply lands here too; callers read caller.truncations to say so

def write_session(directory, label, caller, prompt, content, blocks=()):
    saved = [{'type': 'user', 'text': prompt}, {'type': 'assistant', 'finish': 'stop', 'content': [*blocks, {'type': 'text', 'text': content}]}]
    session = {'info': {'id': caller.sid, 'outcome': 'succeeded', 'engine': 'ollama-direct', 'generations': [{'phase': g.name, 'step': g.step, 'done_reason': g.done_reason, 'output_tokens': g.output_tokens, 'limit': g.limit, 'truncated': g.truncated} for g in caller.generations], 'location': {'directory': str(directory / 'workspace')}}, 'messages': saved}
    path = directory / (label + '.session.json')
    path.write_text(json.dumps(session))
    path.chmod(0o600)
