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
