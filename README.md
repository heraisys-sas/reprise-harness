# Reprise & Harness

Outils macOS pour suivre les sessions d’agents, rechercher leurs diagnostics localement et préparer un routage de modèles économique.

## Composants

| Dossier | État |
| --- | --- |
| `agent-resume/` | Application native SwiftUI/AppKit, collecte locale, quotas disponibles, recherche privée et cache d’analyse chiffrés |
| `deepseek-harness-config/` | Instructions et six compétences de développement, DevOps, sécurité, preuves scientifiques, orchestration et portage Rust ; configuration de référence |
| `routing/` | Routeur expérimental avec embeddings Apple locaux, tests et comparaisons |
| `role-routing/` | Routage optionnel par rôles planner/executor, désactivé par défaut |
| `harness-tools/` | Outils de vérification et pilotes d’évaluation |

Le routage par embeddings Apple est implémenté et testé dans un profil Harness isolé. Il reste expérimental et n’est pas activé dans le profil quotidien. Le modèle génératif Apple Intelligence ne participe pas au routage.

## Démarrage de Reprise

Sur un Mac avec les outils de compilation Swift et Python 3.13 :

```sh
sh agent-resume/native-install --open
```

Avec `--open`, ce script compile l’application et les helpers, puis ouvre Reprise. Sans cette option, il compile seulement et ne perturbe pas une instance en cours. Consulter [le guide Reprise](agent-resume/README.md) avant d’installer le service de reprise : ses relances doivent être explicitement programmées. Le suivi des autres applications ne leur ajoute pas de mécanisme automatique de reprise.

```sh
python3 -m unittest discover -s agent-resume/tests -q
```

## Daemon Quick Apply (`claude-desktopd`)

Le serveur `agent-resume/claude_desktop_quick.py` accepte, sur un socket UNIX privé, des charges utiles Quick Entry validées (prompt, images, fichiers, chat cible) et applique un plafond quotidien de soumissions. Il ne pilote pas l’interface de Claude Desktop : la soumission réelle dans l’overlay Quick Entry exige des autorisations macOS (Accessibilité, Capture d’écran) accordées par l’opérateur, et reste du ressort d’un pilote supervisé.

L’installateur historique écrit le LaunchAgent `com.example.reprise.quick-entry`. Sans `RunAtLoad`, `KeepAlive` ou déclencheur, son chargement seul ne programme pas un démarrage au login. L’écriture du plist ne démarre aucun daemon. Pour démarrer explicitement le daemon, utiliser `python3 agent-resume/resume quick-apply-daemon-start` ; vérifier avec `quick-apply-daemon-status`. Les identifiants historiques `quick-entry` restent utilisés dans les chemins et le protocole.

Installation (écriture du plist uniquement, sans démarrage) :

```sh
python3 agent-resume/install-quick-entry.py
launchctl load ~/Library/LaunchAgents/com.example.reprise.quick-entry.plist
```

Le script refuse d’écraser un plist existant sans `--force`, et se contente de proposer la commande `launchctl load` ; il ne l’exécute jamais. Désinstallation :

```sh
launchctl unload ~/Library/LaunchAgents/com.example.reprise.quick-entry.plist
rm ~/Library/LaunchAgents/com.example.reprise.quick-entry.plist
```

Test manuel du serveur, dans un terminal :

```sh
python3 agent-resume/claude_desktop_quick.py \
  --socket ~/.claude-desktop/quick-entry.sock \
  --state agent-resume/quick-state --daily-limit 8
```

Un autre terminal peut alors joindre le socket avec `agent-resume/claude_desktop_rpc.py` (méthode `ping` ou `quick_entry/submit`) ; aucune requête au modèle n’est envoyée par ce test. Le serveur n’ouvre ni ne pilote l’UI Claude Desktop, et aucune relance automatique n’est prévue : toute reprise est explicitement programmée.

## Analyse des journaux et santé post-update

Reprise lit les journaux locaux de Claude Desktop (aucune permission d’écran, aucune Accessibilité) pour trois usages :

- **Reprise des tâches** : `reprise_logs.resumable()` reconstruit les soumissions Quick Entry interrompues (garde déclenchée, dispatch manquant) ; `reprise_history()` explique les travaux récents des Harness et leurs interactions via Reprise.
- **Santé après mise à jour** : `reprise_health.check()` compare les versions installées (Claude Desktop, Claude Code, Codex…), vérifie si les serveurs supervisés doivent être relancés (par leur socket, pas `ps`) et si les signatures de logs surveillées sont toujours présentes.
- **Veille** : `reprise_health.watch()`, lancée par le `tick` (intervalle 60 s), signale un changement de version post-update et persiste un rapport. Elle ne relance jamais rien ; elle liste les **actions requérant un humain**.

Une couche **générative optionnelle** (`FoundationExplain.swift`, API Foundation Models sur l’appareil) explique l’activité et les actions humaines. Elle se désactive proprement si le modèle est indisponible ; la couche déterministe reste la source de vérité. Aucun de ces composants ne pilote l’interface ni n’automatise la soumission Quick Entry — cela exige des autorisations macOS accordées séparément par l’opérateur.

Exposition : outils MCP `reprise_resume`, `reprise_history`, `reprise_health` ; sous-commandes CLI `./resume reprise-resume|reprise-history|reprise-health|health-watch` ; onglet **Santé post-update** dans l’application.

## Redémarrage de la chaîne

`agent-resume/restart-chain` tente de redémarrer `claude-desktopd`, `codexd` si disponible, puis Reprise, et affiche l’état :

```sh
cd agent-resume
./restart-chain              # recompile + redémarre les daemons + ouvre Reprise
./restart-chain --no-build   # relance sans recompiler
./restart-chain --server     # redémarre les daemons sans ouvrir Reprise
```

Le script tente de supprimer le socket Quick Apply, arrête une éventuelle instance du daemon, recompile (`native-install`, sans ouverture), redémarre les daemons Quick Apply et Codex, puis ouvre Reprise. Il ne ferme pas explicitement une instance Reprise déjà ouverte. Chaque étape est résiliente : un échec local (ex. socket protégé) n'interrompt pas la chaîne. Il ne force jamais l'arrêt d'une application liée (Claude Desktop, Codex Desktop).

## Délégation et retour des analyses

Codex fournit un aller-retour exploitable via `reprise_delegate`. Le plafond de huit appels par jour UTC/projet/endpoint est un compteur local : `budget_blocked` indique ce plafond ; `quota_blocked` indique un refus lié au quota fournisseur.

Pour Claude Desktop, l’architecture recommandée par Astra combine trois composants : **Quick Apply déclenche** le tour, **MCP `get_task`/`submit_result` transporte** la demande et le résultat, **SQLite conserve** l’état durable. Un accusé de soumission Quick Apply ne signifie pas que l’analyse est terminée : seul un `submit_result` validé et enregistré marque l’achèvement.

Le prototype MCP existe dans `agent-resume/ux_review_probe.py`. Le contrat complet reste à implémenter : instantanés des fichiers, manifeste et `request_hash`, résultat lié à la bonne tentative, écriture atomique et revalidation à la lecture. Le branchement `reprise_ux_review` dans Claude Desktop reste à valider par l’opérateur. Aucun renvoi automatique après une soumission incertaine. Voir [l’avis Astra et le contrat prévu](agent-resume/ARCHITECTURE-QUESTION-PENDING.md).

La supervision et la reprise doivent suivre un [socle commun multi-harness](agent-resume/HARNESS-SUPERVISION-ARCHITECTURE.md) : quotas, checkpoints, consommation, boucles et contexte, avec capacités vérifiées par adaptateur. Ce contrat est une cible de développement, pas une annonce de prise en charge complète.

## Contribution

Travailler sur `dev`, puis intégrer via la MR `dev` → `main`. Ne pas committer directement sur `main`.

## Configuration Harness

Lire [les instructions](deepseek-harness-config/CONFIGURATION.md). Les fichiers sont des modèles sans identifiants. L’installateur refuse d’écraser un profil déjà personnalisé ; fusionner ses paramètres après inspection. La présence d’un modèle dans la configuration ne prouve ni sa disponibilité ni l’authentification du compte. Aucun coût ou quota n’est garanti par les instructions.

## Confidentialité

Le dépôt contient uniquement les sources et la documentation sélectionnées. Les conversations, caches, bases, clés du Trousseau, captures et binaires locaux sont exclus. Le chiffrement de Reprise couvre l’index de recherche et le cache d’analyse ; il ne couvre pas les journaux, instructions de tâches ou bases des applications sources.

## Routage local expérimental

Règles françaises → comparaison locale par embeddings → repli standard en cas d’ambiguïté → politique déterministe de modèles autorisés. Aucun serveur réseau ou MCP nécessaire. Voir [la compilation, les tests et les limites](routing/README.md).
