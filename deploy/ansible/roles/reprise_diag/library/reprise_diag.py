#!/usr/bin/env python3
"""Module Ansible : diagnostic lecture seule de l'installation Reprise.

But : donner à un opérateur humain un état complet, sans rien modifier,
même quand toute la chaîne AI est enrayée. Aucune écriture hors le rapport
de diagnostic. Chaque sonde est non fatale : une section en échec n'empêche
pas les autres. Preuves par connexion/lsof, jamais par existence de fichier.
"""
from ansible.module_utils.basic import AnsibleModule


def run(cmd, timeout=20):
    import subprocess
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "")[-4000:], (p.stderr or "")[-800:]
    except Exception as error:
        return -1, "", str(error)


def probe_scheduler(home, label, uid, runtime_python, helpers, project):
    import plistlib
    section = {}
    plist = home / "Library/LaunchAgents" / (label + ".plist")
    section["plist"] = str(plist)
    section["fichier_present"] = plist.is_file()
    if plist.is_file():
        try:
            settings = plistlib.loads(plist.read_bytes())
            section["arguments"] = settings.get("ProgramArguments")
            section["intervalle"] = settings.get("StartInterval")
        except Exception as error:
            section["erreur_plist"] = str(error)
    rc, out, err = run(["/bin/launchctl", "print", "gui/%s/%s" % (uid, label)])
    section["charge"] = rc == 0
    if rc != 0:
        section["launchctl"] = (err or out)[-300:]
    if runtime_python and helpers:
        rc, out, err = run(
            [runtime_python, "-E", "-s", helpers + "/reprise", "scheduler-service-inspect"],
            timeout=40,
        )
        section["inspect_rc"] = rc
        try:
            section["inspect"] = out if rc == 0 else (err or out)[-600:]
        except Exception:
            section["inspect"] = out[-600:]
    return section


def probe_quick_daemon():
    import errno
    import os
    import socket
    section = {}
    rc, out, _ = run(["/usr/bin/pgrep", "-f", "claude_desktop_quick"])
    pids = out.split()
    section["pids"] = pids
    sock = os.path.expanduser("~/.claude-desktop/quick-entry.sock")
    section["socket"] = sock
    alive = False
    try:
        s = socket.socket(socket.AF_UNIX)
        s.connect(sock)
        s.close()
        alive = True
    except OSError as error:
        section["socket_erreur"] = "connecte" if error.errno == errno.EINTR else (
            "vivant" if error.errno == errno.EISCONN else str(error))
    section["socket_vivant"] = alive
    if pids:
        rc, out, _ = run(["/usr/sbin/lsof", "-p", pids[0]], timeout=30)
        section["binaire"] = next(
            (line.split()[-1] for line in out.splitlines() if "bin/python3" in line), "?")
    if not alive:
        section["note"] = "daemon Quick Apply absent ou socket orphe (cf. runbook MANAGED-UPDATE)"
    return section


def probe_codexd():
    rc, out, _ = run(["/usr/bin/pgrep", "-f", "codexd"])
    return {"pids": out.split()}


def probe_metaai(home, support, config_path, taskstore_path):
    # Pont fichiers depuis le 24/11 (#277) : ni port WebDAV ni montage (#294).
    import json
    import os
    import pathlib
    section = {}
    rc, out, err = run(["/bin/launchctl", "print", "gui/%s/app.reprise.metaaid" % os.getuid()])
    section["daemon_charge"] = rc == 0
    bridge_root = "~/Library/Application Support/Reprise/metaaid-bridge"
    config = support / config_path
    if config.is_file():
        try:
            data = json.loads(config.read_text())
            for key in ("enabled", "ui_bridge", "poll_s", "bridge_root"):
                section["config_" + key] = data.get(key)
            retirees = [key for key in ("dav_host", "dav_port", "dav_root", "mount_point", "dav_user", "dav_password")
                        if key in data]
            if retirees:
                section["config_erreur"] = "clés WebDAV supprimées (#276), metaaid refuse de démarrer : " + ", ".join(retirees)
            bridge_root = data.get("bridge_root") or bridge_root
        except Exception as error:
            section["config_erreur"] = str(error)
    else:
        section["config"] = "absente : " + str(config)
    store = pathlib.Path(taskstore_path)
    if not store.is_absolute():
        store = support / taskstore_path
    if store.is_file():
        try:
            import sqlite3
            db = sqlite3.connect("file:%s?mode=ro" % store, uri=True, timeout=3)
            rows = db.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status").fetchall()
            db.close()
            section["taskstore"] = {k: v for k, v in rows}
        except Exception as error:
            section["taskstore_erreur"] = str(error)
    else:
        section["taskstore"] = "absent : " + str(store)
    root = pathlib.Path(str(bridge_root)).expanduser()
    section["bridge_root"] = str(root)
    if root.is_dir():
        for name in ("inbox", "outbox", "processed"):
            d = root / name
            if d.is_dir():
                section["pont_" + name] = len([p for p in d.iterdir() if not p.name.startswith("._")])
    else:
        section["bridge_root_absent"] = True
    return section


def probe_integrations(runtime_python, helpers, project, harness_project, harness_profile):
    import json as jsonmod
    section = {}
    if not (runtime_python and helpers):
        return {"note": "bundle introuvable, sondes integration sautees"}
    script = (
        "import json, pathlib, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "h = pathlib.Path.home()\n"
        "out = {}\n"
        "try:\n"
        "    from claude_integration import verify as cv\n"
        "    out['claude'] = cv(sys.argv[2], h, project=sys.argv[3])['status']\n"
        "except Exception as e:\n"
        "    out['claude'] = 'erreur: %s' % e\n"
        "try:\n"
        "    from harness_integration import verify as hv\n"
        "    out['harness'] = hv(h / '.dsh', sys.argv[4], root=sys.argv[2], project=sys.argv[5])['status']\n"
        "except Exception as e:\n"
        "    out['harness'] = 'erreur: %s' % e\n"
        "print(json.dumps(out))\n"
    )
    rc, out, err = run(
        [runtime_python, "-E", "-s", "-c", script, helpers, helpers, project,
         harness_profile, harness_project], timeout=60)
    if rc == 0:
        try:
            section.update(jsonmod.loads(out.strip().splitlines()[-1]))
        except Exception as error:
            section["erreur"] = str(error) + " : " + out[-300:]
    else:
        section["erreur"] = (err or out)[-400:]
    return section


def probe_logs(home):
    import time
    section = {}
    logs = home / "Library/Logs/reprise"
    if not logs.is_dir():
        return {"note": "aucun dossier " + str(logs)}
    now = time.time()
    for p in sorted(logs.glob("*.log")):
        try:
            st = p.stat()
            section[p.name] = "%d Ko, modifie il y a %d min" % (st.st_size // 1024, int((now - st.st_mtime) / 60))
        except OSError:
            pass
    return section


def write_report(path, sections):
    import json
    import time

    lines = ["# Diagnostic Reprise — " + time.strftime("%Y-%m-%d %H:%M:%S"), ""]
    icons = {}
    for name, data in sections.items():
        lines.append("## " + name)
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, (dict, list)):
                    lines.append("- %s : %s" % (key, json.dumps(value, ensure_ascii=False, default=str)))
                else:
                    lines.append("- %s : %s" % (key, value))
        else:
            lines.append("- %s" % data)
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return lines


def main():
    import os
    import pathlib
    import time
    module = AnsibleModule(
        argument_spec={
            "builds_dir": {"type": "str", "required": True},
            "support_dir": {"type": "str", "required": True},
            "scheduler_label": {"type": "str", "default": "local.agent-resume"},
            "metaai_config": {"type": "str", "default": "connections/metaai.json"},
            "metaai_taskstore": {"type": "str", "default": "metaai-tasks.sqlite"},
            "project": {"type": "str", "required": True},
            "harness_project": {"type": "str", "default": ""},
            "harness_profile": {"type": "str", "default": "desktop"},
            "report_dir": {"type": "str", "default": ""},
        },
        supports_check_mode=True,
    )
    params = module.params
    home = pathlib.Path.home()
    support = pathlib.Path(params["support_dir"])
    uid = os.getuid()

    # Localisation du bundle : builds/<sha> le plus recent.
    builds = pathlib.Path(params["builds_dir"])
    runtime_python, helpers = "", ""
    try:
        latest = max((p for p in builds.iterdir() if (p / "Contents/Resources/helpers").is_dir()),
                     key=lambda p: p.stat().st_mtime)
        helpers = str(latest / "Contents/Resources/helpers")
        runtime_python = str(latest / "Contents/Resources/runtime/bin/python3")
    except Exception:
        pass

    sections = {}
    sections["Bundle"] = {"python": runtime_python or "INTROUVABLE dans " + params["builds_dir"],
                          "helpers": helpers or "INTROUVABLE"}
    sections["Planificateur"] = probe_scheduler(home, params["scheduler_label"], uid,
                                                runtime_python, helpers, params["project"])
    sections["Quick Apply (claude-desktopd)"] = probe_quick_daemon()
    sections["codexd"] = probe_codexd()
    sections["Meta AI (metaaid)"] = probe_metaai(home, support, params["metaai_config"],
                                                 params["metaai_taskstore"])
    sections["Integrations"] = probe_integrations(runtime_python, helpers, params["project"],
                                                  params["harness_project"], params["harness_profile"])
    sections["Journaux"] = probe_logs(home)

    report_dir = pathlib.Path(params["report_dir"]) if params["report_dir"] else support / "diag"
    report = report_dir / ("diag-" + time.strftime("%Y%m%d-%H%M%S") + ".md")
    if module.check_mode:
        module.exit_json(changed=False, report=None, sections=sections,
                         msg="check-mode : rapport non ecrit")
    try:
        write_report(report, sections)
        os.chmod(report, 0o600)
    except Exception as error:
        module.fail_json(msg="Rapport non ecrit : %s" % error)
    module.exit_json(changed=False, report=str(report), sections=sections,
                     msg="Diagnostic ecrit : %s" % report)


if __name__ == "__main__":
    main()
