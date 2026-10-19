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

## Configuration Harness

Lire [les instructions](deepseek-harness-config/CONFIGURATION.md). Les fichiers sont des modèles sans identifiants. L’installateur refuse d’écraser un profil déjà personnalisé ; fusionner ses paramètres après inspection. La présence d’un modèle dans la configuration ne prouve ni sa disponibilité ni l’authentification du compte. Aucun coût ou quota n’est garanti par les instructions.

## Confidentialité

Le dépôt contient uniquement les sources et la documentation sélectionnées. Les conversations, caches, bases, clés du Trousseau, captures et binaires locaux sont exclus. Le chiffrement de Reprise couvre l’index de recherche et le cache d’analyse ; il ne couvre pas les journaux, instructions de tâches ou bases des applications sources.

## Routage local expérimental

Règles françaises → comparaison locale par embeddings → repli standard en cas d’ambiguïté → politique déterministe de modèles autorisés. Aucun serveur réseau ou MCP nécessaire. Voir [la compilation, les tests et les limites](routing/README.md).
