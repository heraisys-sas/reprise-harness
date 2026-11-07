# Premier lot — protection des délégations persistantes

Implémentation du 21 octobre 2026, sans appel fournisseur réel ni redémarrage de service. Ce lot consolide les prototypes existants ; leur réunion en service logique commun et le branchement autonome Quick Apply → tâche MCP restent à livrer.

## Contrat utilisable

Avant chaque appel d’inférence (`reprise_delegate`, `reprise_followup`, `codex_review`, `codex_followup`), le demandeur doit **persister une `request_key` non vide, de 128 caractères maximum**. Même clé + mêmes arguments, parent et empreintes des fichiers = même reçu, sans nouvelle exécution. Même clé + contenu différent = refus. Les anciens clients sans clé doivent être adaptés ; aucune clé aléatoire créée côté serveur ne masque un accusé perdu.

`reprise_requests({"request_key":"…"})` récupère l’état et le retour enregistré. Sans clé, l’outil liste les 100 demandes les plus récentes du projet configuré, avec leur état et acquittement. Les demandes plus anciennes restent accessibles par leur clé. `acknowledge:true` acquitte la consommation d’un reçu enregistré, de manière répétable ; ce n’est pas une validation métier. Les lectures ne prennent pas `expert.lock`.

Le registre `delegation_requests` réside dans `expert.sqlite` ; son périmètre est le chemin canonique du projet, indépendant du socket, dans le répertoire d’état configuré. Deux installations avec des répertoires d’état différents ne partagent pas cette protection. Le résultat complet est privé, comme les autres données locales du pont. Ce prototype conserve le périmètre d’accès du client local configuré ; il n’ajoute pas une authentification multi-utilisateur. Une clé est un identifiant, pas une autorisation.

Avant tout appel externe, une transaction courte enregistre une tentative `indeterminate` avec son identifiant stable. Une exception, un arrêt du processus, `timed_out`, `needs_review`, un refus de permission sans preuve d’arrêt ou un statut inconnu conservent cette barrière. Une nouvelle clé équivalente ne permet pas de contourner une tentative incertaine. Les refus établis avant exécution (quota, authentification, facturation, moteur absent, occupation) et les retours terminés peuvent être enregistrés. Aucun acquittement ne lève une incertitude ; aucune retransmission automatique n’est ajoutée. Une panne avant l’envoi peut donc nécessiter une réconciliation manuelle : prudence assumée, pas garantie d’exécution exactement une fois chez le fournisseur.

L’identité couvre les arguments et les SHA-256 des fichiers explicites, bornés à 30 Mo par fichier pour le calcul. Les limites plus strictes des adaptateurs restent applicables. Le pont revérifie les fichiers au retour : changement/suppression = résultat à réconcilier (`needs_review`). Ce contrôle ne fige pas le dépôt consultable par les outils Codex, et ne prouve pas l’exhaustivité du contexte lu. Le prototype UX, lui, conserve son texte et sa pièce jointe dans le manifeste SQLite.

## Lignée et concurrence

`parent_request_key` rattache un enfant à une demande du même projet. Parent, racine et profondeur sont immuables. Les follow-ups retrouvent par défaut la dernière demande du thread ; une relecture de la même clé conserve son parent initial. Une tentative ancêtre incertaine bloque la descendance. Une demande équivalente à un ancêtre est refusée avec `cycle_blocked`. L’empreinte d’équivalence ignore l’identifiant de thread et le nom alias de l’outil, mais garde backend, nature, niveau, question et entrées explicites. Des formulations différentes ne sont pas reconnues comme sémantiquement équivalentes. Les nouveaux enfants doivent déclarer leur parent ; le service ne peut pas deviner une lignée omise ou traversant un autre registre.

Le verrou existant conserve **une expertise active par répertoire d’état**, pour éviter les lancements simultanés vers une destination native dont l’exclusivité n’est pas assurée. Les transactions SQLite se terminent avant l’inférence. La profondeur de délégation est bornée à huit liens pour contenir la récursion et le parcours d’ascendance ; ce n’est ni un quota quotidien ni un nombre maximal d’itérations internes. Les racines indépendantes terminées peuvent être répétées. Aucun arrêt automatique, relance ou nouvelle délégation n’est déclenché par une suspicion de boucle interne ; leur observation détaillée reste le lot de supervision prévu au plan.

## Quick Apply et retour Claude

`quick_entry/submit` exige également `request_key`, stockée dans `quick.sqlite`. Même clé retrouve la même soumission. `quick_entry/requests` expose recherche/liste/acquittement du reçu local. Les fichiers joints sont identifiés par contenu au moment de l’admission ; le pilote ne bénéficie pas pour autant d’un fichier matériel immuable. Une panne entre l’admission et le reçu laisse une tentative incertaine, sans nouvel envoi. Le daemon utilise un verrou de socket, refuse de remplacer un socket vivant et borne à cinq secondes l’attente inactive d’un client ; il conserve un traitement séquentiel.

Le pont transmet la clé à Quick Apply et propage `admission_only:true`, `response:null`. Son `finished` signifie seulement reçu de soumission ; **seul `submit_result` accepté et commité vaut résultat Claude**. Le lot n’active aucun pilote Desktop ni repli payant.

Mode orchestré (issue #102) : `reprise_delegate(backend="claude-desktop")` avec `tasks` et `request_key` enchaîne dans un seul appel l’admission durable (`tasks.admit` dans `state/quick.sqlite`, prompt augmenté stocké avec la clé, plafond 12 000 caractères sans troncature), le dispatch Quick Apply (`purpose:'delegation'`, `orchestrated:true`), puis la collecte bornée (`max_wait`, plafond 600 s) du `submit_result` validé, avec empreinte SHA-256 canonique. La ligne est marquée `delivery='attempted'` seulement après le succès de `initialize`, sous garde SQL (`delivery IS NULL AND answer IS NULL`) : un rappel avec la même clé ne réémet jamais — il retourne un reçu explicite `admission_only` (ou la réponse collectée si déjà enregistrée). Sans canal de retour disponible, l’appel échoue avant envoi (`return_channel_unavailable`, `automatic_fallback:false`). Voir les tests `test_delegation_desktop_cycle.py`, `test_delegation_desktop_result.py`, `test_delegation_desktop_redispatch.py`.

Dans `ux_review_probe.py`, démarrer avec `--request-key` persistée avant le déclenchement. `get_task({"claim_key":"…"})` exige une clé de réservation persistée côté exécutant avant l’appel. Même clé récupère la même tentative et les mêmes entrées, avec `recovered_reservation:true`, sans augmenter le nombre de réservations. Une autre clé ne reçoit pas la tâche. Cette relecture n’autorise pas une deuxième inférence si la première peut continuer.

`submit_result` conserve l’idempotence des résultats identiques et refuse un conflit. `get_result({"request_key":"…"})` retrouve le résultat ; `acknowledge:true` acquitte sa consommation. Un second processus ne déclare plus les tâches du premier interrompues par sa seule ouverture. Un résultat tardif de la tentative connue reste accepté. `--recover-existing` recharge le manifeste sans relire le fichier source, même modifié ou supprimé ; sans cette option, une entrée différente est refusée. Un ancien état UX sans manifeste est conservé mais refusé : le réconcilier avant migration, sans effacer son résultat.

## Plafonds et limites conservées

Les plafonds quotidiens Claude Code, Claude Desktop et Quick Apply sont supprimés. `expert_calls` et `quick_calls` hérités restent inertes ; ils ne sont ni lus pour l’admission ni incrémentés. `daily_limit` et `--daily-limit` sont uniquement acceptés comme compatibilité obsolète, sans validation de plage ni effet. Les nouveaux launchers et plists ne les émettent plus.

La garde fournisseur Codex, dont la réserve **5 %** et le refus de quota inconnu, reste inchangée. Aucun budget utilisateur explicite n’est retiré. La connaissance fraîche des quotas par compte/profil/pool et la politique de reprise associée constituent le prochain lot.

## Justification et validation

Le choix de distinguer résultat reçu et exécution incertaine suit la sémantique des pannes RPC décrite par Andrew D. Birrell et Bruce Jay Nelson, *Implementing Remote Procedure Calls*, ACM TOCS 2(1), 1984, sections 1.4 et 3 ([texte primaire](https://www.cs.cmu.edu/afs/cs/academic/class/15712-s19/www/papers/birrell84.pdf)). Ce travail n’établit aucune déduplication native de Codex/Claude. Les admissions locales utilisent les transactions `BEGIN IMMEDIATE` documentées par [SQLite](https://www.sqlite.org/lang_transaction.html), sans étendre leur atomicité à l’appel externe. Les garanties décrites ici sont celles de notre code et de nos tests simulés.

Tests de panne : `tests/test_delegation_persistence.py` ; suites existantes du pont, de Quick Apply, de l’installateur et du prototype UX adaptées au nouveau contrat. Validation réelle du pilote Claude, compte gratuit, permissions macOS, réception autonome et reprise après panne de machine encore à confirmer. Aucun résultat visible dans les logs ou sur disque n’est promu en reçu MCP.

Validation finale du 21/10 : `python3 -m unittest discover -s reprise/tests -q` hors sandbox : **276 tests, OK, 1 skipped**, dont 19 tests de persistance et de concurrence. `git diff --check` et `py_compile` des sept modules Python modifiés passent. Le premier passage sandbox rencontrait des refus d’accès aux sockets de santé/scheduler ; le passage autorisé hors sandbox est vert. Des `ResourceWarning` de fixtures existantes subsistent, sans échec. Ruff, Gitleaks et Semgrep ne sont pas installés ; aucun résultat de scanner de sécurité n’est revendiqué. Aucun Swift modifié, donc aucune compilation ni relance native.


## Résultats longs — issue #32

Depuis 0.1.4, les quatre outils d’inférence acceptent `return_mode=receipt`,
y compris les continuations et alias. Ce paramètre de transport ne change ni
l’identité immuable ni la lignée, et sa modification ne relance pas l’inférence.
La découverte `initialize` donne version et SHA-256 du module exécuté ; les
schémas `tools/list` prouvent les paramètres disponibles. Une vérification directe
de release ne prouve jamais que le client Harness l’a déjà chargée.

Depuis 0.1.9, le mode `full` (défaut) livre le résultat tel quel jusqu’à 48 000 caractères, plafond de capture antérieur à #32 : aucun résultat existant ne change. Au-delà,
le pont renvoie le manifeste du reçu (`return_mode_effective=receipt`, `status`
du résultat, `total_length`, `result_sha256`, sans `response`), y compris pour un
résultat incertain et pour une rediffusion de la même clé ; la lecture paginée
`reprise_requests view=page` ou `delegation_collect.py` récupère le contenu complet.
Le `status` d’un manifeste `receipt` est celui de la délégation (non plus celui de la
lecture du reçu), donc un résultat `needs_review` n’est plus présenté comme réussi.
Le helper écrit désormais par fichier temporaire privé puis liaison atomique : un échec
ne laisse aucun fichier partiel et un nouvel essai reste possible.

La capture Codex conserve jusqu’à 2 000 000 caractères Unicode ; au-delà,
le résultat est explicitement incomplet et `needs_review`. Le helper installé
`delegation_collect.py` assemble les pages en lecture seule, vérifie empreinte et
longueur avant création exclusive d’un fichier privé. Il ne fait aucune inférence
ni acquittement et refuse une capture déclarée incomplète. Voir les instructions
de la skill pour son invocation. Les pages ne résolvent pas l’élagage des sorties
des outils natifs d’un autre harness.

## Attente client, tour natif détaché et réconciliation (issue #65)

Depuis 0.1.14, l’attente du client, l’exécution native, l’inactivité et les limites autorisées
sont quatre notions distinctes. `wait_seconds` (défaut 30) borne uniquement l’attente de
l’appel MCP : à son expiration, `turn/interrupt` n’est **pas** envoyé, le tour natif continue
et le reçu reste `state="running"` avec `phase="active"`. `execution_seconds` et
`inactivity_seconds` sont optionnels ; l’inactivité ne produit qu’un diagnostic
(`inactivity_observed`, `idle_seconds`, `events_observed`) et n’arrête rien.
`stop_on_deadline=true` n’interrompt que si l’appelant a fourni `execution_seconds`.

La table `delegation_turns` (même base, créée par `Store`, aucune migration manuelle)
conserve `attempt`, `thread_id`, `turn_id`, `owner`, `started_at`, `deadline_at`,
`last_event_at`, `event_count` et un journal d’événements borné. L’identité native est
écrite par `on_turn` avant `turn/start` puis dès son accusé reçu, donc avant toute
attente et avant la réponse MCP.

Phases de réconciliation, exposées par `reprise_requests view="receipt"`
(`phase`, `work_completed`, `terminal_evidence`, `next_action`) :

| phase | sens | `work_completed` | action |
|---|---|---|---|
| `starting` | fil créé, tour non confirmé | non | `reprise_reconcile` |
| `active` | tour natif en cours | non | `reprise_reconcile` |
| `awaiting_approval` | approbation humaine demandée (`pending_method`) | non | approbation humaine ou `action="stop"` |
| `finished` | tour terminé et livrable capté | oui | `delegation_collect.py` |
| `interrupted` | arrêt confirmé, travail non achevé | non | aucune relance implicite |
| `owner_unavailable` | natif ou texte final non restituable | non | `next_action="collect"`, incertitude conservée |

`reprise_reconcile(request_key, action="observe"|"stop")` ne fait jamais de `turn/start`.
Il lit le tour (`thread/read includeTurns`, avec `thread/resume` sans tour uniquement pour
charger l’historique) et capture un résultat tardif. `action="stop"` exige un motif fermé
(`user_stop`, `execution_deadline`, `budget`) et journalise l’interruption. Une clé en état
`running` n’est pas acquittable, bloque une délégation équivalente et reste consultable
après redémarrage ; `delegation_collect.py --observe` imprime le même manifeste en lecture
seule. L’appropriation concurrente est enregistrée (`claim` : `owned`, `held`, `taken_over`)
sans nouveau tour. Aucun état SQLite n’est forcé.

Limite documentée : si la version native ne restitue pas le texte des items d’un tour
terminé, le reçu reste `owner_unavailable` avec `next_action="collect"` ; Reprise ne crée pas
un second tour pour récupérer ce texte.

### Précisions de validation #65 (0.1.14)

- La réconciliation initialise la connexion native avant observation. Un `turn_id` absent ou
  introuvable reste incertain : aucun dernier tour du thread n’est adopté par supposition.
- Les anciens reçus sans `delegation_turns` sont raccordés seulement si leur résultat conserve
  les deux identifiants et si `expert_threads` confirme la propriété dans la route configurée
  (projet et socket). Un ancien reçu sans cette preuve reste bloqué, sans SQL manuel ni rejeu.
- Une interruption RPC acceptée n’est pas un arrêt confirmé. Seul le statut natif
  `interrupted` du tour exact autorise le reçu terminal ; refus, perte d’ACK ou tour encore
  actif restent `needs_review`. La cause initiale d’un timeout reste conservée lorsque
  `input_changed` est ajouté ; la collecte tardive ne valide pas les entrées modifiées.
- Les empreintes des entrées explicites sont revérifiées lors de la collecte tardive en
  mode expertise. Les routes et le mode implementation sont persistés avant l’attente,
  y compris si le retour MCP est perdu. Modifier seulement les délais ne contourne pas
  la garde contre les tentatives équivalentes incertaines.
- Une attente d’approbation persistée reste visible tant que le natif ne fournit pas de
  preuve terminale ; l’absence de champ approval dans `thread/read` ne vaut pas accord.
- Aucun collecteur ni superviseur de budget autonome n’est ajouté. Une échéance autorisée
  doit tenir dans la fenêtre d’attente connectée et précéder le diagnostic d’inactivité,
  sinon l’admission est refusée. Une déconnexion ou une demande humaine peut interrompre
  cette surveillance : observer le reçu puis arrêter explicitement si nécessaire.
- Pendant l’attente connectée, le verrou existant protège l’appel actif : une réconciliation
  concurrente renvoie `busy`. Après détachement, l’observation et l’arrêt explicite deviennent
  disponibles ; aucune réservation par PID ne demeure après le retour normal.
- Les intégrations gérées avec délégation incluent désormais `reprise_reconcile` dans leur
  allowlist lors du prochain plan/apply. Les anciennes installations nécessitent cette
  migration explicite ; remplacer les fichiers de la release ne suffit pas. Aucun profil
  réel ni service n’est modifié par le lot.
- Les tests utilisent exclusivement des fixtures. L’essai long natif, la continuité réelle
  après fermeture du client et le déploiement restent à valider séparément par l’opérateur.

## Barrières de dispatch séparées

La réservation d'une livraison Quick Apply et la réservation du socket sont indépendantes (table `desktop_dispatch_guard`) et portent chacune un reçu mémorisé :

- Les tâches préexistantes à la migration sont marquées `legacy_unknown` : les anciens dispatchs directs ne laissaient pas de trace.
- Une tentative sans reçu reste incertaine après redémarrage ; jamais de réémission automatique.
- La livraison MCP ne transporte que `delivery`, `attempt_id` et `claim_key`.
- Les anciens writers doivent être arrêtés ou mis à niveau avant la migration : ils ne respectent pas cette table.

## Attente de la réponse Desktop — issue #111

`wait_result(request_key, wait_seconds=600)` sur `reprise_codex` observe une tâche
Desktop existante du projet. Durée finie strictement positive, maximum 600 s ;
booléens, clés vides ou dépassant 128 caractères et paramètres supplémentaires refusés.
Un registre absent, illisible, verrouillé ou incompatible produit une erreur
`desktop_registry_unavailable`, distincte d'une clé absente. Aucune création ni migration
par l'observateur. L'initialisation habituelle du pont reste inchangée.

| status | state | completed | idle | next_action |
| --- | --- | --- | --- | --- |
| finished | recorded | true | false | get_result |
| timeout | pending | false | true | reconcile |
| not_found | omis | false | true | reconcile |
| busy | omis | false | true | reconcile |

Chaque retour porte `request_key` et `backend="claude-desktop"`. Les deux dernières
lignes omettent le champ `state`. `finished` porte le `result_sha256` canonique de
`get_result` (JSON de `{"answer": ...}`, pas le hash du texte brut) ;
`timeout` porte `waited_seconds`. Ces quatre statuts sont des résultats d'observation,
pas des erreurs MCP. L'annulation ne produit pas de résultat.

L'algorithme ouvre une connexion SQLite dédiée `mode=ro`, autocommit, sans attente
de verrou SQLite ; lit immédiatement ; espace les snapshots par 250 ms, 500 ms puis
1 s ; borne chaque pause par une échéance monotone ; effectue la dernière lecture
avant de décider timeout. Un commit visible dans cette dernière lecture gagne la
course ; un commit ultérieur reste récupérable. Aucune transaction ni aucun curseur
ne reste ouvert pendant les pauses. Les erreurs de stockage ne deviennent jamais
`not_found` ou `timeout`.

Huit workers annulables au maximum par connexion stdio, sans file d'attente.
Le neuvième appel valide reçoit `busy`. Les réponses JSON-RPC sont sérialisées ;
`ping` reste disponible pendant les attentes. `notifications/cancelled` annule par
identifiant RPC ; EOF et arrêt annulent tous les workers, avec un budget commun
de join d'une seconde. Les threads sont daemon : une sortie bloquée ne peut pas
retenir le processus. Une annulation peut croiser une réponse déjà envoyée.

Aucune modification de réserve, claim, attempt_id, delivery ou acknowledged.
Plusieurs observateurs peuvent lire le même résultat, sans consommation.
`await_task` attend la livraison du prompt ; `wait_result` attend sa réponse.
Ni timeout, annulation, reconnexion ni redémarrage n'autorisent une réémission.

Invocation : le superviseur lance l'appel bloquant dans un job d'arrière-plan et
conserve sa clé. Aucun tour de modèle n'est immobilisé et aucune boucle de modèle
ne sonde le registre. Après timeout, relire `reprise_requests` et `get_result`
sur le connecteur Desktop sous la même clé ; éventuellement relancer uniquement
l'observation `wait_result`. `next_action="reconcile"` désigne cette procédure,
**pas** `reprise_reconcile`, réservé aux tours natifs Codex.

Pour une clé explicite, `reprise_requests` projette le résultat Desktop tardif si
le résultat historique est `backend="claude-desktop", admission_only=true`.
Les vues receipt/page emploient le même JSON et la même empreinte que `get_result`.
La provenance reste dans `source="desktop_tasks"` et `delegation_receipt` ;
la vue full conserve aussi `delegation_admission` intégral et expose `result.answer`
ainsi que `response`. Aucun reçu de délégation n'est réécrit. Les résultats
substantiels d'autres backends et les listes sans clé restent inchangés.
L'acquittement du résultat projeté est explicite via `get_result` Desktop.

Les prochaines intégrations Harness exposant cet outil utilisent un timeout
transport de 630 s. Une installation existante exige plan/apply et rechargement
explicites ; ce changement ne prouve pas que le client réel tient un appel de 600 s.
