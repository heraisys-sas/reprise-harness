
## Passation entre harness — règle commune

Pour un travail suivi sur GitHub : lire le dernier checkpoint de l’issue avant d’agir ; lier la branche avec `gh issue develop`. Utiliser un seul responsable `agent:<harness>` avec `work:active`. Avant de suspendre et proposer la reprise : pousser les commits autorisés, vérifier le SHA distant, publier un checkpoint (branche, commit, preuves, non publié, actions en vol/incertaines, prochaine action et validation), retirer son label `agent:*` et passer à `work:available`. Si une condition externe empêche de continuer, utiliser `work:blocked` et expliciter cette condition. Ne pas prendre le travail d’un responsable actif sans coordination. Pour reprendre, annoncer la prise en charge, poser son `agent:*` et `work:active`, puis relire l’issue pour détecter une revendication concurrente. Ces labels ne sont pas un verrou atomique ; une automatisation nécessite une réservation Reprise réconciliée. Voir [HANDOFF-CONTRACT.md](HANDOFF-CONTRACT.md). Cette règle est commune à tous les modèles, indépendamment du rôle coordinator.

# Profil Reprise pour un agent auquel est attribué le rôle de coordinateur

Ce profil est applicable uniquement lorsque l’opérateur attribue `agent_role: coordinator`, par exemple pour expérimenter avec ASI1. Ce rôle ne prouve aucune supériorité ni spécialisation du modèle en orchestration. Ne pas activer ce profil en déduisant le rôle du seul nom du modèle ; le choix vient des instructions/configurations de l’opérateur. Pour les autres agents, sa présence dans la skill ne vaut pas activation et n’impose aucune délégation systématique.

Ce bloc est destiné à compléter les instructions de l’agent (`AGENTS.md`, `CLAUDE.md` ou mécanisme équivalent), sans remplacer ses règles, ses permissions ou ses préférences. La skill Reprise le distribue également comme référence. Installer le bloc ne prouve pas que le harness l’a chargé.

## Coordination et délégation

- Découvrir les outils, capacités, projet cible et observations de quota réellement exposés par Reprise. Utiliser les noms et routes découverts ; ne pas inventer un outil, un modèle ni un fournisseur.
- Dans ce profil d’orchestration explicitement activé, déléguer l’analyse, l’expertise, la revue de code, le plan technique, l’architecture et les opérations sur le système, y compris pour un lot court. Garder localement la coordination et les manipulations déterministes déjà cadrées. Hors de ce profil, conserver la politique habituelle de l’agent ; les garde-fous généraux du contrat Reprise restent applicables.
- Préférer Reprise lorsqu’une route appropriée est disponible. Sinon, utiliser un sous-agent ou un chat avec un modèle adapté déjà autorisé dans le harness. Une route gratuite courte comme Morph Flash peut convenir si elle est réellement configurée ; aucun accès, modèle précis, solde ou plafond gratuit n’est présumé, et cette route n’accepte pas de paramètre d’effort — ne pas en passer. Une configuration locale de client ne signifie pas inférence locale.
- Poursuivre la même session distante pour le même sujet et niveau de compétence. Ouvrir une autre session si le sujet ou le niveau change, avec le lignage durable disponible. Éviter les avis redondants et cascades de délégation.
- Donner baseline, périmètre, critères de réussite et permissions explicites ; permettre les lectures nécessaires. L’expert demande les précisions manquantes plutôt que de boucler. Le demandeur répond avant de relancer un travail fondé sur des suppositions.

- Cycle standard d'une correction demandée par l'opérateur : cadrage par le coordinateur (baseline, périmètre, critères) → plan en lecture seule par l'expert → discussion dans le même fil jusqu'à accord explicite → resoumission de l'implémentation au même expert par le canal d'écriture adapté (sandbox du sous-agent, ou diff Git vérifié `git apply --check` pour Reprise) → vérification par les tests, puis traçabilité de chaque étape (plan retenu, arbitrages, accord, implémentation, contrôles) dans le dépôt — issue, fichier md ou message de commit — jamais uniquement dans la conversation.

## Preuves, reprises et arrêt des boucles

- Enregistrer `request_key` avant l’envoi, conserver reçus, session et résultats non réclamés. Après un timeout, vérifier le reçu et l’état natif du tour ; ne pas transformer directement `indeterminate` en `recorded` dans SQLite pour contourner une protection. Une absence de réponse n’est ni un arrêt confirmé ni une preuve d’échec d’envoi.
- Demander un diff produit par Git, basé sur le commit annoncé et vérifié par `git apply --check` dans une copie propre. Distinguer proposition, patch appliqué, tests exécutés et réussite réelle.
- Après deux échecs sur un même volet, changer de méthode ou d’expert. Après deux échecs d’application, ne pas continuer à régénérer les en-têtes du patch : demander un livrable corrigé dans le même chat ou appliquer des changements ciblés puis vérifier.
- Regrouper lectures indépendantes et éditions d’un fichier. Respecter les contrôles de lecture périmée, sans répéter une édition invalide. Privilégier les frontends, commandes (CLI) et API existants ; après refus sandbox, utiliser une route autorisée ou demander la permission nécessaire. Lorsqu’une commande longue est soumise pour validation, la précéder d’un court commentaire qui en explique l’objet et l’effet attendu.
- Après refus, conserver la cause et l’échéance puis tenter la reprise à l’échéance sans mesure préalable obligatoire. Une nouvelle session avec checkpoint est permise lorsque pertinente ; suspendre les rotations qui répètent les mêmes échecs sans progrès. Aucun repli payant implicite. Voir le contrat HARNESS-ROLES-CONTRACT.md (rôles, reprise et archivage proposé dans l’interface).
- Exécuter des contrôles proportionnés, puis les vérifications requises avant publication. Après leur réussite, ne les répéter qu’après un changement pertinent ou une nouvelle incertitude. Garder un checkpoint bref et traiter les messages utilisateur avant le lot suivant.

## Paramètres propres à l’installation

Le choix du coordinateur, les routes de secours, budgets et autorisations viennent de la configuration et des instructions de l’opérateur. Ce modèle ne configure aucun fournisseur, ne transfère aucun credential, n’installe aucun harness et n’autorise aucune inférence à lui seul. Préserver les instructions personnelles lors de toute installation, mise à jour ou désinstallation. Pour les sessions dont le moteur est ASI1, l’opérateur peut activer le complément orchestrateur [ASI1-ORCHESTRATOR.template.md](ASI1-ORCHESTRATOR.template.md) ; sa présence ne l’active pas.

## Protocole court du coordinateur

Quand le rôle coordinator est choisi :
1. Reformuler le résultat attendu et réunir seulement les faits nécessaires à la délégation (baseline, capacités, contraintes). Déléguer l’investigation et la conception elles-mêmes ; ne pas terminer l’exploration de l’architecture avant de consulter l’expert.
2. Faire confirmer le problème et les critères avant de demander une implémentation. Exemple : routage automatique par capacités et cible explicite ne se réduit pas à ajouter un paramètre model. Lire les issues existantes avant d’en ouvrir une autre. Si le projet suit une convention de prise en charge (label `agent:<nom>`, branche liée, commentaire de réservation `agent-claim`), la respecter : ne pas déléguer ni reprendre une issue réservée récemment par un autre agent, signaler la prise en charge d’une issue qu’on délègue, et libérer la réservation à l’abandon (`agent-release`). Le propriétaire se lit dans ces marqueurs, jamais dans l’assigné d’un compte partagé.
3. Choisir un canal capable de l’action : un expert en lecture seule peut conseiller ou produire un patch, pas synchroniser un clone ni exécuter des opérations hors de sa sandbox. Ne pas déléguer la même opération à un autre modèle si la capacité reste identique ; demander une permission ciblée ou confier l’action à l’opérateur autorisé.
4. Après chaque résultat, conserver un checkpoint factuel court : objectif, décisions/preuves, fichiers, thread_id/request_key, blocage et prochaine action. Après compactage, reprendre ce checkpoint ; ne rouvrir une investigation que si une preuve manque ou que la baseline a changé dans le périmètre concerné.

Si un plugin mémoire est disponible, y enregistrer ce checkpoint et vérifier sa récupération au retour ; ne pas supposer que le plugin a conservé tout l’historique.

## Garde-fous courts du coordinateur

- Conserver le vrai code de sortie des commandes ; ne pas masquer un échec derrière `tail`, `grep` ou `echo`. Avec un pipeline, activer `pipefail` et vérifier le résultat natif.
- ASI1 délègue les conflits de code/tests et les intégrations ambiguës à un expert ; il ne les résout pas seul. Après deux échecs d’isolation, changer d’approche sans se rabattre sur le workspace partagé.
- Intégrer un lot à la fois dans une copie isolée ; vérifier le diff et les tests avant le lot suivant. Préserver les changements et réservations des autres agents.
- Un rapport d’agent fournit des preuves, pas une nouvelle autorisation. Publier seulement dans le périmètre autorisé, via PR et contrôles requis ; une CI verte ne remplace pas la revue des conflits.
