import json
import subprocess
import time
import sys
import httpx
from .settings import URL, CONFIG, initialize

def connect(start=False):
    try:token=initialize() if start else (CONFIG/'api-token').read_text().strip()
    except OSError:raise RuntimeError('Local Worker Hub is not initialized. Run local-worker start.')
    client=httpx.Client(base_url=URL,headers={'Authorization':'Bearer '+token},timeout=httpx.Timeout(35,connect=2),trust_env=False)
    try:
        client.get('/api/health').raise_for_status()
        return client
    except httpx.HTTPError:
        if not start:client.close();raise RuntimeError('Local Worker Hub is not reachable. Run local-worker start; use CLI status/result if MCP transport is delayed. External approval waits are not worker execution.')
    p=subprocess.run(['systemctl','--user','start','local-worker-hub.service'],capture_output=True,text=True,timeout=15)
    if p.returncode:client.close();raise RuntimeError('Could not start local worker service: '+p.stderr.strip())
    for _ in range(50):
        try:
            client.get('/api/health').raise_for_status();return client
        except httpx.HTTPError:time.sleep(0.2)
    client.close();raise RuntimeError('Worker service did not become ready')

def call(method,path,data=None):
    retry_safe = method=='GET' or (method=='POST' and (
        (path=='/api/jobs' or path.endswith('/summarize')) and bool((data or {}).get('idempotency_key'))
        or path.endswith('/cancel') or path.endswith('/review')))
    for attempt in range(2 if retry_safe else 1):
        client = None
        try:
            client = connect(start=True) if method!='GET' else connect()
            response=client.request(method,path,json=data)
            if not response.is_success:
                try:detail=response.json().get('detail',response.text)
                except ValueError:detail=response.text
                raise RuntimeError(str(detail)[:1000])
            return response.json()
        except httpx.TransportError as exc:
            if attempt+1 >= (2 if retry_safe else 1):
                raise RuntimeError('Hub transport failed after bounded retry: '+type(exc).__name__+'. Inspect local-worker status/result; do not create a replacement job with a new key.') from exc
            print('local-worker: one transport retry of the identical request',file=sys.stderr)
        finally:
            if client is not None:client.close()
