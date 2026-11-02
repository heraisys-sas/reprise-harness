# Test Quick Apply #102

Baseline : `c01e49b02244ea63cfba50f668b99fa462032ccc`.

Plan retenu : propager la preuve du clic au reçu, journaliser uniquement les étapes sans texte ni identifiant de chat, refuser le spawn si Chrome est absent, permettre un socket applyd isolé, protéger l'appartenance du socket et ne pas arrêter un Chrome tiers à l'arrêt du daemon. Un seul dispatch réel est autorisé ; toute incertitude reste incertaine et aucune réponse du chat n'est lue.

Arbitrages : verrou interprocessus détenu pendant la vie d'applyd ; refus de remplacer un socket joignable ou dont l'état ne peut pas être établi ; suppression à l'arrêt uniquement du socket créé. Le stderr de l'enfant est hérité du parent. Le reçu ne présente `click_observed: true` que si confirmé. L'option `--applyd-socket` permet d'éviter le socket partagé. Ni SIGTERM ni l'arrêt authentifié ne tuent un Chrome identifié par le seul lock du profil.

La barrière durable de dispatch décrite ci-dessous remplace la limitation initiale. Aucun retour ne vaut résultat avant `submit_result`.

Vérification ciblée : 22 tests `test_claude_desktop_dispatch.py` réussis hors sandbox, avec sockets temporaires. Un premier test utilisant un faux serveur défectueux a déclenché un applyd temporaire PID 15193, arrêté immédiatement ; le faux serveur et le test ont été corrigés avant la seconde exécution. Pas de dispatch réel par l'agent d'implémentation.

Activation à froid : `dispatch_supported` reflète une capacité à tenter un dispatch lié au projet lorsque l'enfant répond ou que le binaire Chrome est présent ; `applyd_running` distingue son état effectif. La consultation des capacités ne lance aucun processus et ne garantit ni session ni composeur. Le reçu de `review` propage stage, click_observed et socket lorsqu'ils existent.

Arbitrage concurrence : la réservation conditionnelle vérifie son nombre de lignes et l'absence d'une tentative MCP ; un appel concurrent perdant retourne un reçu indéterminé sans soumettre. Tests ciblés hors sandbox : 27 dispatch, 19 quick et 11 desktop OK, dont deux appels simultanés imposés par barrière ; un seul submit et un seul dispatch. Un test raccorde review à Endpoint réel avec socket froid et transport simulé. Les appels RPC directs répétés sont maintenant protégés par une réservation distincte ; jamais réémettre automatiquement.

Séparation des barrières : table `desktop_dispatch_guard` migrée transactionnellement ; toutes les tâches préexistantes sont marquées `legacy_unknown`, même si leur livraison MCP était vide, car les anciens dispatchs directs ne laissaient pas de trace. Les nouveaux enregistrements disposent de réservations indépendantes pour review et le socket, et d’un reçu mémorisé. Une tentative sans reçu reste incertaine après redémarrage. La livraison MCP conserve exclusivement `delivery`, `attempt_id` et `claim_key`. Les anciens writers doivent être arrêtés ou mis à niveau avant la migration : ils ne respectent pas cette table.

Claude MCP transmet explicitement `--state` à Bridge via `desktop_state` ; les autres frontends conservent leur défaut historique. Les racines de projet demeurent isolées. Tests temporaires : review vers Endpoint simulé, `get_task` et `submit_result` dans un état séparé du projet ; migration legacy, perte de reçu, rejeu et réservations concurrentes. Ces tests simulés ne modifient aucun service réel ni base utilisateur.

Validation supervisée du 02/11/2026 : admission orchestrée sur registre isolé puis un seul dispatch réel vers Chrome dédié ; reçu `dispatch_ok:true`, `stage:envoyé`, `click_observed:true`, canal `chrome-socket`. Étapes enfant observées : connexion CDP, préparation, empreinte vérifiée, envoyé. Aucune réponse du chat relue ; aucun retour `submit_result` vérifié lors de cet essai. Cet essai précède la séparation des barrières ; cette séparation est couverte par des tests simulés et reste à confirmer par un nouvel essai autorisé avec une nouvelle clé.
