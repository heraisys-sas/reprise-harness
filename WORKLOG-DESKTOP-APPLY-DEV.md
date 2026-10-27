# Portage Desktop Apply vers `dev`

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
