---
name: agent-orchestration
description: Déléguer les lots indépendants, garder des modèles stables et borner les corrections pour minimiser le coût des tâches validées.
---

Définir un résultat observable, puis choisir le minimum d’agents nécessaire. Le parent reste responsable de l’intégration. Répartir les fichiers entre enfants ; ne pas faire modifier les mêmes fichiers simultanément.

Avant une délégation, consulter `list_subagent_models` et vérifier l’authentification. Si un routeur par rôles est actif, sa configuration prime : ne pas croire qu’une sélection explicite d’expert sera respectée dans un exécutant forcé sur Flash. Faire remonter au parent les preuves d’un besoin d’expertise.

Prompt d’un lot : contraintes stables, objectif, fichiers, contrats à préserver, critères de réussite et commandes de validation. Un pseudo-code compact peut transmettre l’algorithme, mais doit conserver les invariants et les cas limites. Ne pas traduire automatiquement en chinois pour économiser : mesurer les tokens du modèle et la qualité d’exécution d’abord. L’anglais technique convient aux délégations ; la restitution utilisateur reste en français.

Préférer `subagent` pour un travail autonome et `send_message` pour sa suite. Réserver `subagent_fork` au besoin réel de l’historique. Paralléliser uniquement les travaux indépendants ; conserver trois sous-agents actifs et deux niveaux maximum. Deux cycles de correction au plus avant réévaluation ; pas de Ralph sans demande et budget.

Demander un retour compact : résultat, fichiers, contrôles réussis/échoués/indisponibles et décision attendue. Le parent n’a pas à relire tous les logs ni à refaire chaque contrôle sans changement pertinent. Conserver le contexte utile du même agent ; ne pas ajouter des appels uniquement pour le cache. Mesurer tous les coûts et erreurs de la tâche terminée, y compris délégations, corrections et revue experte.
