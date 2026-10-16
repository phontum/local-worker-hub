"""llama.cpp transport; normalize provider output for the private job protocol."""
import json
import os

BASE_URL = os.environ.get('LOCAL_WORKER_LLAMA_URL', 'http://127.0.0.1:8080').rstrip('/')

def request_body(body):
    options = body.get('options') or {}
    messages = []
    for message in body.get('messages', []):
        message = dict(message)
        message.pop('thinking', None)
        for call in message.get('tool_calls', []):
            if isinstance(call.get('function', {}).get('arguments'), dict):
                call['function']['arguments'] = json.dumps(call['function']['arguments'])
        messages.append(message)
    result = {'model': body['model'], 'messages': messages,
              'stream': bool(body.get('stream')), 'max_tokens': options.get('num_predict', 4096),
              'temperature': options.get('temperature', .2),
              'chat_template_kwargs': {'enable_thinking': bool(body.get('think'))}}
    if not body.get('think'): result['reasoning_effort'] = 'none'
    if result['stream']: result['stream_options'] = {'include_usage': True}
    if body.get('tools'): result['tools'] = body['tools']
    if body.get('format'):
        result['response_format'] = {'type': 'json_object'}
        if isinstance(body['format'], dict): result['response_format']['schema'] = body['format']
    return result

def normalized(value, message=None, reason=None):
    usage = value.get('usage') or {}
    timings = value.get('timings') or {}
    if message is None:
        choice = (value.get('choices') or [{}])[0]
        message = choice.get('message') or {}
        reason = choice.get('finish_reason')
    return {'message': {'role': 'assistant', 'content': message.get('content') or '',
                        'thinking': message.get('reasoning_content') or '',
                        'tool_calls': message.get('tool_calls') or []},
            'done': True, 'done_reason': 'length' if reason == 'length' else 'stop',
            'prompt_eval_count': usage.get('prompt_tokens', 0),
            'eval_count': usage.get('completion_tokens', 0),
            'prompt_eval_duration': int(timings.get('prompt_ms', 0) * 1e6),
            'eval_duration': int(timings.get('predicted_ms', 0) * 1e6),
            'provider_usage': usage}

async def stream(upstream):
    """Reassemble tool arguments before exposing any executable call."""
    calls = {}; final = {}; reason = None; completed = False
    async for line in upstream.aiter_lines():
        if not line.startswith('data:'): continue
        data = line[5:].strip()
        if data == '[DONE]': completed = True; break
        value = json.loads(data)
        if value.get('error'): raise RuntimeError('llama.cpp inference error')
        if value.get('usage'): final = value
        for choice in value.get('choices', []):
            delta = choice.get('delta') or {}
            for call in delta.get('tool_calls') or []:
                index = call.get('index', 0)
                row = calls.setdefault(index, {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                if call.get('id'): row['id'] = call['id']
                for key in ('name', 'arguments'):
                    row['function'][key] += (call.get('function') or {}).get(key, '')
            if delta.get('content') or delta.get('reasoning_content'):
                yield {'message': {'content': delta.get('content') or '', 'thinking': delta.get('reasoning_content') or ''}, 'done': False}
            if choice.get('finish_reason'): reason = choice['finish_reason']
    if not completed: raise RuntimeError('llama.cpp stream ended before completion')
    for call in calls.values():
        call['function']['arguments'] = json.loads(call['function']['arguments'] or '{}')
    yield normalized(final, {'tool_calls': [calls[k] for k in sorted(calls)]}, reason)
