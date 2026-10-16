# Routing development cases

Eight small, public development cases; no external API or model is required by the validators. These are not a held-out benchmark and passing them does not establish production quality or paid savings. Copy one case into a private temporary work directory before giving its `task.md` and starter to an agent. Keep the original checker outside the agent's writable task directory. Do not include real sessions or credentials.

Run from the repository root:

```sh
python3 harness-tools/routing-eval/cases/check.py --case py-window --work /absolute/task-copy
```

The work directory contains the files *inside* the selected case directory. Only the named implementation file may change; compare its diff separately. Exit 0 means all assertions passed, 1 means failed and 2 means unavailable/invalid invocation. Missing Node or Rust is unavailable, never a pass. Each subprocess has a 20-second deadline. These checks execute candidate code with the current process's permissions; use synthetic fixtures and ordinary tool permissions. This checker is not a sandbox.

| Case | Language | Capability | Validator |
|---|---|---|---|
| py-window | FR/Python | bounded streaming, edge cases | exhaustive small input/reference comparison |
| py-usage | EN/Python | unknown usage and cache accounting | missing/null/zero/malformed counters |
| js-retry | FR/JS | retry classification | status/attempt decision table |
| js-prefix | EN/JS | stable exact prefix | UTF-8 byte equality and source immutability |
| rust-usage | EN/Rust | Python → Rust migration | rustc build and corpus parity |
| devops-command | FR/Python | explicit argv and safe configuration | exact argv, validation, adversarial strings |
| compact-resume | FR/Python | restart from compacted context | process restarted on every input, authoritative instruction enforced |
| project-isolation | EN/Python | local policy and provenance | similar names, outside paths, symlinks, unknown provenance, namespace separation |

`compact-resume` is a deterministic handoff simulation: the old conversational context is absent, and each check runs a fresh process. It does **not** exercise a provider's real compaction. A live run must additionally capture the actual pre/post-compaction transcript boundary, permission checks and complete tool evidence. `project-isolation` tests an adapter against the existing scoped API; it does not authenticate MCP clients or prove resistance to a malicious agent that changes the adapter itself.

For each model/route, start from identical copies, use the same tools and pinned instructions, record every attempt/correction plus elapsed time and usage using the existing observation schema. Validate the final artifact with this unchanged checker. Report unavailable separately and count failed/correction calls in task totals. Compare against a single-model baseline; no automatic daily routing follows from these results. Use a separately authored unseen set before selecting a production route.
