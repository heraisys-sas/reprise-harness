import z from "@deepseek-ai/schemastery";
import { credentialRef } from "@deepseek-ai/dsh-credentials";
import { launchEnvironmentOf } from "@deepseek-ai/dsh-launch-environment";
import { WebError } from "@deepseek-ai/dsh-web";
import { search as braveSearch } from "./providers/brave.js";
//#region provider
/** Stable id this provider registers under; the web service's `searchProvider` config selects it. */
const BRAVE_PROVIDER_ID = "brave";
/** Default search endpoint (official Web Search API, `/res/v1` prefix included); query params are appended. */
const BRAVE_DEFAULT_ENDPOINT = "https://api.search.brave.com/res/v1/web/search";
/** Default hint forwarded to the adapter when the request carries no `maxResults`. */
const DEFAULT_MAX_RESULTS = 8;
/**
 * The Brave-backed search provider. `resolveOptions` is a thunk snapshotted at
 * each operation's entry so one search never mixes two config sections.
 */
var BraveSearchProvider = class {
	resolveOptions;
	id = BRAVE_PROVIDER_ID;
	constructor(resolveOptions) {
		this.resolveOptions = resolveOptions;
	}
	available() {
		const options = this.resolveOptions();
		return URL.canParse(options.baseUrl) && ((options.apiKey?.length ?? 0) > 0 || options.resolveApiKey !== void 0);
	}
	async search(request, signal) {
		const options = this.resolveOptions();
		const apiKey = await this.apiKey(options, signal);
		throwIfSearchAborted(signal);
		let items;
		try {
			items = await braveSearch(request.query, {
				apiKey,
				baseUrl: options.baseUrl,
				maxResults: request.maxResults ?? options.maxResults,
				...signal !== void 0 ? { signal } : {}
			});
		} catch (error) {
			if (signal?.aborted === true || isAbortError(error)) throw searchAborted(signal, error);
			if (error instanceof WebError) throw error;
			throw new WebError(`${options.providerName} search failed: ${String(error)}`, "WEB_PROVIDER_ERROR", { cause: error });
		}
		throwIfSearchAborted(signal);
		return {
			sources: toSources(items),
			truncated: false
		};
	}
	/**
	* Resolve one operation's API key without retaining a credential on the provider.
	* @throws {@link WebError} `WEB_PROVIDER_CREDENTIAL_MISSING` when nothing resolves.
	*/
	async apiKey(options, signal) {
		throwIfSearchAborted(signal);
		let resolved;
		try {
			resolved = options.resolveApiKey === void 0 ? void 0 : await options.resolveApiKey();
		} catch (error) {
			if (signal?.aborted === true || isAbortError(error)) throw searchAborted(signal, error);
			throw new WebError(`${options.providerName} search credential resolution failed: ${String(error)}`, "WEB_PROVIDER_ERROR", { cause: error });
		}
		if (resolved !== void 0 && resolved.length > 0) return resolved;
		throw new WebError(`${options.providerName} search has no API key for "${options.apiKeyRef ?? "BRAVE_SEARCH_API_KEY"}"; store it through the credentials service (the web Models page writes it) or set the "${options.apiKeyRef ?? "BRAVE_SEARCH_API_KEY"}" credential reference`, "WEB_PROVIDER_CREDENTIAL_MISSING");
	}
};
/** Normalize adapter items to the seam's source shape: {url} plus present optional fields; dedupe by url. */
function toSources(items) {
	const seen = /* @__PURE__ */ new Set();
	const sources = [];
	for (const item of items ?? []) {
		const url = item?.url ?? "";
		if (url.length === 0 || seen.has(url)) continue;
		seen.add(url);
		sources.push({
			url,
			...item.title != null && item.title.length > 0 ? { title: item.title } : {},
			...item.snippet != null && item.snippet.length > 0 ? { snippet: item.snippet } : {},
			...item.publishedAt != null && item.publishedAt.length > 0 ? { publishedAt: item.publishedAt } : {}
		});
	}
	return sources;
}
/**
* Project one resolved config section into the options the provider serves its
* next search with. Environment fallbacks stay here rather than in the provider.
* @param ctx - plugin context supplying the credential and environment planes.
* @param config - the currently authoritative section (plain values).
* @returns options for one search.
*/
function resolveOptions(ctx, config) {
	const apiKeyRef = credentialRef(config.apiKeyRef);
	const literalApiKey = config.apiKey !== void 0 && config.apiKey.length > 0 ? config.apiKey : void 0;
	return {
		...literalApiKey === void 0 ? {} : { apiKey: literalApiKey },
		apiKeyRef,
		providerName: config.providerName,
		baseUrl: config.baseUrl,
		maxResults: config.maxResults,
		resolveApiKey: async () => {
			const credentials = ctx.get("credentials");
			if (credentials !== void 0) return (await credentials.resolve(apiKeyRef))?.value;
			const ambient = launchEnvironmentOf(ctx).get(apiKeyRef);
			return ambient !== void 0 && ambient.value.length > 0 ? ambient.value : void 0;
		}
	};
}
//#endregion
//#region abort helpers
/** Throw the provider's stable cancellation error when the caller already aborted. */
function throwIfSearchAborted(signal) {
	if (signal?.aborted === true) throw searchAborted(signal);
}
/** Build the provider's stable cancellation error while retaining the caller's reason. */
function searchAborted(signal, fallback) {
	return new WebError(`${BRAVE_PROVIDER_ID} search aborted`, "WEB_ABORTED", { cause: signal?.aborted === true ? signal.reason : fallback });
}
/** True for a fetch/`AbortSignal` abort, surfaced as `WEB_ABORTED`. */
function isAbortError(error) {
	return error instanceof DOMException && error.name === "AbortError";
}
//#endregion
//#region plugin
/** Cordis plugin name used by loader diagnostics. */
const name = "web-search-brave-local";
/** The web seam this provider registers into; the loader injects `ctx.web` from this list. */
const inject = ["web"];
const Config = z.object({
	apiKey: z.string().role("secret").volatile(),
	apiKeyRef: z.string().role("credential-ref").default("BRAVE_SEARCH_API_KEY").volatile(),
	baseUrl: z.string().default(BRAVE_DEFAULT_ENDPOINT).volatile(),
	providerName: z.string().default("brave").volatile(),
	maxResults: z.number().step(1).min(1).default(DEFAULT_MAX_RESULTS).volatile()
});
/** Register the Brave search provider with `ctx.web`. */
function apply(ctx, config) {
	ctx.web.registerSearchProvider(new BraveSearchProvider(() => resolveOptions(ctx, {
		apiKey: config.apiKey.get(),
		apiKeyRef: config.apiKeyRef.get(),
		baseUrl: config.baseUrl.get(),
		providerName: config.providerName.get(),
		maxResults: config.maxResults.get()
	})));
}
//#endregion
export { Config, BRAVE_DEFAULT_ENDPOINT, BRAVE_PROVIDER_ID, BraveSearchProvider, apply, inject, name, resolveOptions };
