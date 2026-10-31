# Dépôts, workspaces et historiques

## Cartographier les portées

Distinguer : dépôt source Git, workspaces de chaque harness, projet UUID Reprise,
`--project-root` de chaque connecteur, `output_resume_project` de Harness,
registre expert, registre Quick Apply, socket et release réellement chargée.
Vérifier les chemins canoniques et le Git common-dir avant association.

Le registre `projects.sqlite` donne une identité stable au projet et des
associations explicites ; il ne migre ni clients ni délégations. Son interface
ne remplace pas librement une association occupée. Les reçus existants restent
indexés par leur ancien chemin, pas automatiquement par l'UUID.

Ne pas déplacer ni supprimer un ancien clone tant que des sessions, reçus ou
processus le référencent. Conserver les changements non publiés et leur origine.
Archiver les preuves privées hors Git ; un checkpoint publié ne doit pas contenir
le chemin personnel de l'opérateur.

## Conserver les registres

Identifier les états réellement sélectionnés avant chaque commande. Dans un
bundle, le layout par défaut place `state` et `quick-state` côte à côte. Définir
`AGENT_RESUME_STATE` au chemin du registre principal, même égal au défaut,
fait sélectionner `state/quick-state` pour le registre secondaire. Ne pas
introduire cet override pour « être explicite » ; utiliser les paramètres des
adaptateurs et préserver le layout constaté. Un layout custom existant doit
être conservé explicitement, pas normalisé sans migration validée.

Inventorier les tentatives running, awaiting_human/awaiting_approval,
needs_review et indeterminate sans publier questions/résultats. Un verrou expert
libre n'exclut pas un tour natif détaché encore actif. Ne changer aucune ligne
SQLite, aucune clé de requête ou phase pour débloquer la migration.

## Basculer les futurs appels

L'updater général conserve l'ancien projet : une mise à jour réussie ne change
donc pas les workspaces des clients.

- Harness : préparer `integration-plan` avec le nouveau `--project`, puis
  `integration-apply` et `integration-verify --project`. Reprendre les options
  du manifeste : rôle, délégation, droits d'écriture, continuation et ASI1.
  Conserver l'ancien `output_resume_project` si ses conversations sont actives ;
  il est distinct du périmètre d'expertise du nouveau connecteur.
- Claude : préparer `claude-integration-plan --project`, puis apply et verify.
  Omettre le choix `--enable-await`/`--disable-await` conserve la valeur existante.
  Le daemon encore lié à l'ancien projet produit un mismatch jusqu'au rebind.
- Codex : utiliser son CLI natif `codex mcp add` après inventaire de l'entrée
  existante. Ne changer que release/runtime et projet explicitement choisis ;
  conserver sockets, états, allowlist, options de backend et autres réglages.
  Ne pas ajouter de droit d'implémentation pour faciliter une installation.

Avant rechargement ou rebind Claude : vérifier attentes MCP, tâches réclamées
non acquittées, connexions socket et activité CDP. L'arrêt de claude-desktopd
peut aussi arrêter le Chrome piloté. Tant que le défaut #94 n'est pas corrigé et
vérifié dans le code ciblé, ne pas utiliser un PID de verrou comme preuve
d'identité ; différer l'arrêt si un verrou CDP existe. Ne pas supprimer le
verrou pour rendre ce contrôle vert. Même sans verrou, authentifier le daemon
par UID, commande native, release et socket, puis confirmer son arrêt réel.

Le dossier principal du projet dans l'interface du client reste une opération
distincte des paramètres MCP. S'il ne peut être changé par un outil autorisé,
donner l'action manuelle ; ne pas réécrire la base interne du client. Préserver
les chats attachés à leurs anciens workspaces et annoncer le chargement inconnu.

## Prouver l'accès historique avant de retirer une route

Conserver les anciens arguments et scopes dans l'audit privé. Avec le Python et
les helpers de la release sélectionnée, observer un reçu connu sous son ancien
projet, sans acquittement :

```sh
"$REPRISE_PYTHON" -E -s -B "$REPRISE_HELPERS/delegation_collect.py" \
  --state "$REPRISE_STATE" --project-root "$REPRISE_OLD_PROJECT" \
  --request-key "$REPRISE_REQUEST_KEY" --observe
```

Une observation locale ne réconcilie pas le tour natif : employer le
`reprise_reconcile(action="observe")` réellement découvert sur la route
historique pour cette étape. Pour un résultat terminal, le collector vérifie
longueur/empreinte et écrit dans un **nouveau** fichier avec `--output`.
Pour les tâches Claude anciennes, préserver l'accès `get_result` sous leur ancien
scope et vérifier une lecture connue sans acquittement avant bascule. Si cet
accès n'est pas disponible, drainer ou différer ; ne pas prétendre les avoir migrées.

## Avant de nettoyer un ancien checkout

Inventorier les références, pas seulement son dernier commit :

- `git -C "$REPRISE_SOURCE" worktree list --porcelain` : liens Git et branches ;
  `git -C "$REPRISE_OLD_CHECKOUT" status --porcelain` : changements non publiés.
  Les fichiers ignorés utiles demandent aussi une conservation explicite.
- Registre `project-list`, manifestes des connecteurs, projets de continuation,
  anciens arguments et reçus encore liés au chemin : inspecter uniquement les
  champs nécessaires, jamais publier des fichiers de configuration entiers.
- Arguments natifs des services/processus et fichiers ouverts, avec les outils
  autorisés du poste ; vérifier les tours/sessions propriétaires connus. Une
  liste de processus vide ne prouve pas qu'un chat froid a abandonné ce chemin.

Si une inspection est refusée, une référence persiste ou un propriétaire actif
reste inconnu, conserver le checkout. Ne nettoyer que sa propre copie isolée,
avec travail publié vérifié et fichiers nécessaires sauvegardés, dans le cadre
du nettoyage autorisé. Pour un worktree géré par le client, préférer son
archivage/récupération natif ; ne pas contourner sa protection par `rm -rf`.

Contrats du checkout : `agent-resume/PROJECT-REGISTRY-CONTRACT.md`,
`HARNESS-INTEGRATION.md`, `CLAUDE-CONNECTOR.md`, `DELEGATION-PERSISTENCE.md`,
`HANDOFF-CONTRACT.md`.
