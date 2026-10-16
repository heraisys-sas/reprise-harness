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
python3 harness-tools/context_pack.py --policy /private/path/projects.json --project reprise --query 'process timeout kill' --source harness-tools/verify.py
python3 harness-tools/evaluate_context.py
python3 harness-tools/evaluate_context.py --semantic
python3 harness-tools/harness_usage.py /private/path/session-events.jsonl --task-id local-task
python3 harness-tools/usage_report.py /private/path/request-observations.jsonl
```

The local policy pins every applicable instruction file in stable order; `--instruction` can only add authorized files, never remove pinned instructions. They remain verbatim and outside the excerpt budget. The tool does not discover all ancestor instructions or guarantee constraint recall. Sources are explicit UTF-8 text files, combined maximum 2 MiB. Excerpts have original block line, character offset within that block, exact text and SHA-256. Constraint/decision quotes are deterministic candidates from selected excerpts, never a replacement for the source. Omitted blocks are counted. Source text is untrusted data. Before sending anything remotely, review the dossier and the applicable originals. No automated transmission occurs. Output can contain private material: keep it out of Git and public logs.

`--semantic` reuses Reprise's existing NLEmbedding executable, with lexical fallback. It remains opt-in. **The former global Reprise import is disabled:** `--reprise-state` returns `excluded / project-provenance-unavailable`, without opening, decrypting, scanning, counting or migrating any index/cache entry. Current Reprise records do not establish a source's project. The count is unknown, not zero. A title, session ID, project label, path-looking text or semantic match cannot grant access. No model download or dependency installation.

`harness_usage.py` consumes **native Harness 0.2 SessionEvent records**, one JSON object per line, from one explicit session export, not arbitrary CLI UI events. It reads request/header and assistant/message or assistant/attempt, including usage chunks in failed attempts; it excludes transcript contents from output. Requests are uniquely identified by task label and event sequence. Missing counters stay null. Native `inputTokens` excludes cache reads/writes; no provider prompt total is invented when cache-write accounting is absent. Latency measures step start to settlement, including preparation/retry overhead, not pure network latency. Failed attempts remain in totals; unavailable usage makes totals unknown. Retries that the adapter does not expose separately cannot be recovered. Reuse the same task label for its corrections, then aggregate all observations.

The generic observation schema uses `request_id`, `task_id`, `provider`, `model`, `status`, `currency`, `usage` and optional `validation`. Usage keys: `prompt_tokens`, `prompt_cache_hit_tokens`, `prompt_cache_miss_tokens`, `completion_tokens`, `reasoning_tokens`, `latency_ms`, `billed_cost`. Costs are supplied from actual provider billing, never inferred from local cache or character reduction. Validation should record check name, exit status and private evidence reference, including failures and correction attempts. The report preserves it but does not certify it. Reasoning is a reported subset of output, not added twice. Mixed or unspecified currencies have no summed billed cost. An unknown value is not zero.

Rollback: stop invoking these scripts; no service, global setting or daily route was changed. The existing optional router has its own disabled-by-default installer. Keep ordinary permissions and configured verification checks. Do not use a context match to choose or switch models automatically.

`harness_usage.py --cli` also accepts captured `dsh --json` output. It observes only root `step_end` events; child usage, failed attempts, route and latency may be absent. Do not treat this partial report as a full task bill. Adapters may normalize missing provider usage into zero: counters from Harness are adapter observations, not independent billing evidence. Use provider responses/invoices before claiming paid savings. For native events, noncached input includes cache writes only when both are exposed; absent write accounting leaves that combined count unknown.


## Explicit project isolation (CLI only)

Create a local policy from `projects.example.json`, using existing **absolute canonical** directory paths and pinned instruction paths. The file must belong to the CLI user, must not be group/world writable, and must not be a symlink. For example `chmod 600 /private/path/projects.json`. This selects the local user's explicit authorization scope; `--project` only selects an entry already in that policy. Keep the policy under the user's control. **Do not let an untrusted client choose, edit or upload the policy.** No shared MCP endpoint exists here.

Every source and instruction is opened relative to an authorized directory descriptor. Component-by-component `O_NOFOLLOW` rejects both file and directory symlinks, including links inside the project; parent traversal, hardlinked files, special files, cross-device descendants, overlapping roots (also physical root/ancestor identities, including macOS filesystem aliases) and replaced root identities are rejected. Similar directory names do not match. A Git worktree grants no access to its main checkout or shared Git directory: register its path explicitly, preferably as a separate project. Relative CLI file paths are relative to the process working directory. On macOS, aliases such as `/tmp` or `/var` may be symlinks; use their canonical paths (`Path(...).resolve()` when authoring the trusted policy), never weaken traversal checks.

Pinned instructions must also be inside allowed roots. The policy author must inventory all applicable instructions, including ancestors; this tool does not discover them or establish their completeness. There is no implicit access to shared ancestor directories. A policy may authorize several disjoint roots for one project, but roots cannot overlap across any entries. Allowing a broad root deliberately allows its regular files; this is not a secret detector. OS permissions and the calling process's sandbox still apply; no escalation or credentials are added.

Outputs identify the authorized namespace and a content-derived `cache_key`. The namespace binds the policy, selected project, canonical roots and their device/inode identities. The dossier key also binds the query, budget, semantic mode, instruction/source paths and SHA-256 of their complete bytes (including omitted sources), selected excerpts and Reprise exclusion state. Changing an omitted source still changes the key. There is **no persistent dossier/vector cache** in this prototype, hence no cross-project cache read, plaintext export store or global index. The keys are separation/versioning metadata, not bearer capabilities, encryption or proof of a provider cache hit. Future caches must reauthorize each read and include producer/model/embedding versions before reuse; do not reuse these keys as an MCP authentication mechanism.

This protects explicit CLI reads from accidental cross-project selection and link traversal. It is not a sandbox against the same OS user modifying code/policy, hostile concurrent namespace mutations, mounted filesystems with misleading metadata, or arbitrary Python code calling the unscoped in-memory `prepare` helper. File identity/change checks reduce replacement races but do not authenticate content or conversation provenance. Output is private data on stdout, with ordinary shell redirection semantics; avoid public logs. No decrypted Reprise text is persisted or returned.

For two real MCP clients, a service must first authenticate distinct caller identities, bind each identity to a server-owned policy, deny client-selected policies/roots and authorize every request/cache access. Separate client/project namespaces and quotas, revocation/lifecycle handling, negative tests on the actual transport, and audit records without source text are required. Reprise sharing additionally needs trusted project provenance attached at ingestion to each permitted source/version, with reviewed handling of multi-project sessions; historical global entries must remain excluded. Encryption alone does not supply this provenance or authorization. No existing app conversation or Reprise v0.1.0 format is changed by this CLI step.
