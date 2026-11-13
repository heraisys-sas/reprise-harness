# Entrée Siri — chantier #144

## Reprise sur dev — 2026-11-13

Copie de travail reportée sans conflit sur `eebb39d57d0415577b8ed921e0bf173bf17c3262` ; le worktree précédent reste intact. Les 42 tests Siri ciblés passent avec le CPython embarqué. La suite complète de cette baseline, sans le lot Siri, passe avec 1 887 tests et 5 ignorés. La suite complète du lot Siri sur la baseline précédente avait passé 1 912 tests et 5 ignorés.

Sonde isolée App Intents sur macOS 27 : compilation, métadonnées et signature ad hoc vérifiées. Hors sandbox, l'inscription PlugInKit a retourné succès, mais la recherche ciblée n'a trouvé aucune extension et l'hôte de découverte a expiré ; le retrait a réussi et la recherche après retrait n'a rien trouvé. Ni invocation `perform()`, ni helper, ni Siri ne sont donc démontrés. Cette sonde n'a pas utilisé de Trousseau ou de contenu utilisateur.

Une seconde sonde a d'abord inscrit l'hôte synthétique dans Launch Services, puis l'extension dans PlugInKit : la recherche ciblée de l'extension a alors retourné une correspondance. L'attente de l'hôte a expiré ; l'extension et l'hôte ont été retirés, puis la recherche PlugInKit a confirmé l'absence de correspondance. Cela prouve une inscription temporaire, pas la découverte par Siri ni l'exécution de `perform()`.

Baseline `e0c2d0d` ; la branche isolée précède le lot UI `4f0735d` et devra être intégrée après revalidation de dev.

Décisions : les demandes Siri ne lancent pas de modèle. Identité stable, refus d'une réutilisation avec texte différent, suppression logique sans résurrection ; stockage local privé chiffré par le coffre avec usage `siri-inbox-v1`, clé préexistante obligatoire, pas de repli en clair. Une erreur de synchronisation après remplacement conserve un état incertain ; seule une répétition de la même identité permet de réconcilier.

Prototype en cours, non exposé à l'application : modèle, stockage local, helper du coffre et canal local CLI avec répertoire d'état explicite et requêtes bornées sur stdin. Ce canal sépare dépôt, listing, lecture et rejet et ne lance aucun service ni modèle. Essais synthétiques avec clé de test : contrat Swift AES-GCM, usages distincts, absence du texte en clair dans le fichier, état corrompu, lien symbolique, remplacement incertain et six processus simultanés. Pas d'accès au Trousseau ni aux données de l'utilisateur.

Un marqueur d'initialisation durable fait échouer la lecture si le registre ou le marqueur seul disparaît ; une interruption entre création du marqueur et première publication nécessite une récupération manuelle et ne réinitialise pas la boîte. Ce mécanisme ne détecte pas la suppression simultanée des deux fichiers ni le remplacement par un ancien registre authentique. Le verrou est coopératif et ne protège pas d'un autre processus du même utilisateur qui remplace les fichiers ou ignore ce verrou.

Reste : revue indépendante sécurité et durabilité (y compris permissions étendues, suppression ou remplacement des fichiers, verrouillage, plantages et reprise), vérification des limites et des rejets, contrat d'initialisation, intégration App Intent et import explicite dans l'interface, validation isolée puis validation Siri réelle. Le démarrage normal de l'application peut terminer une instance homonyme et déclencher des services ; il ne doit pas être utilisé comme récepteur passif sans modification et preuve. La présence de métadonnées App Intent dans un bundle de test ne prouve pas que Siri l'exécute. Aucun déploiement ni appel Siri réel effectué. Revue demandée au fil expert `01a1ceef-6379-79a2-89ba-46273a954ec5` : refus de quota enregistré (`siri-vault-contract-review-144-20261112`) ; pas de repli fournisseur.

## Correction ciblée après revue indépendante — 2026-11-12

Copie de reprise : `tmp/issue-144-rebased`, baseline `db8773b` ; copie originale préservée.

- Une erreur de nettoyage du fichier temporaire ne masque plus l'erreur principale, notamment `WriteUncertain` après publication. Le retry conserve la même identité ; un test vérifie qu'il ne crée pas une seconde entrée.
- Refus CLI distincts, sans message privé : `identity_conflict`, `capacity_exceeded`, `not_found`. Les états existants `write_uncertain`, `registry_missing`, `invalid_request` et `unavailable` sont conservés. Les réponses d'échec n'incluent que le code fermé.
- Les timestamps sont bornés entre 0 et 253402300799 secondes Unix (9999-12-31 23:59:59 UTC). Le contrôle de plage précède toute conversion flottante ; un entier gigantesque est refusé par `ValueError`, sans `OverflowError` et sans modifier l'état.

Commandes exécutées depuis la racine de cette copie, avec le CPython embarqué (aucun Trousseau, modèle ou lancement de Reprise) :

```
/Applications/Reprise.app/Contents/Resources/runtime/bin/python3.13 -B -m unittest discover -s reprise/tests -p test_siri_inbox_model.py -v
/Applications/Reprise.app/Contents/Resources/runtime/bin/python3.13 -B -m unittest discover -s reprise/tests -p test_siri_inbox_cli.py -v
/Applications/Reprise.app/Contents/Resources/runtime/bin/python3.13 -B -m unittest discover -s reprise/tests -p test_siri_inbox_store.py -v
```

Résultats : modèle 6 tests OK (0,011 s), CLI 5 tests OK (0,005 s), stockage 10 tests OK (0,346 s), soit 21 tests ciblés. La suite complète et l'intégration Siri ne sont pas validées par ce lot.

### ACL : proposition initiale, remplacée par le lot implémenté ci-dessous

Contrôler les ACL sur les mêmes descripteurs déjà ouverts et validés par `fstat` via un petit helper macOS utilisant `acl_get_fd_np(fd, ACL_TYPE_EXTENDED)`, `acl_get_entry` puis `acl_free`. Ces déclarations existent dans le header `sys/acl.h` du SDK Xcode local. Politique initiale conservative : refuser toute ACL étendue non vide et toute erreur d'inspection ; conserver les contrôles POSIX/propriétaire/liens existants. Ce contrôle ne doit ni appeler `chmod` ni supprimer/modifier les ACL.

La vérification devra employer uniquement des fixtures temporaires : absence d'ACL, ACL explicite permettant lecture/écriture, ACL héritée, ACL de refus, erreur du helper et substitution de chemin après ouverture. Le contrôle doit porter sur racine, dossier inbox, verrou, marqueur, registre et fichier temporaire avant publication ; tester qu'aucune permission existante n'a changé. Ce mécanisme ne protège pas d'un processus du même utilisateur qui contourne le verrou coopératif, du rejeu d'un ancien registre authentique ni de la suppression simultanée du registre et de son marqueur.

## Lot ACL implémenté et vérifié — 2026-11-12

Le module `siri_inbox_acl.py` inspecte les ACL macOS par `ctypes`/libSystem sur les descripteurs déjà ouverts ; il ne définit ni ne modifie aucune ACL. Après un `fstat` valide, `acl_get_fd_np(fd, ACL_TYPE_EXTENDED)` retournant NULL/ENOENT signifie absence d'ACL sur la cible Darwin vérifiée. Toute ACE, y compris de refus, et tout autre résultat/erreur sont refusés. Un second `fstat` détecte une fermeture pendant l'inspection, sans garantir contre la réutilisation concurrente du numéro de descripteur. Non-Darwin, bibliothèque absente et symbole absent échouent fermés. Les refus CLI sont `unsafe_permissions` et `permissions_unavailable`.

Contrôles raccordés : racine, dossier inbox, verrou, marqueur, registre et temporaire avant écriture. Les quatre modules inbox, dont l'inspecteur, sont ajoutés explicitement au payload et à ses empreintes. Une fixture de payload relocalisé est importée/exécutée avec le CPython embarqué, sans dépendre du chemin source.

Conformité réelle exclusivement temporaire : ACL absente → NULL/ENOENT ; ACE explicite → pointeur valide, première entrée trouvée avec retour 0 ; fin d'ACL → -1/EINVAL ; descripteur invalide → NULL/EBADF. `errno` est ignoré sur succès. Les tests macOS couvrent ACE allow et deny, héritage, substitution d'un chemin après ouverture (l'inspection reste attachée à l'ancien objet), absence de modification du mode/ctime, tous les descripteurs attendus et refus du temporaire avant le premier octet écrit. Les seules ACL modifiées par les tests appartiennent à leurs fixtures jetables, jamais aux données utilisateur.

Commandes finales :

```
CLANG_MODULE_CACHE_PATH=/private/tmp/agent-resume-swift-cache /Applications/Reprise.app/Contents/Resources/runtime/bin/python3.13 -B -m unittest discover -s reprise/tests -p 'test_siri_*.py' -q
/Applications/Reprise.app/Contents/Resources/runtime/bin/python3.13 -B -m unittest discover -s reprise/tests -p test_package_helpers.py -q
```

Résultats : 42 tests Siri OK (0,913 s), dont 11 tests ACL et les fixtures Swift du coffre ; 7 tests packaging OK (1,424 s). Le premier essai élargi avait échoué parce que Swift tentait d'écrire son cache dans `~/.cache/clang/ModuleCache`, interdit par la sandbox ; définir le cache temporaire ci-dessus a permis la compilation, sans élévation et sans changer le produit.

Limites maintenues : contrôle ponctuel sans verrouillage des changements d'ACL, absence de défense contre un processus du même utilisateur contournant le verrou, rejeu authentique ou suppression simultanée registre/marqueur. Une inspection refusée ou un crash après création du marqueur peut toujours exiger la récupération manuelle déjà documentée ; aucune réinitialisation implicite n'est ajoutée. Aucun App Intent/UI, déploiement, installation, lancement de Reprise, inférence ou test Siri réel n'est réalisé par ce lot.
