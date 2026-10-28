# Reprise 0.1.16 — retrait du proxy ASI1 et suivi Claude

Cette version regroupe le suivi passif des chats Claude Desktop déclarés, l’affichage de leurs observations quota/reset et le retrait du proxy planner CUDOS. La confirmation d’absence du planner CUDOS provient d’un employé, rapportée par l’opérateur le 28 octobre 2026. L’inférence et les appels d’outils conservent le fournisseur direct existant.

## Versions et contenu installable

`agent-resume/VERSION` passe de **0.1.15 à 0.1.16**. C’est la version commune consommée par l’app (les deux champs CFBundle), le manifeste des helpers, la CLI et les annonces MCP Codex/Claude et Quick Apply. Les composants modifiés n’ont pas de deuxième numéro indépendant. Les plugins de routage séparés et CPython ne sont pas modifiés : leurs versions ne sont pas artificiellement incrémentées.

- Suivi Claude : sessions déclarées dans le registre du connecteur, route CDP existante opt-in, affichage des échéances sans pourcentage ni compte inventés. Aucun nouveau navigateur ni message automatique. Voir [le guide](agent-resume/CLAUDE-SESSION-OBSERVER.md).
- Retrait : panneau et états Swift, commandes CLI, modules Python, ancien lanceur Node, LaunchAgent de référence et profil planner retirés des sources actives et du payload. Les 21 fichiers archivés sont vérifiés octet par octet contre la baseline Git ; [archive et empreinte](archive/asi1-planner-proxy/README.md).
- Mise à jour Harness : conservation de l’option ASI1 déjà choisie ; aucun forçage d’activation. Correction de la portée du dernier provider YAML et du hash du bloc compat nouvellement créé, pour permettre une mise à jour répétée sans faux conflit.

## Construction depuis dev

Sur macOS ARM64 avec SDK 26 ou ultérieur, depuis le dépôt :

```sh
python3 -B agent-resume/release_build.py build --runtime-archive '/chemin/vers/archive-python-epinglee.tar.gz'
python3 -B agent-resume/release_build.py check --app '/chemin/imprime/Reprise.app'
```

Utiliser l’archive indiquée par `agent-resume/python-runtime.lock.json` ; le build vérifie SHA-256, notices et inventaire. Sur le poste de validation, elle était déjà en cache et aucun téléchargement n’a été nécessaire. Le build compile les cinq binaires, embarque les helpers/runtime et vérifie le CLI relocalisé avec HOME jetable. Il ne lance aucune app ni intégration.

## Préparer les mises à jour

Choisir exclusivement le nouveau bundle, avec des chemins réels explicites :

```sh
python3 -B agent-resume/managed_update.py plan   --bundle '/chemin/imprime/Reprise.app'   --components app,scheduler,harness,claude --profile desktop   --app /Applications/Reprise.app   --state "$HOME/Library/Application Support/Reprise/state"   > /tmp/reprise-0.1.16-plan.json
```

Cette commande prépare sans appliquer. Lire le plan et choisir l’état du scheduler : sans `--activate-scheduler`, son remplacement le laisse arrêté ; avec cette option, ses travaux déjà autorisés peuvent reprendre. Fermer normalement Reprise avant tout remplacement. L’application et la vérification utilisent le [parcours géré](agent-resume/MANAGED-UPDATE.md), sans réinitialiser les registres. Les projets/scopes existants sont conservés, et un conflit est refusé.

L’entrée native MCP Codex et le remplacement authentifié de claude-desktopd conservent leurs parcours séparés (#30 et #25/#27) : ne pas prétendre que le plan ci-dessus redémarre ces processus. Le nouveau code embarqué est versionné 0.1.16 ; le chargement effectif dans les clients ouverts exige une vérification après mise à jour. Aucun client n’est rechargé pour préparer cette release.

Les vieux bundles immuables partagés restent conservés. Pour éviter toute réintroduction du proxy, ne pas réinstaller un ancien bundle ou rejouer son ancien plan. L’archive historique n’est pas une recette d’installation active.

## Validation et retrait local

Suite avec archive Python réelle : **894 tests, 4 ignorés, code 0**, 27,191 s. Les tests propres aux composants retirés sont dans l’archive et ne font plus partie de la suite active. Tests ciblés : 42 Harness (1 ignoré), 21 updater, 5 packaging. Parse/typecheck Swift et syntaxe YAML réussis. Les avertissements ResourceWarning existants restent visibles. Le build complet et son empreinte sont consignés après construction dans le rapport de livraison.

Sur le poste opérateur, le proxy a été déchargé et désactivé, son port a été vérifié par connexion refusée et le seul provider `asi-cloud-planner` a été retiré du profil courant. Le changement concurrent de reasoningEffort a été conservé. Plist, gestion, journaux dédiés et sauvegarde de configuration sont dans une archive privée hors Git. L’arrêt avait laissé un reçu pending après une confirmation trop précoce ; ce reçu est conservé intact avec une réconciliation séparée fondée sur l’absence native, la désactivation et le refus TCP. Aucun stop n’a été rejoué.

La release partagée du scheduler et les registres/connecteurs Claude n’ont été ni supprimés ni modifiés. Leurs manifestes sont inchangés. Le retrait de l’UI dans les sources ne modifie pas un ancien bundle déjà ouvert ; la prochaine installation du nouveau bundle appliquera ce changement visuel.

Revue : préflight indépendant et constats de courses récupérés avant interruption quota des agents, puis corrections et validation finale par le coordinateur. Pas de verdict indépendant final après corrections, pas d’audit dynamique fournisseur ni nouvelle inférence.
