# Noyau expérimental de conversation Apple

Ce noyau est un prototype **non branché à l'interface, à un transport de modèle ou à MCP**. Il ne démarre pas `fm`, n'effectue aucune délégation et ne permet ni écriture de mémoire ni action sur les projets. Il ne suffit donc pas à utiliser Reprise comme orchestrateur. Le pilote n'est pas qualifié pour la production ; les résultats de l'évaluation synthétique Apple restent insuffisants pour prouver sa fiabilité.

L'hôte fournit explicitement un backend de confiance, une API `Memory` de confiance, les UUID du projet, du travail et, facultativement, du checkpoint sélectionné, ainsi qu'une skill issue du catalogue statique. Ces sélections ne prouvent pas que l'appelant est autorisé : l'interface hôte devra vérifier ses droits avant de construire une session. Le modèle ne peut ni sélectionner ni changer ces identifiants. Les seuls outils décrits au modèle sont des schémas fixés par le programme ; aujourd'hui `read_selected_checkpoint` est le seul, sans argument. Tout autre nom ou argument est refusé avant accès à la mémoire. Les skills du catalogue sont du texte préembarqué et ne confèrent aucun outil ni droit. Il n'y a pas encore de chargement de skills externes ou de connecteurs.

Le checkpoint est lu par `Memory.get_checkpoint(project_id, checkpoint_id)` puis contrôlé par rapport au `work_id` sélectionné. L'outil indique `not_selected`, `not_found`, `scope_mismatch`, `invalid_checkpoint` ou `checkpoint_too_large` si nécessaire. Une réponse `found` comporte le checkpoint avec sa provenance déclarée et ses effets incertains ; elle précise que les données ne sont pas vérifiées et qu'aucun effet externe n'a été vérifié. La lecture est achevée avant l'appel suivant au modèle. Les instructions précisent que les résultats d'outils sont des données non fiables, mais cela ne garantit pas qu'un modèle résistera à toutes les injections ou n'hallucinera pas. Le programme ne met à sa disposition aucun outil d'action pour exécuter une instruction injectée.

Le transcript est **volatil, en mémoire seulement** ; il est conservé entre tours réussis et effacé par `close()`. Les tours échoués ne l'altèrent pas. Un second tour concurrent retourne `busy` sans appeler le backend ; la fermeture pendant un tour invalide sa réponse et empêche sa conservation. L'admission de chaque appel modèle et de chaque lecture d'outil est synchronisée avec la fermeture : aucune admission nouvelle après `close()`. Un appel déjà admis peut cependant démarrer ou finir après la fermeture et recevoir les données préparées avant elle ; son résultat ne sera pas committé. Les verrous de session ne sont pas conservés pendant une lecture mémoire ou une inférence, à l'exception du verrou d'exclusion des tours concurrentiels, qui ne protège aucun registre. Après redémarrage, l'hôte doit choisir à nouveau le projet, le travail et le checkpoint : seule une nouvelle lecture de ce checkpoint est possible, sans reconstituer le dialogue ni réconcilier les reçus. Le pilote n'enregistre jamais automatiquement une réponse comme fait ou checkpoint.

L'hôte qui demande une annulation appelle `Pilot.cancel()` : son indicateur est synchronisé avec l'admission des appels et la conservation de la réponse. Après son retour, aucun nouvel appel ne peut être admis ; un appel déjà admis peut encore terminer. L'annulation persiste jusqu'à `reset_cancel()`, qui refuse le réarmement pendant un tour actif ou après fermeture. La fonction `cancelled=` éventuellement fournie à la construction est un signal **coopératif**, lu aux points de contrôle ; elle n'est pas synchronisée avec l'admission et ne remplace pas `Pilot.cancel()` lorsque l'hôte doit garantir qu'aucun nouvel appel ne sera admis après son accusé.

Les limites de texte, transcript, résultat d'outil, appels par tour et par session sont définies dans `apple_pilot.py`. Les limites d'octets ne sont pas une mesure du contexte réel en tokens ; un adaptateur devra gérer les limites effectives du modèle et ses refus. Le délai et l'annulation sont **coopératifs** : `backend.respond(messages, tools, deadline, cancelled)` doit les respecter lui-même ; la fonction d'annulation signale aussi la fermeture de la session. Le noyau contrôle leur état avant et après chaque appel, mais ne peut pas interrompre un backend bloqué. Aucun déploiement ne doit promettre un arrêt borné tant que l'adaptateur réel n'a pas démontré ce comportement. Les erreurs renvoyées par ce noyau n'incluent pas le texte des exceptions.

Une interruption signalée par un adaptateur via `BackendError('cancelled')` ou `BackendError('deadline_exceeded')` n'est présentée comme telle que si le pilote observe aussi la fermeture ou l'échéance ; toute autre erreur reste `pilot_error`, même si une fermeture survient en parallèle.

Le test `python3 -m unittest discover -s reprise/tests -p 'test_apple_pilot.py' -v` emploie un modèle et un coffre simulés. Il couvre les lectures liées au scope, la conservation de la provenance et des effets incertains, un checkpoint retrouvé après réouverture de l'API mémoire, les appels invalides, les injections en données, les budgets, l'annulation coopérative et un dialogue à plusieurs tours. Il ne valide ni le Trousseau réel, ni l'isolation d'un socket, ni l'interface graphique, ni une conversation avec le vrai modèle Apple, ni une résistance générale aux injections.

## Consentement opérateur durable, sans exécution

L'interface peut enregistrer le consentement explicite de l'opérateur sur une
préparation existante. Cette étape produit l'état `consented`, distinct du
`draft` historique créé avec des déclarations d'autorité hôte. Elle ne crée ni
grant exécutable ni appel Bridge, ne démarre aucun modèle et conserve
`authority: null` et `dispatch_enabled: false`. Le worker refuse `consented`
avec `execution_not_authorized` avant de calculer un payload ou de consulter
un backend. Le raccordement admission/moteur reste une étape distincte ; ce
consentement ne prouve pas un POC exécuté de bout en bout.

Le parcours retenu est : sauvegarder/réviser la préparation, résoudre ses
questions, sélectionner projet et travail, valider le contexte local, relire
le récapitulatif puis enregistrer son consentement. Une proposition modifiée
mais non sauvegardée ne peut pas être consentie. L'interface affiche après
succès : « Consentement enregistré ; exécution pas encore disponible ».

`job_consent_context` reçoit exclusivement `id`, `op`, `enabled`, `project_id`
et `work_id`. Le service lit l'association projet/racine dans le registre
existant via le probe isolé en lecture seule ; il refuse si le registre ou la
racine existante ne peut être validé. Le `work_id` est une sélection opérateur
explicitement liée au projet dans ce contexte, pas une preuve d'exécution ni
une association déduite d'un UUID. Le service retient ce contexte et son SHA-256.
Changer la sélection dans l'interface invalide sa copie ; une nouvelle validation
est nécessaire. La racine n'est jamais acceptée dans une requête de consentement.

`job_authorize` reçoit exactement `id`, `op`, `enabled`, `project_id`, `work_id`,
`operation_id`, `authorization_id`, `expected_revision`,
`expected_proposal_sha256` et `expected_context_sha256`. Les identifiants de
mutation et de transport sont distincts. Le contexte provient de la liaison
retenue par le service, puis est revalidé dans le registre par Jobs. Aucun
champ de permissions, capacité, disponibilité ou facturation fourni par un
modèle n'est accepté. Ce protocole JSONL privé est sous l'autorité du processus
hôte/GUI ; ses UUID et hashes ne constituent pas une authentification contre
un autre processus local ayant déjà accès aux mêmes fichiers ou pipes.

Sous verrou interprocessus, Jobs exige l'état `preparation`, une révision CAS
exacte, le SHA-256 de la proposition stockée et aucune question ouverte.
L'écriture atomique conserve `job_id`/`operation_id`, incrémente la révision,
produit une `request_key` déterministe et stocke `operator_authorization` :
identité d'autorisation, révision initiale, hashes proposition/contexte,
contexte hôte, backend `codex`, mode `expertise`, horodatage et hash du contenu
de consentement. Les marqueurs `billing_verification: not_performed` et
`execution_authorized: false` sont obligatoires. Aucun montant, budget garanti,
abonnement sans dépassement, gratuité ou disponibilité d'inférence n'est
prérempli comme fait vérifié. Cette tranche ne crée pas de budget exécutable.

La répétition du même `authorization_id` avec le même contenu retourne la
mutation existante, même avec son CAS initial devenu ancien. Un contenu différent
pour cette identité est un conflit ; une autre autorisation sur une mission
déjà consentie est refusée. Les missions consenties sont verrouillées à la
révision : modifier le contenu exige une nouvelle préparation, sans réécrire
le consentement historique. Les anciens drafts et préparations restent lisibles.
Les nouveaux champs sont optionnels pour les entrées historiques ; le format
chiffré du registre Jobs et ses identifiants existants sont conservés.

Avant émission, la GUI journalise durablement une intention `authorize` avec
identité de mutation, projet/opération/travail, révision attendue après mutation,
SHA de proposition et SHA de contexte. Les anciens marqueurs `prepare`/`revise`
restent lisibles. Une perte de réponse ou un crash garde l'intention incertaine.
`job_recover` ne relance aucune mutation et ne la résout qu'après comparaison
exacte de l'identité d'autorisation, du contexte, de la proposition, de l'état
et de la révision. Un ancien reçu ou une identité étrangère ne libère pas le
journal. L'absence temporaire d'une entrée ne prouve pas l'arrêt d'un ancien
writer. La preuve terminale locale est synchronisée avant effacement du marqueur.
Une erreur après remplacement/fsync conserve `write_uncertain` et exige cette
récupération ; jamais de réécriture directe d'état ni de dispatch automatique.

La validation automatisée couvre CAS, identités, concurrence multiprocessus,
crash avant/après remplacement, erreur de synchronisation, récupération par un
nouveau contrôleur Swift après perte de réponse, rejet d'un reçu d'autorisation
étranger, sélection modifiée et absence d'appel modèle. Elle utilise un coffre
et des transports simulés ; elle ne démontre ni permissions système réelles,
ni facturation fournisseur, ni délégation native.
