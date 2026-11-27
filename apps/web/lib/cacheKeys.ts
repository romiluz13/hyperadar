/**
 * unstable_cache keys every entry by its arguments, so a page's raw query
 * values are bounded here before they reach a cached function.
 */

export const CACHE_KEY_MAX_PAGE = 1000;
export const CACHE_KEY_MAX_PARAM_CHARS = 200;
const OBJECT_ID_HEX = /^[0-9a-f]{24}$/i;

export function cacheKeyPage(requested: string | undefined): number {
	const parsed = Number.parseInt(requested ?? "1", 10);
	if (Number.isNaN(parsed) || parsed < 1) return 1;
	return Math.min(parsed, CACHE_KEY_MAX_PAGE);
}

export function cacheKeyPostId(post: string | undefined): string | undefined {
	return post && OBJECT_ID_HEX.test(post) ? post : undefined;
}

export function cacheKeyParam(value: string): string {
	return value.slice(0, CACHE_KEY_MAX_PARAM_CHARS);
}
