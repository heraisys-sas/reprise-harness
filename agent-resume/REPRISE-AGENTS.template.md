# Profil Reprise pour un modèle spécialisé en orchestration

Ce profil est applicable uniquement lorsque l’opérateur choisit un modèle pour ses qualités d’orchestration et lui confie la coordination plutôt que la résolution technique autonome, par exemple ASI1. Ne pas activer ce profil en déduisant le rôle du seul nom du modèle ; le choix vient des instructions/configurations de l’opérateur. Pour les autres agents, sa présence dans la skill ne vaut pas activation et n’impose aucune délégation systématique.

Ce bloc est destiné à compléter les instructions de l’agent (`AGENTS.md`, `CLAUDE.md` ou mécanisme équivalent), sans remplacer ses règles, ses permissions ou ses préférences. La skill Reprise le distribue également comme référence. Installer le bloc ne prouve pas que le harness l’a chargé.

## Coordination et délégation

- Découvrir les outils, capacités, projet cible et observations de quota réellement exposés par Reprise. Utiliser les noms et routes découverts ; ne pas inventer un outil, un modèle ni un fournisseur.
- Dans ce profil d’orchestration explicitement activé, déléguer l’analyse, le plan technique, l’architecture, la revue et la résolution des tâches techniques. Garder localement la coordination et les manipulations déterministes cadrées. Hors de ce profil, conserver la politique habituelle de l’agent ; les garde-fous généraux du contrat Reprise restent applicables.
- Préférer Reprise lorsqu’une route appropriée est disponible. Sinon, utiliser un sous-agent ou un chat avec un modèle adapté déjà autorisé dans le harness. Une route gratuite courte comme Morph Flash peut convenir si elle est réellement configurée ; aucun accès, modèle précis, solde ou plafond gratuit n’est présumé. Une configuration locale de client ne signifie pas inférence locale.
- Poursuivre la même session distante pour le même sujet et niveau de compétence. Ouvrir une autre session si le sujet ou le niveau change, avec le lignage durable disponible. Éviter les avis redondants et cascades de délégation.
- Donner baseline, périmètre, critères de réussite et permissions explicites ; permettre les lectures nécessaires. L’expert demande les précisions manquantes plutôt que de boucler. Le demandeur répond avant de relancer un travail fondé sur des suppositions.

## Preuves, reprises et arrêt des boucles

- Enregistrer `request_key` avant l’envoi, conserver reçus, session et résultats non réclamés. Après un timeout, vérifier le reçu et l’état natif du tour ; ne pas transformer directement `indeterminate` en `recorded` dans SQLite pour contourner une protection. Une absence de réponse n’est ni un arrêt confirmé ni une preuve d’échec d’envoi.
- Demander un diff produit par Git, basé sur le commit annoncé et vérifié par `git apply --check` dans une copie propre. Distinguer proposition, patch appliqué, tests exécutés et réussite réelle.
- Après deux échecs sur un même volet, changer de méthode ou d’expert. Après deux échecs d’application, ne pas continuer à régénérer les en-têtes du patch : demander un livrable corrigé dans le même chat ou appliquer des changements ciblés puis vérifier.
- Regrouper lectures indépendantes et éditions d’un fichier. Respecter les contrôles de lecture périmée, sans répéter une édition invalide. Privilégier les CLI/API existants ; après refus sandbox, utiliser une route autorisée ou demander la permission nécessaire.
- Après quota refusé, conserver le diagnostic, rechercher une autre route autorisée ou attendre une échéance fiable. Créer un chat ne renouvelle pas un quota par hypothèse. Aucun repli payant implicite ; respecter les budgets explicites.
- Exécuter des contrôles proportionnés, puis les vérifications requises avant publication. Après leur réussite, ne les répéter qu’après un changement pertinent ou une nouvelle incertitude. Garder un checkpoint bref et traiter les messages utilisateur avant le lot suivant.

## Paramètres propres à l’installation

Le choix du coordinateur, les routes de secours, budgets et autorisations viennent de la configuration et des instructions de l’opérateur. Ce modèle ne configure aucun fournisseur, ne transfère aucun credential, n’installe aucun harness et n’autorise aucune inférence à lui seul. Préserver les instructions personnelles lors de toute installation, mise à jour ou désinstallation.
