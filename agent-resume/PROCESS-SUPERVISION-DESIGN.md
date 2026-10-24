# Conception : supervision des processus de Reprise (registre PID, vue ps, relance autorisée)

Statut : **conception déléguée, non implémentée**. Trace d'un lot de design/conception par délégation.

- Expert : Codex `gpt-6.1-sol` (effort medium), thread `01a169f5-8003-7b52-ba2e-b9c77a514e70`,
  `request_key` `sol-process-supervision-design-20261023`, le 23/10/2026. Réponse intégrale conservée
  localement (`sol-process-supervision-design-20261023.response.md`, artifact non versionné).
- Exigence : « Reprise doit connaître les PID de ses dépendances (services, desktop harness…),
  produire l'équivalent d'un `ps`, présenter cette information aux harness autorisés qui lui
  demandent, et être en mesure de les relancer ».
- Issues issues de cette conception : #25 (registre), #26 (exposition MCP), #27 (actions de relance),
  #28 (découplage GUI/superviseur).

## Décision d'acteur (réponse à la question posée)

La GUI est déjà actrice de certains lancements/arrêts (`sharedAction`/`desktopAction`,
`NSRunningApplication.terminate`). Pour la cible, elle doit devenir le **point de commande et de
consentement**, tandis qu'un **superviseur utilisateur indépendant** (LaunchAgent + helper natif
Swift) exécute les relances. Raisons : servir les harness quand la GUI est fermée, et pouvoir relancer
la GUI Reprise elle-même (l'exécuteur doit survivre à sa terminaison).

## Faits de dépôt établis par l'expert

- `codex_expert_mcp.py` : l'allowlist `allowed_tools` autorise **tous les outils connus** en l'absence
  de sélection explicite — ajouter un outil d'action à `TOOLS` sans changer ce défaut l'exposerait
  implicitement aux harness existants.
- `reprise_health.py` : `socket_liveness` distingue reachable/stopped/unknown mais n'attribue aucun
  socket à un PID ; `desktop_mode.py` refuse d'authentifier un processus depuis un fichier PID,
  `daemon_stop()` retourne `unsupported` sans arrêt authentifié.
- `runtime.py` (`killpg` puis `SIGKILL`) = nettoyage d'enfants possédés, pas un contrat sûr pour un
  processus externe.
- `restart-chain` actuel **préserve `codexd`** (contrainte : relance ciblée, pas relance de chaîne).
- `reprise_backends` ne retourne aucune liste `tools` : la découverte effective passe par MCP
  `tools/list` (une extension avec champ `tools` filtré par droits effectifs est proposée).

## Modèle d'identité (quatre niveaux)

| Niveau | Identité et informations |
|---|---|
| Dépendance | `dependency_id` stable, type, profil/installation, propriétaire, capacités autorisées |
| Configuration | Bundle id éventuel, chemin canonique du bundle/binaire, script éventuel, socket configuré, gestionnaire et label éventuels |
| Instance | `boot_id + pid + start_time`, UID, PPID, chemin observé, provenance |
| Observation | `observed_at`, sources, erreurs, couverture, fraîcheur |

`dependency_id` survit aux changements de PID et aux mises à jour. Pour un daemon Python, conserver
aussi le script et les paramètres structurants autorisés ; ne jamais publier l'environnement ni les
arguments complets.

## Collecte et vue ps

- Collecteur **natif Swift** : `sysctl(KERN_PROC…)` comme base (PID, UID, PPID, début d'exécution),
  `libproc` pour le chemin exécutable, `NSRunningApplication` pour bundle id et terminaison
  coopérative. Pas de dépendance tierce, pas de structures `sysctl` reproduites en `ctypes` ;
  Python consomme du JSON versionné. `pgrep -x` reste diagnostic secondaire (nom insuffisant pour un
  daemon Python).
- Attribution des sockets : une connexion réussie prouve un point d'écoute, pas son identité.
  Distinguer `endpoint_state=reachable`, `owner_instance_id` (éventuellement inconnu) et
  `ownership_evidence`. Sans attribution fiable : propriétaire inconnu, **aucun droit d'arrêt**.
- Vue structurée (null + raison pour tout champ indisponible ; `process_state` ∈ running/not_running/
  unknown/ambiguous ; périmètre = dépendances connues, pas un inventaire global du Mac) :

```text
dependency_id, instance_id, kind, display_name
pid, ppid, uid, start_time
bundle_id, executable_path, bundle_path
manager, ownership, control_capabilities
process_state, endpoint_state, readiness
observed_at, stale, evidence_sources, errors
```

- Persistance : SQLite de l'état privé Reprise, tables séparées (dépendances + révisions de
  configuration / snapshots + observations / autorisations + actions durables). Le registre appartient
  au service logique, pas à une instance du pont ni à la GUI ; un seul composant possède les mutations ;
  aucun appel système lent dans une transaction SQLite. Valeurs initiales à mesurer : collecte 10 s,
  observation périmée après 30 s, rétention 24 h ; une action exige toujours une observation neuve.

## Contrats MCP proposés

- **Lecture `reprise_processes`** (read-only) : `view: summary | full | page | action`,
  `dependency_id?`, `refresh?` (sondes seulement), `snapshot_id?`, `offset/max_chars/result_sha256`
  (pattern existant), `action_id?`. Pagination liée à un snapshot immuable ; continuation exige même
  `snapshot_id` et même empreinte ; snapshot expiré → `snapshot_expired`.
- **Action `reprise_restart_process`** : `phase: prepare | execute`, `dependency_id`,
  `expected_instance_id`, `expected_config_revision`, `reason`, `request_key`, `plan_id?`,
  `approval_id?`. `prepare` produit un plan durable sans effet ; `execute` exige plan + autorisation
  correspondante. États métier (`awaiting_approval`, `in_progress`, `succeeded`, `failed`, `refused`,
  `indeterminate`, `unsupported`) placés dans un objet `action` pour préserver la convention de
  transport actuelle (statut ≠ `finished` = erreur MCP).
- **Autorisation à trois barrières cumulatives** : (1) exposition explicite — l'outil d'action est
  absent du défaut historique, présence obligatoire dans `--tools` ; (2) politique locale — opérations
  et `dependency_id` autorisés par intégration configurée ; (3) consentement opérateur — approbation
  ponctuelle liée au plan, à l'instance et à son expiration, recueillie par une interface locale de
  confiance. Un booléen `confirmed=true` envoyé par le modèle n'est pas un consentement opérateur.
  L'allowlist actuelle est une capacité du processus de pont : un futur `integration_id` doit venir de
  la configuration locale, jamais d'un argument déclaratif. Le superviseur vérifie lui-même les
  autorisations (filtrer seulement le pont laisserait un contournement par son interface locale) ;
  journalisation sans secret.

## Algorithme de relance et invariants

1. Résoudre `dependency_id` depuis le catalogue local ; refuser PID, commande ou chemin libre fournis
   par le harness.
2. Vérifier identité de l'intégration, outil activé, cible autorisée et consentement.
3. Collecter un état frais ; vérifier instance attendue, configuration, propriétaire et dépendants.
4. Persister l'intention avec clé d'idempotence et empreinte de la demande.
5. Réserver la cible contre les actions concurrentes, sans garder une transaction ouverte.
6. Revalider immédiatement avant l'arrêt ; utiliser l'adaptateur de contrôle vérifié.
7. Attendre l'arrêt confirmé ; refus, délai ou ambiguïté → arrêter la procédure.
8. Lancer une seule fois par le gestionnaire prévu.
9. Identifier la nouvelle instance et vérifier sa disponibilité.
10. Persister le résultat et libérer la réservation.

`already_stopped` ≠ démarrage (autre action explicite) ; une relance n'élargit pas silencieusement sa
portée à une chaîne.

Invariants : **détecté ≠ contrôlable ; contrôlable ≠ autorisé** ; un fichier PID, un nom ou un socket
accessible ne prouve pas la propriété ; aucun `pkill`, arrêt par nom, suppression aveugle de socket ni
`killpg` sur un groupe externe ; refus par défaut de la terminaison forcée ; une permission d'observer
n'accorde aucune permission d'agir ; même clé + même demande → reçu existant, même clé + demande
différente → conflit ; réconcilier après interruption avant toute nouvelle commande ; un succès de
lancement ne prouve pas que le service est prêt ; aucun redémarrage automatique après détection
d'anomalie. Si aucun contrôle authentifié n'existe pour une cible : observable, relance `unsupported`.

## Partie embarquée dans les harness

Côté harness appelant (DeepSeek Harness, Claude Desktop) : configuration du pont et de son allowlist ;
découverte via MCP `tools/list` (et, si retenue, champ `tools` de `reprise_backends` filtré par droits
effectifs) ; distinction entre outil exposé, superviseur disponible et action autorisée ; consignes :
lire, examiner les preuves, préparer une action explicite, attendre l'approbation, récupérer le reçu ;
conserver `request_key` et `action_id` avant envoi, aucune répétition automatique après timeout.
**Installer l'intégration n'accorde pas le droit de relancer** ; le harness ne contourne pas sa
sandbox avec son propre `ps` — c'est Reprise, non sandboxée, qui produit la vue.

## Rattachements

- #19 (instances multiples de la GUI) : partage l'identité d'instances et le mécanisme de relance ;
  traiter la suppression d'instances par bundle id avant d'activer la relance de Reprise lui-même.
- #6 (installation/maintenance depuis l'app) : porte l'installation du superviseur, des intégrations
  et des allowlists ; activation technique et autorisation d'action restent séparées.
- #14 (détection/scheduler Codex) : consommateur possible des observations ; ce lot ne modifie ni
  scheduler, ni quotas, ni automatisation de reprise.

## Plan de validation après implémentation

Tests ciblés des nouveaux modules, puis régressions `test_codex_expert_mcp.py`, `test_reprise_health.py`,
`test_desktop_mode.py`, `test_runtime.py`, `test_engines.py`, `test_restart_chain.py`, puis suite
complète (`python3 -m unittest discover -s agent-resume/tests -q`) et `swiftc -parse Native.swift
Dashboard.swift` (ajouter le futur helper natif aux vérifications de compilation ; `swiftc -parse` seul
ne valide ni types ni API système). Essais macOS requis : attribution des instances, refus d'arrêt,
récupération après interruption, fonctionnement GUI fermée.

Hypothèses restant à valider par prototype : accès d'un helper natif aux métadonnées sous le compte
utilisateur ; mécanismes de contrôle disponibles selon le propriétaire de chaque service ; API
d'identification du pair socket.
