import json, subprocess, sys

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
        if not line: raise EOFError(proc.stderr.read().decode()[-2000:])
        if line.strip(): return json.loads(line)

send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}})
recv(); send({"jsonrpc":"2.0","method":"notifications/initialized"})
send({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"reprise_backends","arguments":{}}})
r = recv()
data = json.loads(r["result"]["content"][0]["text"])
codex = next((b for b in data["backends"] if b["id"]=="codex"), {})
print("BACKENDS codex models:", codex.get("models"), flush=True)
send({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"reprise_delegate","arguments":{
    "question":"Reponse courte attendue (une phrase): confirme que la route reprise fonctionne apres deploiement. Aucune action fichier.",
    "kind":"expertise","level":"standard","model":"gpt-6-luna","request_key":"probe-luna-20261120-a1","wait_seconds":120}}})
r = recv()
data = json.loads(r["result"]["content"][0]["text"])
print("DELEGATE status:", data.get("status"), "| model:", data.get("model"), "| thread:", data.get("thread_id"), flush=True)
print("response:", (data.get("response") or "")[:400], flush=True)
proc.terminate()
