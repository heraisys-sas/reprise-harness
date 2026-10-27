# Intégration expérimentale Desktop Apply dans Reprise

## Accord et périmètre

- Baseline de correction : `cc2b3ed20e445c122eef4888cbe918d112b10b46`.
- Plan en lecture seule discuté puis accepté par le coordinateur le 27 octobre 2026. Accord explicite limité d'abord au **lot 1** : transport, petite factorisation WebSocket, tests et ce checkpoint. Le coordinateur conserve Git et autorise séparément chaque lot suivant.
- Revue indépendante de cette baseline : `REVIEW-DESKTOP-APPLY-DEV.md`. R1–R5, R7 et R8 sont dans le lot 1. R6 (lecteur de quota et exemple Python) est traité séparément par un autre agent ; aucun fichier de ce volet n'est modifié ici.
- Aucun appel Claude/API, navigateur, daemon, installation, permission, cookie, historique privé ou réseau distant autorisé. Tests seulement simulés ou sur fixtures locales. Aucun commit/push/publication par l'implémenteur.

## Plan retenu

1. **Transport sûr et factorisation** : cible CDP épinglée, origine HTTPS exacte et chat effectif contrôlés autour des mutations ; composeur initialement vide ; SHA-256 du texte intégral après insertion et immédiatement avant le seul clic ; reprise staged avec cible et empreinte obligatoires. Un clic ou un dépôt ne devient jamais `finished`. Extraire le framing déjà utilisé par Codex sans modifier son contrat RPC. Retirer les commandes de cycle de vie non attribuées.
2. **Raccordement au service existant** (accord distinct requis) : configuration opt-in, entrée `quick_entry/submit` et registre existants, reçus durables, sérialisation du composeur et barrière d'incertitude, propagation des détails de transport et capacités séparées. Pas de service/registre parallèle ni d'instruction `get_task` Desktop envoyée comme si le web disposait de ces outils.
3. **Interface et wrappers** (accord distinct requis) : réutiliser Connexions, nommer la surface réelle claude.ai/Chrome, afficher les états distincts et les capacités non prouvées ; une seule implémentation Python d'envoi, wrappers CLI/exemples pour les commandes correspondantes. Pas de pilote Swift supplémentaire.

## Arbitrages du lot 1

- `WebSocketTransport` partage uniquement handshake, framing JSON, contrôles de taille, fragments et ping/pong. Codex conserve I/O par proxy, files de notifications, erreurs `Closed`, routage et propriété. CDP conserve ses propres I/O loopback et son délai total par commande.
- Le choix de cible refuse un hôte contenant simplement `claude.ai`, les URLs avec query/fragment, les cibles ambiguës et tout repli vers une page étrangère. L'URL WebSocket doit correspondre au port local et au `targetId` sélectionné.
- `apply` conserve la possibilité explicite `/new` du prototype. Une conversation existante exige l'UUID exact ; le futur parcours démon imposera une cible explicite. `send_staged` exige désormais `chat_id` et `expected_sha256` et ne navigue ni ne retape le texte.
- Aucun brouillon, même composé d'espaces, n'est remplacé. Le composeur source doit être vide avant toute navigation ; le chat cible déjà ouvert n'est pas rechargé inutilement. La primitive de préparation traite aussi le `textarea`. Une différence de texte après insertion ou juste avant clic empêche l'envoi.
- `posé` signifie dépôt vérifié ; `envoyé` signifie clic observé seulement. `incertain` après perte de réponse au clic conserve `dispatch_attempted=true`. Une réponse perdue à l'insertion peut laisser un brouillon, mais n'a pas tenté de tour. `to_submission` ne renvoie jamais `finished` ; aucune admission ni réponse n'est prétendue.
- Un bouton désactivé est un blocage d'interface de cause inconnue (`bloqué`), pas un quota. `quota()` conserve `blocked=null`, même quand le bouton est désactivé. Aucune date n'est déduite d'un HH:MM et aucune lecture Desktop n'est appliquée au profil Chrome.
- Les diagnostics de transport sont une liste de champs contrôlés : états, booléens, longueur et empreinte ; pas de texte du composeur, bandeau, URL avec query ou erreur brute du pair.
- Accord complémentaire R7 du coordinateur : retirer `pkill`, `Popen` et `open` du module ; `launch`, `shutdown` et `raise_for_login` conservent leurs signatures booléennes mais refusent l'action (`False`). Diagnostics : `lifecycle_supported=false`, motif stable `lifecycle_unverified`. `on_login=raise` n'exécute plus de relance. L'endpoint doit avoir été ouvert et choisi explicitement ; aucune propriété de navigateur n'est établie par un port qui répond. Le lot 3 doit afficher ce refus et ne proposer aucune ouverture fictive.

Pour le lot 2, accord complémentaire du coordinateur : ne pas créer en parallèle une tâche MCP réclamable si le prompt est envoyé directement au web. Le registre commun ne crée pas une capacité de retour. Définir la corrélation admission/claim/résultat avant tout raccordement et conserver le retour web indisponible sans preuve ; aucun retour tardif ne doit autoriser une nouvelle émission ni inventer une identité de chat.

## Frontières de preuve

Les tests de ce lot ne qualifient ni les sélecteurs dans un navigateur réel, ni la propriété de son profil, ni le compte/pool, ni l'admission effective, ni la récupération d'une réponse web. Une connexion CDP locale n'est pas une attestation d'instance. La vérification avant `Input.insertText` et le contrôle après frappe réduisent le risque, mais cette commande CDP ne possède pas de condition atomique liée au document ; une navigation extérieure entre contrôle et insertion demeure une limite, à signaler avant automatisation. Le clic, lui, vérifie cible et texte dans la même évaluation avant l'action, puis conserve l'incertitude en cas de réponse perdue.

Un retour MCP Desktop n'est pas établi pour le web. Seul un vrai `submit_result` accepté clôt les tâches MCP existantes. Aucun extracteur de réponses DOM, admission fictive, boucle d'inférence ou reprise automatique n'est ajouté. Une échéance de refus peut autoriser une tentative à T sans pré-mesure de quota obligatoire ; les conditions de cible, propriété, absence de doublon et capacité de reprise restent nécessaires. `resume_validation_supported` ne sera pas activé par la simple présence de ce pilote.

## Contrat de transport du lot 1

- `apply(prompt, chat_id=None, *, port=9333, settle=3.0, submit=True, on_login="fail")` conserve sa signature. `on_login="raise"` conserve la compatibilité d'appel mais aucune relance n'est effectuée. Un résultat `session` appelle une action humaine.
- `send_staged(port=9333, *, chat_id, expected_sha256)` exige désormais l'UUID et une empreinte SHA-256 hexadécimale minuscule. Une reprise non ciblée n'est plus acceptée. CLI module : `--send-staged --chat UUID --expected-sha256 SHA`.
- Tout résultat porte `ok` (opération locale observée), `stage`, `staged` (booléen ou `null` si le contenu après mutation n'est pas confirmé), `dispatch_attempted`, `click_observed`, `admission_confirmed=false`, `response_received=false`, `lifecycle_supported=false`, `lifecycle_reason=lifecycle_unverified` et `detail` réduit. Une empreinte attendue est rendue dans `detail.expected_sha256` après dépôt/clic vérifié.
- `to_submission` rend toujours `needs_review`, sauf `blocked_auth` avant tentative sur `session`. `response=null`, `resumable=false`, `automatic_fallback=false` ; le transport ne possède aucune clé de requête ni déduplication persistante.
- `quota()` rend `source=interface`, `quota_confirmed=false`, `blocked=null`, disponibilité et indices booléens seulement. Le port absent et le bouton absent restent inconnus.

## Contrôles et checkpoint

- Tests ciblés finaux : `test_claude_desktop_apply*.py` **33 tests, OK, 1 ignoré** (32 tests Python exécutés ; entrée Node ignorée car le binaire est absent du PATH), `test_websocket_transport.py` **10 OK**, `test_codex_rpc*.py` **27 tests, OK, 1 ignoré**, `test_package_helpers.py` **5 OK**. Total des quatre groupes : **75 tests, OK, 2 ignorés**. Le test Codex ignoré est le smoke test opt-in du binaire Codex installé (`REPRISE_CODEX_RPC_SMOKE` non activé) ; aucune socket ni instance Codex n'est créée par les tests exécutés de ce lot.
- Les expressions JavaScript réellement produites par `_prepare`, `_send` et `JS_STATE` ont aussi été exécutées via l'outil JS local sur **33 fixtures DOM synthétiques, toutes réussies** (`tests/fixtures/desktop_apply_dom.mjs`, export `runFixtures`). Cela utilise WebCrypto local et couvre les variations DIV/textarea, brouillons, champs/boutons ambigus, cible/hôte changé, texte modifié et document remplacé pendant le hash. Aucun navigateur, CDP ou processus Claude n'est utilisé. Le test unittest permet de rejouer cette fixture automatiquement quand Node est installé ; elle reste exécutable manuellement par l'outil JS disponible.
- AST Python de 8 fichiers modifiés/ajoutés : OK. `git diff --check` : OK. Le packaging importe aussi le transport et le framing après déplacement du bundle.
- Le premier passage ciblé a révélé une chaîne Python mal échappée dans la construction des expressions JavaScript ; corrigée avant les résultats ci-dessus. L'outil JS sans global `process` a aussi nécessité une garde de compatibilité dans la fixture, puis ses 33 cas ont passé.
- La suite complète n'a pas été relancée, conformément à la demande du coordinateur ; il la lancera après intégration des lots fonctionnels. Aucun test actif de navigateur, admission, réponse, quota fournisseur ou reprise n'a été exécuté. Aucun Swift modifié dans le lot 1.
- **État de remise : lot 1 terminé, aucun commit/add/push et aucun lot 2 commencé.** Fichiers du lot : `claude_desktop_apply.py`, `codex_rpc.py`, `websocket_transport.py`, `package_helpers.py`, tests Python associés, fixture DOM et ce document. Prochaine étape : revue du diff puis commit par le coordinateur et accord séparé avant lot 2. Les fichiers du volet quota R6 et des exemples PR86 sont préservés.

## Correctif ciblé — placeholder ProseMirror

Observation DOM fournie par le coordinateur : DIV contenteditable avec `innerText="\n"`, `textContent=""`, unique enfant élément P contenant un unique élément BR de classe unique `ProseMirror-trailingBreak`. `readText` partagé reconnaît seulement ce squelette précis comme vide ; aucun trim général et aucune modification de la primitive de clic. Les autres structures gardent leur texte, y compris espaces, retours multiples et squelettes contenant une image jointe.

Validation locale : 33 tests ciblés du transport OK dont 1 entrée Node ignorée ; 64 fixtures DOM exécutées et réussies via l'outil JS local, sans navigateur ni connexion CDP. Le placeholder avec bouton désactivé peut être préparé mais ne constitue pas un envoi. Aucun test live effectué par cet agent.

## Suite du coordinateur, distincte des contrôles simulés ci-dessus

Le 27 octobre, le coordinateur a exécuté une continuation CDP ponctuelle expressément autorisée : placeholder vide corrigé et testé, puis une émission effective et une réponse dans le chat ciblé. Voir `agent-resume/evidence/claude-cdp-continuation-20261027.json` et le contrat livré actualisé. La mention historique « aucun test actif » ci-dessus décrit uniquement la remise du sous-agent avant cette expérience. Les lots 2/3 restent planifiés, sans activation.
