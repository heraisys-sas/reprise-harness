# Transport `apply` (CDP) et quota lu dans le journal

Deux modules indépendants, sans dépendance nouvelle :

- `desktop_apply.py` — envoi d'un prompt dans une conversation claude.ai, sans
  permission macOS ;
- `claude_quota_log.py` — échéance de quota lue dans le journal de Claude
  Desktop, sans écran ni permission.

Aucune modification du démon n'est incluse : voir §5, c'est une décision à
trancher.

## 1. Pourquoi ce transport

`claude_desktop_quick.py` valide un payload puis s'arrête : « actually submitting
through Quick Entry's native overlay requires separate operator-granted macOS
permissions and is intentionally left to the supervised driver ». Ce driver ne
peut pas exister côté Desktop :

| Voie | Verdict |
|---|---|
| CDP sur Desktop (`--remote-debugging-port`) | refus au lancement ; jeton `CLAUDE_CDP_AUTH` signé Ed25519, lié au `userDataPath`, TTL 300 s |
| Overlay natif Quick Entry | exige l'Accessibilité macOS |
| claude.ai dans un Chrome contrôlé, via CDP | aucune permission, aucune signature |

On ne pilote donc pas la coquille : on pilote la même application web, dans un
navigateur lancé pour l'occasion.

## 2. `desktop_apply.py`

Client CDP autonome (WebSocket minimal inclus, stdlib uniquement — le démon n'a
pas Node).

```python
from desktop_apply import apply, send_staged, quota

apply(prompt, chat_id)   # pose + envoie
send_staged()            # envoie ce qui est déjà posé, sans retaper
quota()                  # {"blocked", "sendDisabled", "hasText", "resetAt"}
```

Contrat de retour : `{ok, stage, staged, detail}`.

| `stage` | `ok` | `staged` | Sens |
|---|---|---|---|
| `envoyé` | ✓ | ✗ | clic d'envoi effectué |
| `posé` | ✓ | ✓ | texte en place, envoi non demandé |
| `quota` | ✗ | ✓ | bouton désactivé, texte en place — **admission différée, pas un échec** |
| `composeur` | ✗ | ✗ | page non prête |
| `frappe` | ✗ | ✗ | texte absent après insertion |
| `connexion` | ✗ | ✗ | Chrome de diagnostic absent |
| `vide` | ✗ | ✗ | rien à envoyer |

## 3. `claude_quota_log.py`

`claude_quota.py` s'appuie sur le bandeau « En pause : … disponible 27 oct. à
14:40 ». La formulation actuelle (« Réinitialisation : 14:40 ») ne correspond
plus : `parse_pause` renvoie `None`. Le journal, lui, donne l'échéance exacte :

```
[error] [COMPLETION] Request failed {"type":"exceeded_limit","resetsAt":1793065800,…}
```

```python
from claude_quota_log import latest, corroborates, observe

latest()        # {"type", "resets_at", "observed_at", "expired", "blocked", "evidence"}
corroborates(resets_at, banner_text, zone)   # le bandeau confirme-t-il l'heure ?
observe(zone=…)  # vue unifiée, source = "journal" si l'échéance est certaine
```

Deux règles conservées du code existant :

- un événement dont `resetsAt` est passé **n'est pas** un blocage (`expired`) ;
- aucune heure n'est déduite d'un « HH:MM » sans date — l'ambiguïté
  aujourd'hui/demain reste refusée.

## 4. Deux angles morts à couvrir ensemble

| Source | Voit | Ne voit pas |
|---|---|---|
| journal (`exceeded_limit`) | rejets **serveur**, datés | blocage **client** (aucune requête émise) |
| page (`sendDisabled` + texte) | blocage client, heure affichée | au-delà de l'instantané |

Un blocage client ne produit aucune ligne de journal : `blocked` doit être la
conjonction des deux, jamais le journal seul.

## 5. Point d'insertion (non appliqué)

Dans le gestionnaire de socket de `claude_desktop_quick.py`, là où le payload est
aujourd'hui accepté puis laissé en attente :

```python
result = apply(payload["prompt"], payload.get("chatId"))
if result["stage"] == "quota":
    store.mark_staged(payload["request_key"], result["detail"]["resetAt"])
elif result["ok"]:
    store.mark_sent(payload["request_key"], result["detail"]["url"])
```

`request_key` reste la clé d'idempotence du démon : `apply` ne la connaît pas et
ne rejoue rien de lui-même.

Non inclus volontairement : le câblage modifie le comportement du démon, et
`register()` exige un `evidence_sha256` dont la source doit être arbitrée
(ligne de journal ou bandeau corroborant).

## 6. Exploitation

```bash
# un Chrome dédié, avec le port de diagnostic et un profil persistant :
google-chrome --remote-debugging-port=9333 --user-data-dir=~/.local/share/claude-cdp/chrome
# connexion claude.ai une fois dans ce profil, puis :
python agent-resume/desktop_apply.py --quota
```

Le port n'écoute que sur `127.0.0.1`, mais tout processus local peut piloter ce
profil tant qu'il est ouvert : prévoir une fermeture explicite.

## 7. Limites

- dépend de la mise en page de claude.ai (`chat-input-send`, `action-bar-retry`) :
  à revalider après refonte ;
- c'est l'application web, pas la coquille Desktop : pas de projets locaux ni de
  MCP Desktop dans cette session ;
- pas de retour de réponse : le canal est d'émission. Le retour reste
  `submit_result` côté MCP.

## 8. État de preuve

- `desktop_apply.py` : testé en **lecture seule** (handshake, frames, évaluation,
  lecture de quota). Le chemin d'envoi a été prouvé avec le pilote Node
  équivalent — conversation créée, puis réutilisée par `chatId` — pas encore avec
  ce portage Python.
- `claude_quota_log.py` : vérifié sur le journal réel et couvert par 10 tests.
