/**
 * Brave Search adapter — the unit lib/index.js delegates to.
 *
 * Official API (verified 2026-10-29):
 * - GET https://api.search.brave.com/res/v1/web/search (POST also supported).
 * - Headers: `X-Subscription-Token: <API key>` (required) and `Accept: application/json`.
 * - Params: `q` (required), `count` (1..20, default 20, web results only), plus optional
 *   `country` (default US), `search_lang` (default en), `safesearch`, `freshness`.
 * - Response: `web.results[]` with `title` (string), `url` (string), `description` (snippet),
 *   and optional `age` (human-readable) and `page_age` (ISO datetime).
 * Sources: https://api-dashboard.search.brave.com/app/documentation/web-search,
 * https://api-dashboard.search.brave.com/api-reference/web/search/get,
 * https://github.com/brave/brave-search-skills/blob/main/skills/web-search/SKILL.md
 */

/** Hard cap of the `count` parameter, per the official API reference. */
const BRAVE_MAX_COUNT = 20;

/**
 * Run one web search against the Brave Search API.
 *
 * Exact contract expected by lib/index.js:
 *   async search(query, { apiKey, baseUrl, maxResults, signal }) → [{ url, title, snippet, publishedAt? }]
 * - returns one item per result in API order; `publishedAt` prefers `page_age` (ISO) over `age`.
 * - the caller (lib/index.js) dedupes by url, drops empty url entries, and re-enforces the
 *   seam's maxResults; this function only clamps `count` to the API's 1..20 bound.
 * - a missing or malformed `web.results` block throws a plain Error (wrapped as WebError
 *   WEB_PROVIDER_ERROR by lib/index.js); `web.results: []` resolves to [] (legitimate empty).
 * - cancellation: `signal` is forwarded to fetch when provided; the native AbortError
 *   propagates and lib/index.js maps it to WebError WEB_ABORTED.
 * - network failures propagate with their original cause; error messages never include the API key.
 */
export async function search(query, { apiKey, baseUrl, maxResults, signal } = {}) {
	const url = `${baseUrl}?${new URLSearchParams({ q: query, count: String(clampCount(maxResults)) })}`;
	let response;
	try {
		response = await fetch(url, {
			headers: {
				"accept": "application/json",
				"x-subscription-token": apiKey
			},
			...signal !== void 0 ? { signal } : {}
		});
	} catch (error) {
		if (signal?.aborted === true || isAbortError(error)) throw error;
		throw new Error(`Brave Search request failed: ${String(error)}`, { cause: error });
	}
	if (response.status === 401 || response.status === 403) throw new Error(`Brave Search rejected the credentials (HTTP ${response.status}); check the API key stored under the configured credential reference`);
	if (response.status === 429) throw new Error("Brave Search rate limit exceeded (HTTP 429); retry later or upgrade the plan");
	if (!response.ok) {
		let detail = "";
		try {
			const body = await response.json();
			const message = typeof body.error === "string" ? body.error : body.error?.message ?? body.message;
			if (typeof message === "string" && message.length > 0) detail = `: ${message}`;
		} catch {}
		throw new Error(`Brave Search API error (HTTP ${response.status})${detail}`);
	}
	const payload = await parseBody(response, signal);
	const results = payload?.web?.results;
	if (!Array.isArray(results)) throw new Error("Brave Search returned no web results block; the response is missing the `web` object");
	return results.map(toItem);
}

/** Clamp the requested result count to the API's `count` bound (1..20, default 20). */
function clampCount(maxResults) {
	if (maxResults === void 0 || !Number.isInteger(maxResults) || maxResults < 1) return BRAVE_MAX_COUNT;
	return Math.min(maxResults, BRAVE_MAX_COUNT);
}

/** Parse the JSON body, preserving cancellation and keeping the parse failure as cause. */
async function parseBody(response, signal) {
	try {
		return await response.json();
	} catch (error) {
		if (signal?.aborted === true || isAbortError(error)) throw error;
		throw new Error(`Brave Search returned an unparseable response body: ${String(error)}`, { cause: error });
	}
}

/** Map one Brave result to the adapter's item shape; empties are filtered by lib/index.js. */
function toItem(item) {
	const publishedAt = firstNonEmpty(item?.page_age, item?.age);
	return {
		url: item?.url ?? "",
		title: item?.title ?? "",
		snippet: item?.description ?? "",
		...publishedAt !== void 0 ? { publishedAt } : {}
	};
}

/** First present, non-empty string among the values; undefined otherwise. */
function firstNonEmpty(...values) {
	for (const value of values) if (typeof value === "string" && value.length > 0) return value;
	return void 0;
}

/** True for a fetch/`AbortSignal` abort, propagated for lib/index.js to map to WEB_ABORTED. */
function isAbortError(error) {
	return error instanceof DOMException && error.name === "AbortError";
}
