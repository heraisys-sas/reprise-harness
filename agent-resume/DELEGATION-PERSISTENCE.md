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

Dans `ux_review_probe.py`, démarrer avec `--request-key` persistée avant le déclenchement. `get_task({"claim_key":"…"})` exige une clé de réservation persistée côté exécutant avant l’appel. Même clé récupère la même tentative et les mêmes entrées, avec `recovered_reservation:true`, sans augmenter le nombre de réservations. Une autre clé ne reçoit pas la tâche. Cette relecture n’autorise pas une deuxième inférence si la première peut continuer.

`submit_result` conserve l’idempotence des résultats identiques et refuse un conflit. `get_result({"request_key":"…"})` retrouve le résultat ; `acknowledge:true` acquitte sa consommation. Un second processus ne déclare plus les tâches du premier interrompues par sa seule ouverture. Un résultat tardif de la tentative connue reste accepté. `--recover-existing` recharge le manifeste sans relire le fichier source, même modifié ou supprimé ; sans cette option, une entrée différente est refusée. Un ancien état UX sans manifeste est conservé mais refusé : le réconcilier avant migration, sans effacer son résultat.

## Plafonds et limites conservées

Les plafonds quotidiens Claude Code, Claude Desktop et Quick Apply sont supprimés. `expert_calls` et `quick_calls` hérités restent inertes ; ils ne sont ni lus pour l’admission ni incrémentés. `daily_limit` et `--daily-limit` sont uniquement acceptés comme compatibilité obsolète, sans validation de plage ni effet. Les nouveaux launchers et plists ne les émettent plus.

La garde fournisseur Codex, dont la réserve **5 %** et le refus de quota inconnu, reste inchangée. Aucun budget utilisateur explicite n’est retiré. La connaissance fraîche des quotas par compte/profil/pool et la politique de reprise associée constituent le prochain lot.

## Justification et validation

Le choix de distinguer résultat reçu et exécution incertaine suit la sémantique des pannes RPC décrite par Andrew D. Birrell et Bruce Jay Nelson, *Implementing Remote Procedure Calls*, ACM TOCS 2(1), 1984, sections 1.4 et 3 ([texte primaire](https://www.cs.cmu.edu/afs/cs/academic/class/15712-s19/www/papers/birrell84.pdf)). Ce travail n’établit aucune déduplication native de Codex/Claude. Les admissions locales utilisent les transactions `BEGIN IMMEDIATE` documentées par [SQLite](https://www.sqlite.org/lang_transaction.html), sans étendre leur atomicité à l’appel externe. Les garanties décrites ici sont celles de notre code et de nos tests simulés.

Tests de panne : `tests/test_delegation_persistence.py` ; suites existantes du pont, de Quick Apply, de l’installateur et du prototype UX adaptées au nouveau contrat. Validation réelle du pilote Claude, compte gratuit, permissions macOS, réception autonome et reprise après panne de machine encore à confirmer. Aucun résultat visible dans les logs ou sur disque n’est promu en reçu MCP.

Validation finale du 21/10 : `python3 -m unittest discover -s agent-resume/tests -q` hors sandbox : **276 tests, OK, 1 skipped**, dont 19 tests de persistance et de concurrence. `git diff --check` et `py_compile` des sept modules Python modifiés passent. Le premier passage sandbox rencontrait des refus d’accès aux sockets de santé/scheduler ; le passage autorisé hors sandbox est vert. Des `ResourceWarning` de fixtures existantes subsistent, sans échec. Ruff, Gitleaks et Semgrep ne sont pas installés ; aucun résultat de scanner de sécurité n’est revendiqué. Aucun Swift modifié, donc aucune compilation ni relance native.


## Résultats longs — issue #32

Depuis 0.1.4, les quatre outils d’inférence acceptent `return_mode=receipt`,
y compris les continuations et alias. Ce paramètre de transport ne change ni
l’identité immuable ni la lignée, et sa modification ne relance pas l’inférence.
La découverte `initialize` donne version et SHA-256 du module exécuté ; les
schémas `tools/list` prouvent les paramètres disponibles. Une vérification directe
de release ne prouve jamais que le client Harness l’a déjà chargée.

Depuis 0.1.5, le mode `full` (défaut) livre le résultat tel quel jusqu’à 48 000 caractères, plafond de capture antérieur à #32 : aucun résultat existant ne change. Au-delà,\nle pont renvoie le manifeste du reçu (`return_mode_effective=receipt`, `status`\ndu résultat, `total_length`, `result_sha256`, sans `response`), y compris pour un\nrésultat incertain et pour une rediffusion de la même clé ; la lecture paginée\n`reprise_requests view=page` ou `delegation_collect.py` récupère le contenu complet.\nLe `status` d’un manifeste `receipt` est celui de la délégation (non plus celui de la\nlecture du reçu), donc un résultat `needs_review` n’est plus présenté comme réussi.\nLe helper écrit désormais par fichier temporaire privé puis liaison atomique : un échec\nne laisse aucun fichier partiel et un nouvel essai reste possible.\n\nLa capture Codex conserve jusqu’à 2 000 000 caractères Unicode ; au-delà,
le résultat est explicitement incomplet et `needs_review`. Le helper installé
`delegation_collect.py` assemble les pages en lecture seule, vérifie empreinte et
longueur avant création exclusive d’un fichier privé. Il ne fait aucune inférence
ni acquittement et refuse une capture déclarée incomplète. Voir les instructions
de la skill pour son invocation. Les pages ne résolvent pas l’élagage des sorties
des outils natifs d’un autre harness.
