# Point d’étape — intégration Apple Intelligence

Mise à jour : 3 novembre 2026. Copie d’intégration `apple-integration-current-dev` basée sur `f29944b03d43b5649dcd14cbe7a00c2ae63d1661`, incluant le correctif #104 dans son historique. Les espaces de travail concurrents sont préservés.

## Validation et activation du 3 novembre

- Application et planificateur installés et vérifiés avec le bundle précédent, incluant la route Apple et le correctif du tick ; les sauvegardes et anciennes releases sont conservées. Le connecteur MCP Codex a été rechargé sans redémarrage de codexd ; `reprise_backends` réellement chargé annonce `foundation-models/system`, sans repli. La découverte ne constitue pas une nouvelle inférence.
- L’intégration future du MCP Codex est disponible via le script `managed_update` et la carte Intégrations, avec plan lecture seule, sélection explicite des entrées, contrôle de version natif, sauvegarde et intention durables, staging vérifié, et récupération sans rejeu. Une réponse perdue reste indéterminée ; vérifier la configuration ne prouve pas quel appel l’a écrite. Aucun rechargement client automatique n’est promis.
- Validation du lot complet : 1 441 tests réussis hors sandbox, dont 6 ignorés ; syntaxe Swift et build autonome vérifié réussis. Avertissements SQLite `ResourceWarning` et dépréciations Swift observés. Le parcours graphique complet et le Trousseau réel ne sont pas validés ; les prototypes de délégation coordonnée restent désactivés.
- L’état de déploiement et de publication est à vérifier à nouveau après ce checkpoint ; les reçus de maintenance restent la preuve des opérations effectivement appliquées.

## Reprise du 3 novembre 2026

- Le sous-agent du worker a repris son fil après interruption. Le worker et son bridge restent expérimentaux, non raccordés et refusent par défaut : aucun garde concret ne protège la fenêtre entre vérification des droits et admission, et aucun lanceur isolé n’est fourni. Aucune délégation réelle effectuée. La revue indépendante a identifié un risque d’attribution d’un reçu concurrent à une mission : sans identité de requête vérifiable, le worker et les lectures publiques du registre maintiennent désormais `indeterminate`, y compris pour les anciens reçus conservés sans modification. Correction revue ; 34 tests ciblés réussis. Voir `COORDINATOR-JOBS-CONTRACT.md`.
- La découverte locale des capacités n’est pas raccordée au pilote. Sa revue a corrigé deux représentations : périmètre non observé distingué d’une divergence et nom historique du connecteur distingué de son identifiant logique. Huit tests ciblés déclarés réussis ; cela ne prouve ni chargement du client ni capacité opérationnelle.
- Les fils mémoire, architecture, recherche Apple, inventaire et évaluation contrôlés indiquent que leurs derniers lots étaient terminés. Leurs anciennes validations ne couvrent pas les changements ultérieurs. Aucune intégration de branche ni publication effectuée.
- Suite complète hors sandbox : 1 378 tests, 6 ignorés, succès. Elle a été lancée pendant la dernière correction du registre ; les 34 tests de ce périmètre ont ensuite été relancés avec succès. La première tentative avait révélé l’absence des deux sources Swift du pilote dans l’export ; liste corrigée et neuf tests d’export/revue réussis. Des avertissements `ResourceWarning` de connexions SQLite non fermées figurent dans le journal.
- Un prototype interne d’admission protégée est ajouté à `delegation_store.py`, sans branchement au bridge ou au worker : autorisation, révocation, identité de mission et admission partagent une transaction SQLite ; un grant est lié à une mission, une clé et un contenu canonique. Les revues ont conduit à corriger l’unicité de mission, la migration, la liaison du contenu et une mutation concurrente ; les deux relectures confirment cette dernière correction. Le compteur n’est pas un budget partagé multi-missions. L’autorité n’est pas authentifiée, aucun plafond financier fournisseur n’est établi, et le snapshot contrôlé n’est pas encore transmis à un éventuel dispatch : raccordement interdit avant résolution et revue de ces limites.
- Suite complète ultérieure hors sandbox : 1 394 tests, 6 ignorés, succès ; elle tournait pendant la dernière correction du prototype. Les 15 tests ciblés du prototype ont été relancés après cette correction et réussis. Avertissements SQLite `ResourceWarning` toujours présents. Aucun dispatch réel.
- Un handoff interne remet désormais le snapshot canonique seulement après nouvelle admission et vérification post-commit ; refus et rejeu ne le réémettent pas. Deux revues ont conduit à refuser les modes transactionnels et connexions personnalisées incompatibles et à vérifier les lignes après commit. 25 tests ciblés passent. La possession de l’enveloppe n’authentifie pas son destinataire ; les empreintes de fichiers ne figent pas leurs octets. Avant raccordement : authentifier le canal/hôte, assurer une base persistante et une synchronisation adaptée (la base mémoire ou `synchronous=OFF` restent acceptés), revalider les fichiers et conserver l’incertitude après crash. Aucun worker ou dispatch n’utilise ce handoff.
- Validation complète après stabilisation du handoff : 1 405 tests, 6 ignorés, succès hors sandbox ; `ResourceWarning` de connexions SQLite non fermées observés. `git diff --check` réussi. Aucune exécution graphique réelle, signature Siri ou validation Trousseau réel supplémentaire.

## Orientation précisée par l’opérateur

L’AFM doit surtout recueillir l’objectif et les précisions, demander la planification et l’exécution aux harness compétents, puis suivre leurs reçus et blocages. Les harness peuvent utiliser leurs propres sous-agents ou équipes. Reprise impose les permissions, les budgets, la portée et la conservation de l’état ; la conformité ne dépend pas de la seule consigne au modèle. La délégation effective et le suivi des tâches restent à implémenter et à mesurer.

## Objectif maintenu

Intégrer Apple Intelligence dans Reprise comme cible de délégation et étudier son rôle éventuel d’orchestrateur, avec mémoire, skills, connecteurs et accès Siri lorsque les interfaces et les preuves le permettent. Aucun des prototypes ci-dessous ne clôt cet objectif.

## Décisions et preuves

- **Route locale explicite** : `foundation-models/system`, sans outil d’action ni repli automatique. La réponse est conservée dans le registre existant ; aucun suivi conversationnel n’est annoncé pour cette route. La disponibilité de l’exécutable ne prouve pas l’inférence. Test réel hors sandbox : appel `reprise_delegate` sur base temporaire, réponse correcte, rejeu identique sans nouvelle inférence. Voir `APPLE-INTELLIGENCE-INTEGRATION.md`.
- **Reçus et erreurs** : une entrée rejetée avant lancement et un processus Apple prouvé terminé sont enregistrés sans affirmer le résultat de l’inférence. Une terminaison non prouvée reste incertaine. Rejeu et conflit d’identifiant testés, y compris fichiers modifiés.
- **Orchestration non qualifiée** : une mesure exploratoire a obtenu 7/8 scénarios synthétiques ; l’échec concerne le choix d’un outil de checkpoint. L’évaluation ne prouve ni fiabilité générale ni résistance aux injections. Pas de délégation en cascade, d’écriture autonome ni d’activation de production fondée sur ce score. Voir `APPLE-ORCHESTRATOR-EVALUATION.md`.
- **Mémoire** : API distincte, chiffrée par Vault avec purpose dédié, portée projet/travail choisie par l’hôte, checkpoints immuables et opérations idempotentes. Provenance déclarative, aucune preuve externe inventée, pas de purge automatique. Coffre simulé testé ; Trousseau réel et autorisation IPC/MCP non validés. La consultation projet utilise un sous-processus SQLite en lecture seule pour éviter de libérer les verrous POSIX de l’hôte ; limite de course par processus du même compte documentée. Voir `ORCHESTRATOR-MEMORY-CONTRACT.md`.
- **Pilote conversationnel expérimental** : sélection explicite du projet, travail et checkpoint ; skill statique choisie par l’opérateur ; seul outil de lecture du checkpoint sélectionné. Pas de connecteur externe, de délégation ou de sauvegarde automatique. Historique volatil ; admissions et fermeture synchronisées ; appels déjà admis peuvent finir. Voir `APPLE-PILOT-CONTRACT.md`.
- **Transport local** : processus `fm serve` éphémère, socket privé, contrôle du PID du pair avant transmission, modèle `system` explicite, sans repli. Deux tours réels sur données fictives ont réussi, dont lecture d’un checkpoint et conservation d’un effet incertain. Cela ne garantit pas l’isolation face à un autre processus du même compte ni l’arrêt de l’inférence système. Voir `APPLE-PILOT-TRANSPORT.md`.
- **Service GUI** : protocole JSONL borné, activation et sélection explicites, aucun appel modèle avant `turn`. L’arrêt non confirmé reste une erreur visible. Une revue indépendante a vérifié les corrections des courses d’arrêt, de réponse à l’EOF et de nettoyage incertain ; l’interface est en revue pour ses propres délais et fermetures. Voir `APPLE-PILOT-SERVICE.md`.
- **Siri** : prototypes autonomes app et extension compilés ; extraction des métadonnées et cible extension vérifiées. Aucune installation ou découverte Siri réelle, signature complète, partage de snapshot ou accès aux données non démontrés. Pas de preuve d’installation directe de MCP ou SKILL.md dans Siri. Voir `APPLE-SIRI-PROBE.md`.
- **PCC** : la CLI hors sandbox demande Terminal ; appel Swift non signé indisponible malgré disponibilité déclarée. Apple requiert un entitlement et des critères d’éligibilité pour les applications ; pas de route PCC annoncée comme fonctionnelle ni de repli payant. La distribution Developer ID hors App Store n’est pas établie.

## Avancement complémentaire

- Service JSONL testé hors sandbox avec un projet Git et un registre temporaires fictifs : `open` → `turn` → `close`, réponse « Deux plus deux font quatre », sortie zéro ; aucun checkpoint ni Trousseau réel.
- Une page SwiftUI expérimentale est intégrée au clone : activation et projet/travail explicites, checkpoint facultatif, conversation volatile et gestion mémoire par l’opérateur. Compilation, test avec service fictif et revues de fermeture/portée des brouillons réussis ; validation graphique réelle à faire.
- La gestion mémoire explicite est raccordée au service et à une première interface, avec coffre simulé aux tests. Les mutations utilisateur conservent un `operation_id` stable et ne sont pas des outils du modèle. Intentions et reçus sont chiffrés et leur identité comparée ; les répertoires sont resynchronisés avant confirmation. Une revue indépendante a vérifié ces chemins et 31 tests ciblés. Le Trousseau réel et le parcours graphique restent à valider ; la reprise UI d’une opération incertaine est implémentée et testée avec un service fictif ; sa revue confirme le blocage des mutations sans reçu attribué.

- Un inventaire de connecteurs a distingué inspection de configuration, client chargé et capacités réellement disponibles. Un module expérimental d’inspection locale ciblée est en cours de revue ; aucun appel d’outil ni délégation par l’AFM n’est encore activé.

- La responsabilité de routage prévue par `HARNESS-ROLES-CONTRACT.md` reste initialement DeepSeek Harness / ASI1 et configurable ; le rôle AFM n’est pas imposé. Un registre chiffré de brouillons de mission, sans dispatch, est en revue.
- Le premier essai du nouveau banc coordinateur (1/8) est invalide : des identifiants et critères attendus n’étaient pas fournis au modèle. La v2 corrigée a ensuite donné 0/8 lors d’une seule exécution réelle : le modèle n’a généralement pas appelé l’outil attendu. Critères textuels heuristiques et cas synthétiques ; analyse qualitative à mener, aucune conclusion générale ni qualification de production.

- Étude Super Decider : AFM local est candidat pour une aide structurée, pas un routeur autonome validé ni un modèle probabiliste calibré. Le smoke historique a produit un mauvais choix sur cinq avec confiance déclarée 0,91 ; les cas partageaient une session et ne sont pas indépendants. Évaluer en observation face à des règles, puis Super Decider si accès autorisé, avec holdout indépendant et coût pondéré.

## Contrôles

- Suite globale avant les derniers ajouts du service/transport : 1 275 tests, 6 ignorés, succès hors sandbox. Une exécution en sandbox a échoué notamment sur les sockets Unix interdits ; ne pas interpréter cette exécution comme réussite.
- Compilation `sh native-install` réussie dans le clone, sans lancement de Reprise ; avertissements Swift existants.
- Tests ciblés, revues et limites détaillés dans les documents de chaque lot. Après toute modification supplémentaire, réexécuter les tests concernés puis la suite complète avant publication.

## Prochaines étapes

1. Poursuivre le raccord des missions aux harness et le suivi avec reçus et droits ; ne pas modifier la route configurée silencieusement.
2. Tester l’interface expérimentale en conditions réelles : sélection explicite, état et annulation honnêtes, aucun lancement d’inférence à l’affichage ; valider le Trousseau sur environnement de test et le cycle de vie.
3. Prévoir une création et récupération de mémoire utilisable depuis Reprise, et qualifier le modèle sur des trajectoires représentatives avant d’autoriser outils/connecteurs supplémentaires ou délégation.
4. Démontrer séparément l’intégration Siri réelle et les conditions d’accès PCC, sans toucher aux sessions actives.
5. Revue finale, tests, traçabilité Git et intégration suivant les règles du dépôt ; aucune MR vers main sans le feu vert requis.

## Port sur le dernier dev observe le 3 novembre 2026

- Copie isolee `apple-integration-latest-dev` sur `53affea6cc09ab3e9327bf74437ecd9fa9a81d8e`, tete dev distante observee ; aucun commit ni deploiement.
- Correctif issu de `fd75386f7faf8843c947a80abb7422f2364d23b0`, branche `codex/104-ecritures-workspace`, applique comme diff depuis son parent `76b474817b02083a4276b04e91c9795f345f2168` ; ces origines ne sont pas des parents Git du HEAD detache.
- Lot Apple issu de la copie `apple-intelligence-source` de baseline `606fcd2458d1f7ae742ac4597a68eab2dfa25000` : modifications suivies et 41 fichiers non suivis portes. Conflit dans `codex_expert_mcp.py` resolu en conservant le diagnostic #104 et la route Foundation Models. Six ressources Apple ajoutees au packaging courant sans effacer les correctifs depuis la baseline.
- La validation complete, la revue independante et tout deploiement restent a realiser sur cette copie ; la suite lancee sur la copie precedente a ete interrompue avec resultat inconnu.
