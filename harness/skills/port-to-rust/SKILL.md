---
name: port-to-rust
description: Porter efficacement Python ou JavaScript/TypeScript vers Rust, progressivement, avec contrats explicites, tests différentiels, dépendances limitées et benchmarks comparables.
---

Inspecter les sources, tests, interfaces, formats, dépendances et environnements de déploiement. Identifier l'objectif du portage : performance, mémoire, fiabilité ou distribution. Profiler le programme de référence avant une optimisation. Choisir un module ou chemin critique isolable plutôt qu'une réécriture globale.

Capturer les contrats observables : entrées/sorties, erreurs, ordre, sérialisation, précision numérique, effets de bord et comportements limites. Constituer des fixtures depuis les tests et des cas représentatifs; comparer automatiquement l'original et Rust sur les mêmes entrées. Définir explicitement les tolérances flottantes et les différences voulues. Les fixtures ne prouvent pas à elles seules l'équivalence générale.

Points à vérifier selon le langage :
- Python : entiers arbitraires contre entiers bornés, division et modulo négatifs, Unicode et slicing, mutabilité/aliasing, exceptions, itérateurs paresseux, ordre des dictionnaires et conventions de None.
- JavaScript : Number et précision entière, BigInt, null/undefined, coercitions et truthiness, chaînes UTF-16, ordre des propriétés, exceptions, promesses et ordre des effets asynchrones. Préserver la sémantique du contrat plutôt que traduire mécaniquement chaque instruction.

Concevoir des types Rust explicites et idiomatiques. Utiliser Result et Option pour les cas attendus; éviter unwrap/panic sur les données externes. Préférer l'emprunt lorsque simple, accepter une copie justifiée et mesurée plutôt que compliquer les durées de vie. Éviter unsafe et la concurrence prématurée; isoler et justifier tout unsafe réellement nécessaire avec tests adaptés.

Choisir l'intégration selon le produit : binaire CLI ou protocole existant si cela simplifie le déploiement; extension Python via PyO3/maturin, module Node via napi-rs, ou WASM seulement si le besoin le justifie. Vérifier la documentation officielle et les versions compatibles avant de choisir. Réutiliser les bibliothèques existantes, préférer std et quelques crates maintenues. Ne pas ajouter simultanément plusieurs frameworks d'intégration.

Porter par incréments avec façade stable et possibilité de retour à l'implémentation originale. Garder l'original comme oracle tant que les contrats ne sont pas validés. Séparer correction des écarts et optimisations. Automatiser parsing, conversion de fixtures et comparaison avec des scripts déterministes lorsque cela économise réellement du travail.

Contrôles : cargo fmt --check, cargo clippy selon la politique du projet, cargo test et tests différentiels. Vérifier les cibles de compilation et l'intégration Python/Node lorsque concernées. Charger security-checks pour choisir un audit de crates ou des contrôles supplémentaires pertinents; ne pas installer plusieurs outils redondants.

Benchmarker Rust en release avec même workload, résultats vérifiés, échauffement si pertinent et plusieurs répétitions. Inclure les coûts FFI, sérialisation, démarrage et copies dans la mesure de bout en bout. Rapporter temps, mémoire et limites; distinguer microbenchmark et gain utilisateur. Ajouter Criterion seulement si des mesures répétées justifient cette dépendance.

Pour une tâche importante, charger agent-orchestration : analyse des contrats et préparation des fixtures peuvent être parallèles; un agent porte un périmètre de fichiers défini, un autre vérifie indépendamment les écarts. Utiliser un modèle léger pour extraction mécanique et tests, escalader pour ownership complexe, concurrence ou divergences subtiles. Ne pas déléguer chaque fonction ni recopier le dépôt complet dans les prompts. Pour les algorithmes scientifiques spécialisés, charger scientific-evidence.

Livrer le module intégré, les comparaisons exécutées, les différences résiduelles, les dépendances ajoutées et les mesures. Ne déclarer le portage terminé que lorsque les critères fonctionnels et de déploiement convenus sont satisfaits.
