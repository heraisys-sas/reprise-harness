# Architecture cible : supervision et reprise communes à tous les harness

Décision de conception du 20 octobre 2026, issue des demandes utilisateur et de l'inventaire local. Contrat à implémenter progressivement ; ne décrit pas des fonctionnalités déjà toutes présentes.

Revue critique : [réserves Astra et MVP resserré](ARCHITECTURE-CRITIQUE-ASTRA.md). Les garanties de concurrence, permissions, quota et récupération asynchrone restent à consolider avant implémentation active.

État du 21/10 : [premier lot de protections persistantes](DELEGATION-PERSISTENCE.md) implémenté dans les prototypes, avec récupération/acquittement, lignée et plafonds quotidiens retirés. Cela ne livre pas encore leur unification ni le parcours autonome Claude.

## Décision

Reprise suit un travail durable, même lorsqu'il traverse plusieurs sessions. Codex, DeepSeek Harness, AutoClaw, Claude Code, Cowork et Chat Desktop participent au même pipeline. Quick Apply + MCP est un adaptateur de délégation Claude, pas l'architecture centrale du produit.

```text
Sources natives : événements / journaux / métadonnées / livrables
    ↓ adaptateurs de lecture incrémentale
Journal normalisé et provenance (SQLite privé)
    ↓
Graphe travail → sessions → tours → appels et sous-agents
    ↓
État reconstruit + usage + progrès + interruptions + checkpoints
    ↓
Politique commune : quota / boucle / contexte / budget
    ↓
Plan d'action durable → revalidation → commande native ciblée
    ↓
Réconciliation avec preuves natives + état réel des livrables
```

La lecture et l'action sont séparées. Un parseur ne déclenche jamais de commande. Le moteur de règles ne dépend pas des chemins ou du protocole d'un fournisseur. Aucun agent génératif permanent n'est nécessaire pour surveiller.

## Point d'entrée Reprise commun et bidirectionnel

Décision utilisateur du 20/10 : chaque harness connecté doit pouvoir, via le même service Reprise, **recevoir une tâche, rendre son résultat et solliciter un autre harness**. La façade de référence est un serveur MCP Reprise regroupant ces outils ; un connecteur ou une interface native peut exposer le même contrat si nécessaire. Un seul service logique et registre durable, sans imposer un seul processus stdio à tous les clients. Les connexions clientes peuvent être séparées et partagent le service au travers d'un mécanisme local explicite, sans supposer que leurs mémoires de processus sont communes.

Séparer les rôles et leurs autorisations : demandeur, exécutant, destinataire du résultat, opérateur. Une connexion n'obtient pas automatiquement accès à tous les projets ou à toutes les fonctions. Le périmètre est lié au client/projet autorisé, pas à un champ déclaré librement par le modèle.

Familles d'opérations : découverte de capacités ; soumission/délégation ; réception/réservation de tâche ; remise de résultat ; liste/récupération/acquittement des résultats non réclamés ; suivi/reprise/annulation lorsque supportés. `reprise_backends`, `reprise_delegate`, `reprise_followup`, `get_task` et `submit_result` existent dans des prototypes distincts ; les noms et schémas des autres opérations restent à stabiliser. La réunion des prototypes n'est pas encore livrée.

La tâche d'expertise ou Work sollicitée par un exécutant devient un enfant explicite du travail courant : conserver racine, parent, profondeur, demandeur et clé de requête. Limiter profondeur, nombre d'enfants, concurrence et budget agrégé, en distinguant plafonds imposables et alertes. Détecter les cycles de demandes équivalentes dans une même lignée, sans interdire toute revue réciproque légitime. Une attente d'expertise ne doit pas bloquer le traitement des retours MCP ni maintenir un verrou SQLite pendant l'inférence. Un avis reçu ne crée pas de nouvelle délégation par défaut.

**Priorité immédiate : interroger Claude Desktop sur son compte disponible, y compris gratuit, et récupérer la réponse sans bascule payante.** Chemin : harness demandeur → Reprise/SQLite → Quick Apply → Claude appelle `get_task` → éventuelle expertise enfant via Reprise → `submit_result` → résultat durable récupérable par le demandeur. Le plan gratuit demeure soumis à ses limites et permissions ; leur épuisement conserve le job en attente/incertain selon preuve, sans changement de facturation. L'essai supervisé a fonctionné ; l'autonomie et le service unifié restent à valider.

Pour les autres harness, privilégier la même façade de délégation et retour pour les jobs créés par Reprise. Le transport de déclenchement reste natif par adaptateur ; les observations et reçus natifs peuvent compléter le contrat. Cela ne force pas l'installation MCP dans une application qui ne le supporte pas, ni la conversion rétroactive de ses tâches ordinaires.

## Identité, données et états

- `work_id` : travail utilisateur ; rassemble éventuellement plusieurs sessions. Aucun regroupement fondé uniquement sur un titre similaire ou un même dossier.
- `session_key` : harness + instance/profil + identifiant natif. Conserver modèle, fournisseur, version et dossier observés, sans remplacer silencieusement la route d'origine.
- `turn_id`, `tool_call_id`, `parent_session_key` : identités natives lorsque présentes ; lien inconnu conservé comme inconnu.
- `event_id` et curseur : déduplication stable et provenance fichier/offset ou identifiant protocole ; ordre par session, tolérance aux retards, rotations, trous et redémarrages.
- Usage : distinguer delta, cumul et estimation ; couverture racine/enfants explicite. Agréger chaque événement une seule fois, sans additionner le total parent aux détails enfants déjà inclus. Tokens de cache, coût déclaré et facturation effective sont des mesures différentes.
- Checkpoint versionné : faits sourcés, décisions, contraintes, état des fichiers, validations, erreurs déjà explorées, enfants vivants, effets incertains et prochaine action. Résumé généré facultatif, identifié et relié aux sources.

Séparer trois axes :

1. **Exécution** : inconnue, active, en attente, arrêt demandé, arrêt confirmé, tour terminé.
2. **Blocage** : quota, authentification, permission, réponse humaine, propriétaire actif, boucle suspectée, budget ou contexte.
3. **Résultat** : absent, candidat, reçu, validé selon contrat, invalidé/périmé.

Une fin de tour n'est pas une validation métier. L'ancien `finished` ne doit pas devenir « tâche réussie » lors de la migration.

## Adaptateurs et équivalence fonctionnelle

Chaque nouvel adaptateur doit traiter les mêmes rubriques et exposer séparément observation et commande :

| Rubrique | Observation | Action éventuelle |
|---|---|---|
| Sessions | identité, activité, propriétaire, parent/enfants | ouvrir ou cibler une session existante |
| Quota | signal, portée, échéance et disponibilité | reprendre la session après revalidation |
| Consommation | tokens, cache, tentatives, couverture et coût connu | budget ou alerte, selon contrôle réel |
| Boucles | appels/erreurs/cycles et indices de progrès | avertir, demander un changement, interrompre |
| Contexte | taille connue, frontières de compactage | compactage natif ou checkpoint externe |
| Livrables | chemins autorisés, versions, intégrité | récupérer et vérifier un résultat |

Pour chaque capacité : `supported`, `unsupported`, `unknown` ou `degraded`, version testée, preuve, portée et dernière vérification. Une capacité supportée n'est pas une autorisation d'action ; la politique d'activation reste distincte. L'UI affiche ces limites avec la même terminologie pour chaque harness.

L'équivalence recherchée est celle du parcours utilisateur et des garanties : voir une interruption, comprendre son état, conserver le travail et proposer la meilleure reprise disponible. Elle ne suppose pas des API identiques. Si une commande manque, Reprise prépare le checkpoint et une action manuelle explicite ; il ne simule pas une reprise réussie.

## Matrice initiale fondée sur le dépôt

| Harness | Sources présentes | Commandes/limites actuellement établies |
|---|---|---|
| Codex | sessions et événements structurés, quota, transport partagé | reprise existante sous conditions ; interruption RPC des tours lancés par Reprise ; enfant via parent vérifié. Contrôle de tous les tours externes et compactage à valider |
| DeepSeek Harness | sessions natives, événements, lecteurs usage existants | reprise headless avec profil explicite ; disponibilité quota automatique non validée ; commande générale des enfants persistants à vérifier |
| Claude Code | transcriptions JSONL avec usage et marqueurs de compactage | détection ; adaptateur de reprise non validé dans engines.py. Ne pas confondre avec l'adaptateur d'expertise |
| Cowork | métadonnées, audits, livrables, quota/retries/compactage observables | lecture actuellement limitée ; reprise, arrêt et compactage commandables à vérifier |
| Chat Desktop | logs Quick Apply/MCP, signaux de santé | soumission Quick Apply ; retour MCP pour les tâches Reprise. Historique complet et commandes arrêt/compactage non établis |
| AutoClaw | détecteur de session avec état, activité, tokens/coût éventuels | pas d'adaptateur de reprise dans ENGINES ; commandes et sémantique quota à valider |

Cette matrice décrit le code inspecté, pas une certification des fournisseurs. Elle devra devenir un registre vivant, validé par fixtures et essais natifs bornés.

## Chaîne de reprise après quota

1. Enregistrer l'interruption avec sa portée : session, fournisseur ou compte selon preuve ; ne pas confondre quota, budget local et contexte saturé.
2. Figer un checkpoint sans nouvel appel modèle obligatoire. Ne pas reconstituer une autorisation à partir d'un texte généré.
3. Attendre une disponibilité confirmée ou proposer une action manuelle ; une échéance passée ne suffit pas seule.
4. Revalider identité, propriétaire, statut, route, enfants et effets externes ; ne pas reprendre un enfant isolément si son parent est requis.
5. Persister l'intention d'action et sa clé de déduplication AVANT envoi ; associer le reçu natif lorsqu'il existe.
6. Envoyer au plus une fois dans une situation ambiguë, puis réconcilier. Un timeout conduit à « réception incertaine », pas à une relance automatique.
7. Confirmer la reprise par observation native. Distinguer reprise demandée, démarrée, tour terminé et résultat vérifié.

Le scheduler Codex existant reste en place pendant la migration. Généraliser après tests de conformité, sans réduire ses protections ni autoriser implicitement les autres moteurs.

## Boucles : prévenir, détecter, intervenir

Prévention : sous-tâche bornée, critère de fin, budget enfant et parent, retour au parent après deux échecs comparables, refus de réessayer une permission impossible, sorties d'outils limitées, absence de cascade de délégation automatique. Un plafond par requête n'est pas un budget multiétape.

Détection commune : empreintes des appels et résultats, erreur répétée, cycle A/B, enfants recréés pour le même objectif, croissance de consommation sans progrès observable. Les adaptateurs normalisent les détails nécessaires ; les règles communes gardent les preuves et l'incertitude. Calibrer par type de travail et harness ; un modèle haut de gamme n'est pas exempté.

Réaction : incident unique → alerte → checkpoint → changement d'approche proposé au parent → arrêt ciblé si autorisé et confirmé. L'arrêt automatique exige une politique explicite et un adaptateur validé. Ne pas relancer immédiatement la tâche arrêtée pour boucle. Consigner une condition de sortie : nouvelle approche, environnement corrigé ou décision humaine.

## Contexte et compactage

Un même service de checkpoint sert à la reprise quota, au compactage et au transfert. Observer d'abord les compactages natifs ; ne jamais déduire une limite de contexte depuis un nom de modèle. Déclencher uniquement avec capacité native testée, contexte mesuré et frontière sûre entre actions. Après compactage, vérifier la conservation des contraintes et prochaines étapes. Le résumé externe reste une pièce de reprise et ne prétend pas réduire la fenêtre native.

## Résultats Claude et autres harness

Conserver la recommandation Astra A+B pour les tâches Claude déléguées : Quick Apply déclenche ; MCP réserve et remet la demande ; SQLite conserve ; `submit_result` validé est le reçu d'achèvement du contrat. Les audits peuvent retrouver un résultat candidat et expliquer un défaut de retour. Ils n'autorisent pas à fabriquer ce reçu.

Pour un travail externe non créé par Reprise, accepter les preuves natives avec leur niveau de confiance et les validations de livrables configurées. Ne pas imposer MCP à Codex, Harness ou AutoClaw et ne pas exiger rétroactivement `submit_result` pour leurs tâches ordinaires.

## Work/Cowork comme capacité déléguable entre harness

Exigence utilisateur du 20/10 : un harness dépourvu de mode Work doit pouvoir confier un travail à un environnement Work/Cowork via Reprise, comme il demande aujourd'hui une expertise. Cela vise une exécution bornée avec outils et livrables, pas seulement une réponse textuelle. Le nom produit Work/Cowork n'établit aucune équivalence entre fournisseurs : chaque adaptateur décrit l'environnement réel, ses outils, permissions et limites.

Séparer trois dimensions : capacité demandée (`expertise`, `review`, `batch`, futur `work`), environnement d'exécution et transport. Un connecteur est une façade d'accès ; il ne prouve ni un canal d'inférence, ni un contrôle de session. Le contrat commun reste identique derrière un outil MCP, un connecteur ou une interface native.

### Contrat cible, pas API déjà disponible

Le pont actuel n'accepte que expertise/review/batch et ses lots renvoient du texte sans appliquer les changements. Ne pas y ajouter `work` comme simple alias : l'exécution avec outils exige un contrat séparé.

- Découverte : capacités, destination et mode réels, outils disponibles, périmètre de fichiers, politique de facturation vérifiée et contrôles natifs supportés. Ne jamais annoncer un lancement Work à partir d'un simple chat ciblé.
- Soumission : demande bornée, critères d'acceptation, manifestes d'entrée et `request_hash`, `work_id` et parent appelant, clé de déduplication, destination, budget et échéance, droits explicites, sorties attendues et politique de publication.
- Retour immédiat : identifiant durable du job et état de réception. Éviter de maintenir l'appel outil bloqué pendant une longue exécution.
- Suivi : état, checkpoint et consommation/couverture connus ; interrogation bornée avec backoff, notification lorsqu'elle est disponible. Une notification accélère la lecture du registre sans remplacer une preuve.
- Résultat : analyse, manifeste de livrables, empreintes et validations exécutées. Répétition identique idempotente ; ancienne tentative ou résultat divergent rejeté. Distinguer reçu valide et critères métier satisfaits.
- Suite : complément de consigne sur le job existant, reprise native après quota si validée, annulation demandée puis confirmée. Les capacités absentes donnent un état explicite, jamais un succès simulé.

### Flux et responsabilités

```text
Harness demandeur sans Work
    → outil ou connecteur Reprise (même contrat de job)
    → admission et registre durable
    → adaptateur d'environnement Work/Cowork vérifié
    → exécution supervisée : quotas / boucles / checkpoints / budgets
    → collecte et validation des livrables
    → reçu et références retournés au harness demandeur
```

Le job appartient au travail parent, tout en conservant son identité native. Un redémarrage du demandeur ne recrée pas le job. Un envoi incertain reste à réconcilier. Aucun repli automatique vers un autre abonnement ou une API payante. Une délégation ne peut augmenter les permissions du demandeur : tout accès supplémentaire doit relever d'une autorisation utilisateur distincte. Les actes externes suivent les autorisations explicitement fournies, sans être déduits du mot Work.

Les fichiers de sortie sont remis dans un emplacement dédié, avec manifeste ; la fusion ou publication dans le projet est une action distincte selon la politique autorisée. Les sorties d'un environnement sont des données pour le demandeur, pas de nouvelles instructions de confiance. Ne pas copier les cookies, credentials ou profils d'authentification entre harness.

### Choix du transport

Privilégier une interface native documentée lorsqu'elle fournit identité durable, lancement réel du mode demandé, suivi, résultat et contrôle vérifiables. Exposer ensuite ce service par un outil MCP ou un connecteur selon ce que le demandeur sait appeler. Un outil unique de soumission et quelques opérations de suivi bornées évitent de transmettre l'historique complet à chaque appel.

Pour Claude, Quick Apply + MCP demeure un candidat de déclenchement/retour d'un tour Chat ; le lancement d'une véritable session Cowork n'est pas démontré. Les audits Cowork prouvent une source d'observation, pas un point d'entrée de commande. Le registre de capacités doit donc conserver le lancement Work/Cowork en `unknown` jusqu'à un essai dédié. Un échec de ce canal ne doit pas être masqué par une réponse Chat présentée comme un travail Cowork.

Comparer les méthodes sur un même scénario borné : taux d'achèvement validé, doublons, récupération après interruption, permissions requises, latence, tokens/contexte du demandeur et de l'exécutant, coût total connu, appels de suivi et maintenance après mise à jour. La robustesse de la reprise et du reçu est un prérequis ; optimiser ensuite l'économie par travail réussi. Aucun connecteur n'est intrinsèquement moins cher ou plus fiable.

### Validation et livraison

1. Définir le contrat asynchrone et ses fixtures indépendamment du fournisseur ; préserver les outils d'expertise actuels.
2. Inventorier les commandes natives des destinations Work/Cowork installées et distinguer observer/ouvrir/créer/exécuter/reprendre/annuler.
3. Réaliser un pilote demandeur sans Work → environnement Work réel : une entrée, un livrable, un reçu, un checkpoint ; pas d'effet externe.
4. Tester perte de réponse après soumission, demandeur redémarré, quota de l'exécutant, doublon, annulation non confirmée, boucle enfant, livrable périmé et retour tardif.
5. Comparer façade outil/connecteur et transports disponibles, puis activer uniquement les capacités démontrées. Chaque nouvel environnement Work passe les mêmes tests.

## Registre des travaux non réclamés et rattachement après redémarrage

Exigence utilisateur du 20/10 : Reprise conserve les travaux qui attendent un destinataire et peut préparer leur reprise dans une session redémarrée ou ayant cessé de progresser. Le registre appartient au travail durable, pas à la vie du processus ou de la conversation demandeuse.

Ne pas regrouper sous un seul statut des situations différentes :

| Situation | Sens | Réponse proposée |
|---|---|---|
| Demande non prise en charge | Aucun exécutant n'a confirmé la réception/réservation | Réconcilier la soumission ; ne pas renvoyer si réception incertaine |
| Travail réservé sans progrès connu | Tentative active ou incertaine, pas de résultat | Chercher propriétaire, événements et checkpoint ; pas de redistribution implicite |
| Résultat disponible non récupéré | Exécution rendue, parent absent ou redémarré | Présenter le résultat existant au destinataire autorisé, sans nouvelle exécution |
| Résultat récupéré non intégré | Parent a reçu les données mais usage/validation non confirmé | Conserver les références et demander ou observer l'intégration ; ne pas annoncer la tâche terminée |
| Travail interrompu à reprendre | Quota ou interruption confirmée, reste à faire | Préparer une continuation après revalidation |
| Session sans progrès apparent | Silence, répétitions ou contexte dégradé suspectés | Incident à examiner ; ne pas assimiler une impression d'essoufflement à un arrêt confirmé |

Conserver `work_id`, demandeur et destination d'origine, références natives, clés de requête, tentative active, checkpoint, manifeste des résultats, dernière preuve de progrès, motif d'attente et état de livraison. La réception/acceptation du résultat et son acquittement par le destinataire sont enregistrés séparément. Un acquittement ne prouve pas à lui seul la validation métier. Prévoir annulation, archivage explicite et rétention configurable pour éviter un registre rempli de travaux obsolètes.

Au redémarrage, une session authentifiée peut retrouver les travaux par identité stable de demandeur/projet et clé de requête persistée. Fournir une opération de liste/récupération, plutôt que d'obliger le modèle à connaître un job_id perdu. La liste est filtrée par autorisation ; pas d'accès global aux résultats des autres projets. Une notification ou un outil peut signaler les travaux disponibles, sans injecter automatiquement un message dans toute conversation ouverte.

Si la même session native est reprise, conserver son identité et vérifier son état. Si une nouvelle session remplace l'ancienne, créer un rattachement explicite à `work_id` avec provenance et autorité vérifiées ; ni titre similaire ni dossier identique ne suffisent. La nouvelle session reçoit un paquet de reprise borné : objectif, contraintes, décisions, travail déjà fait, livrables et prochaine étape. Si une ancienne tentative peut encore produire des effets, bloquer toute nouvelle exécution concurrente jusqu'à réconciliation ou décision autorisée tenant compte de ce risque. Le rattachement ne clôt pas fictivement l'ancienne tentative.

Une session « essoufflée » peut recevoir une proposition de checkpoint, changement d'approche, compactage natif vérifié ou transfert. Ne pas envoyer une instruction de reprise répétée au même agent en boucle. Politique proposée : un incident durable, une notification dédupliquée, et au plus une action automatique autorisée par incident avant réévaluation ; les lectures de réconciliation restent possibles. Aucun retour résultat ne doit provoquer une cascade de nouveaux jobs.

Tests : résultat rendu pendant quota du parent ; accusé perdu ; parent redémarré sans job_id ; résultat déjà récupéré ; parent archivé ; nouveau destinataire non autorisé ; rattachement légitime ; ancien exécutant encore actif ; retour tardif ; session silencieuse mais productive ; plusieurs résultats pour un même travail sans double exécution.

## Livraison progressive et conformité

1. Schéma commun, identités et journal incrémental ; registre de capacités ; fixtures assainies pour chaque harness.
2. Priorité fonctionnelle : aller-retour Claude Desktop via le service Reprise unifié, avec résultat récupérable après redémarrage et limites du compte gratuit respectées. Pour la supervision, parcours minimal Codex puis observation DeepSeek Harness, avant extension Cowork/Claude Code et AutoClaw. Les contrats et tests demeurent communs.
3. Checkpoints et écran de reprise commun ; intégrer progressivement le scheduler Codex existant.
4. Détection de boucles en observation sur chaque adaptateur, replay de traces positives ET légitimes ; calibrer faux positifs et coût de surveillance.
5. Commandes natives par capacité : reprise, interruption, puis compactage. Une capacité absente conserve un parcours manuel utile.
6. Test d'ajout d'un harness fictif : mêmes règles et vues sans branche fournisseur dans le moteur de politique ; matrice complète avec limites explicites.

Critères communs : pas de double comptage après replay/redémarrage, pas de double lancement incertain, aucune reprise après arrêt volontaire, enfant non orphelin, coût inconnu affiché inconnu, journal partiel sans faux succès, action refusée si version/capacité dégradée. Tester aussi quota restauré mais tâche déjà reprise, retour tardif et état disque modifié depuis checkpoint.

## Limites et recherches restantes

Aucune nouvelle fonctionnalité de contrôle implémentée par cette décision. Valider les API des versions installées et consulter les documents officiels par harness avant commandes actives. Mesurer l'économie par tâche validée, y compris résumés et escalades ; les inventaires locaux ne démontrent ni une économie ni la fiabilité d'un détecteur de boucle.

## Ordre de travail quotas actualisé

Le plafond local quotidien des sollicitations Codex est supprimé. Priorité à une observation fiable et sourcée des quotas par compte/pool, avec portée, fraîcheur et inconnus explicites. Le comportement de reprise dépendant des quotas sera conçu ensuite ; ne pas étendre l’automatisation actuelle avant cette validation. Les contrôles fournisseur et protections de concurrence existants sont conservés.

Les plafonds locaux non-Codex restants doivent également disparaître : lot explicite « remplacer les plafonds arbitraires restants » dans [la roadmap](ROADMAP.md). Déduplication et protection contre les boucles précèdent cette suppression ; connaissance fiable des quotas puis politique de reprise viennent ensuite.

## État quota du 21/10

Le [registre d’observations](QUOTA-KNOWLEDGE.md) distingue désormais route, profil explicite, compte pseudonyme, pool, source, date de lecture et fraîcheur. Il est consultable sans commande d’inférence. La source Codex actuelle est vérifiée en métadonnées ; Claude/DSH et rattachement fiable des journaux restent inconnus. Les politiques du scheduler existant ne consomment pas ce nouveau registre. Les plafonds journaliers mentionnés plus haut ont été retirés pour tous les backends dans `3bae737` ; la réserve fournisseur 5 % demeure.

## Installation et maintenance des intégrations

Le [contrat d’installation autonome](INSTALLATION-CONTRACT.md) définit le paquet indépendant du dépôt, les données durables séparées et les opérations `inspect`, `plan`, `apply`, `verify`, `remove`. Premier parcours complet Harness, puis extension par capacités prouvées. Installation, activation, découverte des outils et résultat vérifié sont des états distincts ; installer une intégration ne lui accorde pas automatiquement l’autorisation d’agir. Chantier planifié, non livré.

L’intégration inclut les consignes du harness pour utiliser ce service de manière économique : découverte, tâches bornées, contexte minimal, réutilisation pertinente et escalade justifiée. Voir l’[exigence d’installation inter-harness](INSTALLATION-CONTRACT.md#usage-inter-harness-depuis-codex-et-les-autres-clients). La décision de déléguer n’est pas systématique ; le coût total et la réussite priment sur un prix de token ou un taux de cache isolé.
