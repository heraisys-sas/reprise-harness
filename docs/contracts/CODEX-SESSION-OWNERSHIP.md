# Contrat de découverte des propriétaires Codex — lot #170

État : **inventaire des stockages livré**, attribution du propriétaire et reprise inter-serveurs **non vérifiées**. Baseline : `dev@194ad1e9de715f0f9017ba1b57729099b97544e6`. Brancher les étapes suivantes séparément.

## Pourquoi

ChatGPT Desktop peut lancer un Codex app-server privé en stdio même si le service
`codexd` est actif et son socket accessible. Les sessions privées ne deviennent pas
automatiquement des threads gérables par `codexd`. Une session peut être visible
dans l'index local sans qu'aucun autre serveur puisse en posséder l'écriture.

Les identités sont distinctes :

- `work_id` : travail durable du registre Reprise, indépendant du processus.
- `session_key` : hash du profil Codex canonique et du `thread_id` natif.
- `thread_id` : UUID natif (non unique **entre profils** sans qualification).
- `owner_instance_id` : propriétaire actuel **inconnu** tant que non authentifié.
- `process_instance_id` : identité du processus observée par un collecteur dédié ; ne vaut pas preuve de possession d'une session.

Une conversation sur `chatgpt.com` n'est pas un thread Codex par simple similitude du nom de l'app.

## Lot implémenté

Commande explicite et **lecture seule** :

```sh
python3 -B reprise/codex_session_owners.py \
  --codex-home "$HOME/.codex" \
  --codex-home "/profil/CODEX_HOME/explicitement/verifie"
```

La commande n'examine que `state_5.sqlite` dans les racines fournies (ou
`~/.codex` par défaut) : `SELECT id,cwd,updated_at FROM threads`
sur une connexion SQLite `mode=ro` + `PRAGMA query_only=ON`.
Elle ne lit ni titres, contenus de conversation, rollouts, jetons ni historiques.
Elle ne lance aucun processus et ne contacte aucun socket.

Les résultats ne publient pas les chemins de dossiers ; `profile_id`, `cwd_sha256`
et `session_key` permettent de comparer les observations sans divulguer
les chemins de travail. Les collisions UUID inter-profils restent séparées.
Un index absent, inaccessible, altéré ou tronqué produit `status: partial`
et un code de sortie 2, même si d'autres profils sont lisibles.

**Limite physique :** sur un index SQLite actif en mode WAL, un lecteur SQLite
peut provoquer des effets sur le fichier partagé `-shm`, bien que le mode
`ro` et `query_only` interdisent toute écriture SQL. L'outil ne
certifie donc pas l'absence absolue de modification de métadonnées système.

## État des preuves et des capacités

| Observation | Déduction autorisée |
|---|---|
| UUID présent dans `state_5.sqlite` | `storage_observed=true` |
| Socket app-server accessible | Endpoint disponible, **pas propriétaire prouvé** |
| `thread/read` sur un endpoint choisi et authentifié, UUID et cwd correspondants | `rpc_readability=confirmed`, pas droit d'écriture |
| `thread.status.type=idle` | État signalé par cet endpoint, pas preuve de libération du writer |
| `thread/resume` et `turn/start` | **Non appelés dans ce lot** |
| Processus/GUI présent | Processus présent, pas attribution de possession |

`read_proof(indexed, observed_thread)` réalise uniquement une comparaison
pure de métadonnées issue d'un `thread/read` ultérieur. Même si la réponse
correspond, `owner_state` reste `unknown` et `controllable=false`.

## Lots futurs (non livrés)

1. Recenser de façon **native et passive** les processus Codex connus (UID, PID,
   date de départ, binaire, profil), sans inférer la propriété d'un thread.
2. Authentifier explicitement le transport d'un app-server propriétaire avant
   lecture RPC, sans démarrer de serveur de substitution.
3. Réconcilier provenance de stockage, thread, endpoint et état du writer
   avec des preuves fraîches. En cas de doute : `blocked_owner` ou `owner_unknown`.
4. Tester la continuation par le propriétaire existant, puis seulement une
   éventuelle libération contrôlée et revalidation avant tout autre writer.
5. Brancher la vue dans Reprise après tests réels. Ne pas modifier le scheduler
   ni proposer une relance automatique de l'app Desktop.

**Interdits :** suppression de verrous, arrêt d'un propriétaire externe,
copie de profil, reparenting implicite, fallback payant ou réenvoi après
une issue incertaine.

Liens : #25, #117, #161, #170.
