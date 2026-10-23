---
name: reprise-delegation
description: Use configured Reprise MCP tools for bounded expertise, independent review and batch generation, with explicit permissions, durable receipts and useful clarification.
---

## Host-agent operating instructions

Read [REPRISE-AGENTS.template.md](REPRISE-AGENTS.template.md) only when the operator explicitly selects `agent_role: coordinator` for the host model (for example to evaluate ASI1 in that role), or when preparing that optional profile. Its presence does not activate it for other agents. Apply this profile within existing operator permissions. The template is distributed beside this skill; adapt it to the harness instruction mechanism without replacing personal instructions. Systematic delegation is conditional on the coordinator/operator policy, not required of every capable model. Free fallback routes must be discovered and authorized, never assumed.

## Discover the installed integration

Managed installations expose this skill through a provider scoped to the selected Harness profile. Legacy installations may have a global copy; do not infer an active integration from that copy. Use the actual tools exposed by that profile. The names below describe the `reprise_codex` server convention, not a guarantee that every profile has loaded it. Configuration written, integration loaded, tools discovered and an exchange confirmed are distinct levels of evidence.

Start with `mcp__reprise_codex__reprise_backends`. Read its returned `project_root` and configured capabilities before sending work. The bridge is bound to that project: do not infer the project from the skill alone or send another repository without explicitly reconfiguring the integration. Availability does not establish authentication, billing or remaining quota. Do not invent a backend, model identifier or tool absent from discovery. Model routing is an implementation choice; use the capabilities actually reported rather than treating example model names as a permanent policy.

## Bound the investment

Use `mcp__reprise_codex__reprise_delegate` only when it is exposed by the selected integration and the task benefits from another expert. Choose `kind` from `expertise` (reasoning, invariants or implementation contract), `review` (actionable findings with evidence), or `batch` (one coherent bounded generation task). Start with `level: "standard"` when sufficient; `deep` is for a concrete reasoning difficulty, sensitive review or documented unsuccessful approach. A missing fact is not, by itself, a reason to spend a more capable model's inference budget.

Give the objective, acceptance criteria, established facts, remaining uncertainties, project-relative paths and the smallest useful context. State explicit permissions and constraints, including whether changes or external calls are authorized. Do not forward secrets, full chat history, credentials or permissions from another harness. Break a large job into bounded lots; do not start parallel delegation cascades. Returned text or patches still require the requester to apply and verify them with deterministic tools. An expert's claim is not proof that a patch applies or tests pass.

## Clarify before repeating; escalate reasoning separately

These rules apply to every model, including inexpensive models. Ask the requester a targeted question when a determining fact is missing, interpretations conflict, or an attempt repeats without progress. State what is known, the blocking uncertainty, attempts and errors already observed, and the minimum answer needed. Useful open questions, challenges to the framing and alternatives are welcome. Group independent questions and continue independent work where possible.

The requester first checks its available information or performs a bounded deterministic check, then asks the user when a fact, preference or permission remains missing. Never invent an answer or treat elapsed time as permission. Clarification supplies missing facts; escalation changes author or reasoning capacity when the reasoning is outside the current model's reach. After two failures on one aspect, change approach or author instead of repeating the same attempt or the same question indefinitely. Do not let a lightweight model loop on an unsuitable task.

There is currently no automatic durable clarification protocol. In manual mode, return a clearly identified blocking question and the checkpoint needed to continue; the requester answers or asks the user. Where a supported session follow-up exists, continue with that answer and only the relevant changes. A question in returned text does not create a service-level waiting state. The target protocol in [CLARIFICATION-CONTRACT.md](CLARIFICATION-CONTRACT.md), including durable intermediate questions, deduplicated answers and suspension/resumption, is **not implemented**. Do not invent question/answer tools or claim these guarantees from this instruction alone. Session reuse may retain context; never promise a shared cache or savings inferred from latency.

## Recover receipts before continuing

Persist a unique `request_key` locally before every inference call, including follow-ups and aliases; retain it after a lost reply. Supply `parent_request_key` for child delegations. Use `reprise_requests` to list recent project requests or retrieve one by key, and acknowledge a received receipt explicitly. Replaying a key does not start a new attempt. After a timeout, lost response or uncertain parent, inspect the durable receipt before any resend; do not choose a new key to bypass an uncertain-attempt barrier. See [DELEGATION-PERSISTENCE.md](DELEGATION-PERSISTENCE.md).


### Bounded receipt retrieval (receipt-json-v1)

Discover these optional parameters in the installed MCP schema before use.
Historical calls default to `full`, including the recent-request list; their
response format and explicit acknowledgement remain supported.

Persist `request_key` before calling any inference tool with `return_mode="receipt"`
(`reprise_delegate`, `reprise_followup`, `codex_review`, `codex_followup`).
Prefer compact receipts for delegation and continuations, then read only useful pages.
The complete result is persisted before the compact manifest is returned,
including on replay. `return_mode` defaults to `full` and is removed before
backend prevalidation, payload construction, identity and signature. Switching
modes never starts a second inference. Existing admission and uncertainty
barriers remain in force.

1. Read `reprise_requests(request_key=key, view="receipt")`. The manifest contains
   `status, request_key, state, attempt_id, acknowledged, available, format,
   total_length, result_sha256`, without result content.
2. If available, retain the fingerprint and request `view="page", offset=0,
   max_chars=6000, result_sha256=fingerprint`. Each page adds
   `offset, text, returned_length, has_more, next_offset`. Follow `next_offset`
   with the same fingerprint until `has_more=false`; the terminal offset is null.
3. Concatenate fragments in order, verify total length and SHA-256, then decode
   JSON. Explicitly acknowledge through `view="receipt", acknowledge=true`
   afterwards. Only `state="recorded"` can be acknowledged; pages cannot
   acknowledge, and reads never acknowledge implicitly.

A result with `status` `blocked_owner` or `blocked_session` is `recorded`: no turn was
submitted, so an equivalent delegation may be requested again under a new `request_key`.
`timed_out`, `needs_review`, `blocked_permission` and any exception leave the attempt
`indeterminate`; reconcile it without redispatching.

`format="receipt-json-v1"` describes JSON of the entire persisted `result`
object: sorted keys, compact separators, `ensure_ascii=False`, no Unicode
normalization. Lengths and offsets count Unicode code points. SHA-256 hashes
the canonical UTF-8 bytes; supplied hexadecimal fingerprints are case-insensitive.
The consultation status does not replace the expertise status inside `result`.

Both compact views require `request_key`. Only `page` accepts `offset`
(nonnegative integer, default 0), `max_chars` (1..6000, default 6000) and
`result_sha256` (64 hex digits). Offset > 0 requires the fingerprint.
JSON integers such as 1.0 are accepted and normalized; booleans, fractional
values and unknown arguments are rejected.

Absent result: `available=false`, null length/fingerprint, no page fields.
Unknown key: `not_found`. Fingerprint mismatch: `result_changed`, no fragment.
Offset beyond the total: `invalid_offset`, no fragment. Offset equal to the
total: empty terminal page. Compact blocked delegations retain their blocking
status and point to an existing uncertain request when applicable.

Compact responses are limited to 7000 serialized code points, including
JSON-RPC envelope, MCP text-block escaping and metadata. Oversized fragments
are deterministically halved, reserialized and checked; returned lengths and
offsets describe the actual fragment. JSON-RPC ids must serialize to at most
256 code points for compact calls; larger ids are rejected before dispatch
(stdio error response uses a null id). Sizing reserves the maximum id and
acknowledgement overhead so neither changes page boundaries.

Fragments may split a grapheme or JSON token: decode only after assembly.
Cumulative pages consume context and remain subject to later compaction.
The historical list and other tools are outside the transport guarantee.
Retrieval cannot recover content already truncated by backend collection.
No transaction spans pages or inference; no SQLite migration, dependency,
backend fallback or automatic retry is introduced.

Use `mcp__reprise_codex__reprise_followup` only for an owned returned session, with its thread ID and explicit `backend: "codex"` or `backend: "claude-code"`. It retains the recorded model and effort. For the same subject or project, prefer continuing that session over opening a new one while the required competence level is unchanged: the expert keeps its prior exploration of the project and the stable instructions, which reliably saves turns, cost and latency; remote prefix-cache reuse itself is not guaranteed. Open a new session when the competence level changes, the subject diverges or the remote history has become unwieldy. Send the answer or changed facts rather than full history. Legacy bridges may also expose the Codex-specific `codex_followup` alias; use it only if actually discovered. Follow-ups are not a bidirectional Claude Desktop channel.

Respect `quota_blocked`, `budget_blocked`, `busy`, `timed_out`, `blocked_permission`, `needs_review`, `billing_unverified` and `blocked_auth`. No repeated polling, automatic retry, credential copying, authentication on the user's behalf or paid API fallback. A timeout may already have consumed quota; truncated output is incomplete. One request at a time. No backend has a local daily call cap: legacy `--daily-limit` and `expert_calls`/`quick_calls` counters are inert. Provider checks, including the current Codex 5% reserve, remain in force. Explicit user budgets remain separate from provider quotas.

## Backend and quota limits

Codex is the default backend. Explicit `claude-code` is usable only when configured and the operator has confirmed subscription-only billing with paid usage credits disabled. It has no tools: only the question and explicit bounded project file snapshots are supplied. Its remaining quota is unknown. AutoClaw is not a validated delegation backend. Offer another engine only after inspecting configured capabilities and billing; never claim a fallback occurred when no alternate adapter ran.

Explicit `backend: "claude-desktop"` uses the structured Quick Apply submission contract: bounded prompt, optional images/files (each under 30 MB) and optional UUID `chatId`; omission lets Desktop choose the active conversation. It dispatches a full turn (`dispatchOnQuickEntrySubmit`). The bridge does not read the model reply. Treat `admission_only: true`, `response: null` as an admission receipt, never an answered question. Native overlay submission requires separately granted macOS permissions and a supervised driver; the bridge does not open or automate the UI. Persist `request_key` before submission; do not retry or fall back automatically.

`reprise_quotas` is read-only quota knowledge, with an explicit engine, cached reads by default and optional metadata refresh. Source, opaque account/pool identity, freshness and unknown states are observations, not routing or admission decisions. Unknown is not zero. Claude and Harness quota sources remain unimplemented. See [QUOTA-KNOWLEDGE.md](QUOTA-KNOWLEDGE.md).

## Target service — not implemented

[HARNESS-SUPERVISION-ARCHITECTURE.md](HARNESS-SUPERVISION-ARCHITECTURE.md) targets one logical service for task receipt, result submission/retrieval and outward delegation. Existing expertise and task-return probes remain separate. The Claude Desktop MCP probe requires the client to invoke `get_task` and `submit_result`; only an accepted final `submit_result` is its result. Quick Apply admission does not prove this return path. Child requests retain lineage and bounded depth/concurrency/budget; no result channel or database transaction should remain held while awaiting inference. Restricted CLI expertise remains restricted. Preserve existing uncertainty barriers; do not present the target protocol as a delivered capability.

### Deliverable and timeout discipline

Give the expert the exact baseline, scope, acceptance checks and enough reads to resolve uncertainty. Request a Git-generated diff checked against a clean baseline, and require an explicit statement of checks actually run. After two failed applications, return diagnostics to the same expert conversation or change implementation method; do not repeatedly regenerate hunk headers. A timed-out turn needs native-state reconciliation, not a direct database state rewrite. Preserve request identity and uncertainty until that reconciliation is evidenced.


### Deterministic local collection

For a complete patch or long answer, avoid manually copying model-visible pages.
The installed helper `delegation_collect.py` reads the configured local state in
read-only mode, checks every offset, total length and SHA-256, then creates a
new private file without inference or acknowledgement:

```sh
python3 /path/to/helpers/delegation_collect.py --state /path/to/state \
  --project-root /path/to/project --request-key saved-key \
  --field response --output /path/to/new-answer.txt
```

Use the runtime Python and paths of the selected integration. `--field result`
(the default) exports canonical JSON. A key does not grant access to another
project scope. Existing output files are never overwritten. Incomplete backend
captures are refused; pagination cannot reconstruct missing text. Native Harness
file-reading tools can still shorten their own display: collect or inspect bounded
ranges instead of treating that display as the full file.
