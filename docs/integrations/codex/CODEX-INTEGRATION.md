# Intégration Codex — historique et variantes de transport

**Pour une délégation actuelle depuis DSH via Reprise**, consulter d'abord la [route opérateur DSH → Reprise → Codex](../harness/DSH-REPRISE-CODEX-ROUTE.md). Elle documente le connecteur **géré** `@deepseek-ai/dsh-mcp-client` en STDIO, la release d'intégration, le proxy App Server sur socket Unix existant, les reçus durables, les modes `expertise`/`implementation` et la réconciliation. **Ne pas confondre** cette route avec le fournisseur `openai-codex` de DSH. Les recettes d'installation manuelle et constats datés ci-dessous sont conservés comme historique ; ils **ne sont pas** des instructions de déploiement prioritaires et ne prouvent pas l'état d'une installation actuelle.

## Delegation installed in DSH (17 October 2026 — historique)

The Desktop profile now contains a backed-up, marked `reprise-codex-expert` MCP entry connected to the shared Codex daemon, not the separate expert socket. It is bound to the project passed to the installer. Load a new Harness session or reload its profile to discover `mcp__reprise_experts__reprise_delegate`, `reprise_backends`, `codex_review` and `codex_followup` (the latter names have the same server prefix).

```sh
python3 install-expertise.py --project-root /absolute/project
```

The installer preserves the rest of the profile, backs up modified files under `~/.dsh/backups`, installs the `reprise-delegation` skill, and is idempotent. Installing for another root replaces this profile’s project binding; do not assume that the active DSH project changes the MCP bridge root automatically.

`reprise_delegate` takes `kind` (`expertise`, `review`, `batch`), `level` (`standard`, `deep`), `question`, and optional project-relative `paths`. The Codex model lineup is dynamic: it is fetched live from the server catalog (`model/list`, 30 s TTL cache) and persisted as last-known-good in `~/Library/Application Support/Reprise/codex-models.json`; when the catalog service is unavailable, the persisted lineup is used and explicit models are admitted against it (a later dispatch fails clearly if the model is really gone). Ordered routing preferences are resolved against the current catalog — default expertise/review and batch use medium effort, deep uses high. Models and efforts are checked against the current catalog before admission to a model turn. Batch generation returns code or patches: Harness applies them and runs deterministic checks. In default `mode: expertise` it does not write; the separately authorized `mode: implementation` can apply project edits and run checks under native workspace-write, without any implicit push, merge or deployment authorization. The historical full response transport was bounded; current deployments support persisted compact receipts and paginated recovery (`return_mode: receipt`), and full results above the current transport threshold return a manifest. See the current operator route and [delegation persistence contract](../../contracts/DELEGATION-PERSISTENCE.md). Split large jobs into coherent lots.

A follow-up remains bound to an allowlisted expert thread, model and effort. It can reload that thread if unloaded, but does not steer an active session. Quota failures never change accounts or billing automatically. `reprise_backends` reports, without inference, every backend of the bridge (`codex`, `foundation-models`, `autoclaw`, `claude-code`, `claude-desktop`, `ironclaw`, `chatgpt`, `metaai`) with its configuration state; this is capability information, not a live quota check.

Live validation through the bundled DSH MCP SDK succeeded: Luna returned `323`, then an allowlisted follow-up returned `324` on the same thread/model. This used the included ChatGPT allowance. Astra/high also started a real targeted code review, read the requested files and stopped at an approval request; the bridge reported `blocked_permission` and interrupted its own turn. This is a verified permission boundary, not a completed review. The test exposed and corrected two earlier simulated-test blind spots: quoted MCP override keys created malformed server entries; resuming a just-created thread before its first turn failed because no rollout existed. Thread-specific configuration disables each inherited server with nested override objects; a real thread-scoped MCP inventory must report every entry disabled before a model turn is admitted. An empty table does not disable inherited entries because configuration tables merge recursively. Only follow-ups call `thread/resume`.

Claude Code now has a separate explicit opt-in expertise adapter; see [its contract and activation gates](../claude/CLAUDE-INTEGRATION.md). It remains disabled by default pending native login and operator confirmation that paid usage credits are disabled. The installed 2.1.299 binary is detected in the VS Code extension, but its CLI reports no authenticated account in the tested environment. A subscription adapter should preserve OAuth authentication with `--safe-mode` rather than `--bare`, use explicit model/session selection and structured terminal results, and never silently use API credentials. AutoClaw’s documented CLI can fall back to embedded execution, so neither a cheap price nor a safe automatic billing route is currently established.

---

# Codex integration options

Verified 2026-10-16 against official documentation and the installed DSH 0.2.0-rc.2 bundle. No credentials or model requests were used.

## Resume and delegation

Codex app-server exposes agent conversations, permissions, tools and streamed execution events through JSON-RPC. It is not an OpenAI-compatible inference endpoint. DSH can delegate a bounded coding task through a Reprise tool adapter, with Codex performing the task inside its own harness. DSH's MCP client supports stdio and Streamable HTTP. `expert_mcp.py` now exposes a narrow stdio bridge with `codex_review` and `codex_followup`.

The installed CLI and [official app-server documentation](https://learn.chatgpt.com/docs/app-server) support these explicit launch/connect forms:

```sh
codex app-server --listen unix://
codex app-server --listen unix:///absolute/private/path/codex.sock
codex --remote unix://
codex app-server proxy --sock /absolute/private/path/codex.sock
```

Launching a listener creates a new server; it does not attach a socket to an already-running Desktop owner. Existing threads held by another server remain protected by writer ownership. A socket must be opened by the owning server or clients must initially share the same daemon. Desktop attachment must be verified separately. Do not delete locks or restart another application's server to obtain ownership.

### Desktop shared-socket mode: retired (issue #172)

The former `shared-mode prepare|launch|check|stop` UI/CLI pathway is now
**unsupported and fail-closed**. On the operator's Mac, ChatGPT Desktop
reliably kept its bundled Codex app-server on **stdio**, while an attempted
`CODEX_APP_SERVER_WS_URL` socket attachment did not remain stable.

The previous check only established socket responsiveness and that
`thread/read` could see a specified thread. It did **not** prove that
ChatGPT Desktop used that server as its RPC client. Nevertheless, the old
check could store a `verified_session` and restore scheduler automation.
That validation is unsafe to reuse and is now projected as **unverified**;
historical fields remain in local storage for diagnostic provenance only.

All four shared-mode actions refuse without side effects: no Desktop launch,
no special socket variables, no app-server daemon start/stop, no thread
mutation and no scheduler auto-enable. The `shared_mode.state()`
projection always reports `startup_eligible: false` and
`desktop_socket_binding_verified: false`, even with historical
success flags. Use Desktop's native behavior normally; existing explicit
Codex RPC delegation routes are **not** retired.

Future restoration requires an independently verified proof of Desktop's
actual owner/endpoint through its lifetime, plus controlled post-fallback
monitoring and a separate operator authorization. A readable thread, socket
liveness or application launch receipt is not sufficient.


**Observation opérateur :** les essais du mode socket ont été effectués alors que ChatGPT Desktop était toujours lancé **depuis Reprise**. La préférence stdio natif ne s'explique donc pas par un Desktop démarré hors Reprise. Le retrait #172 du mode socket partagé est **intégré à dev** ; le transport CDP #161 est intégré comme prototype sans daemon actif. Un événement de quota ne doit pas disparaître lorsque la propriété native reste inconnue : voir [le contrat de continuation](../../contracts/QUOTA-CONTINUATION-CONTRACT.md).

Use owner-only socket directories, retain approval/sandbox policies, and verify thread identity, project path and idle status before submitting a turn. A parent conversation is a useful coordinator only if its own agent tools still control the child; the server owner and the conversational parent are distinct concepts.

The old `codex mcp-server` recipe is obsolete: [OpenAI documents its removal](https://learn.chatgpt.com/docs/mcp-server). A new integration must speak app-server JSON-RPC or explicitly translate a narrow MCP tool contract. App-server remains experimental.

## Genuine inference through ChatGPT plan usage

There is now a separate official route: [Sign in with ChatGPT](https://developers.openai.com/siwc/quickstart). Eligible Plus/Pro users can authorize participating applications to use their plan through the public Responses API. Availability includes open-source clients and selected private clients; this private repository's eligibility has not been established. Documentation mentioning locally run personal projects does not prove admission for this installation.

The [registration contract](https://developers.openai.com/siwc/token-sharing-open-source/sign-in) needs no client secret or partner API key. It requires a dedicated user-consented OAuth flow, PKCE, state/nonce checks, verified ID-token signatures, a stable installation host ID and the issued client ID. Granted scopes must include `chatgpt.tokens.use.direct`. Existing Codex/Desktop credentials must not be copied or repurposed.

A future DSH provider could reuse its existing Responses transport, with an app-specific OAuth credential manager. [Inference requirements](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference) include account-specific model discovery, `store:false`, `stream:true` and an explicit successful terminal event. Catalog visibility does not prove request entitlement. The public endpoint is `https://api.openai.com/v1/responses`.

[Preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations) require full necessary history on HTTP requests and exclude several common parameters, persistent server conversation IDs and hosted tools. Therefore a simple base-URL substitution is insufficient: tool encoding, history, request fields, stream errors and refresh behavior need contract tests.

## Installed DSH capabilities

The shipped `@deepseek-ai/dsh-llm-pi-ai` adapter uses pi-ai 0.87.1 and includes `openai-responses`. Its existing `openai-codex` provider instead targets `chatgpt.com/backend-api` with its own OAuth implementation. That path is distinct from the official public plan-usage flow above; its presence does not validate the new flow or justify extracting another application's tokens. No DSH configuration was changed during this investigation.

The canonical headless bundle is `@deepseek-ai/dsh-headless`. It runs the model/provider configured in that explicit profile; it does not inherit the historical session's provider automatically.

## Quotas and cache

Delegating to Codex still consumes its account allowance. Official plan usage is also subject to admission and plan/credit limits; neither transport creates additional quota. No cache saving or independent quota pool has been measured. Preserve stable instructions, tool schemas and project-specific history, and send compact task boundaries rather than duplicate full conversations. Keep billing routes explicit: do not silently fall back from a quota-blocked subscription to a paid API.

Recommended order: finish verified owner-aware resumption first; add one bounded delegation tool next; build a dedicated public Responses provider only after eligibility and OAuth consent are validated. Reprise should coordinate tasks and expose truthful execution state, rather than emulate an inference API on top of agent turns.

## Expertise tool usage

Expertise threads request `chatgpt_hidden` classification and persistent history. This classification is an observed Desktop filter, not a substitute for lifecycle checks. Codex may return a rollout path before creating its file. Reprise registers follow-ups only after reading the matching thread and verifying that its history file exists. A missing history blocks resumption without creating a replacement.

If preflight fails before any turn submission, Reprise re-reads its newly created thread and deletes it through the native API only when it is still idle, has no turns and no history file. An ambiguous `turn/start` response never triggers deletion or automatic retry. Existing durable histories and caller-supplied follow-up threads are never deleted by this cleanup. The installed-server lifecycle test uses an isolated temporary `CODEX_HOME`, without authentication or inference.

**Legacy/manual path only; do not use as the managed DSH deployment recipe.** The managed route attaches to an **existing** owner through `codex app-server proxy --sock` and never creates a replacement server. Historical isolated development sequence (private socket directory required):

```sh
./resume expert-server --socket /absolute/private/path/codex-expert.sock
./resume server-check --socket /absolute/private/path/codex-expert.sock
python3 expert_mcp.py --project-root /absolute/project --socket /absolute/private/path/codex-expert.sock
```

The last command is an **historical manual** MCP STDIO entrypoint, not an interactive shell. The current managed installer builds the `@deepseek-ai/dsh-mcp-client` entry automatically from a sealed release, with `transport: stdio`, `serverName: reprise_experts`, explicit `--project-root`, `--state` and `--tools`, a call timeout set by the installer, and retries disabled. The example `reprise-expert.local.patch.yml` belonged to a workspace experiment; do **not** infer that it is installed or that its project binding matches the current DSH profile.

Calls return the bounded response, Codex thread/turn IDs, event types and explicit errors. `codex_followup` accepts only review threads recorded by this bridge for its fixed project and endpoint, and reuses their Codex history. No full DSH conversation is copied. Approvals or user questions stop the tool and are reported to DSH; the bridge does not grant them, impersonate a user reply or claim an unfinished review succeeded.

The bridge retains its existing five-percent provider quota reserve and one active expertise request. No backend has a local daily call cap; --daily-limit was removed (#276) and is refused; stored expert_calls counts are inert. All inference tools require caller-persisted request_key; recovery and lineage are described in [DELEGATION-PERSISTENCE.md](../../contracts/DELEGATION-PERSISTENCE.md). This is an admission policy, not a maximum token guarantee: one accepted agent turn can contain many model/tool steps. Read-only sandbox and no command network access are requested on every turn. Inherited MCP servers, apps, plugins, hooks, browser/computer tools and subagents are disabled for these expertise threads through thread-specific overrides; the rest of Codex is unchanged. The fixed cwd and path validation constrain requests, not a hard OS confidentiality boundary for all local reads.

Validation: protocol discovery with the actual bundled DSH MCP client succeeded (`codex_review`, `codex_followup`); fake-server contract tests cover responses, quota reserve, API-account refusal, tool policy, follow-up ownership, scope escape, approvals and persistent call limits. The real Codex socket initialization succeeded without model inference. A live review remains unverified; no paid request or subscription quota was spent on testing.


## Orientation actuelle — service Reprise unifié

Le [contrat multi-harness](../../contracts/HARNESS-SUPERVISION-ARCHITECTURE.md) prévoit un même point d’entrée logique pour demander une expertise, recevoir une tâche et rendre/récupérer un résultat. Codex conserve ses transports natifs et garanties existantes ; le regroupement MCP reste à implémenter. Priorité fonctionnelle immédiate : permettre à un harness demandeur d’obtenir une réponse Claude Desktop, y compris compte gratuit.

## Mode `implementation` : écriture du projet permise par défaut (#104, #252)

Depuis #252 (décision opérateur du 27/11/2026), `reprise_delegate`/`reprise_followup` acceptent toujours `mode: "implementation"` pour Codex. `reprise_backends` rapporte pour `codex` : `modes: ["expertise", "implementation"]`, `workspace_write_authorized: true` et `implementation_scope` = le `project_root` du pont.

Garde-fous conservés : sandbox native `workspaceWrite` limitée au projet, réseau coupé, approbations natives Codex requises (`awaiting_human` ; voir #284 pour leur centralisation). `mode: "expertise"` reste en lecture seule. Le mode est attaché au thread et hérité par `reprise_followup` ; une revue ne devient pas une session inscriptible en cours de route.

Il n'existe plus de grant `--codex-workspace-write` ni d'option `--delegate` : un pont installé avant #252 est réécrit par le prochain `harness-integration-plan`/`apply`, puis doit être redémarré.

## Manifeste experts déclaratif (#258 lot 2, #259)

La configuration des backends experts vit dans `reprise/experts-manifest.json`
(`schema_version: 1`), lu au démarrage du pont par `load_experts_manifest()`.
Principe : **le manifeste décrit, la disponibilité se mesure** — `enabled` ne
rend jamais un backend disponible, et un backend non déclaré n'est jamais
routé par défaillance vers un autre.

Champs par backend (`reprise.expert_mcp`) :

- `session` — **obligatoire, sans défaut** : `stdio_thread` (codex,
  claude-code), `durable_taskstore` (claude-desktop, metaai) ou `push_only`
  (chatgpt, foundation-models, ironclaw) ;
- `capacity` — entier ≥ 1, défaut 1 (#259). Codex est déclaré à 2 ; tout
  backend non listé dans le manifeste garde une capacité 1 (singleton).
  Limite singleton : un seul tour actif par state directory, pas de
  parallélisme pour les singletons physiques ;
- `enabled` — booléen, défaut `true` ;
- `resumable` — booléen, défaut `false`.

Clés inconnues, types invalides ou `session` absente : `ValueError` à la
construction du pont (échec rapide). Fichier absent : même refus (#276), le
manifeste fait partie du bundle ; un backend non déclaré a un seul slot.
`reload_experts_manifest()` relit le
fichier à chaud ; un manifeste invalide au rechargement lève `ValueError`
sans écraser l'état courant.

Refus deterministes (statut `engine_unavailable`, `automatic_fallback: false`,
jamais de tombée vers un autre backend) :

- backend `enabled: false` → « désactivé dans le manifeste » ;
- backend déclaré sans handler de dispatch (ex. `metaai`, déclaré
  `enabled: false` : l'infrastructure TaskStore/WebDAV existe, aucun handler
  d'expert n'est branché) → « déclaré sans handler » ;
- backend connu de l'enum mais absent du manifeste → « inconnu du manifeste ».

Le nouveau diagnostic `reprise_experts_list` (sans argument) croise
déclaration et mesure : par backend, `enabled`/`capacity`/`resumable`/
`session` (manifeste), `handler` (existence d'un dispatch), et
`measured_available`/`availability` mesurés au moment de l'appel (programme
codex joignable, ironclaw configuré, `desktop_values` présents, TaskStore
chatgpt ouvert, etc.).

## Pont ChatGPT desktop (issue #156)

**Historique :** la première approche de #156 était un serveur MCP local
(`chatgpt_mcp.py`, supprimé par cb7989c — fausse piste MCP : le client MCP de
ChatGPT desktop n'a jamais complété la boucle). Le canal actuel est le dispatch
CDP pipe décrit en § Issue #218 ci-dessous (`chatgpt_desktop_pipe.py` :
holder persistant, profil dédié, receipts durables), avec dispatch par
`chatgpt_quick.review` (admission) puis push holder ; retour principal =
capture de réponse par le holder dans le receipt durable, TaskStore
`chatgpt-state` = registre d'admission seul (idempotence du `request_key`),
jamais un canal de retour.

### Issue #218 — émission ChatGPT Desktop sur CDP pipe (validée live sur Mac, 17/11/2026)

Le helper Python `chatgpt_desktop_pipe.py` accepte `inspect`, `probe` et désormais
`send` : l'aller seul vers un renderer `app://-/index.html` possédé par
une instance à profil dédié. Le retour métier = capture de réponse par le
holder dans le receipt durable (mesures du 17/11/2026 : le flux de tokens est
dans le process principal owl, invisible au CDP — capture = lecture rendue
ancrée + preuve de complétude WS `conversation-turn-complete`, fenêtre calme
MutationObserver 20 s (`STREAM_QUIET_MS`), marqueur rendu « réponse
terminée », settle DOM 6 s après turn-complete) ;
ChatGPT Desktop n'a aucun outil MCP Reprise. Un tour visible dans Desktop
ne prouve jamais `response_complete` — c'est le receipt qui le porte.

```sh
python3 -B reprise/chatgpt_desktop_pipe.py send --profile dedicated \\
  --prompt-file /chemin/prompt.txt --request-key cle-stable-du-job
# --prompt-file - lit uniquement stdin ; aucun prompt inline accepté.
```

Le prompt doit être non vide et contenir au plus **20 000 caractères Unicode**
(la limite indépendante **12 000** du bridge n'est pas modifiée).
Sans `--request-key`, son empreinte sert d'identifiant stable : deux demandes
distinctes portant exactement le même texte devront fournir des clés différentes.
L'empreinte, et non le prompt, est enregistrée dans le profil privé.
Une tentative de clic est inscrite durablement **avant** l'appel CDP ; tout
rejeu avec la même clé ne clique jamais une seconde fois, y compris si la
première tentative est restée `incertain`.

Sortie JSON triée : `status` vaut `refused`, `incertain`, `sent`
ou `confirmed`. `sent` exige ensemble un clic observé et le compositeur
vidé ; `confirmed` ajoute la capture d'un texte assistant non vide avec
une preuve de complétude (`completion` = `sentinel`, `turn-complete`,
`dom-marker` ou `quiet`). L'écho du prompt rendu vaut preuve d'arrivée (`user_confirmed`) ;
la consigne porte un sentinel (`REPRISE-CAPTURE-FIN`) qui borne l'extraction.
`response_complete` reste distincte d'`operator_isolation_verified` (toujours
**false** tant que la coexistence n'est pas prouvée). Codes de sortie :
0 si preuves `sent/confirmed`, 64 si prompt absent/vide, 2 sinon.
Une perte du transport après la frontière du clic reste `incertain` et
n'autorise jamais un deuxième envoi. Aucun repli port TCP/API payante.

**Limite opérationnelle déterminante :** l'application ChatGPT officielle
ne garantit pas la prise en charge de `--remote-debugging-pipe`.
`launch_failed` / `pipe_unavailable` sont des résultats attendus et non
des autorisations de passer au port DevTools historique. La route est
validée live sur Mac depuis le 17/11/2026 (`t1-live-1794938260` : PONG
capturé, `delivery=captured`, `completion=sentinel`) ; la coexistence sans
interférence avec l'application opérateur reste non prouvée
(`operator_isolation_verified` toujours **false**) ; les tests synthétiques
de `PipeTransport` n'établissent pas la fonctionnalité Desktop.


### #221 — Backend ChatGPT universel, y compris route non vérifiée

Les connecteurs gérés DeepSeek Harness et Claude Desktop propagent désormais
`--chatgpt-state` (répertoire d'état SQLite, pas un socket réseau), avec le
même périmètre projet que le pont ChatGPT (TaskStore `chatgpt-state`). L'ancien alias
`--chatgpt-socket` est supprimé depuis #250 (refusé par les ponts).
`reprise_backends` liste `chatgpt` même lorsque son état est absent
(`configured: false`) ; avec l'état partagé initialisable,
`configured: true` signifie uniquement que le canal d'admission/retour
est disponible. `availability: unverified` et `transport_verified: false`
sont maintenus jusqu'à un véritable aller-retour Mac, jamais déduits de
`tools/list`. Le champ `dispatch` passe à `holder_capture_sync` quand le
pont pousse vers le holder, ce qui est le défaut depuis #282 (27/11/2026) ;
`admission_only` n'est plus obtenu que par `--no-chatgpt-dispatch`.

L'utilisateur a explicitement autorisé les essais de la route non vérifiée.
Les façades ChatGPT et Claude autorisent désormais l'auto-délégation pour une
**nouvelle demande** avec clé unique ; elles ne réenvoient pas automatiquement
une demande existante ni une tentative incertaine. Le pont `chatgpt_quick.review`
**admet** la tâche dans le registre puis la pousse au holder, sauf si le pont
est lancé avec `--no-chatgpt-dispatch` (voir #226 et #282 ci-dessous). Aucun message Desktop,
modèle exécuté ou `submit_result` accepté ne peut donc être déduit du seul
reçu d'admission.

**Retour de réponse — capture holder, sans action de ChatGPT (17/11/2026).**
Le retour principal ne dépend PAS d'une invocation MCP côté ChatGPT Desktop :
après le clic, le holder capture la réponse directement. Mesures du
17/11/2026 (3 runs instrumentés) : aucun évènement EventSource, aucun fetch
conversation côté renderer — le flux de tokens vit dans le process principal
owl et n'est pas observable au CDP. La capture v1.1 s'appuie donc sur :
lecture rendue ancrée (`bodyProbe` + `extract_reply` : écho du prompt
(dernière occurrence), sentinel `REPRISE-CAPTURE-FIN` prioritaire, pied
« ChatGPT peut faire des erreurs » ; l'extraction retenue est la DERNIÈRE
sonde, la plus longue n'est qu'un repli), preuve de complétude WS
(`conversation-turn-complete` sur le socket topics `ws.chatgpt.com`),
détecteur de changement ciblé (MutationObserver injecté, fenêtre calme
20 s, `STREAM_QUIET_MS`), marqueur rendu « réponse terminée »
(`COMPLETION_MARKERS`) et settle DOM 6 s après turn-complete
(`COMPLETION_SETTLE_SECONDS`) ; deux sondes rendues identiques ne
complètent plus seules (prématurées en live). Le receipt durable
porte `response_complete`, `completion`, `response_sha256`,
`response_chars` et un excerpt borné (1500 car.) — jamais le texte complet.
Le texte intégral (≤ 20 000 car.) n'existe que dans le retour synchrone au
délégant. AXTree est écarté (faux positifs mesurés) ; SSE est mort sur ce
client. ChatGPT n'a AUCUN outil MCP Reprise ; le TaskStore
`chatgpt-state` est un registre d'admission seul (idempotence du
`request_key`), jamais un canal de retour ni un « complément de preuve » : un
aller-retour consultatif se ferme sur le receipt holder seul.

### Issue #226 — Chaînon admission → dispatch holder (actif par défaut depuis #282)

Décision opérateur du 27/11/2026 (#282) : le dispatch holder est actif par
défaut sur `expert_mcp.py` et `claude_mcp.py` ; `--no-chatgpt-dispatch` est la
seule désactivation (l'ancien `--chatgpt-dispatch` est retiré par #252).
Le holder est démarré à la demande par `chatgpt_desktop_pipe.send` et reste
vivant ensuite. Le pont enchaîne après
chaque admission fraîche ou rejouée : `chatgpt_quick.dispatch_to_holder`
lit le prompt admis (jamais dupliqué), pousse via le client holder
(`chatgpt_desktop_pipe.send`, at-most-once par receipts durables), puis
écrit le reçu borné dans la colonne `dispatch_receipt` du registre. Si le
reçu stocké est déjà terminal (`sent`/`confirmed`), aucun re-dispatch n'a
lieu même si les fichiers du holder ont disparu. Un refus holder laisse
l'admission `attempted` et rejouable ; le délégant reçoit `dispatch_receipt`
et l'admission reste `admission_only`. Quand le holder confirme
(`confirmed`/`sent` avec texte), la réponse capturée devient
`result['response']`, `delivery` passe à `captured` et le drapeau
`admission_only` tombe : `reprise_requests` stocke un résultat terminé,
récupérable tel quel par le délégant. Sans le grant, le comportement reste
l'admission seule de #221.

**Binaire du holder — résolution et clone (contrat [APP-CLONES](../contracts/APP-CLONES-CONTRACT.md)).**
Le profil `dedicated` démarre sur le clone gelé
`~/Applications/ReChatGPT.app/Contents/MacOS/ChatGPT` dès qu'il existe
(`resolve_binary`), sinon sur `/Applications/ChatGPT.app` (jamais mutée).
Le clone porte l'identité `com.reprise.rechatgpt` + nom affiché « ReChatGPT »,
auto-update Sparkle coupé pour SON identité, version gelée dans
`app-clones.json` ; sa fenêtre est marquée « ReChatGPT » (titre CDP) et la
fenêtre compagnon owl est fermée (politique une-fenêtre, maintenance idle
10 s). Les opérations inspect/plan/apply/verify et le refresh après update
upstream sont documentés dans le [runbook APP-CLONES](../runbooks/APP-CLONES.md) ;
le branchement comme composant natif `managed_update` suit avec la release.

**Déploiement** : reconstruire la release depuis `dev`, prévisualiser/appliquer
à nouveau les connecteurs gérés (DeepSeek, Claude, ChatGPT),
puis recharger les clients. Lire `reprise_backends` depuis chacun et tenter
une délégation `backend: chatgpt` avec un `request_key` nouveau. Exiger une
preuve distincte d'arrivée Desktop et de capture de réponse (receipt holder,
`response_complete`) avant de déclarer le transport réellement fonctionnel.
Ni fallback réseau/TCP ni bascule vers une
API payante implicite.

### Issue #260 — Followup ChatGPT : continuation de la fenêtre courante

`reprise_followup` accepte désormais `backend: "chatgpt"` avec `thread_id`
égal au `request_key` de l'admission ChatGPT d'origine. La possession est
levée seulement si le registre `chatgpt-state` (`chatgpt_submissions`)
contient, pour CE périmètre projet (`scope` = racine du pont), une ligne dont
le reçu durable de dispatch (`dispatch_receipt`) est présent : la fenêtre
ChatGPTDesktop visée est donc celle d'un aller-retour déjà poussé, jamais une
fenêtre arbitraire. Sans cette preuve, le refus est typé
`thread_not_owned` (`backend: "chatgpt"`, `thread_id`, `resume_with:
"parent_request_key"`), comme pour les autres backends (#100).

La session est déclarée `window_continuation` dans le manifeste experts
(`resumable: true`) : il n'existe pas de thread relançable côté ChatGPT —
le followup continue la fenêtre courante du holder dédié. L'ancrage de la
réponse est renforcé côté pipe : le reçu durable porte désormais
`conversation_id`, extrait (borné à 64 KiB par trame) de l'évènement WS
`conversation-created` lors du drain de fin de tour. Si la fenêtre annonce
un second `conversation_id` distinct pendant le tour, la divergence est
**signalée** dans le reçu (`conversation_id_divergent: true`) sans refuser
le dispatch — preuve conservée, jamais une censure. L'absence d'identifiant
(sig Frames absentes, WS mort) laisse `conversation_id` à `null` et le
comportement inchangé.

Invariants inchangés : le mode `implementation` reste refusé pour chatgpt
(expertise/revue consultative uniquement) ; l'au-plus-une-fois du dispatch
holder porte sur l'identité du followup lui-même (request_key actif), pas
sur le thread d'origine ; aucun repli codex ni cross-backend ; les anciens
reçus sans champ `conversation_id` restent chargeables (champ absent =
`null`).
