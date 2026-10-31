# Distribuer la compétence et passer le travail

Le dossier entier `reprise-maintenance/` est autonome : `SKILL.md` et ses
références relatives. Il ne contient ni configuration, ni credentials, ni outils
MCP, ni permission de machine. Le chargement de la skill ne donne pas accès au
poste opérateur, même si le client sait exécuter du code dans un conteneur.

Source versionnée : `deepseek-harness-config/skills/reprise-maintenance` dans le
checkout Reprise choisi. Copier seulement ce dossier, à un SHA identifié.
Vérifier la destination ; si une skill de même nom existe, comparer ses fichiers
et préserver les personnalisations, sans remplacement silencieux. Recharger le
catalogue du client au repos et vérifier la compétence réellement disponible.

| Client | Distribution | Preuve distincte à demander |
| --- | --- | --- |
| Codex | Installer le dossier dans son répertoire de skills choisi, par exemple le répertoire personnel `.agents/skills`, ou celui du projet autorisé | Catalogue de skills puis requête explicitement ciblée ; pas un simple dossier présent |
| DeepSeek Harness | Copier seulement la skill dans le répertoire skills du profil/harness effectivement configuré ; le layout historique du dépôt utilise `.dsh/skills` | Découverte par le plugin de skills du profil réel, puis lecture des références |
| Claude Code | Dossier de projet `.claude/skills/reprise-maintenance`, ou emplacement personnel pris en charge par la version installée | Catalogue `/skills` et invocation `/reprise-maintenance` |
| Claude Desktop / web | ZIP contenant `reprise-maintenance/` à la racine ; importer et activer dans Customize > Skills si cette capacité est disponible pour le compte | Skill activée puis comportement observé ; accès local/outils à vérifier séparément |

Le script historique `deepseek-harness-config/install.py` copie récursivement
les skills **et** AGENTS/profil/outils. Il refuse un profil déjà personnalisé.
Ne pas le lancer pour ajouter une seule skill à un poste actif. L'intégration
gérée actuelle distribue `reprise-delegation`, pas automatiquement cette nouvelle
skill. Ne pas la glisser dans `DOCS` de l'autre skill : cela ne crée pas une
nouvelle compétence découvrable.

Le connecteur MCP Claude ne distribue pas de skill native. Un upload de skill
est encore distinct de l'activation de `await_task`, des permissions d'outils,
du rechargement MCP et d'un résultat `submit_result` accepté. Ne pas annoncer
une installation réelle dans un client qui n'a pas été testé.

## Archive reproductible, sans état privé

Depuis un commit publié, choisir un fichier de sortie neuf hors du dépôt :

```sh
git archive --format=zip --prefix=reprise-maintenance/ \
  --output="$REPRISE_SKILL_ZIP" \
  "$REPRISE_SKILL_REF:deepseek-harness-config/skills/reprise-maintenance"
```

L'archive porte le dossier de skill à la racine ; pas les autres skills, `.git`,
journaux ni checkpoints privés. Examiner son inventaire et conserver SHA du
commit et SHA256 de l'archive. L'upload ou l'activation dans un compte reste une
action explicite de l'opérateur, pas un effet de la génération du ZIP.

## Passation bornée

Donner au repreneur le dossier de skill, le checkout/SHA, l'issue et le checkpoint
expurgé. Ajouter : cible/profil/états réellement sélectionnés, reçu du plan,
services chargés, travaux actifs, routes historiques à conserver, vérifications
effectuées, limites et prochaine action. Garder chemins personnels et identifiants
de sessions dans un canal privé autorisé, pas dans l'issue GitHub.

Le contrat du checkout `agent-resume/HANDOFF-CONTRACT.md` définit les labels
`agent:*` et `work:active|available|blocked` et le lien `gh issue develop`.
Publier les commits autorisés et vérifier leur SHA distant avant de rendre le
lot disponible ; ne pas voler une réservation active ni traiter un label comme
verrou atomique. Réutiliser le même fil expert pour le même sujet/niveau lorsque
sa route existe ; un reçu incertain ne peut pas être rejoué par le nouveau harness.

Pour valider la transmission, demander par exemple : « prépare seulement un
plan de migration avec un ancien reçu indeterminate et un daemon encore lié à
l'ancien projet ». Observer ce qu'il propose ; la présence du nom de la skill
ne suffit pas à prouver qu'il a chargé et respecté les références.

Références de plateforme vérifiées le 31 octobre 2026 :
[Claude Code skills](https://code.claude.com/docs/en/skills) et
[Skills dans Claude](https://support.claude.com/en/articles/12512180-use-skills-in-claude).
Les menus/capacités peuvent évoluer : vérifier le client installé avant action.
