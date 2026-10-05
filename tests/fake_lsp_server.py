"""A scripted language server for tests: speaks LSP framing and answers a few requests with canned data from argv[1] (JSON)."""
import json
import sys
import time

script = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
stdin, stdout = sys.stdin.buffer, sys.stdout.buffer

def read():
    length = None
    while True:
        line = stdin.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":")[1])
    return json.loads(stdin.read(length))

def send(message):
    body = json.dumps(message).encode()
    stdout.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
    stdout.flush()

while True:
    message = read()
    if message is None:
        break
    method = message.get("method")
    if method == "exit":
        break
    if "id" not in message:
        if method == "textDocument/didOpen" and script.get("diagnostics"):
            uri = message["params"]["textDocument"]["uri"]
            send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics", "params": {"uri": uri, "diagnostics": script["diagnostics"]}})
        continue
    if method in script.get("hang", []):
        time.sleep(30)
    if method == "initialize":
        caps = {k: True for k in ("referencesProvider", "implementationProvider", "callHierarchyProvider")}
        caps.update(script.get("capabilities", {}))
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"capabilities": caps}})
    elif method == "shutdown":
        send({"jsonrpc": "2.0", "id": message["id"], "result": None})
    elif method in script.get("errors", []):
        send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "not supported"}})
    else:
        send({"jsonrpc": "2.0", "id": message["id"], "result": script.get("results", {}).get(method)})
    if script.get("crash_after") == method:
        sys.exit(3)
