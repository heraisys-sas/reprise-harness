---
name: devops-engineering
description: Préparer, diagnostiquer et vérifier CI/CD, conteneurs, infrastructure déclarative et opérations cloud, avec contexte explicite, plans et rollback.
---

Déterminer le contexte effectif depuis les fichiers et les commandes de lecture : versions, environnement, compte cloud, région, cluster et namespace. Lire les pipelines et la configuration déclarative. Utiliser les outils déjà adoptés par le projet.

Pour Terraform/OpenTofu, vérifier format, validation et plan avant un apply autorisé. Ne pas appliquer un plan différent de celui examiné. Pour Kubernetes, examiner manifests, diff, rollout et sondes de santé; confirmer le contexte et le namespace. Pour les images et pipelines, vérifier versions épinglées, cache, secrets, reproductibilité et contrôles disponibles sans imposer un nouvel outil.

En incident, établir une chronologie, vérifier les symptômes et changements récents, puis proposer une mitigation limitée et réversible. Séparer hypothèses et faits issus des logs ou métriques. Éviter les retries répétés d'une opération distante dont l'issue est inconnue.

Avant une mutation de production ou une opération destructive, obtenir l'autorisation si elle n'est pas déjà explicite. Préparer sauvegarde si nécessaire, rollback et critères mesurables de succès. Vérifier la santé après l'opération; conserver les éléments utiles au diagnostic sans secrets.

Consulter les documents officiels et RFC applicables. Charger `scientific-evidence` pour les garanties de cohérence, consensus, tolérance aux pannes, files d'attente ou conclusions de performance qui nécessitent une preuve théorique ou empirique.
