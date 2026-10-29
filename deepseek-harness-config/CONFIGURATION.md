# Configuration DeepSeek Harness — 11 octobre 2026

Configuration de référence pour Harness desktop 0.2.0-rc.2. Installation locale sous `~/.dsh` (ou `DSH_HOME`) ; sauvegardes sous `backups/`. Ce document décrit les vérifications initiales, pas un inventaire garanti du profil actuel. Six compétences sont désormais fournies ; les vérifications historiques ci-dessous portaient sur quatre compétences.

Plugins officiels retenus dans le preset Standard : shell, fichiers et recherche, jobs, compétences, recherche web/lecture, compaction, planification, objectifs et délégation. L'adaptateur multi-fournisseurs `dsh-llm-pi-ai` est maintenant configuré pour `openai-codex`. Aucune nouvelle bibliothèque téléchargée. Quatre compétences locales : code, DevOps, preuves scientifiques et orchestration. L'outil Crossref utilise uniquement Python standard et les certificats système.

Réglages effectifs : raisonnement DeepSeek faible par défaut, trois sous-agents actifs maximum, profondeur de délégation deux. Sélection des sous-modèles autorisée pour Flash/Pro sur les deux routes DeepSeek et Luna/Sol/Astra sur OpenAI Codex. Les modèles OpenAI sont ceux du catalogue livré; leur présence ne prouve pas leur disponibilité dans le compte. Les règles recommandent une escalade selon la difficulté et deux cycles de correction maximum. Ces deux cycles sont une consigne comportementale; les limites de concurrence/profondeur sont techniques. Aucun plafond monétaire ou compteur global de tokens n'est imposé par cette configuration.

Préférences : français, code concis, commentaires utiles seulement, bibliothèque standard et dépendances existantes en priorité, sources scientifiques vérifiées quand pertinentes, préparation des plans et du rollback DevOps.

## Activation et accès OpenAI

Ouvrir une nouvelle conversation avec le preset Standard après relancement de DeepSeek Harness. Les anciennes sessions peuvent conserver leur sélection de modèle et leurs permissions de délégation.

Dans les paramètres de modèles/fournisseurs de Harness, se connecter à `openai-codex` via OAuth ChatGPT. La session ChatGPT de cette application n'est pas automatiquement transférée à Harness. Vérifier ensuite les modèles réellement disponibles avec une courte tâche. Ne pas entrer de clé API OpenAI pour utiliser le forfait ChatGPT : l'accès par clé API relève d'une facturation distincte. Voir la [documentation officielle d'authentification](https://learn.chatgpt.com/docs/auth).

OpenRouter reste désactivé. Son ajout nécessitera une clé, un choix de modèles effectivement disponibles et un budget séparé; l'adaptateur multi-fournisseurs installé est prévu pour ce type de route.

## Vérifications

- Composition Cordis avec les overrides : réussie.
- Démarrage du profil temporaire avec le runtime Electron de l'application : réussi; serveur de test arrêté.
- Découverte des quatre compétences par le registre natif : réussie.
- Recherche Crossref réelle du DOI `10.1038/nature14539` : réussie.
- Concordance des sept fichiers installés avec les sources : vérifiée.

Les appels de modèles, l'authentification OpenAI et les économies de tokens ne sont pas encore validés. Le démarrage de test ne prouve pas le chargement dans une ancienne conversation de l'application.

## Retour arrière

La sauvegarde contient le patch desktop original et un manifeste des fichiers créés. Pour revenir à l'état initial, fermer Harness, restaurer le patch original et retirer uniquement les fichiers marqués `previously_existed: false` dans le manifeste. Ne pas supprimer les identifiants ou les sessions.

## État du routage — 14 octobre 2026

La sélection de modèle reste pilotée par les instructions tant que le plugin n’est pas activé. Le test isolé de `dsh-subagent-router` 0.6.0 a réussi sur le plan logiciel, mais a reproduit un classement insuffisant de certaines demandes françaises. Voir `../routing/README.md`. Le helper Apple de routage est désormais implémenté dans `../routing/`, avec deux moteurs et un test de délégation local ; il reste expérimental et non activé dans le profil quotidien.

## Mode `asi1-orchestrator` — effort bas (25 octobre 2026)

La documentation ASI:One consultée le 26 octobre indique désormais un défaut équilibré pour `asi1`, qui raisonne toujours ; l'ancienne affirmation « maximale » n'est pas retenue. Cela n'atteste pas le défaut du endpoint CUDOS. Sans déclaration `reasoningEfforts` ni capacité de catalogue, `dsh-llm-pi-ai` matérialise le modèle comme non-raisonneur et n'envoie pas ce paramètre. Voir le [contrat ASI1](../agent-resume/ASI1-ORCHESTRATION-CONTRACT.md) pour mini, la route directe et les preuves requises.

Le patch déclare `reasoningEfforts` sur `asi1` pour la route directe `asi-cloud`, avec `supportsReasoningEffort: true` et `supportsDeveloperRole: false`. Le plugin [`agent-resume/asi1-orchestrator-effort.cjs`](../agent-resume/asi1-orchestrator-effort.cjs), monté dans le preset `asi1-orchestrator`, force `low` sur toute requête `asi1` de ce mode, y compris si la session avait sélectionné une autre profondeur. Les autres presets, les modèles Flash et les workers ne sont pas modifiés. La route proxy planner n'est pas proposée : son implémentation n'est pas disponible selon la confirmation de CUDOS.

Un preset DSH ne choisit pas le modèle : `agent-default-model` est global et son `reasoningEffort` s'appliquerait à toutes les nouvelles sessions et à leurs sous-agents. Le mode doit donc être ouvert avec `asi-cloud/asi1` sélectionné ; le preset garantit alors l'effort `low` pour ce mode.

Reprise peut déployer cette capacité depuis **Intégrations Harness** avec l’option **Effort bas pour le mode asi1-orchestrator** (`integration-plan --asi1-effort`) : elle installe le plugin sous `PROFIL/.reprise/plugins/`, insère une entrée marquée dans la liste `plugins` du preset `asi1-orchestrator` et n’insère les déclarations `reasoningEfforts`/`compat` que si elles manquent, avec marqueurs et empreintes. Une déclaration déjà présente est conservée et n’est pas retirée. Voir [HARNESS-INTEGRATION.md](../agent-resume/HARNESS-INTEGRATION.md#effort-bas-du-mode-asi1-orchestrator-25-octobre-2026).

Sources : [Reasoning](https://docs.asi1.ai/documentation/build-with-asi-one/reasoning), [OpenAI Compatibility](https://docs.asi1.ai/documentation/build-with-asi-one/openai-compatibility), [Models](https://docs.asi1.ai/documentation/models).

## Synchronisation de la copie de référence — 29 octobre 2026

`cordis.patch.yml` est redevenu un miroir exact (byte-identique) du patch live `~/.dsh/profiles/desktop/cordis.patch.yml`, vérifié par `cmp`. Il intègre : catalogues Morph (API `openai-responses`, 7 modèles) et Xiaomi (`xiaomi`, `xiaomi-token-plan-ams`), `models: []` sur `llm-deepseek`, `agent-default-model` = `asi-cloud/asi1`, `session-log-deepseek` désactivé, `transcriptView: detailed`, fenêtres de contexte ASI:Cloud corrigées (asi1 300k), suppression des clés `compat` dupliquées et le bloc `# BEGIN REPRISE MANAGED INTEGRATION` (généré par Reprise,chemins absolus — ne pas éditer à la main). Le bundle web Brave ne figure pas ici : il s'applique comme couche bundle (`web-search-provider-study/`), pas dans ce patch de profil. `install.py` refuse tout déploiement si le patch live a divergé (`Profile changed since inspection`) : fusion manuelle requise dans ce cas.
