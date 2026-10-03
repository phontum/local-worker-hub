"""Private bounded local-model trace. Never embedded in compact results."""
import json
import time

MAX_TRACE_BYTES = 16 * 1024 * 1024

def append_trace(directory, phase, step, kind, text):
    path = directory / 'trace.jsonl'
    if path.exists() and path.stat().st_size >= MAX_TRACE_BYTES:
        return False
    with path.open('ab') as stream:
        for offset in range(0,len(text),1000):
            row={'time':time.time(),'phase':phase,'step':step,'kind':kind,'text':text[offset:offset+1000]}
            stream.write((json.dumps(row,ensure_ascii=True)+'\n').encode())
    path.chmod(0o600)
    return True

def read_trace(directory, offset=0, limit=100):
    path=directory/'trace.jsonl'
    if not path.exists():return {'segments':[],'next_offset':offset,'has_more':False,'available':False}
    if path.is_symlink():raise ValueError('Symlink trace denied')
    rows=[];size=0
    with path.open('rb') as stream:
        stream.seek(offset)
        while len(rows)<limit and size<65536:
            position=stream.tell();line=stream.readline(12000)
            if not line or not line.endswith(b'\n'):
                stream.seek(position);break
            rows.append(json.loads(line));size+=len(line)
        next_offset=stream.tell();more=bool(stream.read(1))
    return {'segments':rows,'next_offset':next_offset,'has_more':more,'available':True,
            'capture_limit_bytes':MAX_TRACE_BYTES,'capture_limit_reached':path.stat().st_size>=MAX_TRACE_BYTES}
