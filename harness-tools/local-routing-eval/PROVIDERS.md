# Fournisseurs Foundation Models : disponibilité et ajout mesuré

État vérifié le **16 octobre 2026**, Mac M3, 16 Go, macOS 27.0.1. Aucun paquet installé, modèle téléchargé, achat ou changement de réglage effectué pour cette recherche.

## Ce qui est réellement disponible

| Surface | Constat | Conséquence |
|---|---|---|
| `/usr/bin/fm respond --help` installé | Affiche uniquement `system`, modèle Apple sur appareil ; `fm available --model pcc` rejette avec exit 64 et demande `system` | PCC n'est pas supporté par le sélecteur de cette CLI installée ; cette vérification n'a demandé aucune génération |
| `fm --model system` | Réponse synthétique confirmée hors sandbox par le pilote ; erreur 1008 observée dans le sandbox | Le moteur fonctionne ; le refus dans cet environnement ne prouve pas une absence de modèle. Aucun contournement automatique |
| Swift FoundationModels du SDK local | `ProviderProbe.swift` compilé et exécuté hors sandbox : System et PCC rapportent `isAvailable=true`, `availability=available` | Disponibilité rapportée pour ce processus ; aucune génération PCC testée, aucun entitlement de compte établi |
| Core AI / MLX | Adaptateurs Swift officiels séparés | Ajouter une dépendance au programme d'évaluation, pas un plugin au binaire système `fm` |
| SDK Python Apple | Interface du modèle système, génération guidée et outils | Aucun moteur supplémentaire établi par le simple ajout du SDK |

Le compilateur local répond Swift 6.4, cible arm64 macOS 27 ; SDK retourné par `xcrun --show-sdk-path` sous Xcode.app. L'interface examinée est `FoundationModels.framework/Modules/FoundationModels.swiftmodule/arm64e-apple-macos.swiftinterface`. La sonde sans dépendance tierce a ensuite été réellement compilée et exécutée hors sandbox : `system_available=true`, `system_availability=available`, `pcc_available=true`, `pcc_availability=available`, `generation_requested=false`. Aucun prompt cloud envoyé. Les adaptateurs Core AI/MLX n'ont pas été compilés ni liés.

La page Apple décrit une API Swift commune, des fournisseurs externes et Dynamic Profiles. Partager un transcript entre profils n'établit aucun partage de cache KV, ni économie facturée. Les utilitaires expérimentaux ne sont pas nécessaires au petit évaluateur actuel. [Présentation officielle](https://developer.apple.com/foundation-models-framework/).

## CLI et PCC : documentation plus large que l'aide locale

La session Apple WWDC26 montre `fm respond --model pcc` et le changement de modèle dans `fm chat`. Notre CLI rejette explicitement `pcc` au contrôle `available`, exit 64 (« Please provide one of system »), sans génération. Son sélecteur installé ne supporte donc pas PCC, malgré la démonstration publique. En revanche, `ProviderProbe.swift` rapporte PCC disponible dans le processus exécuté hors sandbox : le framework et la CLI n'exposent pas ici la même sélection. Cela n'est pas un test de génération PCC et n'établit aucun droit de compte. Ne pas remplacer `system` par `pcc` en cas d'échec. L'aide installée n'expose pas de plafond de tokens : le pilote impose délais et bornes de sortie externes. [WWDC26 : CLI et Python](https://developer.apple.com/videos/play/wwdc2026/334/).

PCC est **distant**. Apple exige pour l'intégration applicative l'App Store Small Business Program, moins de deux millions de premiers téléchargements et l'entitlement attribué au compte. Distribution App Store et tests TestFlight/ad hoc sont décrits ; ces conditions ne prouvent pas l'éligibilité de ce projet ou de la CLI installée. Le SDK expose `PrivateCloudComputeLanguageModel().availability` et `quotaUsage` ; garder les refus et quotas visibles lors d'un éventuel test distinct explicitement autorisé. La disponibilité constatée ne justifie aucune inscription : aucune inscription ni demande d'entitlement n'a été lancée ou n'est nécessaire pour poursuivre le pilote local. [Conditions PCC](https://developer.apple.com/private-cloud-compute/).

## Plus petit candidat local supplémentaire

**Qwen3 0.6B quantifié** est un candidat de test raisonnable, pas une recommandation de qualité : le même petit modèle est explicitement illustré par les deux projets officiels. Comparer deux runtimes sur la même révision des poids, le même tokenizer et une quantification documentée ; des quantifications différentes restent un facteur confondant. Ni taille des poids ni nombre de paramètres ne donnent le pic mémoire complet : KV, activations et compilation s'ajoutent. Le choix reste conditionné au gain sur les cas FR/EN et à la contention avec Harness/Reprise.

### Core AI

Le projet impose macOS/Xcode 27+. Il exporte un dossier `.aimodel` accompagné de tokenizer et ressources ; ce n'est pas un fichier MLX interchangeable. Son installation Python/export peut nécessiter des dépendances et beaucoup plus de mémoire que l'inférence : ne pas lancer par défaut. [Projet officiel](https://github.com/apple/coreai-models).

La recette Qwen3 supporte explicitement 0.6B. Elle fournit `--dry-run`, `--output-dir`, puis l'intégration Swift :

```swift
import FoundationModels
import CoreAILanguageModels
let model = try await CoreAILanguageModel(resourcesAt: modelURL)
let session = LanguageModelSession(model: model)
```

Elle propose aussi `llm-runner` et `llm-benchmark`, exécutables distincts de `fm`. Les exporter/compiler plus tard dans un répertoire propre au projet, sans daemon. [Recette Qwen3](https://github.com/apple/coreai-models/blob/main/models/qwen3/README.md).

Le produit SwiftPM est `CoreAILM`, module `CoreAILanguageModels`. Le manifeste entraîne notamment Transformers et XGrammar : ce coût de maintenance doit être comparé au bénéfice, pas présenté comme une dépendance gratuite. [Package.swift](https://github.com/apple/coreai-models/blob/main/Package.swift). Sur macOS 27.0.1, ne pas sélectionner les presets FP4/FP8 exigeant 27.2 ; commencer par la recette macOS 4-bit compatible et borner le contexte, par exemple 4096 pour ce pilote, après inspection de la configuration résolue. [Catalogue et compression](https://github.com/apple/coreai-models/blob/main/models/README.md).

### MLX Swift

Le bridge `MLXFoundationModels` nécessite le SDK 27 et le trait `FoundationModelsIntegration`. Son exemple emploie `LLMRegistry.qwen3_0_6b_4bit`, puis `MLXLanguageModel` dans `LanguageModelSession`. Sans SDK/trait requis, le module peut être vide. Les capacités sont déclarées explicitement ; une conformité de schéma ne prouve pas la justesse du classement. Pour notre usage, déclarer seulement la génération guidée si nécessaire, sans outils ni vision. [Bridge officiel](https://github.com/ml-explore/mlx-swift-lm/blob/main/Libraries/MLXFoundationModels/README.md).

La macro Hugging Face gère téléchargement et tokenizer : ne pas l'exécuter pour une simple détection de capacité. Pour un pilote ultérieur, préférer des poids locaux explicitement choisis et le chargeur local correspondant ; figer révisions et dépendances, car la branche principale 3.x contient des ruptures d'API. La documentation fournit aussi Gemma 3 1B QAT 4-bit, mais aucun deuxième modèle n'est nécessaire avant résultat utile du premier. [MLX Swift LM](https://github.com/ml-explore/mlx-swift-lm).

#### Prochaine intégration MLX : commande et frontières exactes

Les instructions suivantes sont préparatoires et **n'ont pas été exécutées**. La résolution SwiftPM téléchargerait du code, même sans poids : la faire seulement lors de l'ajout explicite du moteur. Créer un package isolé `harness-tools/local-routing-eval/mlx-probe`, sans modifier celui de Reprise. Son `Package.swift` minimal :

```swift
// swift-tools-version: 6.2
import PackageDescription
let package = Package(
    name: "MLXProbe", platforms: [.macOS("27.0")],
    products: [.executable(name: "MLXProbe", targets: ["MLXProbe"])],
    dependencies: [.package(url: "https://github.com/ml-explore/mlx-swift-lm", exact: "3.32.3")],
    targets: [.executableTarget(name: "MLXProbe", dependencies: [
        .product(name: "MLXFoundationModels", package: "mlx-swift-lm"),
        .product(name: "MLXLLM", package: "mlx-swift-lm"),
        .product(name: "MLXLMCommon", package: "mlx-swift-lm")
    ])])
```

Première source `Sources/MLXProbe/main.swift`, uniquement compilation de l'interface, sans chargement :

```swift
import FoundationModels
import MLXFoundationModels
import MLXLLM
import MLXLMCommon
let _: MLXLanguageModel.Type = MLXLanguageModel.self
print("{\"adapter_linked\":true,\"weights_loaded\":false,\"generation_requested\":false}")
```

Depuis la racine du dépôt, après création explicite de ces fichiers :

```sh
swift package --package-path harness-tools/local-routing-eval/mlx-probe resolve
swift build --package-path harness-tools/local-routing-eval/mlx-probe -c release
swift run --package-path harness-tools/local-routing-eval/mlx-probe --skip-build -c release MLXProbe
```

La documentation principale cite 3.32.3, mais cette recherche n'a pas compilé ce tag : vérifier qu'il exporte bien le bridge avant de figer `Package.resolved`. Si le tag ne le fournit pas, arrêter et épingler après revue une révision officielle qui le fournit ; aucune substitution silencieuse à `main`. Les produits et le trait sont décrits dans le [manifeste officiel MLX](https://github.com/ml-explore/mlx-swift-lm/blob/main/Package.swift).

Ensuite seulement, ajouter le tokenizer local et un chargeur explicite. La vraie surcharge locale est `loadModelContainer(from: URL, using: any TokenizerLoader) async throws -> ModelContainer` ; `LLMModelFactory.shared.loadContainer(from: URL, using: ...)` existe aussi. Elle charge les fichiers présents sans `Downloader`. Dans `MLXLanguageModel(configuration:capabilities:weightsLocation:load:)`, faire pointer **les deux closures** vers le même répertoire canonique de snapshot, avec `ModelConfiguration(directory: ...)`, et refuser les fichiers absents. Importer `MLXLLM` pour enregistrer la factory. Ne pas réutiliser la closure Hugging Face distante de l'exemple. Le tokenizer doit lui aussi refuser tout accès réseau. [Source exacte des chargeurs locaux](https://github.com/ml-explore/mlx-swift-lm/blob/main/Libraries/MLXLMCommon/ModelFactory.swift).

Le bridge contient un cache de modèles partagé **dans le processus** ; pour le pilote garder un seul modèle, un snapshot immuable et un processus privé. Ce cache de poids ne confère aucune autorisation projet et ne constitue aucun cache fournisseur facturé. Les opérations de disponibilité et de chargement sont distinctes. [Source du bridge](https://github.com/ml-explore/mlx-swift-lm/blob/main/Libraries/MLXFoundationModels/MLXLanguageModel.swift).

### Python

`apple-fm-sdk` requiert Python 3.10+, Xcode, macOS 26+ et Apple Intelligence actif sur Mac compatible. L'API documentée est `fm.SystemLanguageModel().is_available()` puis `fm.LanguageModelSession()` et `await session.respond(...)`, avec `@fm.generable` pour les sorties structurées. Le README consulté décrit l'accès sur appareil ; il ne démontre aucun registre Python de fournisseurs MLX/Core AI/PCC équivalent à l'API Swift 27. Ajouter ce paquet uniquement si les sessions persistantes ou l'évaluation Python apportent une mesure impossible avec le subprocess actuel ; il n'est pas requis pour invoquer `/usr/bin/fm`. [SDK Python Apple](https://github.com/apple/python-apple-fm-sdk).

## Procédure réversible avant ajout

1. Finir la baseline système et règles/embeddings sur corpus figé. Identifier une lacune mesurée susceptible de bénéficier d'un autre moteur ; une nouveauté d'API ne suffit pas.
2. Préparer un exécutable Swift séparé sous le projet : entrée/sortie JSON bornées, aucun outil actif, aucune lecture de sessions réelles, aucune indexation globale. Lier d'abord le provider choisi sans charger de poids ; consigner refus, version SDK et dépendances.
3. Avant téléchargement explicite, relever licence, révision immuable, taille des fichiers, espace disponible et mémoire en usage. Autoriser une seule famille de poids et son tokenizer ; définir un répertoire de ressources propre au pilote, sans cache global implicite. Les loaders doivent refuser les ressources absentes plutôt que déclencher un téléchargement silencieux.
4. Charger une seule instance, petite fenêtre et sortie courte ; mesurer RSS/pic mémoire, temps chargement, première réponse, p50/p95, erreurs et pression mémoire avec les applications habituelles ouvertes. Arrêter sur contention ; ne pas modifier les réglages système pour améliorer le score.
5. Comparer qualité FR/EN, sous-classements critiques, omissions, délai complet et maintenance au système existant. Pas d'activation quotidienne ni changement de route automatique. Un résultat négatif conduit à conserver la baseline.
6. Retour arrière : arrêter l'exécutable, supprimer seulement son dossier de build, environnement Python éventuel et ressources consignées dans son manifeste ; restaurer son manifeste SwiftPM si modifié. Ne toucher ni Reprise, ni `/usr/bin/fm`, ni identifiants, ni paramètres globaux.

Cette note prépare l'intégration ; elle ne certifie ni mémoire suffisante, ni vitesse, ni qualité des nouveaux moteurs sur ce Mac. Aucun moteur additionnel n'a été installé ou évalué ici.
