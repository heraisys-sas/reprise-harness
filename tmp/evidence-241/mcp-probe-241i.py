import json, subprocess, sys, time

SERVER = "/Users/olivierhz/Library/Application Support/Reprise/integration-releases/4816d91eaa1729bdd3dec89d69190b9f16808f202b6aa7371ffafbf870e36ed0/Contents/Resources/helpers/codex_expert_mcp.py"
proc = subprocess.Popen([sys.executable, "-E", "-s", "-B", SERVER,
    "--project-root", "/Users/olivierhz/Projets/reprise-harness/workspaces/harness",
    "--state", "/Users/olivierhz/Library/Application Support/Reprise/state",
    "--socket", "/Users/olivierhz/.codex/app-server-control/app-server-control.sock",
    "--tools", "reprise_delegate,reprise_requests"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
def send(obj): proc.stdin.write(json.dumps(obj).encode() + b"\n"); proc.stdin.flush()
def recv():
    while True:
        line = proc.stdout.readline()
        if not line: raise EOFError(proc.stderr.read().decode()[-3000:])
        if line.strip(): return json.loads(line)
send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}})
recv(); send({"jsonrpc":"2.0","method":"notifications/initialized"})
send({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"reprise_delegate","arguments":{
    "question":"Reponse en UNE phrase, test post-deploiement: quel est 17*23 ? Aucune action fichier.",
    "kind":"expertise","level":"standard","backend":"chatgpt","request_key":"probe-chatgpt-20261120-a1"}}})
r = recv()
print("SUBMIT:", r["result"]["content"][0]["text"][:600], flush=True)
# poll du résultat durable
for i in range(12):
    time.sleep(10)
    send({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"reprise_requests","arguments":{"request_key":"probe-chatgpt-20261120-a1"}}})
    r = recv()
    d = json.loads(r["result"]["content"][0]["text"])
    st = d.get("status") or d.get("request",{}).get("status")
    print(f"poll {i}: status={st}", flush=True)
    if st in ("finished","completed","recorded","failed","refused"): 
        print(json.dumps(d, ensure_ascii=False)[:1200], flush=True)
        break
proc.terminate()
