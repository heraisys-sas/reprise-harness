#!/usr/bin/env python3
# metaaid.py — service LaunchAgent unique du pont Meta AI ↔ Reprise (24 nov. 2026)
#
# Un seul process :
#   1. watcher du pont (thread, sémantique get_task/submit_result de metaai_bridge_watch)
#   2. pont UI (socket de ReMetaAI + dispatcher, cf. metaai_ui_dispatch)
#
# Transport : dossier plat partagé au connecteur fichiers de Meta AI (TCC),
# surveillé en poll local. L'ancien serveur WebDAV embarqué (wsgidav/cheroot)
# et le mount webdavfs ont été retirés le 24/11 : le partage direct du dossier
# rend serveur et montage inutiles (et supprime toute la classe webdavfs :
# zombies de mount, creds d'URL, négociation 40-60 s, fichiers AppleDouble).
#
# Installé par l'installeur Reprise comme LaunchAgent utilisateur :
#   ~/Library/LaunchAgents/app.reprise.metaaid.plist → /usr/bin/python3 metaaid.py
#   RunAtLoad + KeepAlive(Crashed, SuccessfulExit=false) ; logs ~/Library/Logs/reprise/metaaid.log
# NB : launchd Agent (session user) et non daemon système — grants TCC
# appartiennent à l'utilisateur.

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metaai_bridge_watch as bridge  # noqa: E402

# Configuration de la connexion « metaai » — convention Reprise (JSON plat, cf.
# metaai-config.example.json) ; l'installeur/l'UI de Reprise écrit ce fichier,
# metaaid le lit au démarrage. Absent → défauts bac à sable.
CONFIG_PATH = Path.home() / "Library/Application Support/Reprise/connections/metaai.json"
CONFIG_DEFAULTS = {
    "enabled": True,
    "bridge_root": "~/Library/Application Support/Reprise/metaaid-bridge",
    "poll_s": 2.0,
    "taskstore": "~/Library/Application Support/Reprise/metaai-tasks.sqlite",
    "ui_bridge": False,
    "ui_socket": "~/.reprise/metaai-reprise-trigger.sock",
    "ui_app": "~/Applications/ReMetaAI.app/Contents/MacOS/Meta AI",
}
# Clés de l'ère WebDAV : ignorées, avec un mot dans le log pour la migration.
LEGACY_DAV_KEYS = ("dav_host", "dav_port", "dav_root", "mount_point",
                   "dav_user", "dav_password")


def log(msg: str) -> None:
    print(f"[metaaid {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_config() -> dict:
    cfg = dict(CONFIG_DEFAULTS)
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        legacy = [k for k in raw if k in LEGACY_DAV_KEYS]
        if legacy:
            # Refus net (#276) : jamais de repli sur les défauts avec une config WebDAV.
            raise SystemExit(f"metaaid : clés WebDAV supprimées à retirer de {CONFIG_PATH} : {', '.join(legacy)}")
        cfg.update({k: v for k, v in raw.items() if k in CONFIG_DEFAULTS and v is not None})
        log(f"config chargée: {CONFIG_PATH}")
    except FileNotFoundError:
        log("config absente — défauts bac à sable")
    except Exception as e:
        log(f"⚠ config illisible ({e}) — défauts")
    cfg["bridge_root"] = str(Path(cfg["bridge_root"]).expanduser())
    return cfg


CFG = load_config()
BRIDGE_ROOT = Path(CFG["bridge_root"])


def main() -> None:
    for p in (BRIDGE_ROOT / "inbox", BRIDGE_ROOT / "outbox"):
        p.mkdir(parents=True, exist_ok=True)
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
        log("pont UI désactivé (ui_bridge=false) — transport fichiers seul")
    threading.Thread(
        target=bridge.watch_loop,
        args=(BRIDGE_ROOT, float(CFG["poll_s"]), False, CFG.get("taskstore")),
        kwargs={"ui": ui},
        daemon=True,
    ).start()
    log(f"metaaid prêt (watcher, root={BRIDGE_ROOT})")
    while True:
        time.sleep(60)  # le thread principal reste vivant (KeepAlive launchd)


if __name__ == "__main__":
    main()
