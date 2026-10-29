# Claude Apply : transport CDP expérimental

## Capacité vérifiée et périmètre

Le module `claude_desktop_apply.py` pilote **claude.ai dans Chrome** par un endpoint CDP local explicitement choisi. Il ne contrôle pas le renderer ni l’inspecteur principal de Claude Desktop. Il reste indépendant du daemon Quick Apply, de la découverte des sessions et du planificateur.

Le 27 octobre 2026, un test autorisé sur un navigateur dédié connecté au compte de l’opérateur a confirmé une continuation dans un chat existant : cible UUID exacte, texte intégral vérifié, une émission effective, message visible et réponse de Claude portant le marqueur demandé. La réponse a retrouvé le contexte de cette conversation. La vérification du retour a été réalisée séparément, en lecture seule, par le coordinateur ; le transport ne fournit pas d’extracteur de réponses.

Ce test démontre **un nouveau tour web dans le même chat**. Il ne qualifie ni le bouton natif « Continuer », ni la reprise automatique après quota, ni les outils MCP Desktop depuis le web, ni un retour `submit_result`. Aucun contournement d’authentification, de quota, de fuse, d’attestation ou de bundle n’a été utilisé. Aucune Accessibilité n’a servi à cet envoi.

## Utilisation explicite

Prérequis : endpoint CDP local déjà ouvert et choisi volontairement, navigateur dédié, connexion humaine à claude.ai. L’ouverture d’un port ne prouve pas la propriété du navigateur ou l’identité du compte. Les fonctions de cycle de vie refusent l’action tant que cette propriété n’est pas établie.

```sh
# Observation uniquement : ce résultat ne mesure pas le quota fournisseur.
./resume desktop-apply --status --port 9333

# Une tentative explicite dans une conversation existante.
./resume desktop-apply --chat UUID --prompt-file /chemin/message.txt --port 9333
```

Les options historiques `--raise` et `--raise-on-login` n’ouvrent plus de navigateur. `launch`, `shutdown` et `raise_for_login` retournent `False` ; le motif est `lifecycle_unverified`. Il faut une action humaine ou un futur gestionnaire de cycle de vie qualifié.

```python
from claude_desktop_apply import apply, send_staged, quota

result = apply(prompt, chat_id, port=9333, submit=True)
# Dépôt seulement, sans clic :
staged = apply(prompt, chat_id, port=9333, submit=False)
# Envoi ultérieur : cible ET empreinte complète obligatoires.
result = send_staged(9333, chat_id=chat_id,
                     expected_sha256=staged["detail"]["expected_sha256"])
```

`apply` exige l’UUID exact d’une conversation existante ; un identifiant absent est refusé avant connexion. Les nouvelles sessions du parcours Reprise doivent être initiées et armées depuis Claude Desktop, puis associées au suivi. Ce transport ne crée pas de nouveau chat web. Limite : 12 000 caractères. Il n’y a pas de découpage de prompt, de retry ou de repli vers un fournisseur.

## Résultats et incertitude

| `stage` | Signification | Suite permise |
|---|---|---|
| `envoyé` | réponse à la primitive de clic reçue | vérifier séparément l’admission et la réponse ; ne pas rejouer |
| `posé` | texte intégral déposé et vérifié, sans clic | conserver cible et SHA-256 pour un envoi explicite |
| `incertain` | réponse au clic perdue ou inexploitable | réconciliation humaine ; aucune répétition automatique |
| `bloqué` | bouton désactivé, texte déposé | cause inconnue ; ce n’est pas une preuve de quota |
| `brouillon` | contenu préexistant dans le composeur | refuser l’écrasement |
| `cible`, `empreinte`, `composeur` | précondition non établie | consulter `staged` et `dispatch_attempted` ; ne pas présumer absence d’effet |
| `session` | connexion humaine requise | aucune relance automatique du navigateur |
| `connexion` | endpoint ou échange indisponible | distinguer absence d’envoi et dépôt incertain grâce au reçu |
| `vide`, `paramètres`, `trop-long` | entrée refusée | corriger la requête |

Champs principaux : `staged` peut être `null`, `dispatch_attempted` conserve l’incertitude, `click_observed` constate seulement le clic. `admission_confirmed` et `response_received` restent `false`. `ok=true` signifie une opération locale observée, pas une tâche achevée.

`to_submission` retourne `needs_review`, ou `blocked_auth` pour une connexion requise avant tentative. Il ne retourne **jamais `finished`**, conserve `response=null`, `resumable=false`, `automatic_fallback=false`. Le champ historique `admission_only` ne prouve pas une admission : consulter `admission_confirmed`. Le fait que Claude Desktop reste ouvert sur ce chat synchronisé ne prouve pas qu’il exécutera les outils MCP d’un tour déclenché sur le web ; seul un résultat MCP effectivement reçu et corrélé peut le démontrer.

## Protections et limites

- Origine exacte `https://claude.ai`, URL sans query/fragment, cible CDP épinglée et UUID effectif vérifiés autour des opérations. Aucun repli vers une page étrangère ou plusieurs cibles ambiguës.
- Brouillons source et destination préservés, même lorsqu’ils ne contiennent que des espaces. Seul le placeholder structurel observé `DIV > P > BR.ProseMirror-trailingBreak`, sans texte, est reconnu comme vide.
- SHA-256 du texte complet après insertion et immédiatement avant clic. La vérification de cible et du hash, après calcul asynchrone, précède le clic dans la même évaluation JavaScript.
- `Input.insertText` ne fournit pas de condition atomique liée au document : une navigation extérieure entre préparation et frappe reste une limite. Le contrôle suivant interdit le clic si cible ou texte ont changé. Ce test ponctuel ne qualifie pas une automatisation sans surveillance.
- Aucun lancement, `pkill`, arrêt, permission modifiée ou ouverture d’endpoint implicite. Un port local accessible ne constitue pas une attestation de propriété.
- Le framing WebSocket est partagé avec `codex_rpc` ; propriété, protocole, notifications et erreurs du canal Codex restent dans son adaptateur.
- Le module n’a pas de registre durable ni de sérialisation entre appelants. Le raccordement au service devra réserver une tentative avant l’I/O, garder une barrière durable après effet incertain et éviter les doublons après redémarrage.
- Les diagnostics rendus par le transport n’incluent ni le texte du composeur, ni les bandeaux bruts, ni les erreurs brutes du pair.

## Quotas : observation et attribution

`quota()` lit l’état de l’interface, sans inférence. `sendDisabled` ne distingue pas un quota d’un composeur vide ou d’un autre blocage : `blocked=null`, `quota_confirmed=false`.

`claude_quota_log.py` retient les événements JSON structurés `exceeded_limit`/`rate_limit` et leurs resets, sans exposer la ligne brute. Les lignes malformées sont ignorées. Une erreur sans rapport n’efface pas l’ancien événement. Après échéance, le quota courant reste **inconnu**, pas « libre ».

L’attribution compte/pool du journal Desktop reste inconnue : elle ne peut pas être transférée au profil Chrome. Un événement quota isolé ne désigne pas le chat à reprendre. À une échéance déjà établie, une tentative peut être autorisée sans mesure de quota préalable obligatoire ; les contrôles d’identité, de consentement et de non-duplication restent nécessaires.

## Validation et suite

Les tests Python simulent CDP et les fixtures JavaScript exécutent les expressions réelles sur un DOM synthétique. Ils couvrent notamment cible erronée, brouillons, empreinte, bouton désactivé, réponse perdue, framing et placeholder vide. Le test réel décrit ci-dessus constitue une preuve ponctuelle supplémentaire ; il ne garantit pas les futures versions de claude.ai.

Les sources PR84/85/86 et la revue indépendante sont retracées dans `WORKLOG-DESKTOP-APPLY-DEV.md`, `INTEGRATION-DESKTOP-APPLY-DEV.md` et `REVIEW-DESKTOP-APPLY-DEV.md`. Le plan de découverte, attribution, échéances, interface et orchestration est dans `CLAUDE-SESSION-QUOTA-PLAN.md`. Ces fonctions restent à implémenter et qualifier avant activation automatique.

## Transport par pipe CDP (`claude_cdp_pipe.py`)

Le module `claude_cdp_pipe.py` fournit un transport CCD sur la **paire de descripteurs 3/4** du processus Chrome en mode `--remote-debugging-pipe` : Chrome **lit les commandes sur son fd 3** et **écrit les réponses sur son fd 4** (`devtools_pipe_handler.cc`, chromium main). Les messages JSON sont séparés par NUL (`\0`). Côté parent : un pipe de commandes (le parent écrit, l'enfant lit sur 3) et un pipe de réponses (l'enfant écrit sur 4, le parent lit), passés à l'enfant via `pass_fds=(3, 4)` après un `dup2` autour du spawn ; les fds 3/4 du parent sont sauvegardés (`F_DUPFD`) puis restaurés après le lancement. Ce transport est utilisé par le daemon Quick Apply pour les opérations `cdp/status` et `cdp/resume`.

### Cycle de vie

1. **Lancement paresseux** : `launch_chrome(profile)` démarre Chrome uniquement lors d'un appel `cdp/resume`. `cdp/status` est passif (ne lance rien).
2. **Singleton** : un verrou PID (`<hostname>-pid`) empêche les doubles lancements. Un processus mort est balayé avant relance.
3. **Terminaison** : après chaque tentative de reprise, Chrome reçoit SIGTERM → attente bornée → SIGKILL.
4. **Arrêt authentifié** : `shutdown` exige un token SHA-256 généré au démarrage du daemon.

### Configuration

```sh
# Choisir le transport CDP
./resume claude-quota-config --transport cdp --enabled true

# Vérifier le statut CDP
./resume desktop-apply --status
# Ou via le daemon :
./resume desktop-status  # inclut 'cdp' si disponible
```

### API interne

| Fonction | Rôle |
|---|---|
| `PipeTransport` | Classe principale : `_send()`, `_receive()`, `call()`, `evaluate()`, `insert_text()`, `attach_flat()` |
| `launch_chrome(profile)` | Démarre Chrome avec pipe sur fds 3/4, retourne `(transport, proc)` |
| `terminate_chrome(proc, timeout)` | SIGTERM → wait → SIGKILL |
| `sweep_dead_lock(lock_path)` | Supprime le verrou PID si le processus est mort |
| `check_profile_busy(lock_path)` | Retourne `{'ok': False, ...}` ou `None` |
| `write_lock(lock_path, pid)` | Écrit le PID courant |
| `test_bidirectional()` | Test unitaire sans Chrome (utilise `cat`) |

### Sécurité

- Domaines CDP restreints : `Target.getTargets`, `Target.attachToTarget {flatten:true}`, `Runtime.evaluate` (returnByValue/awaitPromise), `Input.insertText`, `Page.*` minimal.
- Aucun domain enable, aucun isolated world, aucun script injecté globalement.
- Page unique attendue : `/chat/<UUID>` sur `claude.ai`.
