import json
import pathlib
import socket
import sys

payload = json.loads(sys.argv[1])
sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.connect(str(pathlib.Path.home() / ".reprise/metaai-reprise-trigger.sock"))
sock.sendall((json.dumps(payload) + "\n").encode())
buf = b""
while b"\n" not in buf:
    chunk = sock.recv(4096)
    if not chunk:
        break
    buf += chunk
print(buf.decode(errors="replace").strip())
