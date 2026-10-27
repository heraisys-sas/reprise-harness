# Portage Desktop Apply vers `dev`

## R6 — observations quota non attribuées

- **Baseline** : `cc2b3ed20e445c122eef4888cbe918d112b10b46`; revue `REVIEW-DESKTOP-APPLY-DEV.md` R6.
- **Changement** : le lecteur retient le dernier événement structuré `exceeded_limit`/`rate_limit`; une erreur `api_error` ultérieure ne l'efface pas. Un reset expiré ou absent laisse `blocked` inconnu; un reset futur donne seulement un indice de blocage. Les sorties indiquent explicitement l'attribution compte/pool inconnue et remplacent la ligne brute par `evidence_sha256`. Le parseur suit le JSON extérieur et son champ `message` JSON imbriqué, sans extraction regex d'un type trouvé en prose.
- **Adaptateur** : la disponibilité du profil Chrome reste inconnue, car le journal renderer ne démontre pas le compte/pool associé.
- **Compatibilité** : `evidence` est retiré au profit de `evidence_sha256`; `blocked` n'est plus `False` après reset expiré ni sans reset (et `expired` vaut `None` sans reset). `overloaded` n'est plus un type quota reconnu dans ce lecteur.
- **Tests** : fixtures synthétiques couvrant événements multiples, reset absent/expiré, type hors quota, attribution et absence de contenu brut. Aucun journal utilisateur n'est lu.
- **Validation** : `test_claude_quota_log.py` (21) et `test_apply_adapter.py` (4) réussissent sous `HOME` temporaire, après ajout des cas types non scalaires, reset non fini/hors plage et horodatage invalide. Fixtures locales uniquement; aucun journal utilisateur, réseau, daemon, navigateur ou service. Commit réservé au coordinateur après revue.

## Lot 85 — suffixe `Z` pour `resume --at`

- **Baseline** : `dev` `d162a2c401ba306815dc4a268457252de7f8fe57`.
- **Sources fournies** : PR84 `8b0b761c92eb43a2549d07828c6980b060016da8`, PR85 `28c00d43a635698101411e0fff3a017a412af9fd`, PR86 `4bd6b9e74d572468cded63b309a03d8011b648c7`.
- **Portage** : `resume` normalise le suffixe UTC `Z`/`z` en `+00:00` avant `datetime.fromisoformat`; les instants à décalage explicite restent acceptés. Ajout d'un test CLI pour `Z`, `z`, `+02:00` et le rejet d'un instant sans fuseau.
- **Limites** : lot 85 uniquement. Aucun portage des PR84/86, raccord au daemon, appel de service ni reprise d'issue réservée à un autre agent. Aucun `git add`, commit ou push.
- **Validation** : `python3 -m unittest discover -s agent-resume/tests -p test_resume.py -q` — 28 tests réussis, lancé avec `HOME` temporaire (`python3` enveloppé par `subprocess`); aucun service ni inférence démarré.

## Lot 84 — prototype expérimental Desktop Apply

- **Baseline de portage** : commit du lot 85 `9ec03a72dfc6fd76d688099003667cf21ee1875e` sur la baseline `dev` `d162a2c401ba306815dc4a268457252de7f8fe57`.
- **Source** : PR84 `8b0b761c92eb43a2549d07828c6980b060016da8`; sources considérées pour la séquence: PR85 `28c00d43a635698101411e0fff3a017a412af9fd`, PR86 `4bd6b9e74d572468cded63b309a03d8011b648c7`.
- **Portage** : ajout des deux modules et de la documentation; exposition d'une CLI explicite et expérimentale; inclusion des trois ressources dans `package_helpers.ASSETS`, en conservant les ressources de packaging actuelles de `dev`.
- **Garde-fous** : aucun raccord au daemon, routage ou reprise automatique. `quota()` rapporte l'indisponibilité si le navigateur ou le bouton manque; les signaux UI restent heuristiques. `send_staged()` ne vérifie pas le chat exact. Clic, admission du tour et retour MCP restent distincts.
- **Validation** : sous `HOME` temporaire, `test_claude_desktop_apply.py` (14), `test_claude_quota_log.py` (13), `test_package_helpers.py` (5) et `test_resume.py` (29) réussissent, soit 61 tests ciblés. CDP est simulé dans les tests transport; aucun `git add`, commit, push, réseau, app, service ou inférence par ce lot.

## Lot 86 — exemples CDP et WKWebView archivés

- **Baseline** : commit lots 84/85 `9a34fe538356c357c7831fdcea1390cd3e9d3038` sur `dev` `d162a2c401ba306815dc4a268457252de7f8fe57`.
- **Source** : PR86 `4bd6b9e74d572468cded63b309a03d8011b648c7`.
- **Portage** : sept fichiers de `harness-tools/desktop-cdp-example`; adaptateur de quota local vers `agent-resume/claude_quota_log.observe`, avec valeurs inconnues conservées comme inconnues; test sur fichier synthétique. README précise provenance, statut non livré, limites d'admission/retour et le caractère opt-in de `--wait-quota`. SPEC WKWebView marque le login bloqué et décrit l'absence de liste blanche d'hôte.
- **Validation** : adaptateur (3 tests) et AST Python réussis; `bash -n` sur `launch.sh` et `stop.sh` réussi. `node --check cdp.mjs` non vérifié car `node` est absent (`command not found`). Aucun navigateur, CDP, selftest, login, app, service, installation, réseau, commit ou push.

## Vérification du portage complet — coordinateur

- Trois commits locaux : `9ec03a7` (85), `9a34fe5` (84), `5c3996b` (86). Les modes exécutables des scripts exemples sont conservés.
- `python3 agent-resume/ci_test.py` : premier passage sandbox, 865 tests, 39 erreurs de bind refusé et un échec de démarrage de fixture ; ce passage n'est pas une validation.
- Même suite avec autorisation des sockets temporaires, HOME jetable : **865 tests, OK, 4 ignorés**, 19,031 s, code de sortie 0. Journaux `/private/tmp/reprise-dev-port-ci.log` et `/private/tmp/reprise-dev-port-ci-authorized.log`. Quelques ResourceWarning restent présents ; aucune inférence ni service installé utilisé.
- `dev` distant revérifié en lecture seule, toujours `d162a2c401ba306815dc4a268457252de7f8fe57`. Aucun push, modification de PR ou fusion distante.
- Nouvelle demande utilisateur : intégrer le transport au parcours Reprise et factoriser les doublons pour la maintenabilité. Plan délégué à Sol en lecture seule ; le portage testé ci-dessus demeure une baseline, pas une preuve d'intégration produit ou d'envoi réel.

## Consolidation pour dev — 27 octobre 2026

- Origin/dev actualisé à c3c46dcbcbf70af3dec1b69c78c9d80a762659a2 ; cinq commits concurrents intégrés sans conflit via b012604. Aucune branche ni copie de travail concurrente modifiée.
- a164dbe : décodages quota tolérants aux ValueError/RecursionError ; 23 tests quota et 4 adaptateur réussis sous HOME temporaire selon l’agent.
- d56c750 : garde cible/empreinte/brouillon, incertitude conservée, refus lifecycle non attribué, framing WebSocket partagé et placeholder ProseMirror exact. 75 tests ciblés (2 ignorés) pour le lot, puis 33 tests transport (1 ignoré) et 64 fixtures DOM après le correctif placeholder.
- Test réel autorisé : un seul envoi effectif vers un chat existant et réponse corrélée observée via CDP, sans AX. Preuve expurgée : agent-resume/evidence/claude-cdp-continuation-20261027.json. Ce test ne qualifie ni la reprise du tour natif suspendu, ni MCP, ni le planificateur. Les reçus bruts restent dans le workspace de mission, non publiés.
- Revue pré-fusion : module corrigé ; anciens exemples mutants à neutraliser et contrat livré à actualiser. Révision finale et suite complète requises après ces derniers changements.
