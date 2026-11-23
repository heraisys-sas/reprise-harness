# CHANGELOG — Reprise

Historique des versions, consolidé depuis les notes de release et les notes produit datées (détails complets dans l'historique Git, commits et balises).

## 0.1.21 — pont Meta AI : transport fichiers WebDAV, TaskStore partagé, daemon metaaid

## Notes produit — 23 novembre 2026

- **Pont Meta AI fermé de bout en bout avec l'agent réel (23/11)** — Meta AI native (`com.meta.endo.vanilla`, SwiftUI, pas d'Electron → pas de CDP ; route AX écartée) intégrée par son **connecteur fichiers** sur un volume webdavfs localhost monté par le daemon `metaaid` (LaunchAgent `app.reprise.metaaid`, wsgidav + watcher embarqués, installateur `metaai-install.py` : plist écrit, launchctl jamais exécuté silencieusement). Cycle `pending → dispatched → answered` sur le **TaskStore partagé** `metaai-tasks.sqlite` : aller par `dispatch_task` (inbox JSON), retour par outbox **clé/valeur sans JSON** (bug tool-call du modèle : tout contenu JSON est rejeté par le connecteur — erreur « A write needs a `contents` string… ») consommé par le watcher → `record_result`. **Pont MCP** `metaai_mcp_server.py` (stdio, zéro dépendance) sur le même store : `enqueue_task`, `dispatch_task`, `get_task` (usage agent exécutant, sans écriture inbox — jamais en double avec Meta AI), `submit_result`, `get_status`, `list_tasks`. Validé live : `tache-20261123-124609-84bf` — 17 × 23 = 391, `confiance=1`, `source=metaai`. Limites : déclencheur du tour manuel ou « Programmé » natif ; webdavfs CLI ne transmet pas les creds d'URL (auth anonymous localhost-only, code creds prêt) ; le grant de partage Meta AI est révoqué au logout. Leçons : ne jamais stat-er un mount webdavfs zombie (crash loop) ; Python 3.9 sans PEP 604. [Intégration](docs/integrations/metaai/METAI-INTEGRATION.md).

## 0.1.20 — route ChatGPT fermée, clones d'app, santé actionnable

Version commune portée à 0.1.20. **Route ChatGPT fermée de bout en bout** (admission durable → dispatch holder CDP dédié → capture de réponse → résultat livré au délégant ; preuve live t1, dispatch opt-in `--chatgpt-dispatch`, `dispatch_receipt` borné). **Holder longue durée** à relance bornée de son propre Chrome (#198 partiel), politique une-fenêtre + titre ReChatGPT, dispatch Entrée primaire. **Clones d'app** ReChatGPT/ReClaude : identité propre, version gelée, auto-update du cloné coupé, module géré `app_clones.py` + vue Intégrations (lecture). **Santé post-update actionnable** : relance réelle de l'app dans toutes les distributions, liens d'action par élément à examiner ; Connexions sans les entrées « serveur dédié » sans service (#93). Suite complète 2084 tests OK ; détails dans les notes produit datées ci-dessous.

## Notes produit — 17 novembre 2026

- **Clones d'app ReChatGPT / ReClaude (17/11)** — Copies APFS à identité propre (`com.reprise.rechatgpt`, `com.anthropic.claudefordesktop.reprise`), version gelée (auto-update du clone désactivé par son identité), originale `/Applications` pristine. Module géré `app_clones.py` (inspect/plan/apply/verify + manifeste `app-clones.json`), gardes PID/disque/originale, re-sign ad-hoc deep ; le holder dédié démarre sur la copie quand elle existe. Vue « Intégrations → Clones d’app » en lecture du manifeste. [Contrat](docs/contracts/APP-CLONES-CONTRACT.md), [runbook](docs/runbooks/APP-CLONES.md).
- **Route ChatGPT Desktop fermée de bout en bout (17/11)** — Validée live (`t1-live-1794938260` : PONG capturé, `delivery=captured`, `completion=sentinel`). Capture v1.1 par le holder : le flux de tokens vit dans le process principal owl, invisible au CDP ; complétude par WS `conversation-turn-complete`, fenêtre calme MutationObserver (20 s), marqueur rendu « réponse terminée » et settle DOM (6 s) ; SSE mort, AXTree écarté (faux positifs), deux sondes identiques ne complètent plus seules ; extraction ancrée (écho du prompt, sentinel `REPRISE-CAPTURE-FIN` prioritaire, pied), dernière sonde retenue. Dispatch Entrée primaire (keyCode 13), bouton en repli, select-all du composeur avant insertion, staging `ready` double et marqueur fsync avant la frontière irréversible. Chaînon admission → dispatch opt-in `--chatgpt-dispatch` (#226) : reçu borné en colonne `dispatch_receipt`, réponse capturée en retour synchrone, `reprise_backends` à `holder_capture_sync` avec le grant, admission seule (#221) sans. `chatgpt_mcp.py` supprimé (cb7989c) ; `operator_isolation_verified` reste `false` ; release/redéploiement des connecteurs gérés restent à faire.

## 0.1.18 — collecte et échéance Claude

Version commune portée à 0.1.18 (application, helpers, connecteurs). Dans Intégrations, la boîte de réception permet de lister les tâches Claude du projet autorisé, de lire un résultat vérifié par empreinte et de l'acquitter explicitement. Le connecteur permet d'activer ou de désactiver `await_task`, ou de conserver le réglage existant. La livraison incertaine n'est jamais rejouée ; un résultat accepté par `submit_result` reste le critère de fin d'une tâche MCP. Le suivi Claude présente une échéance relevée dans le journal comme événement daté, pas comme un état courant.

## 0.1.17 — attente et retour MCP Claude

Version commune portée à 0.1.17. L'outil `await_task` attend une tâche précise pendant une durée bornée ; masqué par défaut, activé explicitement avec `--enable-await` dans l'intégration Claude gérée (une mise à jour ordinaire conserve ce choix). Livraison et retour par MCP (`await_task` ou `get_task`, puis `submit_result`), avec reçus sans rejeu. Un premier aller-retour réel supervisé a confirmé le retour `await_task` → `submit_result` et son acquittement ; il ne démontre ni le réveil autonome d'une conversation froide, ni l'isolation par identité de chat.

## 0.1.16 — retrait du proxy ASI1 et suivi Claude

Suivi passif des chats Claude Desktop déclarés, affichage de leurs observations quota/reset, retrait du proxy planner CUDOS (absence confirmée par une source employé rapportée par l'opérateur le 28 octobre 2026). L'inférence et les appels d'outils conservent le fournisseur direct existant.

## Notes produit — mi-octobre 2026

Consolidées depuis les notes datées de l'ancien README de l'application :

- **Mode partagé intégré (16/10)** — Page Connexion Codex en trois étapes : préparer le serveur, lancer Desktop, vérifier le chat. Reprises automatiques en pause pendant la préparation ; Desktop déjà ouvert jamais fermé ni relancé ; raccordement WebSocket Unix au Desktop installé, expérimental ; lancement automatique à l'ouverture bloqué par changement de version de Desktop.
- **Connexion Codex dans l'interface (16/10)** — Transport enregistré, socket local et serveur d'expertise dédiés ; chemin personnalisé testé sans appel au modèle avant enregistrement ; réglages moteur inclus dans les sauvegardes version 2.
- **Interface et diagnostics (16/10)** — Correction du lancement Desktop : URL Unix avec `localhost` explicite pour éviter le proxy SOCKS interne ; « Arrêter le serveur » passe par `codex app-server daemon stop` avec contrôles d'état préalables (verrou worker, sessions chargées).
- **Connexions par moteur et état courant (15–16/10)** — Distinction configuration chargée / service joignable ; source de quota distincte avec attribution inconnue ; aucun pourcentage de quota inventé ; échéance passée = mesure périmée, jamais épuisement garanti.
