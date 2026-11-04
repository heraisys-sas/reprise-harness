---
name: code-engineering
description: Implémenter, corriger et vérifier du code dans un dépôt existant, avec reproduction des bugs, tests ciblés et respect des conventions du projet.
---

Inspecter les instructions du dépôt, l'état Git, les manifestes, le lockfile et les commandes CI avant de choisir les outils. Rechercher les fonctions et patterns existants. Ne pas remplacer les changements de l'utilisateur.

Exploiter la bibliothèque standard et les dépendances déjà adoptées avant d'en ajouter. Préférer des bibliothèques peu nombreuses, stables et réutilisables; comparer maintenance, compatibilité, licence et coût transitif. Éviter les doublons et les wrappers sans bénéfice. Garder le code et les commentaires concis, sans sacrifier la clarté ni les garanties d'un composant éprouvé.

Pour un bug, reproduire le symptôme, isoler la cause et vérifier que le correctif fait disparaître la reproduction. Ajouter un test de régression lorsqu'il protège un comportement significatif. Pour une fonctionnalité, identifier les interfaces, les cas limites et les contraintes de compatibilité; implémenter le plus petit changement cohérent.

Refactoriser localement quand une duplication, une branche inutile ou un nom ambigu peut être corrigé à faible coût. Préserver comportement et interfaces, vérifier le diff et les tests concernés. Éviter une refonte qui élargit le périmètre. Charger `security-checks` pour sélectionner les contrôles adaptés plutôt que multiplier les revues génératives.

Exécuter les contrôles existants les plus pertinents, puis élargir si le périmètre ou un échec le justifie. Examiner le diff final pour les erreurs, secrets, dépendances et modifications accidentelles. Annoncer précisément les tests exécutés et leurs résultats.

Consulter la documentation officielle des versions concernées pour les API incertaines. Charger `scientific-evidence` lorsqu'un choix dépend de résultats scientifiques ou de garanties algorithmiques. Une amélioration de performance exige un benchmark pertinent, des conditions comparables et une explication des limites.
