import json, subprocess, sys, os

SERVER = "/Users/olivierhz/Library/Application Support/Reprise/integration-releases/4816d91eaa1729bdd3dec89d69190b9f16808f202b6aa7371ffafbf870e36ed0/Contents/Resources/helpers/codex_expert_mcp.py"

proc = subprocess.Popen([sys.executable, "-E", "-s", "-B", SERVER,
    "--project-root", "/Users/olivierhz/Projets/reprise-harness/workspaces/harness",
    "--state", "/Users/olivierhz/Library/Application Support/Reprise/state",
    "--socket", "/Users/olivierhz/.codex/app-server-control/app-server-control.sock",
    "--tools", "reprise_backends,reprise_delegate,reprise_requests,reprise_reconcile"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def send(obj): proc.stdin.write(json.dumps(obj).encode() + b"\n"); proc.stdin.flush()

def recv():
    while True:
        line = proc.stdout.readline()
        if not line: raise EOFError(proc.stderr.read().decode()[-3000:])
        line = line.strip()
        if line: return json.loads(line)

send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}})
r = recv(); print("INIT:", r.get("result",{}).get("serverInfo"), flush=True)
send({"jsonrpc":"2.0","method":"notifications/initialized"})
send({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"reprise_backends","arguments":{}}})
r = recv()
res = r.get("result",{})
txt = json.dumps(res, ensure_ascii=False)
print("BACKENDS codex models:", [b.get("models") for b in (res.get("backends") or []) if b.get("id")=="codex"] if "backends" in res else txt[:400], flush=True)
send({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"reprise_delegate","arguments":{
    "question":"Reponse courte attendue: confirme en une phrase que la route reprise fonctionne (test admission gpt-6.2-sol, aucune action fichier).",
    "kind":"expertise","level":"standard","model":"gpt-6.2-sol","request_key":"probe-gpt62sol-20261120-a1","wait_seconds":25}}})
r = recv(); print("DELEGATE:", json.dumps(r, ensure_ascii=False)[:2000], flush=True)
proc.terminate()
