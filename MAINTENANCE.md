# Installer, migrer et diagnostiquer Reprise

Le savoir réutilisable est versionné dans ce dépôt ; les inventaires de poste,
plans, reçus, logs, chemins personnels et résultats de sessions restent privés.
Un ancien checkpoint décrit une opération passée, pas l'installation courante.

Point d'entrée pour un agent : la skill portable
[reprise-maintenance](deepseek-harness-config/skills/reprise-maintenance/SKILL.md).
Elle couvre préparation, plans gérés, vérification réelle, diagnostic, portées
historiques et passation. Copier son dossier entier permet de la transmettre
à Codex, DeepSeek Harness ou Claude ; les modalités et limites sont dans
[sharing.md](deepseek-harness-config/skills/reprise-maintenance/references/sharing.md).

## Où se trouve l'information ?

| Sujet | Référence versionnée |
| --- | --- |
| Contrat d'installation et limites | [INSTALLATION-CONTRACT](agent-resume/INSTALLATION-CONTRACT.md) |
| Build autonome, runtime épinglé et artefacts CI | [BUILD-AND-CI](agent-resume/BUILD-AND-CI.md) |
| Updater CLI inspect/plan/apply/verify/cleanup | [MANAGED-UPDATE](agent-resume/MANAGED-UPDATE.md) |
| Service launchd et preuve du tick | [SCHEDULER-SERVICE](agent-resume/SCHEDULER-SERVICE.md) |
| Profil et options DeepSeek Harness | [HARNESS-INTEGRATION](agent-resume/HARNESS-INTEGRATION.md) |
| Connecteur MCP Claude Desktop et attente | [CLAUDE-CONNECTOR](agent-resume/CLAUDE-CONNECTOR.md) |
| Backend distinct Claude Code | [CLAUDE-INTEGRATION](agent-resume/CLAUDE-INTEGRATION.md) |
| Codex et ses routes historiques | [CODEX-INTEGRATION](agent-resume/CODEX-INTEGRATION.md) |
| Projet UUID, workspaces et limites de migration | [PROJECT-REGISTRY-CONTRACT](agent-resume/PROJECT-REGISTRY-CONTRACT.md) |
| Reçus, résultats et incertitude | [DELEGATION-PERSISTENCE](agent-resume/DELEGATION-PERSISTENCE.md) |
| Réservation et checkpoint inter-harness | [HANDOFF-CONTRACT](agent-resume/HANDOFF-CONTRACT.md) |

Les documents historiques peuvent mêler contrat cible, prototype et preuve
datée. Pour une opération, vérifier les CLI et manifestes du commit/release
sélectionnés ; signaler toute contradiction au lieu de réinstaller à l'aveugle.

## Ce qui n'est pas fourni automatiquement

- Le plan général conserve les scopes installés ; il ne migre pas les anciens
  reçus vers l'UUID projet ou un nouveau workspace.
- La skill ne recharge pas de client, ne modifie pas de profil et n'accorde
  aucune permission d'inférence, de système ou de facturation.
- La skill est dans les sources. Elle n'est pas automatiquement embarquée dans
  Reprise.app ni distribuée par ses intégrations gérées actuelles.
- La copie ou l'import de la skill doit être suivi d'une vérification dans le
  harness cible. Aucun chargement réel Claude/Harness n'est prouvé par ce fichier.

Pour reprendre une intervention, demander le checkpoint privé de l'opérateur
avec les chemins/reçus sélectionnés, lire l'issue de coordination puis commencer
par un inventaire sans inférence. Ne jamais importer un registre privé dans Git.
