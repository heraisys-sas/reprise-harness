# Morph in DeepSeek Harness — assessment, 17 October 2026

This optional overlay uses the existing `llm-pi-ai` adapter; it does not require the Morph SDK. It has not been installed or authenticated. Merge its provider entry into the existing Desktop patch, preserve other providers, store `MORPH_API_KEY` in Harness credentials, then select the model through Harness. The output cap is a local initial choice, not a claim about the models' maximum. Reasoning for DeepSeek is explicitly declared; GLM reasoning controls still need wire-level testing. Verify ordinary replies, streaming, tool calls, reasoning replay and cache usage before switching a long session.

The October 17 DeepSeek snapshot is documented as `morph-dsv41flash-1017`; the rolling alias is `morph-dsv41flash`. Morph serves models from DeepSeek and Z.ai, rather than authoring these model families. GLM-5.3 is a candidate expert, not an established winner on this project's tasks. Kimi K3 is also in Morph's catalog.

## Financial evidence: conflicting public rates

USD per million tokens, input / cached input / output:

| Model | Morph public pricing page | Direct HTTP fetch of Morph model JSON during this investigation |
| --- | --- | --- |
| DeepSeek V4.1 Flash | 0.12 / 0.008 / 0.80 | 0.09 / 0.008 / 0.55 |
| GLM-5.3 Flash | 0.20 / 0.04 / 0.70 | 0.082 / 0.029 / 0.65 |
| GLM-5.3 744B | 1.19 / 0.20 / 6.00 | 0.08 / 0.05 / 6.00 |

The web-search fetch of that same JSON returned yet another set of input rates. These are conflicting observations, not verified account billing or a guaranteed discount. Use the account dashboard and a small metered test to establish actual rates before buying credits or claiming savings.

DeepSeek's current direct Flash API resolves to V4.1-Flash-1017 and lists 0.15 / 0.003 / 0.60 off-peak; 0.30 / 0.006 / 1.20 peak. At the conservative Morph page prices, 100k input tokens with 95% cache reuse plus 1k output costs $0.002160 on Morph, versus $0.001635 direct off-peak and $0.003270 direct peak. This is an illustrative token calculation, not a measured job: reasoning/output volume, cache hit rate, corrections and latency decide actual task cost. Reasoning tokens are billed output.

## Useful capabilities

- Automatic prefix caching, usage-reported cached tokens and per-conversation `x-session-id` worker affinity are relevant to Reprise. Keep instructions/tool order stable and pin the model for each task or long conversation. Do not share one header ID globally. The installed DSH adapter forwards a session ID to pi-ai, but deliberately withholds pi-ai's session-affinity compatibility knobs from profile configuration. A tiny adapter extension or plugin is needed for the documented header; a static global header is incorrect. Measure actual cache hits instead of assuming affinity works.
- TTL defaults to LRU without fixed expiry; requested TTLs are rolling out per model and are not a durability guarantee. No keepalive is needed just to use automatic caching.
- Fast Apply can offload merging compact edits. Prefer deterministic exact replacement/unified patches for simple edits; use model merging for ambiguous large edits, inspect the diff and verify. SDK supports `autoWrite: false`; official Morph MCP disables edit_file by default.
- WarpGrep can find relevant context for unfamiliar repositories while keeping exploration out of the main model context. Use local ripgrep for known symbols/simple searches. The official MCP also enables reflex-account read-back tools; limit exposed tools to the ones needed.
- Standby is advertised at half price for selected models and may return 429 under load. Current docs disagree about eligible models and some prices. Verify charged tier/model first; reserve it for background work, do not silently upgrade to default-tier billing.
- Router costs $0.005 per classification, before inference. 2,000 decisions would consume $10. Route once at a task boundary only when its expected gain exceeds that surcharge; do not route each cheap turn. System One typed decisions may deserve a separate evaluation, not unconditional installation.
- Compact rewrites the prefix and therefore forfeits its existing cache. Use it when context pressure or expected future savings outweigh re-prefill. Keep native Git and deterministic parsers rather than adding SDK wrappers for them.

Sources: [SDK](https://docs.morphllm.com/sdk/reference), [models](https://docs.morphllm.com/sdk/components/fast-models), [pricing](https://www.morphllm.com/pricing), [live catalog](https://www.morphllm.com/api/models/json), [cache](https://docs.morphllm.com/sdk/components/caching), [standby](https://docs.morphllm.com/sdk/components/standby), [router](https://docs.morphllm.com/sdk/components/router), [MCP](https://docs.morphllm.com/mcpquickstart), [DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing/).
