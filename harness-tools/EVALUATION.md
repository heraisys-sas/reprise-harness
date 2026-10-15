# Prototype local — 15 octobre 2026

MacBook Air M3, 16 Go, SDK macOS installé dans Xcode. Le swiftinterface FoundationModels expose `LanguageModel`, `PrivateCloudComputeLanguageModel` et les sessions. `fm respond --help` confirme le modèle `system` sur appareil. Un appel synthétique réel à `fm` échoue avec `ModelManagerServices.ModelManagerError 1008` ; disponibilité de la génération non établie. Aucun modèle supplémentaire téléchargé. Aucune comparaison de qualité Core AI/MLX n'a été exécutée : leurs runtimes ne constituent pas un gain démontré pour ce besoin, et le système Apple existant suffit pour évaluer la sélection d'extraits.

## Résultats et décision

49 tests Reprise passent sans modifier ses sources. 15 tests outils passent, dont six sous-cas FR/EN indépendants de conservation de preuve/consignes et un cas de longue ligne. Sept tests du routeur passent, dont un test du handler réel : snapshot stable après modification de configuration, arrêt dynamique, libération à `agent/disposed`. Le smoke lance Harness 0.2 contre un fournisseur HTTP local simulé : planner → executor → planner, zéro requête modèle externe. La version quotidienne n'est pas modifiée.

Huit requêtes de développement sur sept fichiers du dépôt, budget d'extraits 6 000 caractères, instructions conservées séparément :

| Sélection | Preuve attendue retrouvée | Extraits moyens | Temps moyen |
|---|---:|---:|---:|
| Lexicale initiale, mots entiers | 5/8 | variable | ~2 ms |
| Lexicale avec identifiants découpés | 6/8 | 4 852 caractères | 5,6 ms |
| Embeddings Apple + lexical, batching corrigé | 5/8 | 5 822 caractères | 2,33 s |

Corpus source : environ 37 800 caractères, hors instructions. La réduction d'extraits n'est ni une réduction de tokens mesurée ni une économie facturée. Les références et JSON ajoutent du volume ; les consignes restent intégrales. Deux cas lexicaux échouent : `quota usage_limit_exceeded` (cible attendue discutable : le traitement de cette erreur est ailleurs) et `scheduler reprise permission` (preuve trop bas classée). Ces cas restent visibles dans le script, sans corriger a posteriori les cibles pour augmenter le score. Les huit cas ont servi au développement ; ils ne sont pas un jeu indépendant de qualité finale. Les six nouveaux cas synthétiques vérifient des contrats, pas la résolution de tâches de programmation.

L'exécutable Embedding existant traite au maximum 100 textes : le prototype découpe désormais les appels en lots de 80. Le tableau rapporte la nouvelle exécution après cette correction ; les vecteurs sont disponibles pour les huit requêtes. Le mode sémantique reste optionnel. La sélection lexicale suffit pour un dossier consultatif ; le rappel imparfait interdit de remplacer le contexte complet automatiquement. Les sources et les omissions doivent être revues avant action.

Instrumentation : le smoke vérifie aussi deux steps racine et 20 tokens de sortie simulés à travers la sortie JSON du vrai processus Harness. Import explicite des événements natifs SessionEvent de Harness 0.2. Compteurs provider/cache, sorties/raisonnement exposés, tentatives échouées et durée de step sont conservés. Le parseur ne reconstitue pas les échecs invisibles dans l'adaptateur. Coûts facturés et validation de tâche restent inconnus sans observations de facturation et preuves de commandes fournies. Aucune requête payante réalisée, aucun gain €/tâche validée démontré ; budget mensuel non consommé par le prototype. Ce manque interdit d'activer un nouveau routage économique automatiquement.

## Sources vérifiées

- [DeepSeek, Context Caching](https://api-docs.deepseek.com/guides/kv_cache/) : préfixes exacts persistés, cache best effort, champs `prompt_cache_hit_tokens` et `prompt_cache_miss_tokens`. Le hash local ne prouve aucun hit.
- [Apple, Foundation Models](https://developer.apple.com/foundation-models-framework/) : modèle sur appareil et fournisseurs ; disponibilité d'une API ne prouve pas celle du modèle sur cette machine.
- [apple/coreai-models](https://github.com/apple/coreai-models) et [mlx-swift-lm](https://github.com/ml-explore/mlx-swift-lm) : sources officielles consultées ; aucun runtime ajouté.
- Zha, Xu, Wu, Dong, Li (2026), [CacheRouter](https://arxiv.org/abs/2608.22708), prépublication : séparation découverte d'outils/préfixe stable sur leur prototype. Aucun transfert de leurs résultats chiffrés à ce système.
- Cheng (2026), [CacheRoute](https://arxiv.org/abs/2608.19677), prépublication : placement de préfixes côté infrastructure, avec contre-exemples. L'API cliente ne donne pas ici le contrôle des serveurs ; les gains de leur cluster ne sont pas applicables directement.

Installation et retour arrière dans README.md. Dépôt uniquement ; aucun changement de réglage global, d'authentification ou de contrôle de confiance.
