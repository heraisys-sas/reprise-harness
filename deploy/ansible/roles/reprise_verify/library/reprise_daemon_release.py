#!/usr/bin/env python3
"""Module Ansible : preuve du release chargé par le daemon Quick Apply.

Identifie le PID (pgrep) puis lit le binaire Python via lsof — preuve
par lsof, pas par existence de fichier.
"""
from ansible.module_utils.basic import AnsibleModule


def main():
    module = AnsibleModule(argument_spec={})
    import subprocess

    pgrep = subprocess.run(
        ["pgrep", "-f", "claude_desktop_quick"],
        capture_output=True, text=True,
    )
    pids = pgrep.stdout.split()
    if not pids:
        module.fail_json(msg="Daemon Quick Apply absent (pgrep sans résultat)")

    lsof = subprocess.run(
        ["lsof", "-p", pids[0]], capture_output=True, text=True
    )
    binary = next(
        (line.split()[-1] for line in lsof.stdout.splitlines()
         if "bin/python3" in line),
        "",
    )
    module.exit_json(changed=False, pid=int(pids[0]), binary=binary)


if __name__ == "__main__":
    main()
