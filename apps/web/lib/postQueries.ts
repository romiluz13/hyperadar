import type { Document } from "mongodb";

export function recentPostsMatch(match: Document, since: Date): Document {
	return { ...match, postedAt: { $gte: since } };
}

export function publicationWindowMatch(
	match: Document,
	since: Date,
	until: Date,
): Document {
	return { ...match, postedAt: { $gte: since, $lte: until } };
}

export function distinctProjectPostsPipeline(
	match: Document,
	limit: number,
): Document[] {
	return [
		{ $match: match },
		{ $sort: { rankScore: -1, postedAt: -1 } },
		{ $group: { _id: "$project.url", post: { $first: "$$ROOT" } } },
		{ $replaceRoot: { newRoot: "$post" } },
		{ $sort: { rankScore: -1, postedAt: -1 } },
		{ $limit: limit },
	];
}
