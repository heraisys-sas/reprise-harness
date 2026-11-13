# Passation — orchestrateur Apple Intelligence et entrée Siri

État au 13 novembre 2026. Coordination : issue [#144](https://github.com/heraisys-sas/reprise-harness/issues/144), branche `codex/144-siri-demande`. Vérifier le dernier commentaire de l'issue, les réservations et les SHA distants avant d'agir ; d'autres agents travaillent sur le dépôt.

## Objectif et décisions

Construire un orchestrateur utilisable dans Reprise : compréhension de la demande, clarification utile, suivi, correction et arrêt, contrôle humain, résultat et sources. Siri est un point d'entrée, pas le périmètre complet. Utiliser les fonctions natives Apple disponibles, notamment Foundation Models et NaturalLanguage ; privilégier le local. N'envisager une route Apple distante que si sa disponibilité et son absence de coût sont démontrées ; aucun changement de fournisseur ni repli API payant implicite.

La mémoire appartient à Reprise, avec périmètres utilisateur/projet/travail, provenance et capture sélectionnée par l'opérateur. Ne pas aspirer automatiquement les conversations. L'objectif reste un échange réel entre deux harness : capture explicite par A, lecture et provenance par B dans le même projet, refus depuis un autre projet, puis suppression du seul canari via l'API. Les essais à base de doubles ne prouvent pas ce parcours.

## État du dépôt et preuves

- `dev` et la branche Siri pointaient tous deux sur `3e8ed2f532a42281a95bba44e925a66d470509dd` avant ce checkpoint. Le lot inbox a été fusionné dans `dev` ; cette passation est publiée ensuite sur la branche liée à #144. Revérifier les SHA et ne pas écraser les évolutions concurrentes.
- Lot inbox : modèles/stockage/CLI locaux, chiffrement avec clé existante, contrôle ACL, identités stables et refus bornés. Les modules sont empaquetés mais **pas raccordés à App Intents ou à l'interface**. Lire [SIRI-INBOX-WORKING-NOTE.md](SIRI-INBOX-WORKING-NOTE.md), notamment les limites de durabilité et de confidentialité.
- Validation du lot sur base `eebb39d` : **1 923 tests OK, 5 ignorés**, avec le CPython embarqué ; compilation native isolée réussie, revue indépendante du coffre et du packaging sans blocage identifié. Les tests n'ont pas prouvé l'accès au Trousseau réel ni une invocation Siri.
- Sonde isolée macOS 27 : extension synthétique compilée, métadonnées et signature ad hoc vérifiées ; après inscription temporaire de l'hôte et de l'extension, PlugInKit voit cette dernière. Les deux inscriptions ont été retirées. L'exécution de `perform`, du helper, et Siri/Raccourcis restent **non démontrées**. Le moniteur ExtensionFoundation testé ne convient pas aux points App Intents système. Le SDK local inspecté ne contenait pas le framework bêta AppIntentsTesting : revérifier avant d'en dépendre.
- Foundation Models local : générations et essais d'appels d'outils synthétiques ont été observés ; ils ne qualifient pas le modèle comme orchestrateur général. Les preuves et limites sont dans [APPLE-INTELLIGENCE-INTEGRATION.md](../docs/integrations/apple/APPLE-INTELLIGENCE-INTEGRATION.md). Recherche sémantique : `swift/Embedding.swift` utilise NaturalLanguage/NLEmbedding ; ne pas la confondre avec la recherche de la mémoire partagée.
- [Évaluation documentaire Claude-Mem](../docs/integrations/CLAUDE-MEM-EVALUATION.md) : source canonique Reprise conservée ; capture automatique et stockage Claude-Mem standard ne sont pas adoptés. Compatibilité DSH et adaptation sans seconde autorité restent à démontrer.

## Installation et services — état observé, à recontrôler

- Un bundle autonome basé sur `eebb39d` a été construit et vérifié dans `reprise/builds/304e8deed92de82987ccd4df18f4cf846b1832ba6174158778992eacb93a111c/Reprise.app`. Il précède le lot Siri ; reconstruire depuis le `dev` actuel pour le distribuer.
- L'installation `/Applications/Reprise.app` est antérieure. L'opération `9f64c62b-1ce0-47b1-a6c2-a5e9a3961c55` est `applied` mais son `verify` a refusé une dérive du bundle : 92 caches `.pyc` supplémentaires et trois `.pyc` attendus modifiés lors du dernier inventaire. Le reçu est sous `~/Library/Application Support/Reprise/maintenance/updates/…/receipt.json`. Ne pas modifier le reçu, forcer son état, créer une nouvelle tentative pour contourner le refus, ni supprimer des fichiers sans preuve et parcours de reprise revu.
- Une réparation ciblée autorisée a été tentée deux fois ; les deux préflights ont refusé **avant mutation**. Le second refus concernait la reproduction octet à octet d'un cache. Ne pas réexécuter les scripts inchangés ; diagnostic en cours sur la sérialisation `marshal`. Les prototypes dans `/private/tmp` ne sont pas qualifiés pour une nouvelle application réelle sans revue.
- Reprise a été relancée et Quick Apply redémarré depuis le bundle indépendant `304e8…` ; le socket local acceptait une connexion à la dernière vérification. Cet état ne prouve pas une livraison ni un échange MCP. Recontrôler les processus et l'activité avant tout arrêt. Le bouton d'arrêt authentifié échoue actuellement ; voir [#157](https://github.com/heraisys-sas/reprise-harness/issues/157). Ne pas arrêter `codexd` ni perturber les chats.
- L'utilisateur a autorisé une réparation temporaire avec arrêt et relance de Reprise/Quick Apply, et demandé **aucune sauvegarde** de l'ancien bundle. Cela ne supprime pas les contrôles d'identité, de quiescence, de reprise et de vérification. L'accès à l'installation hors workspace reste soumis aux permissions effectives.

## Mémoire inter-harness et intégration DSH

Lors de la dernière inspection, les intégrations mémoire DSH et Claude étaient `off`, toutes deux associées au même projet enregistré ; aucun projet témoin distinct n'était enregistré. Les plans et sondes directes ne prouvent pas le chargement des outils par les clients. La cible de reprise DSH autorisée par l'utilisateur est le workspace actuel `workspaces/harness` ; l'option `--codex-workspace-write` est voulue.

La réconciliation du bloc DSH a refusé le fichier `cordis.patch.yml` portant `UF_TRACKED`. Ne pas effacer ce flag ou réécrire manuellement le YAML. Sur fixtures temporaires, `os.replace` a changé l'identité documentaire malgré le flag ; `FileManager.replaceItemAt` a conservé l'identité et les métadonnées testées, sans preuve de durabilité en cas de panne. Une correction gérée nécessite conception, revue et tests ; coordonner avec le travail existant [#145](https://github.com/heraisys-sas/reprise-harness/issues/145).

## Prochaines actions bornées

1. Relire les issues #144, #145 et #157, l'état réel des services, le reçu et les commits distants. Conserver tous états et données utilisateurs ; ne pas rejouer une action incertaine.
2. Résoudre la vérification du bundle par une méthode démontrée et reprenable, puis construire et vérifier le bundle du `dev` courant avant déploiement. Tests avec le CPython embarqué, sans basculer vers un fournisseur ou une facturation API.
3. Concevoir le récepteur App Intents isolé et l'import explicite d'un brouillon sans écraser le travail existant ni lancer d'inférence ; prouver découverte et exécution sur données synthétiques, puis seulement le parcours Siri réel. Les paramètres techniques et les choix automatiques ne doivent pas encombrer l'interface.
4. Activer les intégrations mémoire uniquement via leurs plans gérés compatibles, constater les catalogues réellement chargés, puis faire le canari inter-harness et le témoin projet. Conserver UUID d'opération, provenance et reçus ; ne jamais modifier directement SQLite pour franchir une protection.

Le POC complet, la validation réelle multi-harness et l'installation du dernier bundle restent ouverts ; ne pas les déclarer terminés sur la seule foi de tests synthétiques.
