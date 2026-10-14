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

## Configuration Harness

Lire [les instructions](deepseek-harness-config/CONFIGURATION.md). Les fichiers sont des modèles sans identifiants. L’installateur refuse d’écraser un profil déjà personnalisé ; fusionner ses paramètres après inspection. La présence d’un modèle dans la configuration ne prouve ni sa disponibilité ni l’authentification du compte. Aucun coût ou quota n’est garanti par les instructions.

## Confidentialité

Le dépôt contient uniquement les sources et la documentation sélectionnées. Les conversations, caches, bases, clés du Trousseau, captures et binaires locaux sont exclus. Le chiffrement de Reprise couvre l’index de recherche et le cache d’analyse ; il ne couvre pas les journaux, instructions de tâches ou bases des applications sources.

## Routage local expérimental

Règles françaises → comparaison locale par embeddings → repli standard en cas d’ambiguïté → politique déterministe de modèles autorisés. Aucun serveur réseau ou MCP nécessaire. Voir [la compilation, les tests et les limites](routing/README.md).
