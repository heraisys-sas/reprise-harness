# Sonde IronClaw par SSH direct

`ironclaw_ssh.probe(Endpoint(...))` effectue uniquement un GET authentifié de
`http://127.0.0.1:<http_port>/api/webchat/v2/session` dans le conteneur distant.
Connexion accessible dans **Connexions → IronClaw SSH** et via les commandes `resume ironclaw-inspect`, `ironclaw-configure` (objet Endpoint JSON sur stdin) et `ironclaw-probe`. Aucune soumission, inférence, installation ou modification distante.
Le résultat contient le statut HTTP, la présence du canal de session et des noms de
champs autorisés. Un HTTP 200 sans canal non vide produit une erreur.

Le fichier local `token_url_file` doit être un fichier régulier appartenant à
l'utilisateur, avec permissions privées (0600 par exemple), contenant une URL avec
exactement un paramètre `token` dans query ou fragment. Il reste hors dépôt.
Le jeton ne figure ni dans argv SSH, ni dans les sorties retournées : la configuration
curl passe par stdin SSH. Les erreurs subprocess sont remplacées par des diagnostics
fixes. Aucun contenu distant, titre de conversation ou identifiant de canal n'est
retourné. Ne pas journaliser les arguments internes `input` de subprocess.

SSH ignore les configurations utilisateur, désactive agent et forwarding, impose une
clé explicite et une vérification stricte du known_hosts existant. La commande distante
est fixe (`/usr/bin/curl --disable --config -`), les ports et noms sont validés. Curl désactive les proxys, ignore son
fichier de configuration, ne suit pas les redirections et borne durée et taille de
réponse. Le runtime distant doit fournir curl. La clé privée n'est jamais lue par Python.

## Preuves du 22 octobre 2026

IronClaw 1.4.1 expose une CLI « Reborn runtime ». Le profil indiqué par `config path`
est `local-dev`. GET /health = 200 ; GET session et threads sans jeton = 401 ;
/openapi.json = 404. La sonde authentifiée a obtenu HTTP 200 avec un canal configuré.
Aucun POST n'a été exécuté.

Les bundles JS publics montrent `/api/webchat/v2` : création de threads, messages via
`channels/{channel}/messages`, timeline paginée, événements SSE, artefact par run,
annulation et résolution de gates. Les actions utilisent `client_action_id`.
Le frontend reconnaît une entrée `assistant`/`assistant_message` `finalized`, liée par
`turn_run_id`, comme réponse finale. Les gates observées sont notamment permission
et authentification ; le contrat de clarification textuelle structurée reste inconnu.

## Suite avant délégation

Prouver les réponses de soumission, l'idempotence serveur, la corrélation run et la
récupération après interruption avant d'implémenter submit/poll. Réutiliser ensuite le
registre durable Reprise (`delegation_store`) : admission avant appel externe, aucun
verrou pendant inférence, résultats conservés jusqu'à réclamation, gates transmises à
l'opérateur. Un canal HTTP authentifié ne démontre aucune capacité Work/Cowork.

La configuration privée est enregistrée dans `state/ironclaw-endpoint.json` avec des permissions 0600 après préparation explicite du registre. Elle contient uniquement hôte, compte, ports et chemins ; les fichiers de clé et de jeton restent inchangés. Une modification des champs dans l’interface doit être enregistrée avant vérification. L’inspection locale ne contacte pas le réseau.

Validation réelle de la CLI embarquée : configuration enregistrée puis sonde HTTP200 authentifiée avec canal de session présent ; aucun secret affiché et aucune tâche soumise. Neuf tests ciblés couvrent transport, erreurs, absence de secret dans argv, permissions et configuration sans accès réseau implicite.

Lecture statique complémentaire : création de thread renvoie `thread.thread_id` ; soumission renvoie `thread_id`, `run_id`, `status`, `outcome`, `accepted_message_ref`. L’événement final `final_reply.reply` fournit `turn_run_id`, `text`, `generated_at`. Les réponses POST n’ont pas été observées en conditions réelles ; pagination timeline et idempotence restent à démontrer. Aucun appel coûteux n’a été lancé pour les deviner.

## Adaptateur durable expérimental

Le payload inclut désormais `ironclaw_tasks.Tasks` et les commandes explicites suivantes, avec requêtes JSON sur stdin :

| Commande | Entrée | Effet |
| --- | --- | --- |
| `ironclaw-submit` | `request_key`, `content`, `allow_submit: true` | Admission SQLite puis au plus une tentative de création et d’envoi ; peut consommer du quota distant |
| `ironclaw-poll` | `request_key` | GET de la timeline, sans nouvelle soumission |
| `ironclaw-result` | `request_key`, `acknowledge: false` (défaut) ou `true` | Lecture locale ; acquittement uniquement après résultat final |
| `ironclaw-requests` | aucune | Liste locale bornée à 100 clés/états/acquittements, sans prompts |
| `ironclaw-fail` | `request_key`, `reason` | Clôture opérateur locale (`failed`, résultat `{"operator": reason}`), réservée aux états `needs_operator`, `ambiguous`, `needs_review`, `blocked_busy` ; aucun POST ni rejeu |

Une identité immuable lie le contenu et l’endpoint ; les identifiants d’action sont enregistrés avant tout appel externe. Une clé réutilisée ne renvoie jamais la tâche. Une tâche équivalente non terminée bloque aussi le contournement par une autre clé. Après perte d’accusé ou interruption d’envoi, l’état reste ambigu/dispatching : inspection requise, aucun rejeu automatique et aucune promesse d’idempotence du fournisseur.

Le suivi accepte uniquement une réponse assistant `finalized` liée au `run_id` enregistré, pagine avec un nombre de pages et des curseurs bornés, et conserve le résultat jusqu’à acquittement. Un `finalized` suivi d’une activité ultérieure du même run (outils, streaming, tour utilisateur, page suivante) reste intermédiaire : la tâche ne devient `finished` que si aucune activité ne suit dans toute la timeline paginée ; aucun statut `errored`/`cancelled` n’est inventé sans preuve du fournisseur. Une tâche `deferred` avec run connu est récupérable ; sans run, intervention requise. `failed` (motif opérateur via `ironclaw-fail`) et `expired` (délai `expire_after` dépassé, uniquement depuis `pending`/`deferred` — un gate `needs_operator` attend délibérément l’opérateur) sont des clôtures locales sans rejeu réseau. Les états busy/inconnus ne valent pas succès. Les lectures de résultats et la liste restent locales même si la connexion est supprimée ou changée ; les appels réseau conservent le contrôle strict de l’endpoint d’origine. Aucune transaction SQLite n’est gardée durant SSH.

Les événements de gate/authentification peuvent être ingérés par l’API Python et suspendent la tâche pour intervention ; aucun branchement SSE automatique ni résolution de gate n’est activé. Les clarifications textuelles structurées restent non démontrées. Aucune intégration automatique au planificateur ni exposition MCP de ces nouvelles commandes n’est annoncée.

L’adaptateur est vérifié sur transports simulés ; seule la sonde GET a été exécutée réellement. Aucun POST réel ni appel LLM n’a été lancé. Avant de qualifier la délégation opérationnelle, réaliser un essai unique autorisé avec un mécanisme de facturation compatible avec le plafond utilisateur, puis vérifier ACK, coupure locale et récupération corrélée sans renvoi.

Validation du lot durable : suite complète de **443 tests réussie, dont 4 ignorés**, puis **28 tests IronClaw ciblés réussis** après ajout des deux cas de concurrence et fermeture/réouverture du registre. Relecture indépendante : cas différé initialement défectueux corrigé, aucun défaut critique résiduel identifié dans le périmètre relu. CLI embarquée vérifiée sur registre temporaire canonique : liste et résultat absent sans configuration ni réseau. Compilation Python, packaging et diff-check réussis ; scan Gitleaks ciblé, zéro détection. Les avertissements de ressources préexistants restent présents. La vérification réelle des POST/retours de tâches n’est pas acquise ; le plafond utilisateur reste de 2 € et le mode de facturation distant est à clarifier avant un essai réel.

## Consolidation du 23/10/2026

Un essai réel minimal a fourni `REPRISE_OK`. L’ACK initial non reconnu a été récupéré par `ironclaw-reconcile` (GET uniquement), puis le résultat a été relu dans un autre processus et acquitté. Cela prouve ce parcours court, pas les soldes fournisseurs ni les fins de runs utilisant des outils. Les paragraphes antérieurs « aucun POST réel » décrivent les validations initiales, désormais dépassées par cet essai.

`ironclaw-reconcile` prend `request_key` et retrouve un unique message utilisateur identique dans la conversation possédée ; un conflit de run ou plusieurs correspondances bloquent la récupération. `ironclaw-followup` prend `request_key`, `parent_request_key`, `content`, `allow_submit:true` : parent terminé et endpoint identique, même conversation, aucun nouveau thread, une seule demande non terminée par conversation, ascendance bornée à huit. Aucun suivi automatique ni test quota supplémentaire n’est activé.

La revue du lot DSH a montré deux chaînes distinctes. Le prototype `ironclaw.py`/`ironclaw run -m` est conservé à titre expérimental mais désactivé dans le MCP : résultat stdout tronqué, santé insuffisamment prouvée, contrôle des outils distant non démontré. La voie durable ci-dessus reste disponible explicitement. Prochaine étape : raccorder le MCP au même `Tasks`/`Endpoint`, sans deuxième configuration de clé, avec polling/reprise et clarification. Le purpose Vault inutilisé a été retiré : SSH direct demeure le choix utilisateur.

Limites encore ouvertes (#17) : la fin du run est désormais décidée sur la timeline complète (un `finalized` suivi d’activité reste intermédiaire, règle conservatrice), et les états `failed`/`expired` existent côté opérateur. Restent ouverts : l’auto-détection des runs en erreur (statuts fournisseurs non observés) et les échéances de quota réelles — à confirmer en conditions réelles. La taille JSON encodée est maintenant vérifiée avant admission et un échec du GET préalable ne consomme plus définitivement l’envoi.

## Signal de fin de run (29 octobre 2026, #90)

La projection SSE `GET /api/webchat/v2/threads/{id}/events` (IronClaw Reborn 1.4.1) expose `run_status: {run_id, status}` via les événements `projection_snapshot`/`projection_update` : c’est la preuve fournisseur de fin de run, au-delà de l’inférence timeline. Statuts observés en conditions réelles : `queued → running → failed` (run bloqué par un LLM distant verrouillé, le 29/10). `ironclaw_ssh.run_statuses()` lit ce flux en lecture seule : le flux restant ouvert, curl sort 28 (max-time) après livraison du snapshot — lecture réussie, pas un échec de transport. `poll()` clôt en `failed` avec `{provider_run_status: 'failed'}` quand le statut du run est `failed` et que la timeline n’a pas de final ; les gates (`needs_operator`) ne sont jamais clôturées automatiquement. Le lecteur est injecté en production uniquement (`ironclaw_connection`) ; sans lecteur, l’heuristique timeline seule s’applique (comportement antérieur inchangé).

Limites documentées : un échec du lecteur SSE est persisté dans `last_error` (`run_status: …`) sans clôture fausse (la timeline reste seule source) ; et si la projection ne livre jamais le `run_status` d’un run, la tâche est clôturée `expired` après 7 jours (`expire_after` en production) ou avant sur décision opérateur (`ironclaw-fail`). Un statut terminal (`failed`, `finished`) gagne sur un statut de transit quel que soit l’ordre des trames SSE.
