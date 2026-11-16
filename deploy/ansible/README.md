# Playbook Ansible — déploiement Reprise

Premier jet (issue #225). Orchestre les étapes du runbook réel (`docs/runbooks/`) :

| Tag | Rôle | Ce que ça fait |
|---|---|---|
| `build` | `reprise_build` | construit le bundle via `restart-chain --runtime-archive` |
| `integrations` | `reprise_integrations` | applique claude/chatgpt/harness via les outils propriétaires (`plan`→`apply`→`verify`) |
| `services` | `reprise_services` | repointe le LaunchAgent, recycle le daemon `claude-desktopd` |
| `verify` | `reprise_verify` | preuve par `lsof` + vérification API module des intégrations |

## Usage

```sh
cd deploy/ansible
ansible-playbook deploy.yml --tags build          # build seul
ansible-playbook deploy.yml --skip-tags build     # le reste
ansible-playbook deploy.yml                       # tout
```

## Invariants respectés

- Aucun fichier managé édité à la main : intégrations via API module Python, plist via PlistBuddy.
- Socket orphelin retiré seulement après `connect()` refusé (cas #105).
- Vérification par connexion/lsof, jamais par existence de fichier.
- Rôles découplés pour réutilisation Orbstck.

## Limites du premier jet

- `reprise_release_sha` vide → dernier build (pas de pilotage par SHA).
- Le rôle `services` ne gère pas `codexd` (jamais redémarré, chats propriétaires).
- Pas encore de `check_mode` fin (`--check` partiellement supporté).
