#!/usr/bin/env python3
# metaaid.py — service LaunchAgent unique du pont Meta AI ↔ Reprise (23 nov. 2026)
#
# Un seul process :
#   1. serveur WebDAV (WsgiDAV app servie par cheroot, même process, port 9770)
#   2. watcher du pont (thread, sémantique get_task/submit_result de metaai_bridge_watch)
#   3. gestion du mount webdavfs (ensure_mount au boot ; webdavfs_agent rebranche ensuite)
#
# Installé par l'installeur Reprise comme LaunchAgent utilisateur :
#   ~/Library/LaunchAgents/app.reprise.metaaid.plist → /usr/bin/python3 metaaid.py
#   RunAtLoad + KeepAlive(Crashed, SuccessfulExit=false) ; logs ~/Library/Logs/reprise/metaaid.log
# NB : launchd Agent (session user) et non daemon système — grants TCC et mount
# webdavfs appartiennent à l'utilisateur.

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metaai_bridge_watch as bridge  # noqa: E402

from wsgidav.wsgidav_app import WsgiDAVApp  # noqa: E402
from cheroot import wsgi  # noqa: E402

# Configuration de la connexion « metaai » — convention Reprise (JSON plat, cf.
# ironclaw-config.example.json) ; l'installeur/l'UI de Reprise écrit ce fichier,
# metaaid le lit au démarrage. Absent → défauts bac à sable.
CONFIG_PATH = Path.home() / "Library/Application Support/Reprise/connections/metaai.json"
CONFIG_DEFAULTS = {
    "enabled": True,
    "dav_host": "127.0.0.1",
    "dav_port": 9770,
    "dav_root": "~/MetaAI-bridge-test/webdav",
    "mount_point": "~/MetaAI-bridge-test/mnt",
    "dav_user": None,
    "dav_password": None,
    "poll_s": 2.0,
    "taskstore": "~/Library/Application Support/Reprise/metaai-tasks.sqlite",
    "ui_bridge": False,
    "ui_socket": "~/.reprise/metaai-reprise-trigger.sock",
    "ui_app": "~/Applications/ReMetaAI.app/Contents/MacOS/Meta AI",
}

def log(msg: str) -> None:
    print(f"[metaaid {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_config() -> dict:
    cfg = dict(CONFIG_DEFAULTS)
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        cfg.update({k: v for k, v in raw.items() if k in CONFIG_DEFAULTS and v is not None})
        log(f"config chargée: {CONFIG_PATH}")
    except FileNotFoundError:
        log("config absente — défauts bac à sable")
    except Exception as e:
        log(f"⚠ config illisible ({e}) — défauts")
    cfg["dav_root"] = str(Path(cfg["dav_root"]).expanduser())
    cfg["mount_point"] = str(Path(cfg["mount_point"]).expanduser())
    return cfg

CFG = load_config()
HOST = CFG["dav_host"]
PORT = int(CFG["dav_port"])
DAV_ROOT = Path(CFG["dav_root"])
MNT = Path(CFG["mount_point"])


def _mount_url() -> str:
    """URL de mount, avec creds de la connexion si auth basique configurée."""
    if CFG.get("dav_user") and CFG.get("dav_password"):
        return f"http://{CFG['dav_user']}:{CFG['dav_password']}@{HOST}:{PORT}/"
    return f"http://{HOST}:{PORT}/"

def ensure_mount() -> bool:
    """Monte le volume si absent ou si le mount existant ne correspond pas au
    mount_point configuré (migration de chemin via la connexion Reprise).
    NE JAMAIS stat-er le mount point au boot : si un
    webdavfs zombie existe (serveur mort), tout accès au chemin bloque au timeout FS.
    Séquence sûre : check table de mounts → unmount force silencieux → stat/mkdir → mount."""
    try:
        mounts = subprocess.run(["mount"], capture_output=True, text=True).stdout
        for line in mounts.splitlines():
            if f"{HOST}:{PORT}" in line:
                mounted_path = line.split(" on ", 1)[1].split(" (", 1)[0] if " on " in line else None
                if mounted_path == str(MNT):
                    log("volume déjà monté au chemin configuré")
                    return True
                if mounted_path:
                    log(f"mount au mauvais chemin ({mounted_path}) — migration vers {MNT}")
                    subprocess.run(["diskutil", "unmount", "force", mounted_path],
                                   capture_output=True, timeout=20)
                    break
    except Exception as e:
        log(f"⚠ mount check: {e}")
    try:
        subprocess.run(["diskutil", "unmount", "force", str(MNT)],
                       capture_output=True, timeout=20)  # silencieux si rien à démonter
    except Exception as e:
        log(f"⚠ unmount: {e}")
    time.sleep(0.5)
    try:
        if not MNT.is_dir():
            MNT.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        log(f"⚠ mount point: {e} — on tente le mount quand même")
    for attempt in range(1, 6):
        log(f"mount_webdav tentative {attempt}")
        try:
            subprocess.run(
                ["mount_webdav", _mount_url(), str(MNT)],
                capture_output=True, text=True, timeout=60,
            )
        except Exception as e:
            log(f"⚠ mount_webdav: {e}")
        try:
            mounts = subprocess.run(["mount"], capture_output=True, text=True).stdout
            if f"{HOST}:{PORT}" in mounts:
                log("volume monté")
                return True
        except Exception:
            pass
        time.sleep(3)
    log("⚠ mount absent après 5 tentatives — le serveur tourne quand même")
    return False


def serve_dav() -> None:
    """WsgiDAV embarqué ; en cas de port occupé, relance avec backoff (KeepAlive
    launchd garde le process, on ne casse pas le watcher en cas de conflit transitoire)."""
    config = {
        "provider_mapping": {"/": str(DAV_ROOT)},
        "host": HOST,
        "port": PORT,
        "verbose": 1,
    }
    if CFG.get("dav_user") and CFG.get("dav_password"):
        config["simple_dc"] = {"user_mapping": {"*": {
            CFG["dav_user"]: {"password": CFG["dav_password"]}
        }}}
    else:
        config["simple_dc"] = {"user_mapping": {"*": True}}  # anonymous — bac à sable seulement
    while True:
        try:
            app = WsgiDAVApp(config)
            server = wsgi.Server((HOST, PORT), app, numthreads=8)
            log(f"WebDAV en écoute sur {HOST}:{PORT} → {DAV_ROOT}")
            server.start()
            log("serveur WSGI terminé — relance dans 5 s")
        except Exception as e:
            log(f"serveur WSGI: {e} — relance dans 5 s")
        time.sleep(5)


def main() -> None:
    # NB : ne jamais toucher MNT ici (risque de block sur webdavfs zombie) —
    # ensure_mount gère le cycle unmount-force → stat/mkdir → mount en sécurité.
    for p in (DAV_ROOT / "inbox", DAV_ROOT / "outbox"):
        p.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=ensure_mount, daemon=True).start()
    ui = None
    if CFG.get("ui_bridge"):
        import metaai_ui_dispatch as uid
        ui_socket = Path(str(CFG["ui_socket"])).expanduser()
        ui_app = Path(str(CFG["ui_app"])).expanduser()
        # Lanceur : au boot, ping socket → lancement de la copie si muette
        threading.Thread(target=uid.ensure_app, args=(ui_socket, ui_app), daemon=True).start()
        ui = {"socket_path": str(ui_socket)}
        log(f"pont UI actif — socket={ui_socket}")
    else:
        log("pont UI désactivé (ui_bridge=false) — transport WebDAV seul")
    threading.Thread(
        target=bridge.watch_loop,
        args=(DAV_ROOT, float(CFG["poll_s"]), False, CFG.get("taskstore")),
        kwargs={"ui": ui},
        daemon=True,
    ).start()
    log("metaaid prêt (watcher + mount lancés)")
    serve_dav()


if __name__ == "__main__":
    main()
