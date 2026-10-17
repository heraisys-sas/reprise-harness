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

Reuse `mcp__reprise_codex__codex_followup` with the returned thread ID; retain the same model and history. Do not resend full chat history. Keep stable contracts first and changing data last. Do not create parallel cascades by default.

Respect `quota_blocked`, `budget_blocked`, `busy`, `timed_out`, `blocked_permission` and `needs_review`. No repeated polling, automatic retry, credential copying or paid API fallback. A timed-out call may already have consumed quota. Truncated output is incomplete. One request at a time and eight calls per project/endpoint/UTC day; failed admitted requests count too.

Claude is the next planned backend. AutoClaw is not yet a validated delegation backend; neither prices nor quota pools can be inferred from a harness name. Offer another configured engine only after inspecting its capabilities and billing. Never claim a fallback occurred when no alternate adapter is configured.
