# Intégration Meta AI — pont fichiers (dossier partagé), TaskStore partagé, daemon metaaid

Validée live le **23 novembre 2026** (`tache-20261123-124609-84bf` : 17 × 23 = 391, `confiance=1`, `source=metaai`). Répondre en français ; identifiants techniques en anglais (convention [AGENTS.md](../../AGENTS.md)).

> **Bascule du 24/11/2026 — transport direct par dossier.** Meta AI partage désormais le dossier du pont **directement** (TCC connecteur fichiers) : le serveur WebDAV embarqué (wsgidav/cheroot, port 9770) et le mount webdavfs ont été **retirés** de metaaid — le seul client réel du serveur était le volume monté, et le partage direct du dossier rend les deux inutiles (plus de webdavfs : zombies de mount, creds d'URL, AppleDouble). Le watcher scrute le dossier en poll local, latence inchangée. L'historique WebDAV est conservé ci-dessous (leçons § webdavfs) pour référence.

## Ce qu'est Meta AI Desktop

`/Applications/Meta AI.app` = app **native** SwiftUI/AppKit (`com.meta.endo.vanilla`, team V9WTTPBFK9, v14.0) : pas d'Electron → **pas de CDP** (la recette ChatGPT #156 inapplicable) ; route AX écartée (décision opérateur) ; historique serveur-side (capture obligatoirement par UI) ; tokens en Keychain (`V9WTTPBFK9.com.meta.endo`) — ne pas extraire. Le seul canal d'entrée/sortie exploitable **sans Accessibilité ni CDP** est le **connecteur fichiers natif** (`connectorAppleFinder`) : actions `list, read, write, mkdir, move, copy, trash` sur des dossiers partagés, remplacement de fichier et Trash toujours confirmés, rien supprimé définitivement, grants révoqués au logout.

## Architecture

```
TaskStore SQLite (reprise/metaai_taskstore.py — seul état, WAL)
├── pont MCP (reprise/metaai_mcp_server.py, stdio JSON-RPC newline, zéro dépendance)
│     enqueue_task · dispatch_task (→ inbox) · get_task (sans inbox) ·
│     submit_result · get_status · list_tasks
└── daemon metaaid (reprise/metaaid.py, LaunchAgent app.reprise.metaaid)
      watcher (reprise/metaai_bridge_watch.py) sur le dossier du pont
      → Meta AI via son connecteur fichiers (dossier partagé en TCC)
```

Cycle : `pending → dispatched → answered` (+ `answered_orphan`). Aller : `dispatch_task` écrit `inbox/<id>.json` (JSON, Meta AI le lit sans problème). Retour : Meta AI écrit `outbox/<id>.txt` en **clé/valeur sans JSON** — le watcher consomme, `record_result` (source `metaai`), et archive le roundtrip (`processed/`).

## Contrat de pont (issues réelles, ne pas re-découvrir)

- **Bug tool-call Meta AI** : tout contenu JSON dans le paramètre `contents` du connecteur d'écriture est rejeté (« A write needs a `contents` string holding the COMPLETE new text of the file. Nothing was written. ») — extension innocente, c'est le contenu. L'outbox est donc en **clé/valeur** (`task_id:` / `reponse:` / `confiance:` par ligne), parseur tolérant dans `parse_kv`.
- **`get_task` ≠ `dispatch_task`** : `get_task` réserve une tâche pour l'agent MCP appelant lui-même **sans** écriture inbox (sinon Meta AI traiterait en double) ; `dispatch_task` matérialise l'inbox pour Meta AI.
- **webdavfs (historique, serveur retiré le 24/11)** : `/Volumes` est root-only → point de montage sous home ; **ne jamais stat-er un mount zombie** (serveur mort = tout accès au chemin bloque au timeout FS → crash loop) ; le mount CLI ne transmet pas les creds d'URL (exit 22 sur serveur auth-required) ; fichiers `._*` AppleDouble dans les listings = bruit à ignorer. Ces leçons ne s'appliquent plus au pont (partage direct du dossier), elles restent valables pour tout futur montage webdavfs (inspections manuelles, curl).
- **metaaid** : LaunchAgent utilisateur (grants TCC appartiennent à la session) ; `RunAtLoad`+`KeepAlive` (différence assumée avec quick-entry : pont long-durée) ; config = **connexion Reprise** `~/Library/Application Support/Reprise/connections/metaai.json` (convention JSON plat) — champs `bridge_root` (dossier du pont), `poll_s`, `taskstore`, `ui_bridge`/`ui_socket`/`ui_app` ; les anciennes clés `dav_*`/`mount_point` sont ignorées avec un avertissement au log ; Reprise écrit le config puis `launchctl kickstart -k gui/$(id -u) app.reprise.metaaid`.
- **Déclencheur du tour** : automatisé par le pont UI (cf. METAI-QUICKINVOKE.md) ; sinon manuel ou « Programmé » natif de l'app (aucun push possible sans AX ni CDP).
- **Share/grant** : lié au chemin partagé — changer `bridge_root` impose un re-partage du nouveau dossier dans l'app.

## Installation (opérateur)

```bash
python3 reprise/metaai-install.py            # déploye modules + connexion (si absente) + plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/app.reprise.metaaid.plist
```

Le plist pointe **ce checkout** (`reprise/metaaid.py`) et des chemins sous le domicile uniquement ; écriture atomique chmod 600 ; refuse l'écrasement sans `--force`. Étape manuelle : partager le dossier `bridge_root` au connecteur fichiers de Meta AI (Paramètres → Applications). Désinstallation : `launchctl bootout gui/$(id -u) app.reprise.metaaid` + suppression plist/modules.

## Tests

`python3 -m unittest discover -s reprise/tests -q -p "test_metaai_*.py"` — 11 tests (TaskStore : ordre de claim, écriture inbox atomique, answered/orphan/fail ; serveur MCP réel en subprocess : initialize/tools, roundtrip dispatch+submit, `get_task` sans écriture inbox, dispatch sans pending). Référence live : cycle complet `enqueue → dispatch → Meta AI réel → watcher → get_status` du 23/11/2026.
