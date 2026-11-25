import json, subprocess, sys

SERVER = "/Users/olivierhz/Library/Application Support/Reprise/integration-releases/4816d91eaa1729bdd3dec89d69190b9f16808f202b6aa7371ffafbf870e36ed0/Contents/Resources/helpers/codex_expert_mcp.py"
proc = subprocess.Popen([sys.executable, "-E", "-s", "-B", SERVER,
    "--project-root", "/Users/olivierhz/Projets/reprise-harness/workspaces/harness",
    "--state", "/Users/olivierhz/Library/Application Support/Reprise/state",
    "--socket", "/Users/olivierhz/.codex/app-server-control/app-server-control.sock",
    "--tools", "reprise_notifications"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
def send(obj): proc.stdin.write(json.dumps(obj).encode() + b"\n"); proc.stdin.flush()
def recv():
    while True:
        line = proc.stdout.readline()
        if not line: raise EOFError(proc.stderr.read().decode()[-2000:])
        if line.strip(): return json.loads(line)
send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}})
recv(); send({"jsonrpc":"2.0","method":"notifications/initialized"})
send({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"reprise_notifications","arguments":{
    "action":"read","consumer_id":"asi1-probe-20261120","request_keys":["probe-luna-20261120-a1","probe-par-20261120-p1","probe-par-20261120-p2"]}}})
r = recv()
print(json.dumps(json.loads(r["result"]["content"][0]["text"]), ensure_ascii=False, indent=1)[:2000])
proc.terminate()
