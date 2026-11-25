# Reprise — suites convenues

Le reste à faire vit ici, en quatre sections ; chaque item pointe vers son issue ou son contrat.
L'historique détaillé des lots datés (21/10 → 04/11) reste consultable dans Git et le [CHANGELOG](CHANGELOG.md).
L'audit d'écart courant : [#119](https://github.com/heraisys-sas/reprise-harness/issues/119).

## Livré

- **Aller-retour Claude Desktop gratuit** : Quick Apply → `get_task` → `submit_result`, `await_task` borné, boîte de réception des résultats (0.1.17/0.1.18) — [contrat](docs/contracts/DELEGATION-PERSISTENCE.md).
- **Reprise Codex après quota à l'échéance**, sessions goal incluses (#109) **dans le périmètre du moteur actuellement admissible** ; délégation Codex avec reçus durables — [rôles](docs/contracts/HARNESS-ROLES-CONTRACT.md). Cela ne garantit pas les threads du Desktop privé stdio ni les autres harness.
- **Clones d'app à identité propre et version gelée** : ReChatGPT.app / ReClaude.app gérés par la même logique inspect/plan/apply/verify (contrat [APP-CLONES-CONTRACT](docs/contracts/APP-CLONES-CONTRACT.md), runbook [APP-CLONES](docs/runbooks/APP-CLONES.md)) ; le holder dédié démarre sur la copie quand elle existe.
- **Route ChatGPT de bout en bout** : admission durable → dispatch holder CDP (Entrée, profil dédié, reçus bornés) → capture de réponse (WS `conversation-turn-complete` + MutationObserver + lecture rendue ancrée + sentinel) → résultat livré au délégant — preuve live t1 (17/11/2026), dispatch opt-in `--chatgpt-dispatch` ; issues #127/#152/#156/#226 clôturées.
- **Installation/maintenance** `inspect/plan/apply/verify/remove` des composants Reprise (app, scheduler, profils Harness, connecteur), déploiements réels 0.1.13→0.1.18 — [contrat](docs/contracts/INSTALLATION-CONTRACT.md).
- Registre projets (UUID ↔ workspaces), mémoire scoping par projet/work, checkpoints chiffrés.
- Connecteur Claude Desktop unifié, Quick Apply CDP direct (applyd supprimé), barrières de dispatch séparées.
- Restructuration du dépôt (`reprise/`, `docs/`, `tools/`, `harness/`, `routing/`) + charte [USAGE](USAGE.md).
- Suppression des plafonds quotidiens locaux, tous backends — [CACHE-POLICY](docs/contracts/CACHE-POLICY.md).
- **Route ChatGPT Desktop fermée de bout en bout** : dispatch CDP pipe par un holder à profil dédié (Entrée primaire, receipts durables at-most-once) et chaînon admission → dispatch opt-in (`--chatgpt-dispatch`, #226) ; capture de réponse v1.1 par le holder (complétude WS `conversation-turn-complete`, fenêtre calme MutationObserver, marqueur rendu, sentinel) ; validée live le 17/11/2026 (`t1-live-1794938260` : PONG capturé, `delivery=captured`) — [intégration](docs/integrations/codex/CODEX-INTEGRATION.md).

## En cours

- **Reprise après quota de tout travail interrompu** : reconnaître le signal sur le bon Desktop, conserver la session, tenter la continuation native à l'échéance sans imposer de nouvelle mesure de quota ; #10, [contrat](docs/contracts/QUOTA-CONTINUATION-CONTRACT.md), [tests locaux](docs/runbooks/QUOTA-LOCAL-ACCEPTANCE.md). La généralisation reste **non livrée**.
- **Codex stdio** : index passif #170 intégré (un seul CODEX_HOME sur le Mac testé), écriture par propriétaire non prouvée ; mode partagé socket retiré sur dev par #172 (CLI/RPC Codex indépendant préservé), `codexd` inchangé. `chatgptd` reste une cible, pas un service déjà livré.
- **Pont ChatGPT Desktop** : release et redéploiement des connecteurs gérés (DeepSeek, Claude, ChatGPT) après fermeture de la route ; preuve de coexistence avec l'application opérateur — `operator_isolation_verified` reste `false`.

- Unification des surfaces MCP : mémoire partagée par projet livrée ; attente bornée des résultats Claude (#111, branche) ; réunion des délégations restante.
- Session goal « Siri 27.2 » : pilot Apple Intelligence, clarification locale, guided generation (#111 et voisins).
- Révision de spec : audit d'écart, index des contrats, ROADMAP restructurée (#119, cette branche).
- Interface : contrat #118 (défauts appliqués + mode avancé) — maquette en itération.

## À faire

- **Work/Cowork déléguable** : pilote réel (une entrée, un livrable, un reçu) — lancement Cowork `unknown` ; [contrat](docs/contracts/HARNESS-SUPERVISION-ARCHITECTURE.md).
- **Détection de boucles** (`loop_signatures`) + budgets parent/sous-agents — calibrage sur traces positives et légitimes.
- **Journal normalisé unique** et `work_id` transverse — étape 1 de la livraison progressive.
- **Compactage natif commandable** (observation seule aujourd'hui).
- **Rattachement générique** des travaux après redémarrage (au-delà de l'inbox Claude).
- **Triage sémantique des journaux** : implémentation du design #118 (NLEmbedding → FM, fenêtre et budget bornés).
- **Synthèse d'activité des paires** : port SwiftUI de la maquette validée (#118).
- **Renommage coordonné des identifiants hérités** (`local.agent-resume` → `reprise`) — USAGE.md, adhérence vérifiée nulle.
- **D3** (compte macOS vierge, mises à jour interrompues) et **D4** (signature, notarisation, distribution) — [installation](docs/contracts/INSTALLATION-CONTRACT.md).
- Critère d'entrée des futurs harness : matrice tenue à jour, fixtures par adaptateur.

## Retiré / abandonné

- Plafonds quotidiens locaux (tous backends) — retirés, compteurs inertes ; [CACHE-POLICY](docs/contracts/CACHE-POLICY.md).
- `claude_desktop_applyd` — supprimé ; dispatch CDP direct par `claude_desktop_quick.py` (#102).
- Proxy planner CUDOS — retiré (absence confirmée chez le fournisseur) ; [archive](archive/asi1-planner-proxy/README.md).
- Chaîne de traduction FR→EN et intégration `engines.py` de Super Decider — rejetées par la revue croisée ; consolidé dans [HARNESS-ROLES-CONTRACT](docs/contracts/HARNESS-ROLES-CONTRACT.md).
- Routage heuristique par défaut dans le profil desktop — décision opt-in (asymétrie FR/EN observée).
