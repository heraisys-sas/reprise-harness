# Next backend: Claude Code

Local read-only investigation, 17 October 2026. No inference or authentication change.

Detected Claude Code 2.1.299 in the VS Code extension. `claude auth status --json` reports `loggedIn: false`, `authMethod: none`, `apiProvider: firstParty` in the tested environment. This does not establish the Desktop subscription state. Delegation is not enabled.

Candidate first adapter: `--print --output-format json --safe-mode --restricted --strict-mcp-config --mcp-config '{"mcpServers":{}}' --tools "" --permission-mode dontAsk --permission-prompts none --disable-slash-commands --no-chrome`, with explicit model/effort, trusted cwd, prompt via stdin and bounded process output/deadline. Validate the exact installed CLI contract before enabling it. With no tools, supply bounded context explicitly. File-backed review needs a separately validated Read/Glob/Grep allowlist and effective policy checks.

Do not use `--bare`: its installed help requires API key/helper authentication and skips OAuth/keychain, so it conflicts with subscription-only routing. `--safe-mode` preserves normal authentication while disabling user customizations; managed policies and built-in plugins still apply. Strip API-key/helper/provider overrides and check auth in the exact child environment. Do not start login or copy another app's credentials.

Record adapter-created session IDs and resume only those explicit UUIDs; no ambiguous `--continue` or arbitrary external sessions. Preserve model and effort per session. Structured terminal results, not exit zero, decide success. A timeout may already have consumed quota, so never retry automatically. No remaining-subscription quota command was established; token/cost metadata is not a remaining quota gauge. The documented dollar cap applies to API spending and must not be represented as a subscription cap.

Keep the common Reprise contract: expertise/review/batch, configured project, engine/model/session evidence, serialized admission and persistent call cap, no silent fallback across accounts or billing. Prices and subscription capacities still require current offer verification before any purchase recommendation.
