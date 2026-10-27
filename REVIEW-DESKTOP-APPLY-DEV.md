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
