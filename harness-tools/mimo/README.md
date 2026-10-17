# MiMo compatibility with the installed Harness

Verified 17 October 2026, without authentication or paid inference. Both routes have now been merged into the local Desktop profile and Flash/Pro added to the subagent model allowlist. ASI:Cloud and the default model are preserved. As requested, no general configuration backup was created. Credentials and live account validation remain necessary.

The installed DSH `llm-pi-ai` adapter already includes pi-ai's `xiaomi`, `xiaomi-token-plan-cn`, `xiaomi-token-plan-sgp`, and `xiaomi-token-plan-ams` providers. Its Xiaomi catalog includes `mimo-v2.6-flash`, `mimo-v2.6-pro` and UltraSpeed. Prefer the first two for this budget. Native catalog entries already carry DeepSeek-style thinking controls and `requiresReasoningContentOnAssistantMessages: true`; reuse those entries rather than inventing another generic route.

- `cordis.patch.yml`: ordinary pay-as-you-go API, provider `xiaomi`, inherited base URL `https://api.xiaomimimo.com/v1`, credential reference `MIMO_API_KEY`.
- `token-plan-ams.patch.yml`: subscription route, provider `xiaomi-token-plan-ams`, inherited base URL `https://token-plan-ams.xiaomimimo.com/v1`, separate credential reference `MIMO_TOKEN_PLAN_AMS_API_KEY`. Use only if that is the region and endpoint supplied by the subscribed plan. An AMS endpoint does not by itself establish all data-residency guarantees.

Merge the desired provider into the existing Desktop patch, preserving the other entries, and store its matching key through Harness credentials. Select the matching provider/model in Harness. No automatic route or billing fallback is configured. No new SDK dependency is required. The ordinary API balance and Token Plan credits are separate.

Both overlays passed the installed adapter's schema. An offline payload capture through the bundled pi-ai transport verified thinking enabled, replay of provider-native reasoning metadata and matching tool-call/result identifiers. No HTTP/model request was sent. The inspected temporary modules were byte-compared with the installed ASAR and matched. Authentication, server-side streaming, multi-turn tool execution and measured cache hits still need a real account test.

MiMo's documented thinking control is enabled/disabled. Generic client effort labels must not be interpreted as separate calibrated reasoning tiers without additional server-side evidence. In thinking mode, Xiaomi uses its own sampling defaults. Raw model video/audio support is broader than the image/text input advertised by this installed adapter; it does not prove those modalities work in Harness.

## Time-based billing

Token Plan applies a 0.8 credit-consumption coefficient between 16:00 and 24:00 UTC. In Paris this is 18:00–02:00 during summer time, and 17:00–01:00 during winter time. On 17 October 2026 use the summer-time window; the winter-time window starts on 25 October. This is a 20% credit discount on a subscription, not a documented discount to ordinary pay-as-you-go API token rates. Current ordinary API pricing publishes no peak/off-peak schedule. Asynchronous Batch API prices are 50% of real-time rates; that is a separate execution mode, not an hourly discount or a drop-in replacement for an interactive tool loop.

Sources: [Chat API](https://mimo.mi.com/docs/en-US/api/chat/openai-api), [API pricing](https://mimo.mi.com/docs/en-US/price/pay-as-you-go), [plan rules](https://mimo.mi.com/docs/en-US/quick-start/faq/token-plan/Plans%26Pricing), [credit rules](https://mimo.mi.com/docs/en-US/quick-start/faq/token-plan/Usage%26Quota), [Batch API](https://mimo.mi.com/docs/en-US/quick-start/usage-guide/text-generation/batch-api).
