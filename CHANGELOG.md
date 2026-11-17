# CHANGELOG — Reprise

Historique des versions, consolidé depuis les notes de release et les notes produit datées (détails complets dans l'historique Git, commits et balises).

## Notes produit — 17 novembre 2026

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
