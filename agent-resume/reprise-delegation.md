---
name: reprise-delegation
description: Ask another AI for conceptual expertise, independent code review, or bounded batch generation through the Reprise MCP tools.
---

Use `mcp__reprise_codex__reprise_backends` to identify the configured project and engines. The bridge is bound to that project: never send work for a different repository without reconfiguring it.

Use `mcp__reprise_codex__reprise_delegate` with a compact, self-contained question, acceptance criteria, project-relative paths and `kind`:
- `expertise`: algorithm, invariants and implementation contract; standard Sol/medium, deep Astra/high.
- `review`: actionable evidence-backed findings; standard Sol/medium, deep Astra/high.
- `batch`: one bounded coherent batch of code, tests or patches; standard Luna/medium, deep Astra/high.

Choose deep only for a concrete conceptual difficulty, sensitive review or documented failed attempt. Batches are generation tasks: the expert returns text or patches; Harness applies them and verifies with deterministic tools. Split a large job into bounded lots. A patch is not proof it applies or passes tests.

Reuse `mcp__reprise_codex__reprise_followup` with the returned thread ID and explicit `backend` (`codex` or `claude-code`); retain the same model and history. Do not resend full chat history. Keep stable contracts first and changing data last. Do not create parallel cascades by default.

Respect `quota_blocked`, `budget_blocked`, `busy`, `timed_out`, `blocked_permission` and `needs_review`. No repeated polling, automatic retry, credential copying or paid API fallback. A timed-out call may already have consumed quota. Truncated output is incomplete. One request at a time and eight calls per project/endpoint/UTC day; failed admitted requests count too. Those eight calls are a local admission counter in the bridge (`expert_calls` in `expert.sqlite`), not a ChatGPT plan limit: `budget_blocked` means that counter, `quota_blocked` means the provider refused (5% reserve on `account/rateLimits/read`).

Codex remains the default. Explicit `backend: "claude-code"` is available only when configured and included-quota billing has been confirmed by the operator: standard Sonnet/medium, deep Opus/high. Claude has no tools; only the question and explicit bounded project file snapshots are supplied. Follow-ups retain its concrete model. Respect `billing_unverified` and `blocked_auth`; do not enable paid usage credits or authenticate on behalf of the user. Remaining Claude quota is unknown. AutoClaw is not yet a validated delegation backend; neither prices nor quota pools can be inferred from a harness name. Offer another configured engine only after inspecting its capabilities and billing. Never claim a fallback occurred when no alternate adapter is configured.

Explicit `backend: "claude-desktop"` targets a running Claude Desktop session through its Quick Entry submission contract. It is a structured Quick Apply payload: a bounded prompt, optional images and file attachments (each under 30 MB), and an optional target `chatId`. The bridge validates and routes the payload to a local Quick Entry supervision endpoint, which dispatches it as a full turn (`dispatchOnQuickEntrySubmit`), not a composer prefill. The target chat is a UUID; omitting it lets Desktop choose the active conversation. This channel does not read the model's reply back into Harness, so treat its result as "turn submitted", not an answered question. It never opens or automates the Desktop UI: actually submitting through the native overlay needs separately operator-granted macOS permissions and a supervised driver. No automatic retry or fallback to another backend. Daily admission caps apply like every other backend.


## Planned common service — not implemented

The [multi-harness contract](HARNESS-SUPERVISION-ARCHITECTURE.md) targets a single logical Reprise service exposing task receipt, result submission/retrieval and outward delegation to every authorized harness. Existing expertise and task-return probes are still separate. Current priority is a recoverable Claude Desktop answer, including on a free account, without paid fallback. Child requests must retain lineage and bounded depth/concurrency/budget; never hold the result channel or a database transaction while awaiting inference. Existing restricted CLI expertise remains restricted.
