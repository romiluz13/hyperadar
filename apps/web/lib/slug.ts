import { createHash } from "node:crypto";

/** URL <-> slug helpers for project routes. Keeps web slugs and Port identifiers aligned. */

/** Convert a project URL to its pre-v2 readable slug. */
export function legacyUrlToSlug(url: string): string {
	try {
		const u = new URL(url);
		const parts = u.pathname.split("/").filter(Boolean);
		if (u.hostname === "github.com" && parts.length >= 2) {
			return `${cleanSlug(parts[0])}-${cleanSlug(parts[1])}`;
		}

		if (
			(u.hostname === "youtube.com" || u.hostname === "www.youtube.com") &&
			parts[0] === "watch" &&
			u.searchParams.get("v")
		) {
			return `youtube-${cleanSlug(u.searchParams.get("v")!)}`;
		}
		if (u.hostname === "youtu.be" && parts[0]) {
			return `youtube-${cleanSlug(parts[0])}`;
		}

		if (
			(u.hostname === "reddit.com" || u.hostname === "www.reddit.com") &&
			parts[0] === "r" &&
			parts[2] === "comments" &&
			parts[3]
		) {
			return `reddit-${cleanSlug(parts[1])}-${cleanSlug(parts[3])}`;
		}

		const host = u.hostname.replace(/^www\./, "");
		const queryIdentity = u.searchParams.get("id") ?? u.searchParams.get("v");
		return cleanSlug(
			[host, ...parts, queryIdentity].filter(Boolean).join("-"),
		).slice(0, 120);
	} catch {
		return cleanSlug(url).slice(0, 120);
	}
}

/** Keep readable routes while making every full URL identity collision-resistant. */
export function urlToSlug(url: string): string {
	const base = legacyUrlToSlug(url) || "project";
	const suffix = createHash("sha256").update(url).digest("hex").slice(0, 16);
	return `${base.slice(0, 103).replace(/-$/, "")}-${suffix}`;
}

export function legacySlugCandidates(slug: string): string[] {
	const parts = slug.split("-").filter(Boolean);
	const candidates = new Set<string>();
	if (/^(?:[0-9a-f]{8}|[0-9a-f]{16})$/.test(parts.at(-1) ?? "")) {
		candidates.add(parts.slice(0, -1).join("-"));
	}
	for (let index = 0; index < parts.length; index += 1) {
		candidates.add(parts.slice(index).join("-"));
		candidates.add(parts[index]);
	}
	candidates.delete(slug);
	return [...candidates].filter(Boolean).slice(0, 40);
}

export function legacyUrlPatterns(slug: string): RegExp[] {
	const legacySlug = slug.replace(/-(?:[0-9a-f]{8}|[0-9a-f]{16})$/, "");
	const youtube = legacySlug.match(/^youtube-([a-z0-9-]+)$/);
	if (youtube) {
		const id = escapeRegex(youtube[1]);
		return [
			new RegExp(`[?&]v=${id}(?:&|$)`, "i"),
			new RegExp(`youtu\\.be/${id}(?:[?/#]|$)`, "i"),
		];
	}

	const reddit = legacySlug.match(/^reddit-.+-([a-z0-9]+)$/);
	if (reddit) {
		return [
			new RegExp(
				`reddit\\.com/r/[^/]+/comments/${escapeRegex(reddit[1])}(?:[/?#]|$)`,
				"i",
			),
		];
	}
	return [];
}

function cleanSlug(value: string): string {
	return value
		.toLowerCase()
		.replace(/[^a-z0-9]+/g, "-")
		.replace(/^-|-$/g, "");
}

function escapeRegex(value: string): string {
	return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Legacy best effort only; collision-resistant project slugs are not reversible. */
export function slugToUrl(slug: string): string {
	const parts = slug.split("-");
	if (parts.length >= 2) {
		return `https://github.com/${parts[0]}/${parts.slice(1).join("-")}`;
	}
	return `https://github.com/${slug}`;
}
