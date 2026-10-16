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

Start the explicitly separate local server in a terminal (private socket directory required):

```sh
./resume expert-server --socket /absolute/private/path/codex-expert.sock
./resume server-check --socket /absolute/private/path/codex-expert.sock
python3 codex_expert_mcp.py --project-root /absolute/project --socket /absolute/private/path/codex-expert.sock
```

The last command is the MCP stdio entrypoint for DSH, not an interactive shell. Configure a DSH `@deepseek-ai/dsh-mcp-client` row with `transport: stdio`, `serverName: reprise_codex`, a Python 3.11+ executable, the script and those arguments; set `toolCallTimeoutMs: 330000` and disable reconnection retries. Use a project-specific overlay rather than installing a project-bound tool globally. A local `reprise-expert.local.patch.yml` is generated for this workspace, excluded from Git; it has not been merged into the Desktop profile.

Calls return the bounded response, Codex thread/turn IDs, event types and explicit errors. `codex_followup` accepts only review threads recorded by this bridge for its fixed project and endpoint, and reuses their Codex history. No full DSH conversation is copied. Approvals or user questions stop the tool and are reported to DSH; the bridge does not grant them, impersonate a user reply or claim an unfinished review succeeded.

The bridge enforces a five-percent quota reserve, one active expertise request and a persistent eight-call daily cap per project/endpoint, including failures. This is an admission policy, not a maximum token guarantee: one accepted agent turn can contain many model/tool steps. Read-only sandbox and no command network access are requested on every turn. Inherited MCP servers, apps, plugins, hooks, browser/computer tools and subagents are disabled for these expertise threads through thread-specific overrides; the rest of Codex is unchanged. The fixed cwd and path validation constrain requests, not a hard OS confidentiality boundary for all local reads.

Validation: protocol discovery with the actual bundled DSH MCP client succeeded (`codex_review`, `codex_followup`); fake-server contract tests cover responses, quota reserve, API-account refusal, tool policy, follow-up ownership, scope escape, approvals and persistent call limits. The real Codex socket initialization succeeded without model inference. A live review remains unverified; no paid request or subscription quota was spent on testing.
