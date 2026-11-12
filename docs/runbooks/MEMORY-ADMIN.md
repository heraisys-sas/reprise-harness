# Administrer explicitement les scopes mémoire

Ces commandes administrent le stockage local choisi par l'opérateur. Elles ne
configurent aucun harness. Utiliser un état existant privé et son chemin absolu
canonique : un alias symbolique est refusé. Les plans et listes contiennent des
métadonnées privées ; conserver leurs fichiers hors dépôt dans un dossier `0700`.

Le frontend est `reprise/reprise`. Les commandes ci-dessous sont des syntaxes,
avec des chemins et UUID à remplacer par les valeurs réelles de l'installation.
La clé du coffre doit déjà être accessible sans interaction pour une application.
Les commandes n'autorisent pas le Trousseau et ne lancent aucune inférence.

## Inspecter et préparer la migration

```sh
reprise/reprise memory-inspect --state /absolute/private/state
reprise/reprise memory-migration-plan --state /absolute/private/state > /absolute/private/plans/migration.json
```

Inspecter le plan : état, version, empreintes, identité des fichiers et avertissement
de rollback. Le plan ne crée ni stockage, ni répertoire, ni verrou. S'il existe des
clients mémoire actifs, coordonner leur arrêt avant migration ; le frontend ne les
arrête pas. Une release v1 refuse le stockage v2.

Appliquer seulement le plan retenu :

```sh
reprise/reprise memory-migration-apply < /absolute/private/plans/migration.json
```

Une réussite annonce `effect=recorded`, le résultat de migration et le nom de la
sauvegarde chiffrée lorsqu'un fichier v1 existait. Un état neuf ne reçoit pas de
fausse sauvegarde. Le stockage v1, ses identifiants et reçus sont conservés lors
de la migration ; la sauvegarde n'est pas une garantie d'effacement des anciennes données.

Un refus de plan périmé impose une nouvelle inspection et un nouveau plan ; ne
modifier aucun champ pour contourner le refus. Un résultat `indeterminate` exige
d'inspecter stockage, version et sauvegarde avant toute reprise. Aucun échec ne
prouve qu'une mutation n'a pas eu lieu. Ne restaurer aucune sauvegarde par défaut.

## Enregistrer un scope

La migration doit être terminée. Les projets existent déjà dans le registre.
Le sujet utilisateur et le travail sont des UUID explicitement choisis par l'hôte ;
ils n'authentifient aucun compte ni aucun service de travail externe.

```sh
reprise/reprise memory-scope-plan --state /absolute/private/state --kind user --subject-id SUBJECT-UUID > /absolute/private/plans/scope.json
reprise/reprise memory-scope-plan --state /absolute/private/state --kind project --project-id PROJECT-UUID > /absolute/private/plans/scope.json
reprise/reprise memory-scope-plan --state /absolute/private/state --kind work --project-id PROJECT-UUID --work-id WORK-UUID > /absolute/private/plans/scope.json
```

Choisir une seule de ces commandes, examiner son plan puis appliquer :

```sh
reprise/reprise memory-scope-apply < /absolute/private/plans/scope.json
reprise/reprise memory-scope-list --state /absolute/private/state
```

Le résultat fournit `scope_id`. L'enregistrement est idempotent, mais un plan
périmé reste refusé : produire un nouveau plan après une autre mutation. Enregistrer
un scope ne donne aucun droit à un client. Les grants et le lancement d'un client
avec `--scope-id` restent des opérations distinctes. Les installateurs projet v1
continuent à fonctionner et n'accordent aucun scope nouveau implicitement.

## Restauration et conservation

La sauvegarde `memory.v1-backup.enc` est chiffrée. Revenir à une release v1 exige
une procédure coordonnée distincte ; restaurer cette sauvegarde après des écritures
v2 perdrait ces écritures. Les scopes et reçus v2 ne sont pas rétroconvertis par ce
frontend. Il n'existe ni commande de purge physique ni garantie d'effacement des
snapshots, sauvegardes ou contenus déjà remis à un client.

Le module `memory_admin.py` et ce runbook doivent être ajoutés au payload vérifié
avant usage depuis une application empaquetée. Leur présence dans le checkout ne
prouve pas leur distribution dans une release installée.
