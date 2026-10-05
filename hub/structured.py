"""Tools-off structured-output phases for board deliberation: triage, proposals and the arbiter.

Same native chat gateway, tracing and usage events as the main engine. A thinking phase reasons first, then
formats in a separate thinking-off turn (thinking plus JSON in one call exhausted the token cap in Phase 0).
"""
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
import httpx
from .board_prompts import BOARD_PROFILES, SCHEMAS, phase_kind, system_for
from .engine import chat_response
from .models import JobRequest
from .phases import resolve_phase, model_support
from .settings import URL, CONFIG, initialize
from .trace import append_trace

RETRY_CAP = 2

class Flusher:
    """Batches streamed text into the private trace, like the main engine."""
    def __init__(self, directory, phase, step, event):
        self.directory, self.phase, self.step, self.event = directory, phase, step, event
        self.buffers = {'thinking': '', 'content': ''}
        self.last = time.monotonic()

    def segment(self, kind, text):
        self.buffers[kind] += text
        if sum(map(len, self.buffers.values())) >= 1000 or time.monotonic() - self.last >= .25:
            self.flush()

    def flush(self):
        for kind, text in self.buffers.items():
            if text:
                captured = append_trace(self.directory, self.phase, self.step, kind, text)
                self.event({'type': 'trace', 'phase': self.phase, 'step': self.step, 'kind': kind, 'characters': len(text), 'captured': captured})
                self.buffers[kind] = ''
        self.last = time.monotonic()

async def call(client, directory, label, phase, step, body, event):
    url = f'{URL}/inference/{directory.name}/chat'
    headers = {'Authorization': 'Bearer ' + initialize()}
    flusher = Flusher(directory, phase, step, event)
    event({'type': 'step-start'})
    try:
        try:
            value = await chat_response(client, url, body, headers, flusher.segment)
        except httpx.HTTPStatusError as error:
            marker = directory / f'inference-retry-{label}.json'
            if error.response.status_code not in (502, 503, 504) or marker.exists() or len(list(directory.glob('inference-retry-*.json'))) >= RETRY_CAP:
                raise
            # The failed model response executes nothing. Retry this phase's request once, within the job cap.
            flusher.flush()
            marker.write_text(json.dumps({'phase': phase, 'step': step, 'status': error.response.status_code}))
            marker.chmod(0o600)
            event({'type': 'inference-retry', 'phase': phase, 'step': step, 'status': error.response.status_code, 'tools_reexecuted': False})
            value = await chat_response(client, url, body, headers, flusher.segment)
    finally:
        flusher.flush()
    event({'type': 'step-finish', 'tokens': {'prompt_tokens': value.get('prompt_eval_count'), 'completion_tokens': value.get('eval_count')}})
    return value

async def run_structured(directory, label, prompt, phase):
    request = JobRequest.model_validate_json((directory / 'request.json').read_text())
    roles = directory / 'role-config.json'
    if not roles.exists():
        roles = CONFIG / 'roles.json'
    user = json.loads(roles.read_text()) if roles.exists() else {}
    if phase not in BOARD_PROFILES:
        raise ValueError('Unknown board phase: ' + phase)
    profiles = {**user, phase: {**BOARD_PROFILES[phase], **user.get(phase, {})}}
    spec, profile = resolve_phase(request, profiles, phase)
    # Board phase thinking comes from the phase profile, not the request-wide override.
    thinking = bool(profile.get('thinking', False))
    supports = await model_support(spec)
    unsupported = []
    if supports is not None and thinking and 'thinking' not in supports:
        thinking = False
        unsupported.append('thinking')
    schema = SCHEMAS[phase_kind(phase)]
    system = system_for(phase) + '\nCurrent UTC time: ' + datetime.now(timezone.utc).isoformat()
    messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': prompt}]
    sid = 'direct-' + uuid.uuid4().hex

    def event(part):
        print(json.dumps({'sessionID': sid, 'part': part}), flush=True)

    event({'type': 'effective-config', 'phase': phase, 'model': spec.model, 'unsupported': unsupported, 'context': spec.context,
           'thinking': thinking, 'output': spec.output_limit, 'two_step': thinking,
           'system_hash': hashlib.sha256(system.encode()).hexdigest()})
    options = {'num_ctx': spec.context, 'num_predict': spec.output_limit, 'temperature': spec.temperature}
    result = {'ok': False, 'value': None, 'error': None, 'done_reason': None, 'model': spec.model,
              'prompt_tokens': 0, 'output_tokens': 0, 'thinking': thinking}
    async with httpx.AsyncClient(timeout=httpx.Timeout(900, connect=5), trust_env=False) as client:
        step = 0
        if thinking:
            first = await call(client, directory, label, phase, step, {'model': spec.model, 'messages': messages, 'stream': True,
                                                                        'think': True, 'options': options}, event)
            messages += [{'role': 'assistant', 'content': (first.get('message') or {}).get('content') or ''},
                         {'role': 'user', 'content': 'Transport protocol only, not a user requirement. Return the final result as JSON matching the schema, using your analysis above.'}]
            result['prompt_tokens'] += first.get('prompt_eval_count') or 0
            result['output_tokens'] += first.get('eval_count') or 0
            step = 1
        final = await call(client, directory, label, phase, step, {'model': spec.model, 'messages': messages, 'stream': True, 'think': False,
                                                                    'format': schema.model_json_schema(), 'options': options}, event)
    result['prompt_tokens'] += final.get('prompt_eval_count') or 0
    result['output_tokens'] += final.get('eval_count') or 0
    result['done_reason'] = final.get('done_reason')
    try:
        parsed = schema.model_validate_json((final.get('message') or {}).get('content') or '')
        result.update(ok=True, value=parsed.model_dump())
    except ValueError as error:
        result['error'] = ('Invalid structured output (' + str(final.get('done_reason')) + '): ' + str(error))[:400]
    path = directory / (label + '.result.json')
    path.write_text(json.dumps(result))
    path.chmod(0o600)
