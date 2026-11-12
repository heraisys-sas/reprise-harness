---
name: scientific-evidence
description: Rechercher et vérifier les publications académiques nécessaires à une décision scientifique, statistique, ML/IA, cryptographique ou algorithmique, puis relier les preuves à l'implémentation.
---

Formuler la question technique et les critères de décision avant de chercher. Utiliser `web_search` pour trouver des travaux primaires pertinents, puis `web_fetch` pour consulter la page éditeur, le DOI, le texte public ou la prépublication. Les outils natifs DeepSeek suffisent; ne pas supposer qu'un connecteur ChatGPT est disponible dans Harness.

Pour rechercher ou vérifier les métadonnées Crossref, appeler via le shell `python3 ~/.dsh/skills/scientific-evidence/scripts/paper_lookup.py --query "question" --limit 5` ou `--doi "10.xxxx/identifiant"`. Cet outil utilise l'API publique Crossref, retourne un JSON compact et ne nécessite aucune clé. Il vérifie l'existence des métadonnées, pas le résultat scientifique ni l'évaluation par les pairs; consulter ensuite le texte. Une absence dans Crossref ne prouve pas l'absence d'une publication. Ne transmettre aucune donnée confidentielle dans une requête.

Privilégier articles originaux, conférences et revues du domaine; utiliser des revues systématiques lorsque la question porte sur un ensemble de résultats. Chercher les travaux fondateurs et les résultats récents lorsque nécessaires. arXiv, HAL, OpenReview et dépôts universitaires peuvent donner accès au texte; préciser le statut d'évaluation par les pairs. La documentation officielle reste la référence pour l'API et sa version.

Vérifier titre, auteurs, année, DOI ou URL, statut éditorial et contenu qui soutient la conclusion. Ouvrir les références citées; ne pas déduire un résultat depuis le seul titre ou un extrait de moteur. Si seul le résumé est accessible, le signaler et limiter la conclusion. Ne pas inventer d'identifiants ni contourner les restrictions d'accès.

Évaluer les hypothèses, datasets, baselines, métriques, tailles d'effet, incertitudes, reproductibilité et limites selon le sujet. Pour une garantie théorique, vérifier ses préconditions; pour un benchmark, vérifier sa comparabilité au workload du projet. Rechercher les résultats contradictoires si la décision est importante ou controversée.

Présenter la conclusion applicable, les preuves vérifiées, leurs limites et le choix concret de code ou d'infrastructure. Citer près de l'affirmation : titre, auteurs, année et DOI ou URL. Distinguer fait établi, prépublication et inférence. Proposer un test local pour vérifier le transfert au projet lorsqu'il est possible. Ne pas lancer une revue exhaustive quand quelques sources ciblées suffisent.
