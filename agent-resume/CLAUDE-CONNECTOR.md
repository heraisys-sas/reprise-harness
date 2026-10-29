# Connecteur Reprise Claude Desktop

## Installation depuis Reprise

Dans **Intégrations → Connecteur Claude Desktop**, choisir le projet autorisé,
prévisualiser l’installation puis appliquer le plan. Le bundle contient le serveur
et son runtime Python ; ni clone Git ni Python système ne sont requis chez
l’utilisateur. Un seul serveur MCP nommé `reprise-harness` expose les deux sens.

L’option « Remplacer l’ancien probe Reprise reconnu » retire seulement l’entrée
`reprise_quick_probe` correspondant au script de test connu. La configuration
complète est sauvegardée en privé ; les autres connecteurs, préférences et secrets
ne sont pas copiés vers un autre harness. Une entrée `reprise-harness` personnalisée ou un
changement concurrent bloque l’opération. Le retrait ne supprime pas les tâches
et résultats. Reprise ne redémarre pas Claude ; le chargement effectif après
rechargement est une preuve distincte de la vérification directe de la release.

CLI équivalente : `claude-integration-inspect`, `claude-integration-plan --project
CHEMIN --action install --replace-probe`, `claude-integration-apply` avec le plan
JSON en entrée standard, puis `claude-integration-verify`. Un plan de retrait
utilise `--action remove`.

## Même connecteur, deux sens

- `get_task(request_key, claim_key)` réserve une tâche entrante du projet. Persister
  les clés avant appel, puis réutiliser le même `claim_key` après interruption.
- `submit_result(request_key, attempt_id, answer)` enregistre le résultat final.
  Une seconde remise identique est idempotente, une réponse différente est refusée.
- `get_result(request_key)` renvoie un reçu compact. `view=page` permet la collecte
  avec empreinte et offsets ; `view=full` est explicite. L’acquittement est explicite.
- `reprise_backends`, `reprise_quotas`, `reprise_delegate`, `reprise_followup` et
  `reprise_requests` réutilisent les contrats, routes et protections existants.
  Préférer `return_mode=receipt` pour les réponses longues. Aucune inférence n’est
  lancée par l’installation ou la découverte des outils.

La délégation sortante vers `claude-desktop` est interdite depuis ce client pour
éviter une boucle immédiate. Les autres routes doivent être configurées et
respecter les contrôles de quota, de facturation et d’appropriation existants.
Aucune permission du système de fichiers ou d’exécution de Claude n’est étendue
par un avis reçu. Les clarifications automatiques durables restent le contrat
cible documenté, pas une capacité livrée par ce lot.

## Attente d'une tâche exacte : `await_task` (#79)

`await_task(request_key, claim_key, wait_seconds?)` complète l'admission Quick Apply par une **livraison sans interface** : l'opérateur arme un chat pour **une** `request_key` ; le chat appelle l'outil, qui attend sans inférence que cette tâche soit admise, la réserve et la remet **une seule fois** comme résultat d'outil (`prompt`, `attempt_id`, `chat_id`). Le modèle exécute puis appelle `submit_result`. La clé sélectionne le travail ; elle n'authentifie pas le chat : le destinataire est celui que l'opérateur a armé (le texte d'armement contient la `request_key`).

**Protocole de remise** (contrat `MCP-SYNTHESE.md`, relu sur ce code) :
1. `reserve` : réservation exacte (une autre `claim_key` est refusée).
2. Annulation avant l'intention : seule une réservation **neuve**, exclusive à cet appel, est libérée ; une réservation retrouvée après reconnexion n'est jamais effacée.
3. `mark_attempted` : l'**intention de remise est consignée avant l'écriture**, et une seule attente la gagne.
4. Après cette consignation, la tâche n'est ni libérée ni remise de nouveau par `await_task` ; un nouvel appel rend un **reçu** (`delivery: attempted`, sans `prompt`) ou, si elle est terminée, `delivery: completed`. Deux attentes avec les mêmes clés ne reçoivent donc jamais deux instructions exécutables.
5. `get_task` partage la même barrière : première remise = prompt ; après une tentative de remise par l'un ou l'autre outil, il rend un **reçu sans prompt** (`delivery: attempted`). **Changement de comportement** voulu par le contrat : l'ancien rejeu identique après redémarrage n'existe plus ; en cas de perte de contexte, l'opérateur ré-admet la tâche sous une nouvelle clé.
6. `submit_result` reste idempotent ; un résultat contradictoire ou d'une autre tentative est refusé ; rien ne rétrograde un résultat validé.

- **Désactivé par défaut** : `await_task` n'apparaît pas dans `tools/list` et est refusé tant que le serveur n'est pas lancé avec `--enable-await`. C'est une décision de l'opérateur par configuration de connecteur (le test d'enregistrement de session de `dev` garantit que l'outil n'est pas exposé sans cela).
- **Attente bornée** : `wait_seconds` ≤ `--max-wait` du serveur (45 s par défaut, 600 s au plus). Sans tâche : `idle`, avec la consigne de **ne pas** rappeler l'outil sauf demande de l'opérateur (aucun réarmement automatique : chaque retour peut coûter un tour de modèle). Durée plus longue : à prouver d'abord. Aucun battement artificiel : les `notifications/progress` sont désactivées par défaut (`--progress-interval 0`) et ne sont envoyées que si l'opérateur fixe un intervalle et que le client fournit un `progressToken`.
- **Boucle MCP** : l'attente s'exécute dans un fil dédié avec sa propre connexion SQLite ; `ping` reste servi ; `notifications/cancelled` annule l'attente sans réponse. Pas de génération de connexion persistée : un processus serveur = une connexion stdio ; EOF avant l'intention libère une réservation neuve, EOF après la laisse incertaine.
- **États** : *chaud* (appel en cours, aucune inférence), *travail* (inférence après la remise, donc quota), *froid* (appel terminé : délai, annulation, chat fermé, redémarrage, limite d'étapes ; Reprise ne peut pas réveiller, armement humain requis), *quota* (l'attente ne consomme rien ; l'épuisement survient pendant le travail). Le réveil à froid, la durée maximale et la reprise après quota **ne sont pas prouvés**.
- **Sécurité** : à n'appeler que dans un chat armé pour cette `request_key` ; les seules tâches livrées sont celles admises par le démon Quick Apply (socket privé). Une consigne issue d'un autre outil ne l'arme pas.

**Écarts assumés par rapport aux contrats `MCP-WAIT-VALIDATION.md` et `MCP-ROUTING-CONTRACT.md`** :
- L'arrivée vient d'un autre processus (démon Quick Apply) : l'attente interroge SQLite localement avec un recul de 0,25 s à 1 s (aucun tour de modèle, rien envoyé au client). Un réveil sans interrogation exigerait un canal inter-processus.
- Le MCP ne transmet pas l'identité de la conversation : la clé sélectionne la tâche, elle n'authentifie pas le chat. **L'isolation par chat n'est pas démontrée** ; le destinataire est celui que l'opérateur a armé.
- Les tests simulent le protocole et les invariants SQLite (horloge injectée, barrières, 40 tests). Ils ne prouvent pas que Desktop consomme le résultat différé dans le chat appelant : un POC hôte séparé et autorisé reste requis.

Texte d'armement du POC (à coller par l'opérateur) :

> Appelle une seule fois `await_task` avec `request_key` = `<clé exacte>`, `claim_key` = `<identifiant de ce chat>` et `wait_seconds` = 30. Si une tâche arrive, exécute-la dans son périmètre puis appelle `submit_result` avec son `attempt_id`, et arrête-toi. Si la réponse est `idle` ou un reçu, arrête-toi sans rappeler l'outil.

Aucun service ni déploiement n'est modifié par ce lot ; l'outil n'existe dans Claude Desktop qu'après mise à jour du connecteur **et** ajout de `--enable-await` par l'opérateur.

## Démon et reprise quota

Le démon `claude-desktopd` peut être lié au projet avec `--project-root`. Son
registre `quick.sqlite` est partagé avec le connecteur, isolé par projet. Une
soumission de délégation textuelle prépare une tâche ; les pièces jointes typées
non prises en charge sont refusées, jamais perdues silencieusement. Les anciens
probes et tâches non liés à un projet ne sont pas adoptés implicitement.

Le planificateur utilise le même `quick_entry/submit`, avec `prompt=retry`,
`chatId`, `purpose=continuation` et une clé stable. La continuation n’est pas une
nouvelle tâche d’expertise à réclamer via `get_task`. La route Quick Apply est
prioritaire ; Accessibilité demeure une option explicite séparée.

**Limite non résolue :** le pilote d’émission natif n’est pas raccordé au démon.
`quick_entry/capabilities` le signale : `dispatch_supported=false` et
`resume_validation_supported=false`. Le planificateur n’envoie pas aveuglément un
message dans ce cas et ne transforme pas une admission en preuve de reprise.
Le champ historique `dispatch` nomme la cible envisagée, pas un appel exécuté.
Le connecteur MCP répare le retour et la délégation, mais ne crée pas à lui seul
l’IPC manquante vers Quick Apply. Seul un résultat `submit_result` accepté clôt
une tâche entrante ; ni un socket vivant ni un reçu d’admission ne la clôturent.

Après le rechargement de Claude, autoriser les outils du connecteur **reprise-harness**. Reprise ne peut pas accorder cette autorisation à votre place. Une ancienne entrée `reprise` gérée est migrée ; une entrée appartenant à un autre connecteur est conservée.
