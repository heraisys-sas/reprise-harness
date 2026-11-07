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


## Première exécution Apple locale supervisée

Une nouvelle préparation peut recevoir un consentement explicitement ciblé
`foundation-models` via `job_authorize_local`. Les consentements `codex`
existants et leur identité sont conservés ; ils ne deviennent pas admissibles
au moteur local et ne déclenchent aucun repli. Le consentement seul reste sans
exécution. Les outils AFM de conversation n'exposent pas ces commandes hôte.

Le parcours opérateur ajoute `job_local_plan`, puis `job_local_start` avec un
`run_id` durable, une révision CAS et le hash du manifeste relu à l'écran.
Le service retient le manifeste côté hôte ; aucun chemin exécutable ou payload
fourni par le client n'est accepté comme substitut. La planification vérifie
uniquement le fichier helper et son contrat statique `--supervision-contract` ;
elle n'appelle ni génération ni API de disponibilité du modèle. Un ancien
helper sans ce contrat est refusé. La présence du binaire ne prouve pas la
disponibilité ni la réussite d'une génération.

Le manifeste lie projet, travail, racine du registre, proposition, consentement,
job/requête, adaptateur `native-foundation-explain`, exécutable et SHA-256,
une admission maximum et une échéance entière de 1 à 120 secondes. La route
utilise le helper natif connu, `SystemLanguageModel.default`, sans outils,
configuration de fournisseur externe ni fallback. Cette politique décrit le
chemin réellement autorisé ; elle n'attribue pas un prix vérifié ou une
capacité financière à une autre route. La garde financière Codex reste intacte.

Jobs persiste d'abord une intention chiffrée `local_pending`, qui ne prouve
ni admission ni départ. L'admission effective réutilise les transactions
`create_guarded_grant`/`guarded_handoff` du Store : droit lié au payload,
budget unitaire et unicité de mission sont vérifiés et consommés atomiquement.
Seul un handoff neuf et committé donne un snapshot au superviseur. Il contient
le payload et le manifeste immuables ; un rejeu n'en produit pas un nouveau.
Les identités de job/requête ne masquent pas une demande locale équivalente
encore incertaine lors du contrôle de cycle/déduplication du Store.

Avant départ, le superviseur revalide le contexte et le helper, copie ses octets
dans un répertoire privé, contrôle le SHA et le contrat de cette copie, puis
exécute uniquement cette copie avec une commande fermée et un environnement
réduit. Il conserve son `Popen`, surveille sortie bornée, annulation et échéance,
puis récupère le processus. Aucun verrou/transaction SQLite n'est conservé
pendant l'inférence. Les connexions SQLite possèdent les descripteurs d'une
base existante ; aucune fermeture de fd brut de cet inode ne doit libérer les
verrous POSIX d'un autre thread. La création initiale privée est sérialisée
avant ouverture des connexions.

`job_local_stop` vise exactement le `run_id` connu. Le drapeau persistant conserve la demande ; dans le propriétaire courant, le
contrôle local est synchronisé avec l'admission et le dernier départ. Un
travail déjà admis est arrêté par son propriétaire. La requête d'arrêt n'est
pas une preuve d'arrêt. Le superviseur signale seulement son groupe enfant
possédé et exige `wait/reap` avant de rapporter une interruption confirmée.
Un échec de nettoyage reste indéterminé. Aucun PID relu dans un ancien reçu
n'est tué. Sans propriétaire vivant, la récupération conserve l'incertitude.

Le helper natif arme aussi son propre timer avant l'accès au modèle :
`--deadline-seconds` borne sa durée même si le propriétaire Python meurt.
Le handler termine le processus avec le code 124 sans attendre stdout, un
verrou de sortie ou une annulation coopérative du modèle. Cette preuve borne
le processus applicatif possédé ; elle ne prouve pas l'arrêt de toute activité
interne d'un service système Apple. Une mort du propriétaire ne fabrique pas
un reçu terminal : l'intention reste indéterminée et n'est jamais relancée.
L'inscription et le démarrage des threads d'exécution sont atomiques avec
`job_closing`, afin qu'une admission déjà en préparation ne puisse échapper
au drain de fermeture.

Le reçu final est relu via `guarded_receipt` avec grant, job, binding complet
et identité immuable du payload. Il distingue processus démarré/récupéré,
code de sortie, cause d'arrêt, résultat et travail achevé. La réponse est
chiffrée dans le reçu durable et dans la projection Jobs ; son SHA est vérifié
à la récupération. Une perte de la projection après le reçu Store permet de
retrouver le résultat sans génération supplémentaire. Un crash avant cette
preuve conserve `local_indeterminate`, sans recréer d'admission ou de processus.

La GUI journalise l'identité `local_start` avant émission. Sa récupération
compare run, manifeste, contexte/travail et révision ; un reçu étranger ne
résout pas l'intention. Le suivi automatique ne fait que lire les états actifs,
sans inférence ni rejeu, et s'arrête aux états enregistrés/indéterminés.
Un état `finished` avec réponse valide prouve une génération capturée, pas la
satisfaction de tous les critères métier. La limite unitaire est par mission,
pas un budget financier ou un plafond agrégé entre plusieurs missions.

Les tests de développement utilisent exclusivement des helpers simulés, des
registres temporaires et un coffre de test. Le timer natif est testé en compilant
son préfixe sans aucun appel d'API modèle. Ils couvrent concurrence, non-rejeu,
arrêt pendant exécution, échéance après mort du propriétaire, course de
fermeture, verrous POSIX, perte de projection et récupération GUI. Ces tests ne
constituent pas une preuve E2E avec le modèle Apple réel ; celle-ci reste un essai
opérateur distinct, explicitement autorisé, sans publication automatique.

### Format de la demande locale et lecture historique

Les nouveaux manifestes portent `prompt_format: operator-question-v2`. Ce champ
participe au hash du manifeste et à l’identité du payload admis. L’objectif de la
proposition consentie est la demande `operator_question` ; les autres champs
(contexte, critères, contraintes déclarées) restent des données non fiables.
Le helper les encode dans une enveloppe JSON distincte et ne demande aucune
explication supplémentaire contradictoire avec l’objectif. Cette séparation
n’est pas une preuve de résistance du modèle à toute injection ; les permissions,
les outils désactivés et l’absence de repli restent imposés hors du modèle.

Un manifeste sans `prompt_format` conserve exactement le payload et le format
historiques pour la lecture et la vérification de son reçu. Il n’est ni migré ni
réémis. Une version inconnue est refusée. La récupération et un nouvel appel du
même run admis ne lancent aucune génération ; une modification du format exige
une nouvelle préparation et un nouveau consentement explicites, jamais une
relance automatique d’un état incertain. Un reçu `finished` atteste une réponse
valide du helper, pas la satisfaction des critères métier ni l’absence de refus.

### Candidat de préparation issu d’un reçu local

Le socle hôte expose `job_candidate_extract`, `job_candidate_import` et
`job_clarify`, sans outil modèle, sans génération et sans dispatch. Il ne
raccorde pas encore la conversation ou la GUI à une analyse automatique.
Un modèle peut seulement proposer des données ; l’import exige une confirmation
opérateur explicite, distincte de toute autorisation d’exécuter la mission.

Le document candidat est un JSON strict, sans balise Markdown :
`{"schema":"reprise-preparation-candidate-v1","proposal":{"objective":"…","acceptance":["…"],"constraints":[],"questions":["…"]}}`.
Les champs supplémentaires (dont backend, permissions et identifiants), clés
dupliquées, constantes non JSON, questions dupliquées et dépassements de bornes
sont refusés. Les bornes existantes des propositions s’appliquent ; la réponse
candidate est limitée à 13 Kio. Le texte reste non vérifié, même si sa structure
est valide. L’objectif proposé ne remplace jamais automatiquement une mission.

`job_candidate_extract` reçoit exactement `id`, `op`, `enabled`, `project_id`,
`operation_id` (source), `work_id`, `run_id`, `expected_response_sha256`.
La source doit être `local_recorded`, avec un reçu `finished`, processus démarré
et récupéré, sortie zéro et consentement Apple local du même projet/travail.
La lecture vérifie les jointures admission/grant/requête, l’unique consommation,
le binding, les identités payload/manifeste/attempt, puis l’égalité du résultat
chiffré avec sa projection Jobs et le hash de la réponse. Le registre d’admission
existant est ouvert en lecture seule, sans constructeur de Store, initialisation,
acknowledgement ou réconciliation. L’extraction retourne `candidate`, son SHA-256
canonique et `origin` (projet/travail/racine/contexte, opération/job/requête/run,
identité de l’admission et hashes du reçu et de son snapshot). Aucun texte ou
manifeste fourni par le client ne remplace la réponse attribuable.

`job_candidate_import` reçoit exactement `id`, `op`, `enabled`, `project_id`,
`operation_id` (destination), `work_id`, `source_operation_id`, `source_run_id`,
`expected_response_sha256`, `expected_candidate_sha256`, `import_id`,
`expected_revision`, `expected_proposal_sha256`, `operator_confirmed: true`.
Le service relit et attribue la source ; il n’accepte aucun candidat dans cette
requête. Sous le même verrou Jobs, l’import revalide le snapshot source et
applique le CAS destination. Une création exige les deux valeurs attendues à
`null` et une destination absente. Une révision exige la révision et le hash
courants d’une `preparation`. Source et destination doivent être des opérations
distinctes du même projet/travail. L’import produit uniquement `preparation`,
`authority: null`, `request_key: null`, sans consentement ni moteur.

`candidate_import` conserve le document original, sa provenance et l’identité
de confirmation. La répétition exacte du même import retourne l’état actuel sans
écraser les révisions ultérieures. Une autre identité ou un autre candidat sur
cette opération importée est refusé : créer explicitement une nouvelle préparation.
La provenance reste récupérable par `job_recover`. Une erreur de synchronisation
après remplacement reste `write_uncertain` ; récupération par la même identité,
sans réémission automatique. Aucun ancien reçu ou consentement n’est modifié.

Les `proposal.questions` restent des chaînes pour la compatibilité historique.
Les préparations importées portent en plus `clarifications` : identité stable,
question originale, réponse, résolution explicite et historique des mutations.
`job_clarify` reçoit exactement `id`, `op`, `enabled`, `project_id`, `operation_id`,
`question_id`, `answer`, `resolved` (booléen), `resolution_id`, `expected_revision`.
Une réponse non vide (maximum 1 Kio) ne résout pas implicitement la question :
`resolved: false` la maintient ouverte ; `true` la retire de la liste ouverte en
conservant question et réponse dans les métadonnées. La réouverture est explicite.
Le CAS et les identités de résolution empêchent écrasement et répétition ;
l’historique complet est borné à 24 Kio, avec refus avant mutation à saturation.
Une révision ordinaire ne peut supprimer une clarification encore ouverte.

Le consentement refuse toute question ou clarification non résolue et tout
changement du travail lié à la provenance. Il lie le hash des clarifications ;
le manifeste local et le payload incluent le même snapshot comme données non
fiables. Une clarification ne devient ni instruction hôte ni permission. Après
consentement, aucune modification de clarification n’est possible. Les formats
historiques sans ces champs restent inchangés, y compris leurs identités de reçu.
Les tests de ce socle utilisent un coffre et des réponses simulés ; ils ne
prouvent ni la pertinence des questions d’un modèle réel ni une interaction GUI.

L’extraction copie la preuve SQLite puis ferme sa transaction avant tout appel
au coffre. `evidence` contient le binding et le hash de cette attribution ; il
est conservé dans `candidate_import.source_evidence`. L’import prend d’abord
le verrou Jobs (attente maximale existante de cinq secondes), vérifie source et
CAS, chiffre le nouvel état et synchronise son fichier temporaire sans verrou
SQLite. Juste avant remplacement, un garde relit l’attribution dans une courte
transaction SQLite en lecture seule et compare son hash. La connexion du garde
attend au maximum 0,25 seconde un verrou SQLite ; une source modifiée ou occupée
est refusée avant remplacement, avec suppression du temporaire et sans relance.
Un verrou SQLite indisponible retourne `candidate_source_unavailable` avec
`effect: not_admitted` ; aucune nouvelle tentative automatique n’est effectuée.

L’ordre imbriqué est Jobs puis lecture SQLite. Les chemins d’exécution locale
committent leurs transactions SQLite avant leurs projections Jobs ; ils ne
prennent pas le verrou Jobs avec une transaction SQLite ouverte. Le garde ne
lance ni coffre, ni subprocess, ni inférence et n’attend pas le verrou Jobs.
Il reste actif pendant `replace` et `fsync` du répertoire : ces appels système
n’ont pas de délai garanti. Une panne ou lenteur du système de fichiers peut
donc prolonger le verrou de lecture et retarder un writer SQLite ; aucune
absence totale de blocage n’est promise. Une erreur après remplacement conserve
`write_uncertain`. Les tests interprocessus vérifient qu’un writer indépendant
peut committer pendant le chiffrement, est bloqué au remplacement seulement,
puis peut de nouveau committer après libération. Ils ne simulent pas un disque
réellement bloqué. Ces preuves et hashes restent une attribution locale entre
composants hôte, pas une authentification contre un processus hostile du même UID.

### Parcours GUI d’analyse vers une mission

L’interface propose une préparation d’analyse native distincte de la mission.
Le template hôte `reprise-analysis-v2` demande uniquement un candidat JSON strict,
sans exécuter la demande ni déduire permissions, cible ou capacités. La demande
opérateur est encodée comme donnée JSON dans les contraintes, en conservant ses
retours à la ligne ; l’enveloppe encodée est limitée à 1 Kio. Aucun transcript,
checkpoint ou souvenir n’est ajouté automatiquement. La qualité des questions
reste non vérifiée et une réponse non JSON ne déclenche aucune réparation.

Créer cette préparation ne lance aucun modèle. L’opérateur valide son contexte,
consent à l’analyse Apple locale, relit le manifeste puis autorise un départ
unique par le parcours supervisé existant. Les contrôles de suivi et d’arrêt
restent disponibles ; les changements de sélection sont bloqués pendant une
exécution suivie. La réponse brute et le reçu restent consultables même si le
candidat est refusé par le parseur.

Après extraction attribuable, l’opérateur peut créer explicitement une autre
préparation de mission. L’analyse et la mission ont des identités distinctes ;
la mission commence sans consentement, permission ou exécution hérités. Elle
reste éditable avant son propre consentement. Les questions importées sont
présentées avec leurs réponses et leur historique ; « Enregistrer sans résoudre »
et « Confirmer la résolution » sont des actions distinctes. La réouverture est
explicite. Les réponses non enregistrées restent volatiles et ne sont pas une
preuve durable. Aucune réponse n’est transformée automatiquement en fait mémoire.

Le journal conserve une liaison privée d’identités par projet/travail, avec
écriture atomique et synchronisation : analyse source et destination de mission.
Il refuse une autre source ou destination pour cette liaison. Ce parcours prend
donc en charge une analyse et une destination par travail ; une nouvelle analyse
requiert la sélection explicite d’un autre travail. La liaison ne contient aucun
texte de demande ou de réponse. La GUI permet de recharger séparément les deux
opérations ; un ancien reçu terminal ne remplace pas cette sélection explicite.

L’intention d’import est journalisée avant la liaison destination et avant
émission. Si le crash survient entre marqueur et liaison, le marqueur permet de
reconstituer cette dernière. Les nouveaux marqueurs d’import et de clarification
conservent uniquement identités, révision et hashes, jamais le texte des réponses.
La récupération compare import/source/run/réponse/candidat ou l’identité exacte
de résolution dans l’historique. Un reçu étranger ne libère pas le journal ;
la GUI n’émet pas de nouveau départ, import ou résolution pour remplacer une
réponse perdue. Une opération absente après incertitude reste bloquante.

Les liaisons et marqueurs historiques restent lisibles. Les tests GUI exécutent
le contrôleur Swift réel avec le service, les Jobs, le registre et l’admission
réels, mais un coffre et un helper de génération simulés. Ils vérifient le
parcours complet, les réponses perdues, les faux reçus, le CAS et l’absence de
réémission. Ils ne démontrent ni l’utilité des clarifications d’un vrai modèle,
ni une conversation native à plusieurs tours via ce parcours.

Un refus d’import explicitement confirmé `effect: not_admitted` ne laisse pas la
liaison destination inutilisable : après relecture du même candidat, l’opérateur
peut demander un réessai explicite avec le même `import_id`, la même destination
et les mêmes identités source/run/réponse/candidat. Le journal conserve la preuve
du refus avant cette nouvelle émission. Une réponse perdue ou un effet inconnu
n’ouvre jamais ce réessai : seule la récupération de la destination est permise.


### Entrée d’analyse dédiée v2

Les nouvelles analyses utilisent le manifeste `prompt_format:
preparation-analysis-v2`, distinct de `operator-question-v2`. Le choix exige
le template hôte exact `reprise-analysis-v2`, une enveloppe de demande valide
et aucune provenance de mission importée ; le contenu du texte opérateur ne
sélectionne jamais le format. Les critères techniques stockés pour l’analyse
ne sont pas transmis comme données à copier dans la future mission.

Le payload modèle contient exactement `prompt_format` et
`operator_request: {"text": "demande originale"}`. La demande n’est plus un JSON
encodé dans `constraints`, puis dans `facts`, puis dans une autre chaîne JSON
lors du rendu modèle. Son enveloppe de stockage historique dans Jobs reste une
donnée hôte ; le service la décode explicitement avant l’admission. Le helper
rend une unique enveloppe JSON structurée `operator_request`, séparée des règles
fixes du programme. Champs supplémentaires et types incorrects sont refusés.
La demande reste non fiable, jamais une instruction hôte ni une autorisation.

Les règles v2 demandent un objectif décrivant le résultat métier, des critères
vérifiables du livrable et les seules contraintes métier déclarées. Elles
interdisent de prendre l’analyse, le format JSON ou Reprise comme objectif, ou
de copier un critère technique d’analyse. Elles demandent zéro question quand
la demande suffit, sinon au maximum trois questions strictement bloquantes,
sans redemander une précision déjà donnée. Ce cadrage est une consigne au modèle,
pas une preuve que ses questions sont utiles ou son objectif fidèle. Le parseur
candidat conserve son schéma v1 et sa validation structurelle ; la relecture
humaine reste indispensable. Aucun filtrage heuristique n’est présenté comme
une validation sémantique.

Le helper annonce les formats supportés via son contrat statique, avant toute
API modèle. Une analyse v2 est refusée au plan puis à la revalidation du snapshot
si ce format n’est pas annoncé ; aucun repli vers l’ancien rendu n’est permis.
L’annonce ne démontre ni disponibilité réelle ni qualité de génération. Le
contrat de supervision et les limites d’outils/temps restent inchangés.

Le format, la proposition et le hash du helper participent à l’identité admise.
Un manifeste absent de version ou portant `operator-question-v2` conserve
strictement son payload et son rendu historiques, y compris les analyses v1.
Les anciens reçus ne sont ni migrés ni réémis. Les tests figent deux identités
synthétiques calculées depuis le formateur publié antérieur, vérifient l’entrée
v2 plate, le refus d’un helper ancien avant admission, les injections
structurelles et la récupération sans replay. Aucun test de modèle réel n’est
nécessaire à ces contrôles, et ils ne prouvent pas l’amélioration sémantique.

### Analyse guidée v3 et propositions inexploitées

Les nouvelles analyses utilisent `reprise-analysis-v3` et le format distinct
`preparation-analysis-v3-guided`, explicitement annoncé par le helper. La demande
opérateur reste une donnée structurée non fiable. Le helper utilise un type
`@Generable` avec descriptions métier et bornes de collections ; l’hôte sérialise
les champs générés vers le candidat v1, sans exemple littéral de réponse dans le
prompt v3. La génération guidée garantit une structure, pas la fidélité métier,
l’utilité des questions ou l’absence d’influence des données hostiles. La relecture
humaine et le consentement de mission séparé demeurent obligatoires.

À l’extraction et à l’import, le parseur commun refuse les valeurs de gabarit
connues `résultat métier attendu` et `critère du livrable`, normalisées Unicode,
casse et espaces. Cette barrière ciblée ne détecte pas toutes les erreurs
sémantiques. Le refus conserve le reçu et sa réponse ; aucun import ni nouvel
appel automatique n’en découle. Les reçus historiques restent lisibles et leur
identité inchangée, y compris lorsque leur proposition est désormais refusée.
Les formats antérieurs gardent leur construction historique sans réémission.
