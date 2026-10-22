---
name: ironclaw-tunnel
description: Diagnostiquer, soumettre des tâches et exposer un agent IronClaw/OpenClaw distant à travers une session SSH, par SSH direct, avec reçus durables et sans forwarding réseau direct.
---

# Test et exploitation d'un agent IronClaw distant

## Chemin actuel (23/10/2026)

Utiliser les commandes Reprise durables `ironclaw-submit`, `ironclaw-poll`, `ironclaw-reconcile`, `ironclaw-followup`, `ironclaw-result` et `ironclaw-requests` décrites dans `agent-resume/IRONCLAW-SSH.md`. Soumission uniquement après autorisation explicite ; conserver `request_key`. Une réconciliation ne fait que des GET. La configuration gérée référence les fichiers SSH/token existants, sans coffre obligatoire.

Le prototype MCP `ironclaw run -m` ci-dessous est historique et **désactivé dans le pont** : stdout n'est pas une preuve de résultat structuré, ni de restriction des outils distants. Ne pas le réactiver ni installer une autre configuration en contournement. Son raccordement à `Tasks`/`Endpoint` reste à réaliser. Les anciennes recommandations de coffre ci-dessous sont remplacées par le choix SSH direct ci-dessus.


Traite un agent distant de type **IronClaw Reborn** (`ironclaw serve --host 0.0.0.0 --port 3000`, CLI `ironclaw` v1.4+, runtime qui fait tourner un LLM). Le canal est **SSH en mode commande** : le forwarding `-L` vers l'UI est souvent *refusé* par le sshd distant ; on utilise alors le shell pour (a) soumettre des tâches via `ironclaw run -m` et (b) relayer l'UI par-dessus la session.

Cette page est la source maîtresse. La réplique installée dans `~/.dsh/skills/ironclaw-tunnel/` est écrasée à chaque `install.py` du dépôt.

## Paramètres d'un agent

| Élément | Défaut observé | Explicite |
|---|---|---|
| Hôte | `baremetal06.agents.near.ai` | `IRONCLAW_HOST` |
| Port SSH | `29403` (le 28797 d'un vieux script est refusé) | `IRONCLAW_PORT` |
| Utilisateur | `agent` | `IRONCLAW_USER` |
| Clé privée | `~/Ironclaw/agent-private-key.pem` | `IRONCLAW_KEY` |
| Empreinte de l'hôte | `SHA256:4H4N7730TrPNMZMTbhmNxGxU98Ys4aKnxKgOjvRL+3k` (ed25519) | vérifier par `ssh-keygen -lf` |
| Port UI distant | `127.0.0.1:3000` | `IRONCLAW_UI_PORT` |

**Ne jamais** lire une clé privée en entier ni l'envoyer en log : `ssh-keygen -lf` (empreinte) et `stat` (droits) suffisent. Le choix utilisateur est SSH direct : clé existante privée `0600`, sans copie ni obligation de coffre Reprise. Ne jamais modifier son chiffrement ou ses droits implicitement.

## Checklist de diagnostic (dans l'ordre)

1. **Réseau ET clé en un test unique** — le shell prouve les deux :
   ```sh
   ssh -o BatchMode=yes -o ConnectTimeout=12 -i "$IRONCLAW_KEY" \
     -p "$IRONCLAW_PORT" "$IRONCLAW_USER@$IRONCLAW_HOST" \
     'echo SHELL_OK; hostname; whoami'
   ```
   `Permission denied (publickey)` = clé non autorisée (ou mauvaise clé). `Connection refused` = mauvais port/hôte. Ne pas insister sur un port refusé ; chercher le port actif via les notes locales (`~/Ironclaw/tunnel-webui.sh`, `known_hosts`).
2. **Service d'UI** :
   ```sh
   ssh … "$IRONCLAW_HOST" 'curl -s -o /dev/null -w "%{http_code}\n" --max-time 5 http://127.0.0.1:3000/'
   ```
   `200` = UI up (le `serve` écoute souvent sur 0.0.0.0). Fichier `/data/ironclaw-reborn/local-dev/reborn-local-dev.db` = base de l'agent (schéma `root_filesystem_*`, `trigger_*`).
3. **Statut du runtime** : `ironclaw status`, `ironclaw models list|status`.
4. **Soumission réelle** (mesure latence + modèle distant) :
   ```sh
   ssh … "$IRONCLAW_HOST" 'ironclaw run -m "Réponds juste PING" 2>&1 | tail -3'
   ```
   Attendu : réponse du modèle en 3-5 s. C'est **le** canal de tâche exploitable ; il consomme le modèle configuré côté distant (`providers.json`, `model_slots: default, mission`).

## Canal de tâche recommandé

- **Aller-retour vérifiable** : `ssh … 'ironclaw run -m "<prompt>"'` → réponse sur stdout. Borner le prompt (pas de tuyaux interactifs), `tail` pour couper les logs INFO du runtime.
- **Health déterministe** (par ordre de coût) : `curl` local sur l'UI (200) puis `ironclaw status`, `ironclaw service status` (unité OS si installée), `ironclaw models status` (route du modèle).
- **Modèle du canal** : `ironclaw models status` expose `default.provider` (observé : `morph`) et `default.model` (observé : `morph-dsv41flash-1017`) → l'agent distant tourne déjà sur Morph/DeepSeek V4.1 Flash ; inutile d'aligner un modèle local, le `run -m` consomme celui-là.
- **Pas d'inference publique** : l'UI est un WebChat avec token ; n'invente aucun endpoint `/api`. Ne pas confondre la base SQLite du runtime (index FS/triggers) avec une API de tâche.

## Relais de l'UI sans `-L` (quand le forwarding est refusé)

Si `ssh -L 127.0.0.1:39999:127.0.0.1:3000` échoue avec `administratively prohibited: open failed`, SANS reconfiguration SSH (config root illisible), utiliser un **relais par session SSH** : un processus local accepte une connexion TCP, ouvre la session SSH en mode commande, et un mini-relais distant (`exec 4<>/dev/tcp/127.0.0.1/3000; cat >&4 &; exec cat <&4`) relie l'UI à stdin/stdout. Préférer un daemon dédié (section suivante) plutôt qu'un script jetable ; borner à **une connexion à la fois** et avec timeout (ex. 8 s) pour ne pas bloquer.

## Architecture de supervision (cible)

Un daemon local à la `codexd`/`claude-desktopd` — exploiter le modèle existant (synthèse de conception croisée, pas encore implémentée) :
- **Deux transports distincts** (pas de multiplexage stdin/stdout partagé) : une session SSH persistante dédiée au relais UI (mini-relais `exec 4<>/dev/tcp/.../3000; cat >&4 &; exec cat <&4`, une connexion à la fois, timeout 8 s), et les tâches `ironclaw run -m` en sessions courtes bornées.
- `agent-resume/engines.py` : ajouter un moteur `ironclaw` (exécutable = `ssh` ; transport = session SSH + socket local de contrôle Unix 0600 type `claude_desktop_quick.py`), mêmes règles `socket_path`/`select`.
- `agent-resume/codex_expert_mcp.py` : nouveau backend `ironclaw` dans `reprise_backends` ; brancher sous le même compteur `expert_calls` (réservation avant dispatch, 8/jour, `budget_blocked`, aucun fallback ni double décompte). Statuts partagés : `engine_unavailable`, `timed_out`, `needs_review`.
- JSON-RPC minimal : `initialize`, `ping`, `status`, `submit`, `proxy`, `health`. SQLite : `ironclaw_tasks` (hash du prompt, jamais le prompt brut) + `ironclaw_events`.
- États : `stopped / starting / running / stale / failed`, vérifiés par `connect()` (orphelin = `stopped`). Reconnexion bornée (10-60 s, 3 échecs max) ; erreurs permanentes (Permission denied, empreinte inconnue) non retentées.
- Health ladder déterministe : shell + `ironclaw status` + curl 200 (lecture seule) ; `ironclaw models status` (dispo de slot) ; canary `run -m "PING"` borné (<=1/h).
- Ordre d'implémentation conseillé : d'abord la preuve de bornage bout en bout (PING->PING puis budget_blocked au 9e appel, sans fallback), ensuite le relais mono-connexion (partie la plus risquée, blocage possible).
## État de l'implémentation (lot 20/10/2026)

Le canal de tâche est désormais branché dans le pont MCP (pas seulement documenté) :
- `agent-resume/ironclaw.py` : adaptateur `submit`/`review`/`health` — SSH BatchMode, prompt transmis en base64 (aucun échappement), réponse relue ; clé en octets matérialisée en fichier 0600 puis supprimée ; statuts partagés `engine_unavailable/timed_out/blocked_permission/needs_review`. Testé (13 cas).
- `agent-resume/codex_expert_mcp.py` : backend `ironclaw` déclaré dans `reprise_backends`, accepté par `reprise_delegate`, **sous le même compteur de budget** (réservation avant dispatch, `budget_blocked`, aucun fallback) ; `reprise_followup` backend ironclaw refusé (run -m sans état) ; nouveau flag CLI `--ironclaw-config '<JSON host,port,user,key>'`. Testé (5 cas via Bridge).
- `agent-resume/Vault.swift` : purpose `ironclaw-key-v1` ajouté à la whitelist (source) — **recompilation requise** (`sh native-install`) pour que `vault.save(..., purpose='ironclaw-key-v1')` fonctionne.

Vérification bout-en-bout du canal MCP réel (20/10/2026) : pont lancé avec `--ironclaw-config` → `reprise_backends` déclare ironclaw `configured: true` et `reprise_delegate backend=ironclaw` retourne `status: finished` avec la réponse du modèle distant (`PONG`), sous le compteur de budget (`expert.sqlite`).

Restant (suite) : daemon `ironclawd` persistant avec relais UI mono-connexion (schéma ci-dessous), recompilation de `Vault`, accroche d'une config 0600 réelle (`agent-resume/ironclaw-config.example.json`).
## Sécurité

- SSH direct avec la clé existante privée `0600` ; aucun coffre obligatoire, aucune copie de credential.
- Utiliser `ironclaw_ssh.py` : hôte déjà approuvé requis, agent et forwarding désactivés, configuration SSH utilisateur non héritée.
- Aucun changement de fournisseur ni repli payant ; disponibilité fournisseur distincte de ses crédits restants.
- Les anciennes commandes `ironclaw run -m` sont du diagnostic historique, pas le contrat d'exécution Reprise.

## Enrichir cette page

Quand un nouvel état, une nouvelle commande ou une nouvelle borne est observée (reconnexion, sémantique de `ironclaw service`, quota, coût), mettre à jour **ici** puis augmenter la version dans le frontmatter et committer sur `dev`. L'installation dans `~/.dsh` reste une action utilisateur (`install.py`) hors sandbox, avec sauvegarde automatique.

## Vérifications rapides validées (état au 20/10/2026 sur l'instance brave-wolf-fovaz)

- Shell distant : OK (`ironclaw@4e61ea374797`, Linux 6.8, ~11 To libres).
- UI locale : HTTP 200 sur 127.0.0.1:3000.
- `ironclaw run -m "PING"` → `PING` en ~4 s.
- Forwarding `-L` : refusé (`administratively prohibited`) → recours relais SSH.
- Clé active : `~/Ironclaw/agent-private-key.pem`, empreinte `4H4N7730…`.