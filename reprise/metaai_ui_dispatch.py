#!/usr/bin/env python3
# metaai_ui_dispatch.py — dispatcher UI du pont Meta AI (invocation rapide)
#
# Après dispatch_task (inbox/<id>.json écrit sur le volume WebDAV), ce module
# pilote la copie patchée « Meta AI Reprise » par sa socket de contrôle
# (~/.reprise/metaai-reprise-trigger.sock, cf. metaai_reprise.m) :
#   insert(prompt) puis send() — zéro Accessibilité, zéro CDP.
#
# Idempotence : chaque inbox injectée est marquée par un fichier
# .ui-state/<id> sous la racine WebDAV (hors inbox — Meta AI ne voit que
# inbox/outbox) ; un redémarrage de metaaid ne ré-injecte pas.
#
# Lanceur : ensure_app() ping la socket et lance la copie si muette
# (lancement proposé par le build, exécuté ici par le daemon — la doctrine
# « jamais de démarrage silencieux » s'applique à l'INSTALLATEUR, pas au
# daemon déjà installé explicitement par l'opérateur).

import json
import subprocess
import time
from pathlib import Path

PROMPT_TEMPLATE = (
    "Traite la tâche du fichier inbox/{task_id}.json du dossier partagé : lis-le, "
    "calcule la réponse à la question posée, puis écris un fichier outbox/{task_id}.txt "
    "dans le dossier partagé, contenant exactement trois lignes clé/valeur (pas de JSON) : "
    "task_id: {task_id} — reponse: <ta réponse> — confiance: <0 à 1>."
)


class UiDispatchError(Exception):
    pass


def log(msg: str) -> None:
    from datetime import datetime
    print(f"[ui-dispatch {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def send_command(socket_path: Path, payload: dict, timeout_s: float = 10.0) -> dict:
    """Commande JSON newline vers la socket de contrôle de la copie."""
    import socket as _socket
    path = Path(socket_path)
    if not path.exists():
        raise UiDispatchError(f"socket absente: {path}")
    sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    sock.settimeout(timeout_s)
    try:
        sock.connect(str(path))
        sock.sendall((json.dumps(payload) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
    finally:
        sock.close()
    line = buf.decode(errors="replace").strip()
    if not line:
        raise UiDispatchError("réponse vide de la socket")
    return json.loads(line.splitlines()[0])


def ping(socket_path: Path, timeout_s: float = 5.0) -> bool:
    try:
        return bool(send_command(socket_path, {"cmd": "ping"}, timeout_s).get("ok"))
    except Exception:
        return False


def build_prompt(task_id: str, payload=None, template=None) -> str:
    tpl = template or PROMPT_TEMPLATE
    return tpl.format(task_id=task_id)


def inject_task(socket_path: Path, task_id: str, template=None) -> dict:
    """inject + send pour une tâche ; vérifie le vidage du composer (len=0)."""
    prompt = build_prompt(task_id, template=template)
    ins = send_command(socket_path, {"cmd": "insert", "text": prompt})
    if not ins.get("ok"):
        raise UiDispatchError(f"insert refusé: {ins}")
    snd = send_command(socket_path, {"cmd": "send"})
    if not snd.get("ok"):
        raise UiDispatchError(f"send refusé: {snd}")
    time.sleep(0.6)
    st = send_command(socket_path, {"cmd": "len"})
    cleared = st.get("len") == 0
    return {"inserted": True, "sent": True, "composer_cleared": cleared}


def ensure_app(socket_path: Path, app_binary: Path, wait_s: float = 15.0) -> bool:
    """Lanceur : si la socket ne répond pas, lancer la copie et attendre la socket."""
    if ping(socket_path, timeout_s=3.0):
        return True
    binary = Path(app_binary).expanduser()
    if not binary.exists():
        log(f"⚠ copie absente ({binary}) — lancer metaai_reprise_build.py")
        return False
    log("socket muette — lancement de la copie Meta AI Reprise")
    subprocess.Popen([str(binary)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    deadline = time.time() + wait_s
    while time.time() < deadline:
        time.sleep(1.0)
        if ping(socket_path, timeout_s=2.0):
            log("copie lancée — socket répondante")
            return True
    log("⚠ socket toujours muette après lancement")
    return False


def dispatch_pending(dav_root: Path, socket_path: Path, template=None) -> int:
    """Injecte toutes les inbox non marquées ; pose le marker .ui-state/<id>."""
    root = Path(dav_root)
    inbox = root / "inbox"
    state = root / ".ui-state"
    if not inbox.is_dir():
        return 0
    state.mkdir(parents=True, exist_ok=True)
    count = 0
    for f in sorted(inbox.glob("*.json")):
        if f.name.startswith("._") or f.name.startswith("~"):
            continue
        marker = state / f.name
        if marker.exists():
            continue
        try:
            task = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            log(f"⚠ inbox illisible {f.name}: {e}")
            continue
        task_id = task.get("task_id") or f.stem
        try:
            res = inject_task(socket_path, task_id, template=template)
            marker.write_text(json.dumps(res), encoding="utf-8")
            count += 1
            log(f"prompt UI injecté pour {task_id} (composer vidé: {res.get('composer_cleared')})")
        except Exception as e:
            log(f"⚠ injection UI impossible pour {task_id}: {e}")
            break  # socket muette : inutile d'insister sur les suivantes ce cycle
    return count
