/** URL <-> slug helpers for project routes. Keeps web slugs and Port identifiers aligned. */

/** Convert a project URL to a SEO-friendly slug. GitHub repos -> owner-repo. */
export function urlToSlug(url: string): string {
	try {
		const u = new URL(url);
		// github.com/owner/repo -> owner-repo
		const parts = u.pathname.split("/").filter(Boolean);
		if (u.hostname === "github.com" && parts.length >= 2) {
			return `${parts[0]}-${parts[1]}`.toLowerCase();
		}
		// fallback: last path segment
		return (parts[parts.length - 1] || u.hostname).toLowerCase();
	} catch {
		return url
			.toLowerCase()
			.replace(/[^a-z0-9]+/g, "-")
			.replace(/^-|-$/g, "");
	}
}

/** Reverse: slug -> likely project URL. For GitHub owner-repo slugs. */
export function slugToUrl(slug: string): string {
	const parts = slug.split("-");
	if (parts.length >= 2) {
		return `https://github.com/${parts[0]}/${parts.slice(1).join("-")}`;
	}
	return `https://github.com/${slug}`;
}
