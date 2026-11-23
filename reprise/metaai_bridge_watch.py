#!/usr/bin/env python3
# metaai_bridge_watch.py — adaptateur pont fichiers ↔ Reprise (prototype, 22 nov. 2026)
#
# Contrat de pont (validé en réel) :
#   inbox/  : tâches écrites par REPRISE en JSON (Meta AI lit sans problème)
#   outbox/ : résultats écrits par META AI au format clé/valeur, sans accolades ni
#             guillemets (le tool-call du modèle casse sur les contenus JSON) :
#               task_id: tache-0002
#               reponse: Montevideo
#               confiance: 1
#
# Le watcher consomme les deux sens et déplace les fichiers traités vers
# processed/inbox-<ts>/ et processed/outbox-<ts>/. Les hooks handle_task et
# handle_result sont les points de branchement get_task / submit_result du
# TaskStore SQLite de Reprise.
#
# Usage : python3 metaai_bridge_watch.py [--root ~/MetaAI-bridge-test/webdav] [--once]

import json, re, shutil, sys, time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from metaai_taskstore import TaskStore
except ImportError:
    TaskStore = None

_TASKSTORE = None

def set_taskstore(path):
    """Branche le TaskStore (cœur commun) ; None = hooks sans état (prototype)."""
    global _TASKSTORE
    if path and TaskStore is not None:
        _TASKSTORE = TaskStore(path)
        log(f"TaskStore branché: {path}")
    else:
        _TASKSTORE = None

ROOT = Path.home() / "MetaAI-bridge-test" / "webdav"
POLL_S = 2.0

def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def parse_kv(text: str) -> dict:
    """Parse le format clé/valeur contractuel de l'outbox (tolérant, sans JSON)."""
    d = {}
    for line in text.splitlines():
        m = re.match(r"\s*([A-Za-z_-]+)\s*:\s*(.+?)\s*$", line.rstrip("\r"))
        if m:
            d[m.group(1)] = m.group(2)
    return d

def handle_task_visible(task: dict, path: Path) -> None:
    """Observation seule : la tâche de inbox appartient à Meta AI jusqu'à l'arrivée
    de sa réponse. On n'archive PAS ici — la consommation se fait dans handle_result
    (roundtrip complet), comme get_task/submit_result."""
    log(f"tâche visible pour Meta AI: {task.get('task_id')} ({path.name})")

def handle_result(result: dict, raw: str, path: Path) -> None:
    """HOOK REPRISE : consommer le résultat Meta AI (équivalent submit_result).
    Prototype : log seul. Brancher ici la validation + écriture SQLite."""
    tid = result.get("task_id")
    if not tid:
        log(f"⚠ résultat sans task_id — laissé sur place: {path.name}")
        return False
    log(f"RÉSULTAT reçu: task_id={tid} reponse={result.get('reponse')} confiance={result.get('confiance')}")
    if _TASKSTORE is not None:
        try:
            status = _TASKSTORE.record_result(tid, result, raw, "metaai")
            log(f"TaskStore ← {tid} → {status}")
        except Exception as e:
            log(f"⚠ TaskStore indisponible: {e}")
    # consommation du roundtrip : archiver aussi la tâche source de inbox
    for cand in (ROOT / "inbox").glob(f"{tid}*.json"):
        archive(cand, "task")
    return True

def is_meta_file(p: Path) -> bool:
    """Ignore les fichiers AppleDouble et temporaires de webdavfs/macOS."""
    n = p.name
    return not (n.startswith("._") or n.startswith(".DS_Store") or n.startswith("~"))

def archive(path: Path, kind: str) -> None:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    moved_root = ROOT / "processed"
    moved_root.mkdir(parents=True, exist_ok=True)
    dest = moved_root / f"{kind}-{stamp}-{path.name}"
    shutil.move(str(path), str(dest))
    log(f"archivé → {dest.name}")

def consume_outbox(directory: Path) -> None:
    """L'outbox contractuel = fichiers .txt clé/valeur. Les .json présents sont des
    échecs d'écriture de Meta AI : signalés, jamais consommés ni archivés."""
    if not directory.is_dir():
        return
    for f in sorted(directory.iterdir()):
        if not f.is_file() or not is_meta_file(f):
            continue
        if f.suffix != ".txt":
            log(f"⚠ outbox non-contractuel ignoré: {f.name}")
            continue
        try:
            raw = f.read_text(encoding="utf-8", errors="replace")
            if handle_result(parse_kv(raw), raw, f):
                archive(f, "result")
        except Exception as e:
            log(f"⚠ erreur outbox {f.name}: {e}")

def consume_inbox(directory: Path) -> None:
    """Observation seule (cf. handle_task_visible)."""
    if not directory.is_dir():
        return
    for f in sorted(directory.iterdir()):
        if not f.is_file() or not is_meta_file(f):
            continue
        try:
            handle_task_visible(json.loads(f.read_text(encoding="utf-8")), f)
        except Exception as e:
            log(f"⚠ erreur inbox {f.name}: {e}")

def watch_loop(root: Path, poll_s: float = POLL_S, once: bool = False, taskstore_path=None,
               ui=None) -> None:
    """Boucle de pont exposée pour l'intégration (metaaid la lance en thread).

    ui=None : comportement historique (observation inbox seule). ui=dict
    (socket_path, template?) : dispatcher UI actif — chaque inbox non marquée
    est injectée + soumise dans la copie patchée par la socket de contrôle."""
    global ROOT
    ROOT = Path(root).expanduser()
    set_taskstore(taskstore_path)
    inbox, outbox = ROOT / "inbox", ROOT / "outbox"
    log(f"watcher démarré — root={ROOT}")
    while True:
        consume_inbox(inbox)
        consume_outbox(outbox)
        if ui:
            try:
                import metaai_ui_dispatch as uid
                uid.dispatch_pending(ROOT, Path(ui["socket_path"]),
                                     template=ui.get("template"))
            except Exception as e:
                log(f"⚠ dispatch UI: {e}")
        if once:
            return
        time.sleep(poll_s)

def main() -> None:
    args = sys.argv[1:]
    root = Path.home() / "MetaAI-bridge-test" / "webdav"
    if "--root" in args:
        root = Path(args[args.index("--root") + 1]).expanduser()
    watch_loop(root, POLL_S, "--once" in args)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("arrêt")
