# Architecture de Reprise — état actuel

Référence de l'architecture **livrée**, établie depuis le code de `dev` (`017a7f8`, 28/11/2026) pour #289. La cible multi-harness et ses réserves restent dans [HARNESS-SUPERVISION-ARCHITECTURE](HARNESS-SUPERVISION-ARCHITECTURE.md) ; ce document décrit ce qui existe, ses frontières et ses écarts connus. Il remplace l'ancienne critique d'architecture Astra (retirée de l'arbre le 04/11 ; recommandation A+B conservée ci-dessous).

## En une phrase

Reprise est une application macOS qui **observe** les sessions d'agents (Codex, Harness, Claude, ChatGPT, Meta AI…), **reprend** les travaux interrompus par quota et sert de **service de délégation durable** entre harness, sans inférence propre en arrière-plan ni repli payant implicite.

## Composants

| Composant | Rôle | Code principal | Point d'accès |
|---|---|---|---|
| Application | Tableau de bord, réglages, connexions, pilote Apple | `reprise/swift/` (`Native.swift`, `Dashboard.swift`, `Integrations.swift`, `ApplePilotUI.swift`) | `Reprise.app` |
| CLI | Opérations explicites (intégrations, état, mises à jour, Quick Apply) | `reprise/reprise` | `reprise <commande>` |
| Scheduler | Relance des interruptions quota Codex confirmées, une fois par événement | `scheduler.py`, `scheduler_service.py` | LaunchAgent `local.agent-resume` (`reprise tick`) |
| Pont MCP de délégation | Outils `reprise_*` pour les harness clients | `expert_mcp.py`, `expert_router.py`, `delegation_*.py` | stdio, entrée gérée `reprise-harness` |
| Connecteur Claude Desktop | Réception (`get_task`/`await_task`/`submit_result`) + délégation sortante | `claude_mcp.py`, `desktop_task_store.py` | stdio, entrée gérée de `~/Library/Application Support/Claude/claude_desktop_config.json` |
| `claude-desktopd` | Daemon Quick Apply : déclenche un tour Claude Desktop | `claude_desktop_quick.py`, `desktop_mode.py` | socket `~/.claude-desktop/quick-entry.sock` |
| `codexd` | Daemon Codex (`app-server daemon`) — **externe**, jamais redémarré par Reprise | `codex_rpc.py`, `codex_expert.py` (clients) | socket `~/.codex/app-server-control/app-server-control.sock` |
| Pont ChatGPT | Admission durable + holder CDP qui dispatch et capture la réponse | `chatgpt_quick.py`, `chatgpt_desktop_pipe.py` | état `chatgpt-state` |
| `metaaid` | Pont fichiers Meta AI (inbox/outbox JSON) et injection UI | `metaaid.py`, `metaai_taskstore.py`, `metaai_ui_dispatch.py`, `metaai_mcp_server.py` | LaunchAgent `app.reprise.metaaid`, `connections/metaai.json` |
| Experts locaux et distants | Foundation Models (local), Claude Code (CLI), IronClaw (SSH, payant, jamais automatique) | `apple_expert.py`, `claude_expert.py`, `ironclaw*.py` | appel explicite |
| Mémoire projet | Mémoire locale chiffrée, opt-in, sans inférence | `orchestrator_memory*.py`, `reprise_memory_mcp.py` | stdio `reprise-memory` |
| Registre projets | UUID de projet ↔ workspaces de harness | `project_registry.py` | CLI `reprise project-list` / `project-create` / `project-associate` |
| Mises à jour gérées | Bundle relocalisable, releases d'intégration, plan/apply/verify | `managed_update.py`, `package_helpers.py`, `harness_integration.py`, `claude_integration.py`, `codex_config_update.py` | `~/Library/Application Support/Reprise/integration-releases/<sha256>` |

## Délégation : le chemin d'un appel

```text
Harness client (Codex, DSH, Claude Code, Claude Desktop)
  → outil MCP reprise_delegate(request_key, question, kind[, backend, model, budgets])
  → expert_mcp.Bridge : validation, budgets admis (#279), portes manifeste,
    admission durable (delegation_store) AVANT tout envoi
  → backend : codex (codexd) | claude-code (CLI) | claude-desktop (Quick Apply + MCP)
              | chatgpt (holder CDP) | metaai (inbox fichiers) | foundation-models | ironclaw
  → reçu durable ; résultat lu par wait_result / reprise_requests / reprise_reconcile
```

Invariants (détail : [DELEGATION-PERSISTENCE](DELEGATION-PERSISTENCE.md)) :
- **Admission ≠ envoi ≠ résultat.** Aucune transaction ne couvre un appel externe ; un envoi incertain devient `indeterminate` et se réconcilie, il n'est jamais rejoué automatiquement.
- **`request_key` persistée par l'appelant** : la même clé rend le même reçu après redémarrage ; la lignée passe par `parent_request_key`.
- **Aucun repli implicite.** Le routeur (`expert_router`) ne substitue qu'entre routes `free`/`included` mesurées, jamais vers une route payante, jamais après un refus de manifeste ; aujourd'hui il ne trouve en pratique aucun candidat (#288).
- **Claude Desktop (recommandation Astra A+B, 20/10)** : Quick Apply déclenche, MCP `get_task`/`await_task` remet la demande, SQLite conserve ; seul `submit_result` validé vaut retour.
- **Backends asynchrones** (metaai, chatgpt) : l'admission rend la main ; la réponse se collecte par `wait_result` sur la même clé.

## Déclarations de backends

| Source | Ce qu'elle déclare |
|---|---|
| `reprise/experts-manifest.json` (obligatoire depuis #276) | backends activés, capacité, reprise de session, `default_chain`, `automatic_fallback` |
| `backend_registry.py` | facturation déclarée, modes, consentement, snapshot mémoire (coordinateur) |
| `expert_mcp.py` | handlers (`EXPERTS_HANDLED_BACKENDS`), budgets admis (`BACKEND_BUDGETS`, #279), capacité d'écriture |

Ces sources divergent (metaai et claude-desktop sans facturation déclarée ; asi1 sans handler) : unification proposée dans #292.

## État durable

Tout l'état vit sous `~/Library/Application Support/Reprise/` (jamais dans le dépôt) : `state/` (SQLite privés : reçus de délégation, tours actifs, journal de routage, quotas, tâches Desktop), `integration-releases/` (bundles immuables adressés par empreinte), `connections/` (configurations explicites, p. ex. `metaai.json` avec `bridge_root`), états propres à ChatGPT et Meta AI. Les registres chiffrés passent par `vault.py` (Trousseau `local.agent-resume.vault.v1`). Aucune donnée d'état n'est copiée dans un bundle (`package_helpers`).

## Frontières

- **Lecture et action séparées** : les observateurs (`detect.py`, `claude_session_observer.py`, `codex_thread_observer.py`, `quota_knowledge.py`) ne déclenchent jamais de commande.
- **Pas d'inférence de fond** : Reprise n'appelle un modèle que sur demande explicite (délégation, pilote Apple opt-in).
- **Daemons d'autrui** : `codexd` et Claude Desktop ne sont jamais tués ni redémarrés ; l'état d'un service se vérifie par `connect()` sur son socket.
- **Intégrations** : `inspect → plan → apply → verify → remove`, sauvegardes privées, aucune activation implicite du client ([INSTALLATION-CONTRACT](INSTALLATION-CONTRACT.md)).
- **Écriture déléguée** : `mode=implementation` (Codex) écrit dans le projet du pont, réseau coupé (#252) ; `expertise` reste en lecture seule.

## Modules livrés mais non branchés

`reprise_gh.py` (#270, correctif #291), `perimetre_guard.py` et `evidence_citations.py` (#268), `competence_{types,stats,rank}.py` (O1) existent avec leurs tests mais ne sont ni appelés, ni empaquetés, ni exposés (revue #290).

## Écarts connus

| Écart | Suivi |
|---|---|
| Substitution automatique sans candidat (prix externe exigé, facturation metaai inconnue) | #288 |
| Capture ChatGPT incomplète (écho du prompt) | #288, #280 |
| Registres de backends multiples | #292 |
| Identifiants hérités `local.agent-resume` | ROADMAP, #289 (lot séparé) |
| Matrice multi-harness et service unifié de réception | [HARNESS-SUPERVISION-ARCHITECTURE](HARNESS-SUPERVISION-ARCHITECTURE.md) |
