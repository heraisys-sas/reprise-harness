---
name: reprise-delegation
description: Use configured Reprise MCP tools for bounded expertise, independent review and batch generation, with explicit permissions, durable receipts and useful clarification.
---

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

Use `mcp__reprise_codex__reprise_followup` only for an owned returned session, with its thread ID and explicit `backend: "codex"` or `backend: "claude-code"`. It retains the recorded model and effort. Send the answer or changed facts rather than full history. Legacy bridges may also expose the Codex-specific `codex_followup` alias; use it only if actually discovered. Follow-ups are not a bidirectional Claude Desktop channel.

Respect `quota_blocked`, `budget_blocked`, `busy`, `timed_out`, `blocked_permission`, `needs_review`, `billing_unverified` and `blocked_auth`. No repeated polling, automatic retry, credential copying, authentication on the user's behalf or paid API fallback. A timeout may already have consumed quota; truncated output is incomplete. One request at a time. No backend has a local daily call cap: legacy `--daily-limit` and `expert_calls`/`quick_calls` counters are inert. Provider checks, including the current Codex 5% reserve, remain in force. Explicit user budgets remain separate from provider quotas.

## Backend and quota limits

Codex is the default backend. Explicit `claude-code` is usable only when configured and the operator has confirmed subscription-only billing with paid usage credits disabled. It has no tools: only the question and explicit bounded project file snapshots are supplied. Its remaining quota is unknown. AutoClaw is not a validated delegation backend. Offer another engine only after inspecting configured capabilities and billing; never claim a fallback occurred when no alternate adapter ran.

Explicit `backend: "claude-desktop"` uses the structured Quick Apply submission contract: bounded prompt, optional images/files (each under 30 MB) and optional UUID `chatId`; omission lets Desktop choose the active conversation. It dispatches a full turn (`dispatchOnQuickEntrySubmit`). The bridge does not read the model reply. Treat `admission_only: true`, `response: null` as an admission receipt, never an answered question. Native overlay submission requires separately granted macOS permissions and a supervised driver; the bridge does not open or automate the UI. Persist `request_key` before submission; do not retry or fall back automatically.

`reprise_quotas` is read-only quota knowledge, with an explicit engine, cached reads by default and optional metadata refresh. Source, opaque account/pool identity, freshness and unknown states are observations, not routing or admission decisions. Unknown is not zero. Claude and Harness quota sources remain unimplemented. See [QUOTA-KNOWLEDGE.md](QUOTA-KNOWLEDGE.md).

## Target service — not implemented

[HARNESS-SUPERVISION-ARCHITECTURE.md](HARNESS-SUPERVISION-ARCHITECTURE.md) targets one logical service for task receipt, result submission/retrieval and outward delegation. Existing expertise and task-return probes remain separate. The Claude Desktop MCP probe requires the client to invoke `get_task` and `submit_result`; only an accepted final `submit_result` is its result. Quick Apply admission does not prove this return path. Child requests retain lineage and bounded depth/concurrency/budget; no result channel or database transaction should remain held while awaiting inference. Restricted CLI expertise remains restricted. Preserve existing uncertainty barriers; do not present the target protocol as a delivered capability.
