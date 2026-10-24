# Checkpoint — Claude Desktop aller-retour, premier lot local

Date des observations : 2026-10-24, vers 17:33 UTC (19:33 Europe/Paris).
Issues : #31 et #36 ; dépendance client #30.
Branche locale : `codex/31-live-roundtrip-20261024`.
Baseline : `8227b753d337c915cf5d28484bef6d9c1ea7e6b1` ; workspace initial propre.

## Résultat local

La configuration réelle contient `mcpServers.reprise`, sans `reprise-harness`.
Le manifeste installé annonce `reprise-harness`, avec une empreinte différente
de celle de l'entrée `reprise`. Le contenu des entrées n'est pas reproduit ici.

L'installateur pouvait créer une seconde entrée sans résoudre cette incohérence.
Il refuse désormais installation et retrait dans ce cas ambigu, sans écriture.
Si l'empreinte de l'entrée historique correspond exactement au manifeste, une
migration gérée vers `reprise-harness` est possible ; les sauvegardes, contrôles
de concurrence et plans existants sont conservés. L'inspection expose les noms
configuré et déclaré et leur concordance ; la vérification exige leur cohérence.
Une entrée tierce `reprise` reste préservée lorsque le connecteur géré est présent.

Fichiers de code : `claude_integration.py`, `tests/test_claude_integration.py`.
Modifications locales non committées et non publiées ; aucun push, merge,
changement de clone partagé, configuration réelle ou redéploiement.

## Preuves et limites du premier lot (19:33 Europe/Paris)

- Tests ciblés : `python3 -m unittest discover -s agent-resume/tests -p test_claude_integration.py -q` : 16 tests OK.
- Suite complète : `python3 -m unittest discover -s agent-resume/tests -q` :
  655 tests exécutés, 6 ignorés, 1 échec et 1 erreur (code de sortie 1).
  `test_import_excludes_runtime_socket_and_locks` échoue sur `socket.bind`
  avec `PermissionError: [Errno 1] Operation not permitted`.
  `test_first_launch_from_bundle_without_preexisting_quick_state` constate
  la sortie du démon de fixture (code 1) ; cause interne non confirmée.
  Des `ResourceWarning` signalent aussi des fichiers/une connexion SQLite
  non fermés. Suite non validée ; pas de contournement ni relance privilégiée.
- `git diff --check` : OK.
- Tentative de connexion JSON-RPC au socket existant : `Unavailable`.
  Cela ne prouve ni arrêt du démon ni version chargée. Aucun redémarrage tenté.
- Sur les 500 dernières lignes du log `mcp-server-reprise.log` : 4 occurrences
  d'`initialize`, 2 de `tools/list`, aucune de `get_task` ou `submit_result`.
  Ce relevé borné ne prouve pas l'absence de tout résultat ailleurs.
- Le code de cette baseline annonce `dispatch_supported=false` : aucun pilote
  natif Quick Apply branché n'a été établi pendant ce lot.
- L'accès GitHub par `gh issue view 36` a échoué ; derniers commentaires, claims,
  labels et lien distant de la branche non revérifiés. Pas de nouvelle recherche
  d'API de déclenchement, pas de publication GitHub.

## Demande historique et prochaine action à la fin du premier lot

`request_key=claude-review-a51b51bf6d39409080e2cc104ef39089`,
`submission_id=5fe9586c-8d55-4292-a9bd-4badb9144e65` : état actuel non revérifié,
aucune lecture de reçu aboutie, aucun acquittement ni rejeu. Ne pas la rejouer
automatiquement et ne pas modifier directement son état SQLite.

Le parent doit relire #31/#36 et confirmer la réservation, puis faire examiner
les différences entre l'entrée réelle et le manifeste par l'opérateur autorisé
(ne pas assimiler l'empreinte différente à une propriété démontrée de Reprise).
Préparer ensuite un nouveau plan géré, coordonner la release partagée et le
rechargement ciblé hors tour Claude actif, avec les permissions nécessaires.
Relever version et capacités du démon chargé, rattachement projet/état et
autorisations des outils Claude. Un essai réel exige un déclencheur conforme
prouvé puis `submit_result` et lecture du résultat ; admission et découverte
d'outils restent des preuves distinctes. Aucun aller-retour réel validé ici.

## Followup du 24/10/2026, vers 20:55 Europe/Paris

### Coordination relue via le connecteur GitHub

- #31 est toujours ouverte, avec `agent:codex` et `work:active`.
  Le [commentaire du parent](https://github.com/heraisys-sas/reprise-harness/issues/31#issuecomment-6387362083)
  rattache ce chat `01a16ef7-b787-77e0-ab93-68f1c17b19b8` à la branche actuelle
  et réserve les services/paquet partagés à la coordination avec #7.
  La branche distante est retrouvée ; `gh issue develop --list` non exécuté.
- #36 reste ouverte et `work:available` ; aucun claim pris sur le scheduler.
  Les quatre commentaires, dont l'avis Astra et l'essai réel admission seule,
  ont été lus. Aucun déclencheur Quick Apply externe conforme n'y est démontré.
  Pas de nouvelle recherche Electron/CDP, cookies ou API privée.
- #30 reste ouverte. Aucun label, commentaire ou fichier distant modifié.

### Preuves réelles nouvelles, lecture seule autorisée du socket

- `initialize` : `reprise-claude-desktop-quick`, **0.1.0**.
- `quick_entry/capabilities` : refus RPC (`Unavailable`). Le rattachement
  projet/état et les capacités de ce processus restent inconnus.
- `quick_entry/requests`, sur la demande historique uniquement, avec
  `acknowledge=false` : `status=finished`, `state=recorded`,
  `acknowledged=false`, même `submission_id`, `admission_only=true`.
  Le champ `dispatch_attempted` est absent ; ne pas lui inventer une valeur.
  `finished` décrit ici la consultation du reçu, pas un résultat Claude.
  Aucun rejeu ni acquittement effectué ; aucune preuve nouvelle d'émission.
- Configuration/manifeste : incohérence toujours présente. Les options projet,
  quick-state et expert-state de `reprise` concordent avec le manifeste ;
  le programme Python et la liste complète des arguments diffèrent.
  L'entrée reconstruite depuis les champs du manifeste correspond à son hash.
  Le fichier VERSION de la release du manifeste indique **0.1.8** : c'est une
  preuve sur disque, distincte du démon 0.1.0 observé au socket.
- L'écrivain historique de cette divergence reste non identifié. La recherche
  ciblée trouve l'installateur géré et l'ancien installateur UX ; ce dernier
  ne gère que `reprise_ux_review`. Cela ne prouve pas qui a réécrit `reprise`.

### Correctif et validation finale

Le vérificateur perdait la version obtenue par `initialize` quand la lecture
des capacités échouait. Il conserve désormais cette version, garde le
rattachement et le dispatch `unknown`, et expose `capabilities_observed=false`.
Les tests couvrent le démon ancien, les capacités observées et un état différent.

- Deux tests de sockets précédemment en échec : **2 tests OK** hors sandbox.
- Suite ciblée `test_claude_integration.py` après correctif : **18 tests OK**.
- Suite complète après correctif : `python3 -m unittest discover -s agent-resume/tests -q`
  hors sandbox avec approbation native : **657 tests OK, 4 ignorés**, code 0.
  Les `ResourceWarning` de fichiers/SQLite restent présents.
- `git diff --check` : OK.

Les constats du premier lot sur l'accès GitHub, la version du démon, le reçu
et les échecs de tests sont donc remplacés par les preuves ci-dessus.
Les trois fichiers restent locaux, non committés et non publiés. Aucun service
installé ni configuration réelle modifié ; seuls des démons de fixture ont été
lancés et arrêtés par les tests, dans leurs répertoires temporaires.

Prochaine action bornée : faire arbitrer l'entrée divergente par l'opérateur
et le parent, puis autoriser le plan géré et le rechargement ciblé de la release
coordonnée hors tour Claude actif. Le nouveau refus exige cette réconciliation
explicite plutôt que d'écraser une entrée dont l'appartenance n'est pas prouvée.
La mise à jour seule du démon ne fournit pas le pilote Quick Apply manquant.
L'aller-retour réel reste bloqué tant qu'aucune route conforme ne déclenche le
tour avec les outils MCP Desktop autorisés, puis ne rend un `submit_result`
corrélé et lisible par l'émetteur. Ne pas substituer un navigateur compagnon
aux capacités Desktop et ne pas présenter un résultat de fixture comme réel.

## Déploiement coordonné par le parent

Revue indépendante via Reprise sans anomalie établie. Configuration historique
réconciliée après vérification exacte de ses arguments et de sa release gérée,
avec sauvegarde privée du config et manifeste. Installation gérée du connecteur
reprise-harness 0.1.11 réussie ; découverte de dix outils confirmée sans inférence.
Le premier lancement réel révèle une création de bytecode dans la release
immuable par desktop_mode : les flags -E -s -B sont ajoutés au lancement du
daemon, avec régression de test. Le contrôle d'intégrité doit rester positif
après lancement réel. L'ancien daemon DSH est remplacé uniquement après
vérification de sa commande exacte ; ses données historiques sont conservées.

Limite maintenue : aucun pilote natif Quick Apply conforme n'est raccordé.
La configuration déployée et la découverte d'outils ne prouvent pas le chargement
par Claude Desktop ni un aller-retour réel ; dispatch_supported reste false.
Aucun redémarrage forcé de Claude Desktop ni de codexd.
