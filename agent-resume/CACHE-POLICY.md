# Cache and quota policy — 2026-10-16

Reprise can preserve the history and request structure it controls; it cannot export a provider's KV cache, infer its actual expiry from a local timer, or make two harnesses serialize identical contexts. A cache key does not replace the prefix. Account, endpoint, region, model, instructions and tool definitions may affect reuse. Authentication is independent of caching.

Current implementation: the Codex expertise bridge reuses its own conversation IDs for follow-ups, requests fresh quota information before each call, and refuses new calls at or above 95% usage in any reported quota window. This 5% reserve is a local heuristic, not a provider guarantee; an admitted turn may consume more than the reserve. Unknown quota measurements also block admission. No local daily call cap applies to any delegation backend; legacy counters/options are inert. Persistent request identity and uncertainty barriers are described in [DELEGATION-PERSISTENCE.md](DELEGATION-PERSISTENCE.md). One active expert request per shared state directory prevents parallel oversubscription by this bridge. These controls do not suspend independently running Desktop, CLI or Harness sessions.

No artificial cache-refresh requests are implemented or scheduled. Native retention settings and useful work take priority. No account tokens are copied between applications and no paid API fallback is enabled.

## Verified provider behavior

| Route | Retention | Practical choice |
| --- | --- | --- |
| Anthropic API | Native 5-minute or 1-hour TTL, refreshed on reuse. Writes have a premium; reads remain billed. | Five minutes for continuous work; one hour for useful work with longer gaps. An official zero-output prewarm exists, but is not a free ping and has request-mode restrictions. |
| Claude Code | Separate main/subagent TTL settings, subject to installed version and authentication route. | Prefer native `promptCacheTtl` and `subagentPromptCacheTtl` over an external replay loop. Do not infer subscription usage coefficients from API prices. |
| OpenAI API | Model-specific: GPT-5.6 and later document `prompt_cache_options.ttl: "30m"`; earlier models have different retention settings. | Preserve history and inspect usage/cache metrics. API settings do not prove Codex exposes equivalent controls. |
| Codex subscription via app-server | Codex owns request construction; Reprise has no verified cache-retention override. | Reuse the same expertise thread, avoid changing tools/instructions mid-conversation, and measure reported usage. No cache-residency guarantee. |
| DeepSeek API | Automatic, best effort; no fixed contractual TTL found. | Keep the prefix and provider stable; no timed maintenance loop. |
| MiMo API | Discounted cached input documented; no exact TTL/refresh contract established here. | Observe cache usage and keep API versus Token Plan accounting distinct. |
| Kimi K3 API | Native 5-minute/1-hour retention documented; do not extrapolate to K2 or subscription plans. | Use native options only on the documented model/endpoint. |
| GLM / Z.AI API | Automatic caching; exact TTL/refresh contract not established here. | Do not invent a keepalive interval; distinguish Coding Plan endpoint from prepaid API. |

Sources: [Anthropic API](https://platform.claude.com/docs/en/build-with-claude/prompt-caching), [Claude Code](https://code.claude.com/docs/en/prompt-caching), [OpenAI API](https://developers.openai.com/api/docs/guides/prompt-caching), [DeepSeek](https://api-docs.deepseek.com/guides/kv_cache/), [MiMo](https://mimo.mi.com/docs/en-US/pricing), [Kimi](https://platform.kimi.ai/docs/guide/context-caching), [Z.AI](https://docs.z.ai/guides/capabilities/cache).

## Maintenance economics and safeguards

Compare total maintenance cost against the expected cost of rebuilding the prefix for actual future work. For one future request, maintenance is worthwhile only if its total cost is below the probability of reuse multiplied by the saving from a hit. Include cache reads, possible writes on misses, output/reasoning tokens, request limits and concurrency. A long idle period can make repeated cheap reads more expensive than one later rebuild.

For example, with a hypothetical cache read multiplier of 0.1 and rebuild multiplier of 1.25, fifteen maintenance reads cost 1.5 units, exceeding one rebuild before any output is counted. The example is normalized, not a price quote.

A future active-maintenance adapter requires an exact reproducible provider request, measured hits, explicit provider/model/account scope, a durable reservation of worst-case monetary cost before transmission, a hard daily budget, bounded concurrency and expiry, no retries after uncertainty, and automatic shutoff after a miss or absent telemetry. A local budget is a safeguard against ordinary bugs, not an absolute billing guarantee: use provider-side spending limits where available. Maintenance must never keep running overnight merely because a local timer says the cache is warm.
