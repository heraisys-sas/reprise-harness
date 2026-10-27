# Revue indépendante du portage Desktop Apply

## Périmètre et verdict

- Baseline : `d162a2c401ba306815dc4a268457252de7f8fe57`.
- SHA revu : `cc2b3ed20e445c122eef4888cbe918d112b10b46` (confirmé par `git rev-parse HEAD` avant les lectures).
- Diff : portages 85, 84 et 86, soit les 18 fichiers annoncés. Les numéros de ligne ci-dessous désignent ce SHA, pas les corrections ultérieures.
- Compétences appliquées : `engineering:code-review` et `security-checks`.
- Verdict : **changements requis avant tout raccordement réel ou reprise automatique**. Le statut expérimental documenté est cohérent avec la livraison actuelle. Les précautions du README ne constituent toutefois pas des garanties exécutées par le code.
- Aucun défaut actionnable relevé dans la normalisation `Z`/`z` ni dans la préservation de la liste existante des assets. Cela ne vaut pas qualification dynamique du transport.

## Constats actionnables

### R1 — P1 — Vérifier la cible effective avant d'écrire et avant de cliquer

**Preuve :** `agent-resume/claude_desktop_apply.py:158–163`, `185–186`, `408–432`, `446–451`. La sélection accepte une URL contenant simplement `claude.ai`, puis n'importe quelle page si aucune ne correspond. `Page.navigate` n'examine pas `errorText`; après une attente fixe, seul un contenteditable visible est requis. L'origine et le chat effectifs ne sont jamais confrontés à la cible. Même défaut dans `harness-tools/desktop-cdp-example/cdp.mjs:38–41`, `368–387` et `wkwebview/ClaudeApply.swift:178–188`.

**Scénario :** un onglet est déjà sur le chat A; la navigation vers B échoue ou redirige, mais un composeur demeure disponible. Le prompt destiné à B est inséré et envoyé dans A. Une navigation concurrente entre frappe et clic présente le même risque; un champ de login peut aussi satisfaire le sélecteur Python. La forme UUID du paramètre ne protège pas la destination effective.

**Correction attendue :** choisir une cible appartenant à l'instance autorisée; analyser l'URL et exiger l'origine HTTPS exacte, puis la route et le chat attendus. Revalider immédiatement avant insertion et clic, avec arrêt explicite en cas d'écart; exiger le composeur du chat avec son bouton d'envoi. Tester navigation échouée, redirection, faux hôte et changement de chat entre étapes. Limite déjà documentée, mais bloqueur fonctionnel et de confidentialité au raccordement.

### R2 — P1 — Lier une reprise staged au chat et au texte autorisés

**Preuve :** `agent-resume/claude_desktop_apply.py:461–481`; `harness-tools/desktop-cdp-example/apply.py:208–224` et `cdp.mjs:424–440`. `send_staged` redécouvre un onglet, accepte tout texte non vide et clique sans cible attendue, hash ni preuve que ce brouillon appartient à la demande initiale.

**Scénario :** un message reste posé après blocage. Pendant l'attente, l'utilisateur ou une seconde demande ouvre un autre chat et change le brouillon. Le prochain poll de `apply_when_allowed` envoie ce nouveau brouillon. Un simple appel concurrent peut également remplacer la demande à reprendre.

**Correction attendue :** reprendre uniquement avec une cible explicite et le hash du texte intégral attendu, en contrôlant les deux juste avant le clic. L'appelant doit conserver ces preuves et sérialiser les opérations sur un même composeur; à défaut, refuser la reprise. Une reprise sur le seul « composeur actif » ne doit pas être exposée au daemon.

### R3 — P1 — Ne pas convertir un clic ou un dépôt en fin de soumission

**Preuve :** `agent-resume/claude_desktop_apply.py:446–456`, `475–481`, `359–373`; tests `agent-resume/tests/test_claude_desktop_apply.py:62–71`. `b.click()` suffit à retourner `ok:true, stage:envoyé`. `to_submission` transforme ensuite ce résultat en `finished`. Un simple `posé`, sans aucun tour émis, est également `finished` avec `admission_only:false`.

**Scénario :** le clic est exécuté mais la requête est refusée par le fournisseur, interrompue par le réseau ou n'est pas émise par l'éditeur. L'appelant peut terminer la demande comme réussie. Dans le cas `submit=False`, il peut même recevoir une apparence de résultat complet sans admission ni réponse. Une exception après un clic impose en outre de conserver l'incertitude; un nouvel essai aveugle peut dupliquer le tour.

**Correction attendue :** rendre distincts dépôt, clic tenté, admission démontrée, état indéterminé et résultat récupéré. Réserver la réussite de soumission à une preuve indépendante attribuée à la bonne demande; réserver le résultat au canal de retour réel. Corriger les tests qui figent actuellement ces faux positifs. La déduplication durable demeure à l'appelant, mais doit conserver les tentatives dont l'effet est inconnu.

### R4 — P1 — Contrôler le texte intégral avant l'envoi

**Preuve :** `agent-resume/claude_desktop_apply.py:295–305`, `429–437`. Pour un `textarea`, `_select_all` ne fait que `focus()`, sans sélectionner le contenu existant. Après `Input.insertText`, le seul contrôle est `hasText`; aucune comparaison au prompt demandé n'est faite. `apply` remplace aussi un brouillon contenteditable existant sans condition préalable. Le pilote Node possède une comparaison intégrale dans `cdp.mjs:116–121`, ce qui montre une divergence concrète entre les implémentations.

**Scénario :** un textarea contient déjà un brouillon; l'insertion ajoute le prompt au texte au lieu de le remplacer, puis l'ensemble est envoyé. Si l'insertion échoue mais qu'un ancien brouillon demeure dans le composeur, `hasText` permet d'envoyer ce brouillon. En contenteditable, un brouillon utilisateur peut être perdu par la sélection totale.

**Correction attendue :** refuser un composeur non vide sauf opération de remplacement explicitement autorisée, cibler le bon champ et vérifier son texte intégral ou son hash après insertion et avant clic. Couvrir textarea prérempli, insertion sans effet, texte partiel et modification concurrente. Ne pas inclure le texte brut dans les diagnostics.

### R5 — P2 — Conserver « cause inconnue » pour un bouton désactivé

**Preuve :** `agent-resume/claude_desktop_apply.py:348–353`, `439–441`, `472–473`; `to_submission:374–375`. La présence de texte et un bouton désactivé deviennent un `stage:quota`, puis un `blocked_session` avec raison « Quota Claude épuisé », même si `JS_STATE.blocked` est faux et sans événement explicite de quota. Le comportement Node est analogue dans `cdp.mjs:402–413`.

**Scénario :** l'éditeur est momentanément désactivé pendant un chargement, un tour existant ou une autre validation. Reprise classe la demande en quota et peut attendre inutilement, afficher une cause erronée ou lancer une politique de reprise de quota inappropriée. Le README précise correctement que ce signal ne prouve pas un quota, mais le mapping exécutable fait cette affirmation.

**Correction attendue :** exposer un état d'interface indisponible de cause inconnue et conserver séparément les indices; `quota` exige un message explicite ou une observation attribuable. Aucun échéancier ne doit être déduit d'un seul `HH:MM`.

### R6 — P2 — Ne pas prendre la dernière erreur quelconque pour l'état de quota

**Preuve :** `agent-resume/claude_quota_log.py:61–81`, `129–154`; `harness-tools/desktop-cdp-example/apply.py:110–127`, `186–191`. `latest` retourne immédiatement la dernière ligne `[COMPLETION]` avec un champ `type`, y compris une erreur sans rapport avec le quota. Le résultat est exposé comme source `journal` et observation de quota; un événement `rate_limit` sans `resetsAt` reste inversement bloqué sans limite de fraîcheur. Le journal global n'est lié à aucun compte ou pool de l'instance Chrome contrôlée.

**Scénario :** un rejet `exceeded_limit` non expiré est suivi d'une erreur `api_error` sans échéance : le lecteur remplace le blocage par `blocked:false`. Une vieille erreur de rate limit sans échéance peut produire un blocage permanent. Un compte Desktop différent de celui du profil Chrome peut aussi imposer au mauvais transport le quota d'un autre compte.

**Correction attendue :** préserver les événements de quota pertinents séparément des autres erreurs, borner la fraîcheur des événements sans échéance et conserver l'attribution inconnue tant qu'elle n'est pas démontrée. Les autres erreurs ne prouvent pas une levée de quota. Ne pas utiliser le journal global pour admettre ou bloquer automatiquement le profil Chrome sans relation de compte/pool établie. Ajouter des fixtures multiévénements et changement de compte; aucun besoin de lire les journaux privés.

### R7 — P2 — Arrêter un navigateur identifié, pas un préfixe de ligne de commande

**Preuve :** `agent-resume/claude_desktop_apply.py:234–244`; `harness-tools/desktop-cdp-example/apply.py:161–168`; `stop.sh:6–7`. `pkill -f` reçoit `user-data-dir={PROFILE}`, une expression régulière sans frontière finale; le port fourni n'identifie pas le processus arrêté. `raise_for_login` appelle directement cette fonction.

**Scénario :** le profil configuré est `.../chrome`, mais un second navigateur utilise `.../chrome-backup`. La même expression correspond aux deux lignes de commande et peut fermer la seconde session, contrairement au contrat « uniquement le navigateur de diagnostic ». Les caractères regex présents dans un chemin étendent encore la correspondance.

**Correction attendue :** conserver une identité de processus/instance vérifiée au lancement, ou utiliser la fermeture CDP de l'instance dont le profil et la propriété ont été établis. Si l'identité est inconnue, refuser de fermer. Ne pas réparer par une regex seulement échappée qui resterait une correspondance de préfixe.

### R8 — P2 — Respecter les messages WebSocket fragmentés et borner les lectures

**Preuve :** `agent-resume/claude_desktop_apply.py:70–77`, `105–128`, `165–176`. `recv_text` ignore FIN et retourne chaque frame texte ou continuation comme un message complet; `CDP.call` applique immédiatement `json.loads`. Les tailles de header et frame ne sont pas bornées. Le pong émis au ping ne reprend pas le payload reçu. Le handshake ne vérifie pas `Sec-WebSocket-Accept`.

**Scénario :** un pair CDP envoie une réponse JSON valide répartie sur plusieurs frames : le client parse le premier fragment et lève `JSONDecodeError` au milieu de l'opération. Une longueur annoncée démesurée par un service local sur le port configuré entraîne une lecture/mémoire sans borne. Une erreur après une opération mutante doit rester indéterminée, pas devenir une autorisation de rejouer.

**Correction attendue :** réutiliser le framing partagé existant après vérification de ses garanties; réassembler les continuations, traiter les contrôles conformément au protocole, vérifier le handshake et borner les messages. Vérifier ces frontières avec des octets synthétiques hors ligne. L'inventaire ci-dessous n'est pas une validation dynamique du serveur Chrome.

## Frontières de sécurité et inconnues conservées

- **Port CDP et profil :** `browser_up` ne prouve que la réponse HTTP de `/json/version`; `launch` adopte toute instance déjà présente sur le port (`claude_desktop_apply.py:224–229`, `256–257`). Rien ne démontre sa propriété, son profil ou son compte. Un service local peut préoccuper ce port. Le profil est persistant et sa création ne fixe pas explicitement `0700` (`260`); droits effectifs et comportement de Chrome non audités. Exiger une configuration opt-in et une identité d'instance avant l'exposition produit; ne pas promettre une isolation vérifiée sur la seule base de loopback.
- **Diagnostics :** les outils Node retournent le composeur complet dans `JS_STATE` (`cdp.mjs:151`), dans `typed.after` et parfois dans la sortie de `send` (`174`, `395`, `459–465`). Ce sont des exemples de diagnostic, mais les réutiliser dans des logs produit peut copier un prompt ou un brouillon confidentiel. Réduire à longueur/hash et métadonnées autorisées avant raccordement. Aucun secret ni journal utilisateur n'a été consulté ici.
- **Permissions :** cette route Chrome web ne démontre ni les capacités Work/Cowork du client Desktop, ni l'accès aux outils MCP stdio configurés pour Desktop. Un transport d'envoi web ne constitue pas un retour d'expertise. L'inspecteur natif est une capacité distincte, non exécutée et non utilisée ici.
- **TOCTOU et concurrence :** une vérification initiale d'URL ou de texte ne suffit pas; elle doit entourer chaque mutation. Le daemon doit préserver la sérialisation d'un composeur et enregistrer durablement la demande avant toute opération dont le résultat pourrait devenir indéterminé.
- **Maintenabilité :** Python, Node et WKWebView ont déjà divergé sur la validation du texte, les sélecteurs et les statuts. La factorisation doit porter d'abord sur le contrat et les invariants testables, puis sur le framing Python partageable. Éviter d'étendre trois chemins d'envoi concurrents au produit.

## Contrôles réellement exécutés et limites

- Lecture du diff et des implémentations Python, Node, Swift, shell; des tests ajoutés; des consommateurs immédiats Quick Apply et de la CI locale.
- `git diff --check d162a2c401ba306815dc4a268457252de7f8fe57..HEAD` : code 0, aucun diagnostic.
- Inventaire local ciblé : aucune configuration versionnée Semgrep, Bandit, Gitleaks, Ruff, ESLint ou ShellCheck trouvée par les recherches de noms utilisées. CI existante `.github/workflows/reprise.yml` : contrôles de patch/shell, tests déterministes et build, sans analyseur SAST dédié configuré dans ce workflow.
- `command -v` : Python 3, Git et Bash disponibles; `bandit`, `semgrep`, `ruff`, `node`, `gitleaks`, `trivy`, `shellcheck`, `actionlint` absents du PATH observé. Aucun analyseur installé ni service cloud appelé. Une première tentative de lecture de `.github/workflows/ci.yml` a échoué car ce nom n'existe pas; le workflow réel `reprise.yml` a ensuite été lu.
- Contrôles fournis par le coordinateur, **non réexécutés par cette revue** : suite `python3 agent-resume/ci_test.py`, 865 tests OK dont 4 ignorés avec HOME jetable et sockets fixtures autorisés; 3 tests de l'adaptateur exemple, AST Python et syntaxe Bash. `node --check` non exécuté faute de Node.
- Aucun réseau, navigateur, service, login, processus utilisateur ou inférence lancé; aucun audit dynamique CDP, test de framing ou envoi réel effectué. Les scénarios ci-dessus sont déduits des branches exécutables et doivent recevoir des tests synthétiques lors des corrections. Aucun patch produit; seul ce rapport est écrit.

## Checkpoint

Le portage est revu au SHA fixe indiqué; R1–R4 sont les priorités avant le raccordement réel, R5–R8 doivent être traités ou rendus inaccessibles au chemin produit. Le premier lot de correction et factorisation est en cours dans le même clone par un autre agent, hors de cette revue. La prochaine revue comparera ses changements à cette baseline et distinguera correction statique, preuve de tests et admission réelle encore non démontrée.

## Réexamen R6 — `cb9913d985990d6a6166505e0de2c083033965dc`

Revue des cinq fichiers de ce commit via `git show`/`git diff` fixes, sans lecture de journaux privés et sans réexécuter les tests. Le coordinateur fournit 21 tests lecteur et 4 tests adaptateur réussis.

**Correction métier validée par lecture :** `claude_quota_log.py:69–78` ignore les types non quota; `latest:95–97` poursuit alors vers un événement pertinent antérieur. Un reset expiré ou absent laisse désormais `blocked:None` (`115–121`) et aucun reset absent ne devient permanent. Les types non scalaires, booléens de reset, nombres non finis/hors plage et horodatages invalides sont traités défensivement. L'attribution compte/pool inconnue est explicite dans les sorties; l'adaptateur Chrome rend `available:None` (`harness-tools/desktop-cdp-example/apply.py:122–129`), ce qui empêche `status` de fusionner ce journal global comme signal de blocage Chrome. La ligne brute disparaît au profit de son hash (`latest:122`). Ces modifications traitent les causes métier de R6 sans prétendre démontrer l'état courant du compte Chrome.

**R6.1 — P2 restant — Ignorer aussi les JSON dépassant les limites du décodeur.** Aux lignes `claude_quota_log.py:63–66` et `74–77`, les deux décodages capturent `JSONDecodeError` et `TypeError`, mais pas `RecursionError`, ni le `ValueError` générique levé par la limite de conversion des entiers de Python. Une ligne `[COMPLETION]` récente très imbriquée ou contenant un entier de plusieurs milliers de chiffres interrompt donc `latest`, au lieu d'être ignorée en faveur du quota antérieur. L'adaptateur intercepte `ValueError` au prix de la perte de l'observation précédente, mais laisse aussi remonter `RecursionError`. La limite de lecture de 2 Mo n'empêche pas ces deux cas, qui tiennent dans quelques Ko.

Reproduction **synthétique proposée, non exécutée** : après une ligne quota valide, ajouter une ligne au préfixe horodaté valide dont le corps est construit par `'{"type":"api_error","x":' + '[' * 2000 + '0' + ']' * 2000 + '}'`. Le décodage échoue avant le filtrage du type non quota. Autre fixture, avec la limite standard de conversion des entiers active : `'{"type":"rate_limit","resetsAt":' + '9' * 5000 + '}'`. Couvrir également ces corps dans un `message` JSON imbriqué. Correction localisée attendue : capturer les échecs de limites aux deux décodages et poursuivre la recherche, avec fixtures qui préservent l'événement antérieur. Aucun changement du protocole de quota n'est nécessaire.

**État après ce réexamen :** R6 métier corrigé; R6.1 défensif reste à traiter. Les autres constats se rapportent toujours au SHA initial et attendent la relecture du lot 1.

## Revue finale avant fusion — `d56c750ea6ba51b0e4732968ea3eef26c0e96907`

Lecture indépendante des objets Git de ce SHA fixe, cumulativement depuis `d162a2c401ba306815dc4a268457252de7f8fe57`. La fusion locale ultérieure de `origin/dev` est annoncée par le coordinateur au SHA `b0126042555673bd8da9153d68470449c89094d0`; ses fichiers transport/quota/exemples sont annoncés identiques. Cette section ne prétend pas revoir de nouveaux changements amont hors périmètre. Aucun test, processus, réseau ou service n'a été lancé par cette relecture.

### Corrections vérifiées

| Constat initial | État du chemin `agent-resume` au SHA revu |
|---|---|
| R1 — cible | Origine HTTPS exacte, route et chat contrôlés; cible CDP épinglée, refus des pages étrangères/ambiguës et des endpoints WebSocket non locaux ou incohérents. Navigation vérifiée. Contrôles après insertion et au clic présents. **Limite d'insertion résiduelle ci-dessous.** |
| R2 — staged | `send_staged` exige UUID + SHA-256 intégral, ne navigue pas et rejoint la même primitive de clic gardée (`claude_desktop_apply.py:408–429`). Correction du module, mais **contournement restant dans les exemples F1**. |
| R3 — succès | Clic, dépôt et incertitude restent distincts; `to_submission` ne rend jamais `finished` et conserve `dispatch_attempted` (`284–299`, `330–341`). Aucune admission ni réponse annoncée par le transport. |
| R4 — texte | Composeur source et destination exigés vides; sélection textarea correcte; contrôle SHA complet après insertion et avant clic (`218–253`, `368–401`). Pas de texte brut dans les retours contrôlés. |
| R5 — quota UI | `bloqué` reste une cause d'interface inconnue; `quota()` garde `blocked:null` et `quota_confirmed:false` (`310–325`). |
| R6 et R6.1 — journal | Les deux décodages capturent désormais aussi `ValueError` et `RecursionError` (`claude_quota_log.py:63–77`); fixtures de limites ajoutées. Les corrections métier du réexamen précédent restent présentes. **R6 et R6.1 clos sur ce périmètre statique.** |
| R7 — lifecycle | Le module refuse lancement, adoption et arrêt : signatures conservées, retour `False`, diagnostic `lifecycle_unverified`; aucune commande de processus. **Anciennes commandes encore exécutables dans les exemples F1.** |
| R8 — framing | Handshake, masquage client, fragments, ping/pong avec payload, limites et JSON objet partagés dans `websocket_transport.py`. Extraction comparée à `codex_rpc.Client` : I/O proxy, notifications, délais et erreurs `Closed` conservés. Aucun défaut nouveau de régression Codex identifié dans le diff lu. |

**Placeholder ProseMirror :** `readText` (`claude_desktop_apply.py:175–187`) reconnaît seulement DIV contenteditable, `innerText="\n"`, `textContent=""`, unique enfant élément P, unique enfant élément BR sans enfant et avec la seule classe `ProseMirror-trailingBreak`. Pas de trim général; les autres structures conservent leur texte. Les fixtures correspondantes distinguent espaces, retours multiples, images, types de nœuds et classes supplémentaires. Correction cohérente avec l'observation DOM fournie; aucune généralisation à tous les états réels de l'éditeur n'est démontrée par cette lecture.

### F1 — P1 bloquant — Les exemples exécutables contournent encore les protections corrigées

**Preuves au SHA final :** `harness-tools/desktop-cdp-example/apply.py:58–85` appelle toujours le pilote Node autonome, et `88–98` expose encore `send_staged(port)` sans chat/hash. Sa boucle `apply_when_allowed:222–224` réémet par ce chemin. `cdp.mjs:368–419` conserve l'insertion puis clic sans vérifier la cible effective; `424–440` envoie tout composeur actif non vide. `apply.py:163–165` et `stop.sh:6–7` conservent `pkill -f user-data-dir=PROFILE`. Ces fichiers ont été ajoutés par le portage cumulatif; ce ne sont pas des défauts amont sans lien avec le changement.

**Scénario exact :** un utilisateur suit le banc d'essai et lance `apply.py --wait-quota --chat A`. Le message est posé; pendant le délai, un autre chat B et un autre brouillon deviennent actifs. Le poll appelle l'ancien `send-staged`, sélectionne le premier onglet et envoie ce brouillon sans la vérification UUID/SHA qui protège désormais le transport unique. Le même ancien chemin peut encore écraser un brouillon, confondre bouton désactivé et quota, annoncer le succès au seul clic, puis arrêter une autre instance dont le profil partage le préfixe. La mention « exemples non livrés » n'empêche pas leur exécution ni leur réutilisation depuis le dépôt.

**Correction minimale avant fusion du périmètre demandé :** faire des commandes d'envoi des wrappers du transport renforcé avec les mêmes paramètres obligatoires, ou neutraliser les commandes mutantes et la boucle d'attente de l'archive. Retirer/refuser également l'arrêt par recherche de préfixe. Conserver les observations historiques sous forme documentaire si nécessaire. Ne pas laisser deux moteurs d'envoi dont l'un contourne les invariants annoncés comme corrigés. Les commandes génériques `send`, `retry`, `eval` et `navigate` du banc doivent être prises en compte dans cette décision, car désactiver seulement `apply` ne supprime pas les primitives de mutation alternatives.

### F2 — P2 bloquant — Actualiser le contrat livré avec les nouvelles garanties

**Preuve :** `agent-resume/CLAUDE-DESKTOP-APPLY.md:60–73` présente toujours `envoyé`/`posé` comme `finished`, avec `admission_only:false` pour un dépôt; `90` recommande `send_staged(port)` sans les paramètres désormais obligatoires; `92` promet une fenêtre visible, et `43–47` prescrit d'attendre un quota UI ou d'appeler `launch()`. Ce document demeure un asset livré. Le nouveau checkpoint d'intégration dit l'inverse, sans rendre l'ancien contrat caduc.

**Scénario exact :** un intégrateur utilise le document de référence pour brancher `quick_entry/submit`, traite un clic comme terminé et réintroduit l'erreur R3 dans le consommateur; un opérateur utilise les commandes de reprise ou de login documentées et reçoit une erreur ou un refus inexpliqué. Ce sont des instructions opérationnelles incompatibles avec le code livré, pas une préférence rédactionnelle.

**Correction attendue :** mettre ce contrat à jour, ou le marquer clairement historique en tête avec lien vers la source de vérité courante et retirer les recettes opérationnelles contradictoires. Mettre aussi à jour le README exemple si F1 change les commandes accessibles. Le contrat doit annoncer `needs_review`, l'incertitude, UUID/hash obligatoires et le refus du lifecycle non attribué.

### Limites de qualification conservées, distinctes des corrections

- **TOCTOU avant frappe :** `_prepare` termine son évaluation avant `Input.insertText` (`claude_desktop_apply.py:388–391`). Une navigation extérieure ou un changement de focus peut encore intervenir entre ces commandes et recevoir le texte dans un autre champ/document. Le contrôle après frappe interdit ensuite le clic si la cible ou l'empreinte a changé, mais n'annule pas la divulgation/altération éventuelle du texte déjà inséré. Le checkpoint le reconnaît explicitement (`INTEGRATION-DESKTOP-APPLY-DEV.md:31`). La qualification possible reste **essai manuel explicite sur endpoint préparé**, pas garantie atomique d'insertion ni feu vert d'automatisation. Un verrou du daemon ne protégerait pas à lui seul contre une navigation humaine.
- **Identité d'instance :** cible et endpoint local sont contrôlés, mais la propriété du profil et son compte ne sont pas attestés. L'opt-in et le port déjà ouvert ne remplacent pas cette preuve. Le lifecycle reste correctement refusé; aucune activation automatique n'est ajoutée.
- **Admission et retour :** le coordinateur rapporte **une émission live** par `apply` dans le chat ciblé, avec empreinte du prompt visible, réponse contenant le marqueur attendu et fin de génération. C'est une preuve de ce parcours observé, fournie par le coordinateur, **non reproduite par cette revue**. Elle ne qualifie ni retour MCP, ni corrélation durable du résultat, ni déduplication après perte d'ACK, ni reprise après quota. Le code continue à les annoncer non prouvés.
- **Tests :** cette relecture examine les tests ciblés et les résultats transmis par le coordinateur/implémenteur sans les relancer. La suite complète de 865 tests mentionnée dans la revue initiale concernait le portage initial; elle ne prouve pas à elle seule les corrections cumulées. La fixture DOM comprend 64 cas après correction du placeholder selon le checkpoint; elle n'est pas un navigateur réel. Les tests de framing vérifient notamment la fermeture spécifique Codex et CDP.

**Verdict final au SHA `d56c750ea6ba51b0e4732968ea3eef26c0e96907` : changements requis avant fusion, pour F1 et F2.** Les protections du module Python renforcé sont acceptables pour le périmètre expérimental manuel documenté, avec les limites ci-dessus. Elles ne sont pas encore uniformément appliquées à tous les chemins exécutables ajoutés et ne constituent pas un raccordement daemon validé.

## Clôture ciblée — `b03f580fe7775545769b2e7ec44f6eb1ec244a91`

Dernière vérification limitée à F1, F2 et à la garde UUID demandée par l'opérateur, par lecture des objets Git fixes. Pas de nouvelle revue générale, de test, de runtime, de réseau ou de mutation Git. La suite complète finale est en cours chez le coordinateur; son résultat n'est pas présumé ici.

- **F1 clos.** `harness-tools/desktop-cdp-example/apply.py:34–42`, `76–105` refusent envois, reprise staged, attente/polling, probes et lifecycle par une exception déterministe; la CLI rend 2 (`108–122`). Plus de sous-processus, socket ou moteur Node appelé. Seul `quota_blocked` reste une lecture locale non attribuée. `cdp.mjs` est un stub de refus pour toute commande, y compris les anciennes primitives génériques; `launch.sh` et `stop.sh` rendent 2 sans action. Le README décrit la neutralisation et dirige vers la seule CLI produit. La source WKWebView reste explicitement une archive de recherche séparée non qualifiée, sans raccord au produit ni recette active d'envoi. Les tests ajoutés vérifient le refus des points d'entrée et l'absence d'effets externes; leur réussite (7 tests) est transmise par le coordinateur, non réexécutée ici.
- **F2 clos.** `agent-resume/CLAUDE-DESKTOP-APPLY.md` décrit maintenant le contrat courant : clic et dépôt sans achèvement, `needs_review`, cible/hash obligatoires pour staged, refus des fonctions de lifecycle, cause inconnue du bouton désactivé, limite TOCTOU d'insertion et absence de registre durable. Le test live unique est distingué de MCP et de la reprise après quota. Les anciennes recettes opérationnelles contradictoires ont été retirées.
- **Garde UUID validée statiquement.** `claude_desktop_apply.py:346–351` refuse absent, `None`, vide ou invalide avant toute connexion. La seule destination construite par `apply` est `/chat/<UUID>` (`365`); aucun repli `/new` ne subsiste dans cette action. Le test `test_existing_chat_is_required_without_connecting` couvre l'absence/None/vide et l'absence d'appel CDP; les autres tests d'entrée fournissent désormais un chat valide pour exercer leurs propres gardes. L'UUID valide ne prouve pas à lui seul une origine Desktop : initiation/armement Desktop et attribution restent un contrat d'usage et un futur registre, comme indiqué dans `agent-resume/CLAUDE-SESSION-QUOTA-PLAN.md`.

**Verdict publiable : aucun constat bloquant restant dans cette revue, au SHA `b03f580fe7775545769b2e7ec44f6eb1ec244a91`, pour la livraison du transport expérimental manuel et du plan documenté. Fusion acceptable après réussite des contrôles finaux requis par le coordinateur.** Ce verdict ne qualifie pas l'automatisation, le raccordement daemon, la propriété du profil, l'atomicité de l'insertion, le retour MCP, la découverte des sessions ni la reprise après quota. Le seul envoi live démontré reste celui rapporté précédemment; aucun envoi supplémentaire n'a été réalisé pour cette clôture.
