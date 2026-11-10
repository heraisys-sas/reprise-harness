# Lot #130 — Architecture relais : tout passe par claude-desktopd

## Arbitrage final opérateur (10/11/2026 18h)
`claude_desktopd` (claude_desktop_quick.py) = **unique détenteur** de `~/.claude-desktop/quick-entry.sock`.
L'injection ReClaude (desktop_patch.py) devient **client/relais** du daemon : connect au socket, annonce
relais, applique `QTI` natif quand le daemon pousse un payload. L'option `reclaude.sock` (2 sockets) et la
« fusion » détenue par l'injection (net.createServer + unlinkSync, protocol reprise-dispatch-1) sont
**abandonnées**.

Tracé sur l'issue : issuecomment-6773625362 (architecture finale), -6773487674 (socket fichier conservé),
-6773222890 (observations terrain lsof), -6773512615 (reclaude.sock, remplacé). Problème « appli liée » =
Claude.app hardcodé (desktop_mode.py:22) posé sur #125 (issuecomment-6773232485).

## À faire (état au 10/11 18h)
- [ ] Spécifier le protocole relais daemon↔injection (relay/register, push payload sur connexion tenue ouverte
      ou relay/poll) AVANT le code.
- [ ] Réécrire desktop_patch.py : INJECTION = connecteur client + QTI ; supprimer serveur/unlinkSync/protocol.
- [ ] Côté daemon : détection relais, marquage « relais indisponible » → admission_only (déjà prévu),
      reconnexion si le daemon redémarre.
- [ ] Clients inchangés (quick-entry.sock) ; run_quick retrouve resume_validation_supported:true.
- [ ] réaligner docs/contrats (QUICK-ENTRY-CONTRACT.md, CLAUDE-CONNECTOR.md, README, AGENTS, sentinelles)
      sur « un seul socket, détenu par le daemon » ; supprimer le récit de fusion/injection-serveur.
- [ ] Traiter le worktree detached : rattacher à codex/130-desktop-patch-hardening avant commit.
- [ ] Sous-agent correcteur repo-wide (fragments périmés) — lancer après le réalignement doc/code.
- [ ] Daemon orphelin 22853 (release 1bd85615, socket unlinké) : arrêt ou resync — arbitrage opérateur.

## Lot B — protocole relais (spécification, écrite avant implémentation)

Le daemon `claude-desktopd` (`claude_desktop_quick.py`) est l'unique détenteur du socket
`~/.claude-desktop/quick-entry.sock`. L'injection ReClaude (`desktop_patch.py::INJECTION`,
miroir `reprise/tools/reprise_qe_inject.js`) devient un **client relais** : une connexion
UNIX tenue ouverte sur ce socket, sur laquelle le daemon pousse les payloads Quick Apply.
Les clients JSON-RPC existants (`claude_desktop_rpc.py`, bridge MCP, `claude_quota.py`)
ne changent pas : leurs connexions restent une-requête-par-connexion.

### Messages (JSON, une ligne = un message, `\\n` délimiteur, plafond 1 Mio/ligne)

1. **Greeting daemon → tout client** (inchangé) : `{"hello":true}`.
2. **Enregistrement relais (relais → daemon, première ligne)** :
   `{"hello":true,"role":"relay","protocol":"reprise-relay-1","version":1}`.
   Sans `role:"relay"`, la connexion reste une connexion RPC standard (comportement
   actuel). Un second enregistrement relais remplace le précédent (dernier connecté
   gagne) ; l'ancienne connexion est fermée par le daemon.
3. **Push payload (daemon → relais, sur la connexion tenue ouverte)** :
   `{"jsonrpc":"2.0","method":"relay/dispatch","params":{"request_key":"…","prompt":"…",
   "chatId":"…"}}` (params = payload déjà admis via `quick_entry/submit` ; la garde
   `TRIGGER_MIN_CHARS=3` est réappliquée côté relais avant l'appel natif `TYr`).
4. **Résultat (relais → daemon, même connexion)** :
   `{"jsonrpc":"2.0","id":<id du push>,"result":{"dispatch_attempted":true,
   "request_key":"…"}}` ou `{"error":{"code":-32602,"message":"…"}}`.
   Le `id` renvoyé DOIT être celui du push. Le daemon n'attend pas bloquant : un
   relais mort avant réponse laisse l'admission durable comme résultat
   (`admission_only`), conformément au contrat existant.
5. **Ping/heartbeat (relais → daemon)** : `{"jsonrpc":"2.0","id":N,"method":"ping"}`
   → `{"jsonrpc":"2.0","id":N,"result":{}}`. Le relais ping toutes les 30 s ; deux
   pings sans réponse ou une écriture en erreur ⇒ reconnexion avec backoff (1 s,
   puis ×2, plafond 15 s). Le daemon considère le relais déconnecté dès qu'il
   détecte fermeture/erreur de socket (pas de keepalive actif côté daemon).

### Versionnage

`protocol` (valeur `reprise-relay-1`) est vérifié par le daemon ; un relais avec un
autre protocole reçoit `{"hello":true,"role":"relay","refused":true}` puis déconnexion,
et retente avec backoff — jamais un silence. Évolutions : champ optionnel côté relais
toléré ; changement incompatible ⇒ nouvelle valeur `protocol`.

### Capacités et retombées

- `quick_entry/capabilities` : `resume_validation_supported` devient dynamique —
  `True` si et seulement si un relais est enregistré, sinon `False` (motif dans
  `reason`). `run_quick` (`claude_quota.py`) n'est pas modifié : il voit la capacité
  passer à `True` via le daemon et ne soumet qu'alors.
- Reconnexion relais : relance automatique côté relais (backoff ci-dessus) ; côté
  daemon, détection passive — « relais indisponible » retombe sur l'admission seule
  existante (`admission_only: true`).
- ReClaude fermée : le socket reste détenu par le daemon ; les soumissions restent
  admises durablement, `resume_validation_supported` repasse à `False`.

### Retiré de l'injection / conservé

Retiré du JS injecté : `net.createServer`, `unlinkSync`, écoute de socket, ancien
protocole `reprise-dispatch-1`, déduplication `__seen`, regex `request_key`, greeting
côté « service ». Conservé : la garde d'envoi Quick Apply/ProseMirror (clic bouton +
vérification d'envoi via `TYr`) et `TRIGGER_MIN_CHARS = 3`.

## Notes
- Le garde QTi/ProseMirror reste côté injecté ; MV3 (#107) = piste distincte si CDP/QTI échoue encore.
- Suite complète 1665 OK (skipped=5) sur le worktree ; Lot A non committé.
