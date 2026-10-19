# Reprise & Harness

Outils macOS pour suivre les sessions d’agents, rechercher leurs diagnostics localement et préparer un routage de modèles économique.

## Composants

| Dossier | État |
| --- | --- |
| `agent-resume/` | Application native SwiftUI/AppKit, collecte locale, quotas disponibles, recherche privée et cache d’analyse chiffrés |
| `deepseek-harness-config/` | Instructions et six compétences de développement, DevOps, sécurité, preuves scientifiques, orchestration et portage Rust ; configuration de référence |
| `routing/` | Adaptation du routeur avec helper Apple local, tests et comparaison des deux moteurs |

Le routage par embeddings Apple est implémenté et testé dans un profil Harness isolé. Il reste expérimental et n’est pas activé dans le profil quotidien. Le modèle génératif Apple Intelligence ne participe pas au routage.

## Démarrage de Reprise

Sur un Mac avec les outils de compilation Swift et Python 3.13 :

```sh
./agent-resume/native-install
```

Ce script compile l’application et les helpers, puis ouvre Reprise. Consulter [le guide Reprise](agent-resume/README.md) avant d’installer le service de reprise : ses relances doivent être explicitement programmées. Le suivi des autres applications ne leur ajoute pas de mécanisme automatique de reprise.

```sh
python3 -m unittest discover -s agent-resume/tests -q
```

## Serveur Quick Entry

Le serveur `agent-resume/claude_desktop_quick.py` accepte, sur un socket UNIX privé, des charges utiles Quick Entry validées (prompt, images, fichiers, chat cible) et applique un plafond quotidien de soumissions. Il ne pilote pas l’interface de Claude Desktop : la soumission réelle dans l’overlay Quick Entry exige des autorisations macOS (Accessibilité, Capture d’écran) accordées par l’opérateur, et reste du ressort d’un pilote supervisé.

Le LaunchAgent `com.example.reprise.quick-entry` démarre ce serveur au login. Il ne contient que des chemins de ce dépôt et du dossier de l’utilisateur courant, et ne porte ni `RunAtLoad` ni `KeepAlive` : le serveur ne démarre jamais automatiquement sans action explicite de l’opérateur.

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

`agent-resume/restart-chain` redémarre proprement la chaîne de dépendances (GUI Reprise + serveur Quick Entry) et vérifie l'état :

```sh
cd agent-resume
./restart-chain              # recompile + relance le serveur Quick Entry + Reprise
./restart-chain --no-build   # relance sans recompiler
./restart-chain --server     # seul le serveur Quick Entry (pas de relance du GUI)
```

Le script nettoie le socket Quick Entry orphelin, arrête une éventuelle instance du serveur, recompile (`native-install`, sans relancer sauf `--open`), démarre le serveur, puis relance l'application. Chaque étape est résiliente : un échec local (ex. socket protégé) n'interrompt pas la chaîne. Il ne force jamais l'arrêt d'une application liée (Claude Desktop, Codex Desktop).

## Configuration Harness

Lire [les instructions](deepseek-harness-config/CONFIGURATION.md). Les fichiers sont des modèles sans identifiants. L’installateur refuse d’écraser un profil déjà personnalisé ; fusionner ses paramètres après inspection. La présence d’un modèle dans la configuration ne prouve ni sa disponibilité ni l’authentification du compte. Aucun coût ou quota n’est garanti par les instructions.

## Confidentialité

Le dépôt contient uniquement les sources et la documentation sélectionnées. Les conversations, caches, bases, clés du Trousseau, captures et binaires locaux sont exclus. Le chiffrement de Reprise couvre l’index de recherche et le cache d’analyse ; il ne couvre pas les journaux, instructions de tâches ou bases des applications sources.

## Routage local expérimental

Règles françaises → comparaison locale par embeddings → repli standard en cas d’ambiguïté → politique déterministe de modèles autorisés. Aucun serveur réseau ou MCP nécessaire. Voir [la compilation, les tests et les limites](routing/README.md).
