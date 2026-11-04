# Sonde de métadonnées App Intents pour Siri

Cette sonde construit un bundle de test indépendant, avec une action App Intent et un App Shortcut de démonstration. L'action renvoie un texte fixe ; elle ne lit pas l'état de Reprise. Le script ne lance pas le bundle, ne l'installe pas, ne l'enregistre pas auprès du système et ne modifie pas l'application Reprise.

Depuis le dépôt, choisir un chemin absolu **qui n'existe pas**, dont le parent existe :

```sh
python3 agent-resume/siri_intents_probe.py --output /private/tmp/reprise-siri-exemple
python3 -m unittest discover -s agent-resume/tests -p test_siri_intents_probe.py -v
```

Le répertoire est créé exclusivement ; tout chemin déjà présent, y compris un lien symbolique, est refusé. Un échec laisse les artefacts partiels dans ce nouveau répertoire pour diagnostic et ne les efface pas. Utiliser un nouveau nom pour un nouvel essai. Le script requiert macOS, Xcode et son SDK, ainsi que `swiftc`, `xcodebuild` et `appintentsmetadataprocessor`. Il s'appuie sur la liste de protocoles livrée dans la toolchain active et échoue si elle ne correspond pas au format attendu. Les caches de compilation demandés par la sonde sont créés dans le répertoire de sortie.

Le compilateur Swift produit le binaire et un fichier séparé de constantes extraites ; l'outil Apple produit ensuite `Contents/Resources/Metadata.appintents`. La sonde vérifie que les métadonnées contiennent l'action découvrable, qu'elle ne demande pas l'ouverture de l'application et qu'un raccourci lui est associé. Elle ne passe pas l'option `--force-metadata-output`, qui contournerait les erreurs d'extraction. Le JSON final indique explicitement `installed: false`, `launched: false` et `siri_verified: false`.

Validation locale le 2 novembre 2026 : Xcode 27.1 (27A9275), SDK macOS 27.0 et cible arm64 macOS 26.0 ; extraction et vérification réussies dans `/private/tmp/reprise-siri-probe-script-20261102-1303`. Cela démontre uniquement la compilation et l'extraction statique sur cette configuration. La présence des métadonnées ne prouve pas la découverte dans Raccourcis, ni une interaction Siri. L'étape éventuelle de traitement linguistique, les exigences de signature et la prise en charge d'autres versions de Xcode restent à vérifier. Le prototype n'est pas un assistant Siri opérationnel et n'offre ni données Reprise, ni connecteur MCP.

Apple décrit l'extraction des métadonnées lors de la construction dans [Explore enhancements to App Intents](https://developer.apple.com/videos/play/wwdc2023/10103/) et recommande de valider successivement les intentions, Raccourcis, Spotlight si pertinent, puis Siri dans [Build intelligent Siri experiences with App Schemas](https://developer.apple.com/videos/play/wwdc2026/240/).

## Extension App Intents isolée

Pour reproduire la sonde statique d'une extension distincte ciblant macOS 27 :

```sh
python3 agent-resume/siri_intents_probe.py --extension --output /private/tmp/reprise-siri-extension-exemple
```

L'option construit un `.appex` sans l'embarquer dans une application hôte. Le code déclare `AppIntentsExtension` et limite l'intention à `.appIntentsExtension` via `allowedExecutionTargets` ; le SDK installé expose ce réglage à partir de macOS 27. L'extraction vérifie que `allowedTargets` désigne `com.apple.appintents-extension` et que `openAppWhenRun` est faux. Le fichier Info.plist utilise le point d'extension déclaré par le modèle macOS d'Xcode. Le mode sans cette option reste une application de test ciblant macOS 26.

Le 2 novembre 2026, avec Xcode 27.1 et le SDK macOS 27.0, les deux modes ont compilé et extrait leurs métadonnées en arm64 : `/private/tmp/reprise-siri-extension-script-20261102-1308` et `/private/tmp/reprise-siri-app-script-20261102-1308`. Les six tests Python utilisant des outils simulés ont réussi. Une sonde manuelle précédente de l'extension a aussi établi que son binaire porte une signature ad hoc produite par l'éditeur de liens, sans identité d'équipe ni entitlement visible ; son Info.plist et ses ressources ne sont pas scellés. `codesign --verify --strict` sur cette extension non signée comme bundle complet échoue : ce résultat est attendu et ne constitue pas une validation d'exécution.

Le modèle de cible d'extension macOS d'Xcode active App Sandbox et prévoit un hôte. Il reste à vérifier, avec un bundle hôte distinct dûment configuré, la signature, les éventuels entitlements et le partage de snapshot entre les deux processus. Ni la possibilité d'accéder à Application Support depuis l'extension, ni la découverte dans Raccourcis, ni le déclenchement Siri, ni l'absence d'effet sur une instance réelle n'ont été démontrés. Aucune application n'a été lancée ou installée pour ces essais.
