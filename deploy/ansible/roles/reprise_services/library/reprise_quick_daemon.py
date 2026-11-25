#!/usr/bin/env python3
"""Module Ansible : cycle de vie du daemon Quick Apply (claude-desktopd).

Arrêt propre (SIGTERM), test de vivacité par connect() — jamais par
existence du fichier socket — retrait du socket orphelin uniquement si
la connexion est refusée (cas #105), relance sur le release courant et
attente du socket.
"""
from ansible.module_utils.basic import AnsibleModule

SOCKET = "~/.claude-desktop/quick-entry.sock"


def socket_alive(path):
    import errno
    import socket

    try:
        s = socket.socket(socket.AF_UNIX)
        s.connect(path)
        s.close()
        return True
    except OSError as error:
        return error.errno == errno.EINTR


def main():
    import os
    import signal
    import subprocess
    import time

    module = AnsibleModule(
        argument_spec={
            "state": {"type": "str", "choices": ["started", "restarted"], "default": "started"},
            "runtime_python": {"type": "str", "required": True},
            "helpers": {"type": "str", "required": True},
            "project_root": {"type": "str", "required": True},
            "state_dir": {"type": "str", "required": True},
            "wait_timeout": {"type": "int", "default": 30},
        },
        supports_check_mode=False,
    )
    params = module.params
    sock = os.path.expanduser(SOCKET)
    changed = False

    if params["state"] == "restarted":
        pgrep = subprocess.run(
            ["pgrep", "-f", "claude_desktop_quick"], capture_output=True, text=True
        )
        for pid in pgrep.stdout.split():
            try:
                os.kill(int(pid), signal.SIGTERM)
                changed = True
            except ProcessLookupError:
                pass
        time.sleep(2)

    alive = socket_alive(sock)
    if not alive and os.path.exists(sock):
        # Socket orphelin : connect() refusé -> retrait autorisé (cas #105).
        os.remove(sock)
        changed = True

    if not socket_alive(sock):
        env = dict(os.environ, AGENT_RESUME_STATE=params["state_dir"])
        subprocess.Popen(
            [params["runtime_python"], "-E", "-s", "-B",
             params["helpers"] + "/claude_desktop_quick.py",
             "--project-root", params["project_root"]],
            cwd=params["state_dir"], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        changed = True

    deadline = time.time() + params["wait_timeout"]
    while time.time() < deadline:
        if socket_alive(sock):
            module.exit_json(changed=changed, socket=sock, ready=True)
        time.sleep(0.5)
    module.fail_json(msg="Socket Quick Apply non prêt après %ss : %s" % (params["wait_timeout"], sock))


if __name__ == "__main__":
    main()
