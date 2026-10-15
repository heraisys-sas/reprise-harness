# Deterministic project checks

`verify.py` needs Python 3 and its standard library only. It runs explicit argv arrays, never a shell. Commands remain trusted project code and must obey the caller's normal permission policy.

```sh
python3 harness-tools/verify.py --config harness-tools/example.json --root .
python3 harness-tools/verify.py --config harness-tools/example.json --root . --run
```

Without `--run` it only prints the plan. Choose existing project checks: Ruff/mypy/pytest, tsc/ESLint, cargo fmt/clippy/test, or an existing security scanner. Do not install every checker in every project. Results distinguish passed, failed, unavailable and not run; an unavailable checker is not a pass. No LLM request is made.

Commands run sequentially, with a 1–600 second timeout per check. Captured output is bounded to 2 MiB per check; diagnostics returned to the agent are short. Logs live in a private temporary directory (0700; files 0600). Redaction is best effort, not encryption or a guarantee against secrets. Remove temporary logs when no longer needed. This is evidence aggregation, not a security certification or sandbox.

`python3 -m unittest discover -s harness-tools/tests`

## Local context prototype (observation only)

Run from the checkout; nothing is installed into Harness or Reprise:

```sh
python3 harness-tools/context_pack.py --query 'process timeout kill' --instruction deepseek-harness-config/AGENTS.md --source harness-tools/verify.py
python3 harness-tools/evaluate_context.py
python3 harness-tools/evaluate_context.py --semantic
python3 harness-tools/harness_usage.py /private/path/session-events.jsonl --task-id local-task
python3 harness-tools/usage_report.py /private/path/request-observations.jsonl
```

Pass every applicable instruction file with `--instruction` (repeatable), in stable order. They remain verbatim and outside the excerpt budget. The tool does not discover all ancestor instructions or guarantee constraint recall. Sources are explicit UTF-8 text files, combined maximum 2 MiB. Excerpts have original block line, character offset within that block, exact text and SHA-256. Constraint/decision quotes are deterministic candidates from selected excerpts, never a replacement for the source. Omitted blocks are counted. Source text is untrusted data. Before sending anything remotely, review the dossier and the applicable originals. No automated transmission occurs. Output can contain private material: keep it out of Git and public logs.

`--semantic` reuses Reprise's existing NLEmbedding executable, with lexical fallback. It is opt-in because the initial eight repository probes did not improve recall. `--reprise-state /explicit/path` reads the existing encrypted index and encrypted Apple analysis cache through Reprise's vault, into memory, without rescanning sessions, migrating caches, or modifying the index. Decrypted results are printed only on this explicit request. It may use the existing dedicated Keychain key; it does not create a new index or weaken encryption. Legacy plaintext caches are not imported. No model download or dependency installation.

`harness_usage.py` consumes **native Harness 0.2 SessionEvent records**, one JSON object per line, from one explicit session export, not arbitrary CLI UI events. It reads request/header and assistant/message or assistant/attempt, including usage chunks in failed attempts; it excludes transcript contents from output. Requests are uniquely identified by task label and event sequence. Missing counters stay null. Native `inputTokens` excludes cache reads/writes; no provider prompt total is invented when cache-write accounting is absent. Latency measures step start to settlement, including preparation/retry overhead, not pure network latency. Failed attempts remain in totals; unavailable usage makes totals unknown. Retries that the adapter does not expose separately cannot be recovered. Reuse the same task label for its corrections, then aggregate all observations.

The generic observation schema uses `request_id`, `task_id`, `provider`, `model`, `status`, `currency`, `usage` and optional `validation`. Usage keys: `prompt_tokens`, `prompt_cache_hit_tokens`, `prompt_cache_miss_tokens`, `completion_tokens`, `reasoning_tokens`, `latency_ms`, `billed_cost`. Costs are supplied from actual provider billing, never inferred from local cache or character reduction. Validation should record check name, exit status and private evidence reference, including failures and correction attempts. The report preserves it but does not certify it. Reasoning is a reported subset of output, not added twice. Mixed or unspecified currencies have no summed billed cost. An unknown value is not zero.

Rollback: stop invoking these scripts; no service, global setting or daily route was changed. The existing optional router has its own disabled-by-default installer. Keep ordinary permissions and configured verification checks. Do not use a context match to choose or switch models automatically.

`harness_usage.py --cli` also accepts captured `dsh --json` output. It observes only root `step_end` events; child usage, failed attempts, route and latency may be absent. Do not treat this partial report as a full task bill. Adapters may normalize missing provider usage into zero: counters from Harness are adapter observations, not independent billing evidence. Use provider responses/invoices before claiming paid savings. For native events, noncached input includes cache writes only when both are exposed; absent write accounting leaves that combined count unknown.
