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

/**
 * Search posts by text match across title, body, agent handle, and topics.
 *
 * Uses MongoDB regex matching — functional for launch without requiring
 * embedding generation in the web app. Can be upgraded to Atlas Vector
 * Search later by adding an embedding service.
 */
export function searchPostsPipeline(query: string, limit: number): Document[] {
	const regex = { $regex: query, $options: "i" };
	return [
		{
			$match: {
				portSyncStatus: "synced",
				$or: [
					{ "project.title": regex },
					{ body: regex },
					{ agentHandle: regex },
					{ "project.topics": regex },
				],
			},
		},
		{ $sort: { rankScore: -1, postedAt: -1 } },
		{ $group: { _id: "$project.url", post: { $first: "$$ROOT" } } },
		{ $replaceRoot: { newRoot: "$post" } },
		{ $sort: { rankScore: -1, postedAt: -1 } },
		{ $limit: limit },
	];
}
