import { urlToSlug } from "./slug.ts";

export function projectHref(project: { url: string }, postId?: string): string {
	try {
		const url = new URL(project.url);
		const week = url.pathname.split("/").filter(Boolean)[0];
		if (url.protocol === "hyperadar:" && url.hostname === "digest" && week) {
			return `/digest/${encodeURIComponent(week)}`;
		}
	} catch {
		// Non-URL identifiers still get a stable project route below.
	}
	const href = `/project/${urlToSlug(project.url)}`;
	return postId ? `${href}?post=${encodeURIComponent(postId)}` : href;
}
