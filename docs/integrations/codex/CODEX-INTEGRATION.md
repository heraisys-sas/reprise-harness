# Delegation installed in DSH (17 October 2026)

The Desktop profile now contains a backed-up, marked `reprise-codex-expert` MCP entry connected to the shared Codex daemon, not the separate expert socket. It is bound to the project passed to the installer. Load a new Harness session or reload its profile to discover `mcp__reprise_codex__reprise_delegate`, `reprise_backends`, `codex_review` and `codex_followup` (the latter names have the same server prefix).

```sh
python3 install-expertise.py --project-root /absolute/project
```

The installer preserves the rest of the profile, backs up modified files under `~/.dsh/backups`, installs the `reprise-delegation` skill, and is idempotent. Installing for another root replaces this profile’s project binding; do not assume that the active DSH project changes the MCP bridge root automatically.

`reprise_delegate` takes `kind` (`expertise`, `review`, `batch`), `level` (`standard`, `deep`), `question`, and optional project-relative `paths`. Standard expertise/review uses **gpt-6.1-sol / medium**; standard batch uses **gpt-6-luna / medium**; deep uses **gpt-6-astra / high**. Models and efforts are checked against the server catalog before admission to a model turn. Batch generation returns code or patches: Harness applies them and runs deterministic checks. It is not an autonomous write worker. Output is bounded to 48,000 characters; truncation is marked incomplete. Split large jobs into coherent lots.

A follow-up remains bound to an allowlisted expert thread, model and effort. It can reload that thread if unloaded, but does not steer an active session. Quota failures never change accounts or billing automatically. `reprise_backends` reports Codex and optional Claude configuration and AutoClaw unvalidated, without inference; this is capability information, not a live quota check.

Live validation through the bundled DSH MCP SDK succeeded: Luna returned `323`, then an allowlisted follow-up returned `324` on the same thread/model. This used the included ChatGPT allowance. Astra/high also started a real targeted code review, read the requested files and stopped at an approval request; the bridge reported `blocked_permission` and interrupted its own turn. This is a verified permission boundary, not a completed review. The test exposed and corrected two earlier simulated-test blind spots: quoted MCP override keys created malformed server entries; resuming a just-created thread before its first turn failed because no rollout existed. Thread-specific configuration disables each inherited server with nested override objects; a real thread-scoped MCP inventory must report every entry disabled before a model turn is admitted. An empty table does not disable inherited entries because configuration tables merge recursively. Only follow-ups call `thread/resume`.

Claude Code now has a separate explicit opt-in expertise adapter; see [its contract and activation gates](../claude/CLAUDE-INTEGRATION.md). It remains disabled by default pending native login and operator confirmation that paid usage credits are disabled. The installed 2.1.299 binary is detected in the VS Code extension, but its CLI reports no authenticated account in the tested environment. A subscription adapter should preserve OAuth authentication with `--safe-mode` rather than `--bare`, use explicit model/session selection and structured terminal results, and never silently use API credentials. AutoClaw’s documented CLI can fall back to embedded execution, so neither a cheap price nor a safe automatic billing route is currently established.

---

# Codex integration options

Verified 2026-10-16 against official documentation and the installed DSH 0.2.0-rc.2 bundle. No credentials or model requests were used.

## Resume and delegation

Codex app-server exposes agent conversations, permissions, tools and streamed execution events through JSON-RPC. It is not an OpenAI-compatible inference endpoint. DSH can delegate a bounded coding task through a Reprise tool adapter, with Codex performing the task inside its own harness. DSH's MCP client supports stdio and Streamable HTTP. `codex_expert_mcp.py` now exposes a narrow stdio bridge with `codex_review` and `codex_followup`.

The installed CLI and [official app-server documentation](https://learn.chatgpt.com/docs/app-server) support these explicit launch/connect forms:

```sh
codex app-server --listen unix://
codex app-server --listen unix:///absolute/private/path/codex.sock
codex --remote unix://
codex app-server proxy --sock /absolute/private/path/codex.sock
```

Launching a listener creates a new server; it does not attach a socket to an already-running Desktop owner. Existing threads held by another server remain protected by writer ownership. A socket must be opened by the owning server or clients must initially share the same daemon. Desktop attachment must be verified separately. Do not delete locks or restart another application's server to obtain ownership.

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

Start the explicitly separate local server in a terminal (private socket directory required):

```sh
./resume expert-server --socket /absolute/private/path/codex-expert.sock
./resume server-check --socket /absolute/private/path/codex-expert.sock
python3 codex_expert_mcp.py --project-root /absolute/project --socket /absolute/private/path/codex-expert.sock
```

The last command is the MCP stdio entrypoint for DSH, not an interactive shell. Configure a DSH `@deepseek-ai/dsh-mcp-client` row with `transport: stdio`, `serverName: reprise_codex`, a Python 3.11+ executable, the script and those arguments; set `toolCallTimeoutMs: 330000` and disable reconnection retries. Use a project-specific overlay rather than installing a project-bound tool globally. A local `reprise-expert.local.patch.yml` is generated for this workspace, excluded from Git; it has not been merged into the Desktop profile.

Calls return the bounded response, Codex thread/turn IDs, event types and explicit errors. `codex_followup` accepts only review threads recorded by this bridge for its fixed project and endpoint, and reuses their Codex history. No full DSH conversation is copied. Approvals or user questions stop the tool and are reported to DSH; the bridge does not grant them, impersonate a user reply or claim an unfinished review succeeded.

The bridge retains its existing five-percent provider quota reserve and one active expertise request. No backend has a local daily call cap; legacy --daily-limit and stored expert_calls counts are inert. All inference tools require caller-persisted request_key; recovery and lineage are described in [DELEGATION-PERSISTENCE.md](../../contracts/DELEGATION-PERSISTENCE.md). This is an admission policy, not a maximum token guarantee: one accepted agent turn can contain many model/tool steps. Read-only sandbox and no command network access are requested on every turn. Inherited MCP servers, apps, plugins, hooks, browser/computer tools and subagents are disabled for these expertise threads through thread-specific overrides; the rest of Codex is unchanged. The fixed cwd and path validation constrain requests, not a hard OS confidentiality boundary for all local reads.

Validation: protocol discovery with the actual bundled DSH MCP client succeeded (`codex_review`, `codex_followup`); fake-server contract tests cover responses, quota reserve, API-account refusal, tool policy, follow-up ownership, scope escape, approvals and persistent call limits. The real Codex socket initialization succeeded without model inference. A live review remains unverified; no paid request or subscription quota was spent on testing.


## Orientation actuelle — service Reprise unifié

Le [contrat multi-harness](../../contracts/HARNESS-SUPERVISION-ARCHITECTURE.md) prévoit un même point d’entrée logique pour demander une expertise, recevoir une tâche et rendre/récupérer un résultat. Codex conserve ses transports natifs et garanties existantes ; le regroupement MCP reste à implémenter. Priorité fonctionnelle immédiate : permettre à un harness demandeur d’obtenir une réponse Claude Desktop, y compris compte gratuit.

## Mode `implementation` : autorisation d'écriture du projet (#104)

`reprise_delegate`/`reprise_followup` avec `mode: "implementation"` ne sont acceptés que si l'opérateur a accordé les écritures du workspace projet **à l'installation de l'intégration**. Sans cette autorisation, `reprise_backends` rapporte pour `codex` : `workspace_write_authorized: false`, `writes: false`, `modes: ["expertise"]`, ainsi que `implementation_unavailable_reason: "workspace_write_not_authorized"` et `enable_with` (la marche à suivre).

Le refus a lieu **avant toute admission** (aucune ligne `delegation_requests`, aucun tour Codex) et renvoie une erreur explicite, `data.kind: "workspace_write_not_authorized"`, au lieu du refus générique « Request refused or unavailable » (les autres refus restent génériques, cf. #53). Contournement sans autorisation : `mode: "expertise"` renvoie un patch en lecture seule que le coordinateur applique lui-même.

Activation (l'autorisation est une décision de l'opérateur, jamais déduite d'une demande) :

```
harness_integration integration-plan --home … --profile … --project /absolute/project --action install --delegate --codex-workspace-write
harness_integration integration-apply
```

`--codex-workspace-write` exige `--delegate`. Le périmètre inscriptible est `implementation_scope` = le `project_root` du pont ; les approbations natives Codex restent requises. L'autorisation est lue au démarrage du pont : **redémarrer le pont** après `integration-apply`, puis vérifier `reprise_backends` (`workspace_write_authorized: true`, `implementation_scope` renseigné).

## Pont MCP ChatGPT desktop (issue #156)

Nouveau serveur MCP local `chatgpt_mcp.py` (CLI `--project-root`, `--state`, `--expert-state`) qui
expose à ChatGPT desktop un canal d'expertise Reprise en boucle fermée, sur le transport partagé
`mcp_transport.py` (extrait de `codex_expert_mcp.py`, consommé aussi par `claude_mcp.py` sans
changement observable).

**Outils exposés :**
- INBOX (`get_task`, `submit_result`, `get_result`) : la même boucle SQLite que Claude Desktop
  (`TaskStore`, états absent|completed|attempted|reserved, résultat immuable = preuve de complétion).
- OUTBOUND (`reprise_backends`, `reprise_requests`, `reprise_quotas`, `reprise_delegate`,
  `reprise_followup`, `codex_review`, `codex_followup`) : mêmes schémas que la facade Claude,
  filtrés pour retirer le backend `chatgpt` (loopback refusé : `'ChatGPT loopback is disabled'`).

**Politique v1 :**
- backend `claude-desktop` reste sélectionnable en OUTBOUND depuis la facade ChatGPT ;
- `claim_next`/`await_task` absents ; `register_session` non déclaré (hors INBOX_TOOLS) ;
- aucun nouveau socket ChatGPT : le dispatch passe par `chatgpt_quick.review` (Endpoint + ledger),
  et le retour par `get_task`/`submit_result` sur le TaskStore ;
- pas de repli silencieux de VERSION : le fichier `VERSION` packagé est requis.

**Projection dans le bridge (`reprise_delegate` backend `chatgpt`) :**
- `wait_status`/`wait_for_result`/`_task_snapshot` acceptent `backend='chatgpt'` et lisent
  l'état `chatgpt-state` ; `wait_result` du bridge route selon le backend de la requête
  enregistrée (`requests.lookup`), défaut `claude-desktop` ;
- tagging `admission_only: true` étendu à `chatgpt` (réponse `None` = admission seule, le
  round-trip MCP complète la requête) ;
- `reprise_requests`/replay projettent la source `chatgpt_tasks` (receipt avec `result_sha256`,
  pagination, `delegation_receipt`) ; l'acquittement passe par `Endpoint.mark_acknowledged`
  (one-way attempted→acknowledged, idempotent, seule écriture de delivery venant de l'Endpoint) ;
- journal `routing_events` : `request_key` préservé (argument explicite, sinon `active_key`).

**Sûreté d'admission (crash-safe) :** validation complète des arguments (12 000 caractères,
`request_key` ≤ 128) AVANT toute écriture ; `admit()` du TaskStore précède l'INSERT du ledger —
un crash entre les deux écritures est récupéré par projection au replay, sans ré-exécution.

**Budgets transport :** la facade ChatGPT utilise `CLAUDE_MAX_LINE_BYTES` (8 MiB), compatible
avec la limite `submit_result` de 2 000 000 caractères (test UTF-8 multioctet inclus).
