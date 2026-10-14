# Routage Apple local — expérimental

## Implémentation

`plugin/` adapte le routeur MIT de NinjaSln-labs (provenance et licence conservées). `subagent_model` avec `model: "auto"` applique des règles de consultation, de petite modification et de risque, puis appelle un helper Swift natif pour les cas restants.

Le helper propose deux moteurs :

- `sentence` (défaut) : `NLEmbedding.sentenceEmbedding`, exemples séparés français/anglais.
- `contextual` : `NLContextualEmbedding`, moyenne des vecteurs de sous-mots, sans entraînement d’un classificateur. Il utilise uniquement les assets déjà disponibles ; aucun téléchargement automatique. Ce moteur peut nécessiter l’accès XPC au service Apple, indisponible dans certains sandboxes.

Les deux moteurs comparent la demande à des exemples annotés. Ils ne mesurent pas directement la difficulté ni la probabilité de succès. Le score est la moyenne des deux meilleurs cosinus par catégorie ; score < 0,40 ou écart < 0,025 → niveau `standard`. Ces seuils sont heuristiques, non calibrés. La reconnaissance linguistique accepte FR/EN seulement ; une langue non reconnue ou une erreur du helper produit un repli `standard`.

Le plugin ne lance aucun LLM de routage, ne modifie pas le modèle principal et ne route que ses délégations. Aucun serveur, service MCP ou processus persistant. Prompts transmis par stdin, aucun stockage sur disque. Cache borné en mémoire (128 décisions), clé par empreinte du texte, du chemin et du moteur ; deux helpers simultanés maximum, délai de 2 secondes. Un changement de binaire ou d’exemples nécessite de redémarrer le profil pour vider le cache. Les vecteurs des exemples fixes sont préparés à la compilation dans un fichier local ne contenant aucun prompt utilisateur, validé par empreinte du catalogue, révision du modèle et dimensions ; un cache absent ou invalide est recalculé. Le texte est limité à 4 000 caractères et le moteur contextuel peut tronquer à 256 sous-mots : classifier des consignes courtes, pas des dépôts ou transcriptions complets.

En mode Apple, `autoProviderOrder` est une **liste autorisée stricte**. Les quatre `autoTierPicks` doivent être explicites ; un candidat absent provoque une erreur au lieu d’une sélection heuristique. Les modèles explicites ou hérités sont également contrôlés. Escalade automatique et changement automatique de fournisseur sont forcés désactivés ; `subagent_recommend` est refusé pour éviter son appel LLM. Les permissions, limites de profondeur et concurrence des agents restent celles de Harness. Les autres outils natifs de délégation ne sont pas interceptés : il ne s’agit pas d’une barrière globale de sécurité du Harness.

## Résultats locaux — 15 octobre 2026

- Compilation Swift et contrôle TypeScript réussis.
- Suite du plugin : **157 tests réussis**, dont les régressions FR, contrôle des routes explicites/héritées, annulation, timeout, cache et utilisation du véritable modèle Apple de phrases.
- Reprise : **22 tests réussis**.
- Test sur le Harness installé avec fournisseur HTTP simulé limité au loopback : `routing-flash → routing-expert → routing-flash`, décision `apple-embedding`, retour `SMOKE_OK`. Aucun appel de modèle externe ; la qualité d’un véritable modèle distant n’est pas évaluée.
- Jeu diagnostique synthétique de 20 demandes : **15/20** classements attendus avec le moteur de phrases, **14/20** avec le contextuel. Latences médianes des appels au helper : **79 ms / 231 ms** sur ce Mac, mesures ponctuelles non garanties. Répétition en cache sous la milliseconde lors du test.

Les erreurs comprennent une demande conceptuelle française sous-classée et des petites tâches surclassées. Ce jeu limité et construit localement ne constitue pas un benchmark indépendant. Les fichiers `evaluation*.json` conservent aussi les erreurs. Aucun gain économique ni supériorité globale par rapport aux règles seules n’est établi. **Pas d’activation dans le profil quotidien à ce stade.**

## Compiler et reproduire

Prérequis : macOS, compilateur Swift, Python 3.13 pour Reprise, Node et pnpm. Les scripts utilisent par défaut les runtimes livrés avec DeepSeek Harness ; `ROUTER_NODE`, `ROUTER_PNPM` et `DSH_CLI` permettent de choisir des exécutables déjà installés. Aucun secret n’est requis par les tests.

```sh
./routing/bootstrap
./routing/build
./routing/test
node routing/evaluate.mjs sentence
node routing/evaluate.mjs contextual
python3 routing/smoke.py
```

Le Node signé de Harness refuse certains modules natifs tiers de Vitest ; le binding WebAssembly de Rolldown permet le test sans changer les protections macOS. Le test smoke crée et supprime un profil temporaire, installe le paquet local hors ligne et démarre un faux fournisseur sur `127.0.0.1`. Le runtime Harness peut demander ses autres dépendances déjà en cache. Aucun compte ou identifiant réel n’est utilisé. Le moteur contextuel ne reçoit aucune instruction de génération ; c’est seulement une extraction de vecteurs.

## Installation dans un profil choisi

Après compilation, `./routing/install NOM_DU_PROFIL` installe le paquet `file:` local. **Ne pas installer conjointement le routeur amont** : il enregistre les mêmes outils. Le patch livré active le moteur local mais laisse les fournisseurs et modèles vides pour refuser toute délégation non configurée ; compléter les listes dans le profil avant utilisation. La sélection de modèles réels et leur authentification doivent être vérifiées séparément. Relancer le profil après une mise à jour.

Exemple de forme (remplacer chaque identifiant par un modèle effectivement vérifié) :

```yaml
- id: subagent-router
  config:
    appleEmbedding: true
    appleEmbeddingEngine: sentence
    autoProviderOrder: [fournisseur-verifie]
    autoTierPicks:
      trivial: [modele-economique-verifie]
      light: [modele-economique-verifie]
      standard: [modele-code-verifie]
      complex: [modele-expert-verifie]
```

`appleHelperPath` permet d’indiquer un exécutable local de confiance ; il n’est jamais fourni par la demande utilisateur. Les noms de modèles ne sont pas utilisés pour estimer leurs prix en mode local. Pour revenir en arrière, retirer uniquement ce plugin du profil et restaurer sa configuration sauvegardée. Reprise n’a pas besoin d’être ouvert.

## Sources

- [NLEmbedding](https://developer.apple.com/documentation/naturallanguage/nlembedding)
- [NLContextualEmbedding](https://developer.apple.com/documentation/naturallanguage/nlcontextualembedding) — Apple recommande NLEmbedding pour la similarité sémantique ; les contextuels peuvent aussi alimenter un classificateur Create ML, ce que cette version ne fait pas.
- [Routeur amont](https://github.com/NinjaSln-labs/dsh-subagent-router), commit `dfadfaf40f44017c5fa7bfa1f62f06cca265ae5e`, MIT.

## Comparaison avec la version d’origine

Même jeu diagnostique, même grille : routeur amont 0.6.0 **12/20**, règles locales seules avec repli standard **12/20**, règles + embeddings de phrases **15/20**, règles + embeddings contextuels **14/20**. Pour FR : amont 6/12 contre 8/12 pour phrases ; pour EN : 6/8 contre 7/8. L’adaptation corrige six cas et dégrade trois cas par rapport à l’amont. Cela ne prouve pas un gain sur les tâches réelles ni un gain de coût.

`evaluation-baseline.json` et `evaluation-rules.json` détaillent les décisions. Le patch `baseline-tests.patch` s’applique au commit amont déjà indiqué (à la place du patch historique), dans un checkout frère nommé `harness-router-test` ; exécuter Vitest avec le filtre `baseline comparison` pour reproduire le résultat amont. Le test ne fait aucun appel LLM. La comparaison des niveaux n’évalue pas la politique d’ancrage au modèle parent, qui peut encore réduire les économies avec la configuration amont par défaut.

## Comparaison entièrement en anglais

Les 20 demandes du même jeu ont été traduites en anglais en conservant les étiquettes attendues. Résultats : amont **15/20**, règles locales seules **11/20**, règles + embeddings de phrases **17/20**, règles + contextuels **12/20**. La version de phrases corrige quatre classements et en dégrade deux par rapport à l’amont, notamment une preuve de terminaison sous-classée et une traduction surclassée.

La traduction retire un désavantage linguistique de l’amont, mais ne crée pas un jeu indépendant : il reste petit, subjectivement annoté et contient des demandes proches. Ce résultat ne suffit pas à établir une supériorité générale ou une rentabilité. Les règles locales seules ne constituent pas une traduction complète des heuristiques amont : leur comparaison ne prouve donc pas que les embeddings sont nécessaires.

Données : `dataset-english.json`, `evaluation-baseline-english.json`, `comparison-english.json`. Pour reproduire, appliquer `baseline-tests.patch` à la copie amont, exécuter son test `baseline comparison` en définissant `ROUTING_DATASET` vers `dataset-english.json` et `ROUTING_BASELINE_OUTPUT` vers `evaluation-baseline-english.json`, puis lancer `node routing/compare-english.mjs`. L’accès au service Apple XPC doit être disponible pour le moteur contextuel. Aucun appel LLM externe.

## Combinaison et validation sur de nouvelles demandes

`appleHybrid: true` (défaut lorsque le routage Apple est activé) utilise l’heuristique amont comme référence. Les règles explicites peuvent la corriger ; sinon, une correspondance Apple n’est retenue qu’avec une marge d’au moins 0,04. Une classification amont complexe n’est pas rétrogradée par les seuls embeddings. Les traductions simples disposent d’un chemin rapide. En cas de panne/ambiguïté, la référence amont est conservée. `appleHybrid: false` permet de comparer la version locale précédente.

Le jeu `dataset-holdout.json` a été rédigé par Astra sans lire les classificateurs ni les jeux existants pendant cette sous-tâche, avec cinq exemples par niveau et par langue. L’agent avait une exposition antérieure au sujet ; les étiquettes restent subjectives. Elles ont été fixées avant l’exécution, et la politique n’a pas été ajustée après observation de ces résultats.

Sur ces **40 nouvelles demandes** : amont **19/40**, local seul **16/40**, hybride **20/40**. Français : amont 7/20 et hybride 7/20 ; anglais : 12/20 et 13/20. Sur l’ancien jeu de 20 demandes anglaises, l’hybride reste à 17/20. Le potentiel théorique de 19/20 obtenu en sélectionnant après coup le meilleur des deux systèmes n’est donc pas atteint. **Le bénéfice de l’hybridation est trop faible pour justifier une activation automatique générale.**

### Latence et optimisation

Mesures locales sans génération LLM, processus Node neuf par variante ; les assets Apple peuvent déjà être chauds dans le système. Les requêtes ont été rejouées avec leur cache de décisions vide, puis répétées en mémoire. Trois passages alternent prototypes pré-calculés et calculés à chaque appel. Les médianes des statistiques de passage sont :

| Mode hybride | Médiane | p95 | Répétition en cache, médiane |
| --- | ---: | ---: | ---: |
| Sans prototypes pré-calculés, Swift optimisé | 69,1 ms | 88,6 ms | 0,012 ms |
| Avec prototypes pré-calculés, Swift optimisé | 40,2 ms | 44,8 ms | 0,011 ms |

L’heuristique amont seule est d’environ 0,01 ms médian dans ce test. Le surcoût observé de l’hybride pré-calculé est donc proche de 40 ms médian sur ce jeu (33 helpers sur 40 cas). Les cas résolus par règles ne lancent aucun helper. Ces durées ne sont pas des garanties ni des mesures du premier démarrage du Mac ; 40 cas et trois passages donnent une indication locale seulement.

Les composants natifs mesurés avec pré-calcul sont d’environ 12,3 ms de préparation, 7,5 ms d’extraction du vecteur et 0,036 ms de comparaison. Les médianes de composants ne s’additionnent pas exactement ; lancement du processus, chargement des frameworks, sérialisation et transport expliquent le reste. Le passage de Swift non optimisé à `swiftc -O` a aussi réduit la comparaison d’environ 3,1 à 0,04 ms sur un passage initial ; ce contraste n’est pas un benchmark de Rust.

Rust n’est pas installé sur le poste et aucun portage n’a été réalisé. Remplacer uniquement nos calculs de similarité ne peut supprimer qu’une fraction de milliseconde mesurée. Les API NaturalLanguage restent natives Apple ; un pont Rust ajouterait de l’interopérabilité et de la maintenance, sans gain démontré sur leur initialisation. On privilégie donc ici Swift optimisé et les prototypes pré-calculés. Un worker persistant pourrait réduire d’autres coûts, mais n’est pas implémenté afin de ne pas ajouter un service résident pour un gain de classement encore insuffisant.

Reproduction après `./routing/build` :

```sh
node routing/benchmark-hybrid.mjs upstream routing/dataset-holdout.json
node routing/benchmark-hybrid.mjs local routing/dataset-holdout.json
node routing/benchmark-hybrid.mjs hybrid routing/dataset-holdout.json
python3 routing/test_prototypes.py -q
```

`benchmark-*.json` et `latency-repetitions.json` conservent les décisions, durées et contre-exemples. Le test de prototypes vérifie l’équivalence, l’absence du texte utilisateur dans le cache, ainsi que les révisions, empreintes et données corrompues. Aucun nouveau fournisseur, LLM de décision, serveur ou agent résident.
