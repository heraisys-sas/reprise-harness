# Playbook Ansible — déploiement et récupération Reprise (issue #225)

Outils orientés **opérateur humain** : utilisables même quand toute la chaîne AI
(experts, ponts, daemons) est enrayée. Aucune étape ne dépend d'un daemon AI vivant ;
tout passe par les opérations gérées du bundle (`inspect` → `plan` → `apply` → `verify`)
et les opérations launchd documentées, jamais par édition manuelle de fichiers managés.

## Premier réflexe : le diagnostic

```sh
cd deploy/ansible
ansible-playbook deploy.yml --tags diag
```

Lecture seule. Sonde : bundle, planificateur (`scheduler-service-inspect` inclus),
daemon Quick Apply `claude-desktopd` (socket + lsof), codexd (présence), pont Meta AI
(metaaid launchd, connexion, TaskStore, inbox/outbox du `bridge_root`), intégrations
(`verify` claude/harness), journaux. Rapport markdown écrit sous
`~/Library/Application Support/Reprise/diag/diag-<horodatage>.md` — rien d'autre.

## Carte de secours : symptôme → tag

| Symptôme | Commande | Ce que ça fait | Référence |
|---|---|---|---|
| Tout semble cassé, on veut voir | `--tags diag` | état complet lecture seule + rapport | — |
| Le planificateur ne tick plus | `--tags services` | repointe le plist scheduler + kickstart | [SCHEDULER-SERVICE](../../../docs/runbooks/SCHEDULER-SERVICE.md) |
| Quick Apply ne répond plus (socket orphelin) | `--tags services` | SIGTERM, retrait socket si `connect()` refusé, relance, attente | [MANAGED-UPDATE](../../../docs/runbooks/MANAGED-UPDATE.md#redémarrage-du-daemon-claude-après-apply) |
| Intégration claude/harness déréglée | `--tags integrations` | plan → apply → verify par API module | — |
| Le pont Meta AI ne répond plus | `--tags metaai` | répare modules/plist metaaid, recyle le daemon, attend port + signale montage | [METAI-INTEGRATION](../../../docs/integrations/metaai/METAI-INTEGRATION.md) |
| Copie Meta AI patchée périmée après update de l'app | `--tags metaai-copy -e reprise_metaai_redeploy=true` | plan lu + confirmé à la volée, apply, verify | [METAI-QUICKINVOKE](../../../docs/integrations/metaai/METAI-QUICKINVOKE.md) |
| Installer une nouvelle version du bundle | `--tags build,update` | build autonome puis managed_update (plan lu avant apply) | [RUNTIME-PACKAGING](../../../docs/runbooks/RUNTIME-PACKAGING.md), [MANAGED-UPDATE](../../../docs/runbooks/MANAGED-UPDATE.md) |

Double consentement sur toute écriture irréversible : variable dédiée (`reprise_auto_approve`,
`reprise_metaai_redeploy`) **et** confirmation à la volée (`pause`). `cleanup` n'est jamais
lancé par le playbook — décision humaine après lecture des nouveaux logs (le reçu liste
les seules sauvegardes supprimables).

## Rôles

| Tag | Rôle | Ce que ça fait |
|---|---|---|
| `diag` | `reprise_diag` | diagnostic lecture seule, rapport markdown horodaté |
| `build` | `reprise_build` | construit le bundle via `restart-chain --runtime-archive` |
| `update` | `reprise_update` | `managed_update` inspect/plan/pause/apply/verify (reçu horodaté) |
| `services` | `reprise_services` | repointe le LaunchAgent scheduler, recycle `claude-desktopd` |
| `integrations` | `reprise_integrations` | réapplique claude + harness via les outils propriétaires |
| `metaai` | `reprise_metaai` | répare/recycle le pont Meta AI (metaaid, port, montage) |
| `metaai-copy` | `reprise_metaai` | redéploie la copie patchée (consentement double) |
| `verify` | `reprise_verify` | preuve par `lsof` + vérification API module des intégrations |

## Usage

```sh
cd deploy/ansible
ansible-playbook deploy.yml --tags diag                 # voir, ne rien changer
ansible-playbook deploy.yml --tags services             # recycler les services
ansible-playbook deploy.yml --skip-tags build,update    # tout le reste sans reconstruire
ansible-playbook deploy.yml                             # tout (build + update inclus)
```

## Invariants respectés

- Aucun fichier managé édité à la main : intégrations via API module, plist via PlistBuddy,
  planificateur par ses opérations gérées, pont Meta AI par `metaai-install.py` et ses CLI.
- Pont Meta AI par fichiers (#277) : ni port ni montage ; le rôle recycle `metaaid` et vérifie
  qu'il tourne. Une clé WebDAV résiduelle dans `connections/metaai.json` est signalée par le
  diagnostic (metaaid refuse de démarrer avec, #276).
- Socket orphelin retiré seulement après `connect()` refusé (cas #105).
- Vérification par connexion/lsof, jamais par existence de fichier.
- `chatgpt` retiré des intégrations managées : route fermée (#226),
  `chatgpt_integration.py` supprimé du bundle sur dev.
- Rôles découplés pour réutilisation Orbstck.

## Limites connues

- `reprise_release_sha` vide → dernier build (pas de pilotage par SHA).
- Le rôle `services` ne gère pas `codexd` (jamais redémarré, chats propriétaires) ;
  `diag` signale seulement sa présence.
- Pas encore de `check_mode` fin (`--check` partiellement supporté ; `diag` l'est).
- Le redéploiement de la copie Meta AI patchée exige Meta AI (originale) fermée et
  les outils de build présents ; le plan refuse sinon.
