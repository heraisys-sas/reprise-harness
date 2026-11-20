import json, subprocess, sys, threading

SERVER = "/Users/olivierhz/Library/Application Support/Reprise/integration-releases/4816d91eaa1729bdd3dec89d69190b9f16808f202b6aa7371ffafbf870e36ed0/Contents/Resources/helpers/codex_expert_mcp.py"
proc = subprocess.Popen([sys.executable, "-E", "-s", "-B", SERVER,
    "--project-root", "/Users/olivierhz/Projets/reprise-harness/workspaces/harness",
    "--state", "/Users/olivierhz/Library/Application Support/Reprise/state",
    "--socket", "/Users/olivierhz/.codex/app-server-control/app-server-control.sock",
    "--tools", "reprise_backends,reprise_delegate,reprise_requests,reprise_reconcile,reprise_notifications,reprise_followup"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def send(obj): proc.stdin.write(json.dumps(obj).encode() + b"\n"); proc.stdin.flush()
def recv():
    while True:
        line = proc.stdout.readline()
        if not line: raise EOFError(proc.stderr.read().decode()[-2000:])
        if line.strip(): return json.loads(line)

send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}})
recv(); send({"jsonrpc":"2.0","method":"notifications/initialized"})
send({"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}})
r = recv()
tools = [t["name"] for t in r["result"]["tools"]]
print("TOOLS:", tools, flush=True)

results = {}
def tour(rid, rk):
    send({"jsonrpc":"2.0","id":rid,"method":"tools/call","params":{"name":"reprise_delegate","arguments":{
        "question":f"Reponse en UNE phrase, question test {rk}: quelle est la capitale de la France ? Aucune action fichier.",
        "kind":"expertise","level":"standard","model":"gpt-6-luna","request_key":rk,"wait_seconds":150}}})
    r = recv()
    d = json.loads(r["result"]["content"][0]["text"])
    results[rk] = (d.get("status"), d.get("thread_id"), (d.get("response") or "")[:120])

t1 = threading.Thread(target=tour, args=(3,"probe-par-20261120-p1"))
t2 = threading.Thread(target=tour, args=(4,"probe-par-20261120-p2"))
t1.start(); t2.start(); t1.join(200); t2.join(200)
for k,v in results.items(): print(k, "->", v, flush=True)
proc.terminate()
