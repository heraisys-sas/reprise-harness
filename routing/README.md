# Routage Apple local — expérimental

## Implémentation

`plugin/` adapte le routeur MIT de NinjaSln-labs (provenance et licence conservées). `subagent_model` avec `model: "auto"` applique des règles de consultation, de petite modification et de risque, puis appelle un helper Swift natif pour les cas restants.

Le helper propose deux moteurs :

- `sentence` (défaut) : `NLEmbedding.sentenceEmbedding`, exemples séparés français/anglais.
- `contextual` : `NLContextualEmbedding`, moyenne des vecteurs de sous-mots, sans entraînement d’un classificateur. Il utilise uniquement les assets déjà disponibles ; aucun téléchargement automatique. Ce moteur peut nécessiter l’accès XPC au service Apple, indisponible dans certains sandboxes.

Les deux moteurs comparent la demande à des exemples annotés. Ils ne mesurent pas directement la difficulté ni la probabilité de succès. Le score est la moyenne des deux meilleurs cosinus par catégorie ; score < 0,40 ou écart < 0,025 → niveau `standard`. Ces seuils sont heuristiques, non calibrés. La reconnaissance linguistique accepte FR/EN seulement ; une langue non reconnue ou une erreur du helper produit un repli `standard`.

Le plugin ne lance aucun LLM de routage, ne modifie pas le modèle principal et ne route que ses délégations. Aucun serveur, service MCP ou processus persistant. Prompts transmis par stdin, aucun stockage sur disque. Cache borné en mémoire (128 décisions), clé par empreinte du texte, du chemin et du moteur ; deux helpers simultanés maximum, délai de 2 secondes. Un changement de binaire ou d’exemples nécessite de redémarrer le profil pour vider le cache. Le texte est limité à 4 000 caractères et le moteur contextuel peut tronquer à 256 sous-mots : classifier des consignes courtes, pas des dépôts ou transcriptions complets.

En mode Apple, `autoProviderOrder` est une **liste autorisée stricte**. Les quatre `autoTierPicks` doivent être explicites ; un candidat absent provoque une erreur au lieu d’une sélection heuristique. Les modèles explicites ou hérités sont également contrôlés. Escalade automatique et changement automatique de fournisseur sont forcés désactivés ; `subagent_recommend` est refusé pour éviter son appel LLM. Les permissions, limites de profondeur et concurrence des agents restent celles de Harness. Les autres outils natifs de délégation ne sont pas interceptés : il ne s’agit pas d’une barrière globale de sécurité du Harness.

## Résultats locaux — 15 octobre 2026

- Compilation Swift et contrôle TypeScript réussis.
- Suite du plugin : **149 tests réussis**, dont les régressions FR, contrôle des routes explicites/héritées, annulation, timeout, cache et utilisation du véritable modèle Apple de phrases.
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
