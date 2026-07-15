export function uniqueProjects<T extends { project: { url: string } }>(
	posts: T[],
	limit: number,
) {
	return uniqueProjectsExcluding(posts, new Set(), limit);
}

export function uniqueProjectsExcluding<
	T extends { project: { url: string } },
>(posts: T[], excluded: ReadonlySet<string>, limit: number) {
	const seen = new Set<string>();
	const unique: T[] = [];
	for (const post of posts) {
		if (excluded.has(post.project.url) || seen.has(post.project.url)) continue;
		seen.add(post.project.url);
		unique.push(post);
		if (unique.length === limit) break;
	}
	return unique;
}

export function feedEvidenceLabel(summary?: string): string | null {
	if (!summary) return null;
	const lifetimeAverage = summary.match(/avg since creation=([\d.]+)\/wk/i);
	if (lifetimeAverage) {
		return `avg ${lifetimeAverage[1]}★/wk since creation`;
	}
	const hackerNews = summary.match(/HN points=([\d.]+)/i);
	if (hackerNews) return `${hackerNews[1]} HN points`;
	const youtubeViews = summary.match(/YouTube views=([\d]+)/i);
	if (youtubeViews) {
		return `${new Intl.NumberFormat("en", { notation: "compact" }).format(Number(youtubeViews[1]))} YouTube views`;
	}
	const redditSearch = summary.match(
		/Google SERP rank=([\d.]+); visibility proxy=([\d.]+)\/100/i,
	);
	if (redditSearch) {
		return `Google rank ${redditSearch[1]} · Reddit visibility proxy ${redditSearch[2]}/100`;
	}
	if (/Historical Reddit engagement snapshot/i.test(summary)) {
		return "Reddit search snapshot · exact count unverified";
	}
	return null;
}

export function sourceFamily(source?: string): string | null {
	if (!source) return null;
	const normalized = source.toLowerCase().replaceAll("-", "_");
	if (normalized === "reddit" || normalized.startsWith("reddit_")) return "reddit";
	if (normalized === "hn" || normalized === "hacker_news") return "hacker_news";
	return normalized;
}
