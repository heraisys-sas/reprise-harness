import json, subprocess, sys, os, time

SERVER = "/Users/olivierhz/Library/Application Support/Reprise/integration-releases/4816d91eaa1729bdd3dec89d69190b9f16808f202b6aa7371ffafbf870e36ed0/Contents/Resources/helpers/codex_expert_mcp.py"

def frame(obj):
    body = json.dumps(obj).encode()
    return b"Content-Length: %d\r\n\r\n" % len(body) + body

proc = subprocess.Popen([sys.executable, "-E", "-s", "-B", SERVER,
    "--project-root", "/Users/olivierhz/Projets/reprise-harness/workspaces/harness",
    "--state", "/Users/olivierhz/Library/Application Support/Reprise/state",
    "--socket", "/Users/olivierhz/.codex/app-server-control/app-server-control.sock",
    "--tools", "reprise_backends,reprise_delegate,reprise_requests,reprise_reconcile"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def send(obj): proc.stdin.write(frame(obj)); proc.stdin.flush()

def recv():
    headers = {}
    while True:
        line = proc.stdout.readline()
        if not line: raise EOFError(proc.stderr.read().decode()[-3000:])
        line = line.strip()
        if not line: break
        k, _, v = line.partition(b":"); headers[k.lower()] = v.strip()
    n = int(headers[b"content-length"])
    return json.loads(proc.stdout.read(n))

send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}})
r = recv(); print("INIT OK", flush=True)
send({"jsonrpc":"2.0","method":"notifications/initialized"})
send({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"reprise_backends","arguments":{}}})
r = recv(); print("BACKENDS:", json.dumps(r, ensure_ascii=False)[:600], flush=True)
send({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"reprise_delegate","arguments":{
    "question":"Reponse courte attendue: confirme en une phrase que la route reprise fonctionne (test admission gpt-6.2-sol, aucune action fichier).",
    "kind":"expertise","level":"standard","model":"gpt-6.2-sol","request_key":"probe-gpt62sol-20261120-a1","wait_seconds":20}}})
r = recv(); print("DELEGATE:", json.dumps(r, ensure_ascii=False)[:2000], flush=True)
proc.terminate()
