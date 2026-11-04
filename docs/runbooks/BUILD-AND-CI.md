# Build autonome et CI légère

Le build à utiliser pour les vérifications produit est celui de `release_build.py`, pas le lancement de développement de `native-install --open`.

```sh
python3 agent-resume/release_build.py build --runtime-archive /chemin/archive-python-epinglee.tar.gz
sh agent-resume/relaunch
```

La commande de build affiche le chemin exact `agent-resume/builds/EMPREINTE/Reprise.app`. Elle copie les sources dans une zone temporaire, construit les quatre binaires et le runtime épinglé, déplace le bundle et vérifie le CLI sur un HOME/registre jetable. Elle refuse une source modifiée pendant le build et publie dans un nouveau répertoire sans remplacer un bundle ouvert. Le bundle contient `Contents/Resources/build-evidence.json` : commit observé, empreinte exacte des sources (y compris changements locaux), empreintes du bundle et preuve du smoke CLI. Le commit seul ne prouve donc pas un arbre propre.

`relaunch` vérifie les sources et l’intégrité du bundle avant ouverture sans arguments de développement ; aucun daemon ni réglage d’automatisation n’est modifié. Le lancement normal de Reprise peut fermer une autre instance Reprise (comportement de l’application), sans toucher à Codex/Harness/Claude. Aucun lancement n’est inclus dans la compilation ou la CI. En cas de build périmé, reconstruire. Les anciens builds sont conservés ; aucune purge automatique.

`restart-chain --runtime-archive FILE` construit puis lance ce parcours autonome. `restart-chain --no-build` lance seulement un build courant vérifié. Il ne redémarre plus Quick Apply par défaut. `--server` reste une action explicite sur le daemon Quick Apply ; `--development` conserve la chaîne historique et son lancement avec arguments. Dans ces deux modes explicites, l’état par défaut est `~/Library/Application Support/Reprise/state` ; un override volontaire `AGENT_RESUME_STATE` reste possible. Aucun de ces scripts ne redémarre codexd ni le planificateur. Les intégrations MCP/planificateur utilisent leurs propres releases et nécessitent une mise à jour explicite depuis Intégrations ; compiler la GUI ne les met pas à jour silencieusement.

## GitHub Actions

`.github/workflows/reprise.yml` : un seul job sur `macos-26` ARM64, uniquement changements Reprise/workflow, événements push dev/main, PR et manuel. Concurrence annulant les exécutions remplacées, délai 20 minutes, permissions `contents:read`, aucun secret de signature/déploiement. Pas de runner persistant sur le poste utilisateur. Le choix du runner correspond au minimum Foundation Models : [référence GitHub](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).

Seule l’archive officielle Python est mise en cache avec une clé liée au verrou ; son SHA est revérifié même après cache. Suite unittest via `ci_test.py` sur HOME jetable, test d’archive opt-in, compilation sans `--open`, contrat natif et smoke CLI relogé. Pas de test GUI, de génération Foundation Models, de connexion compte ou d’inférence payante.

Le job livre un ZIP du bundle exact et son SHA256, nommés par commit, rétention sept jours. Ce sont des artefacts de validation non notarialisés ; aucun auto-déploiement, publication GitHub Release ou installation sur la machine réelle. La signature stable, notarisation, compte macOS vierge et validations UI/quotas/reprise métier restent distincts. Les exécutions hébergées consomment les minutes Actions selon le forfait du dépôt.
