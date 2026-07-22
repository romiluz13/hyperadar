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
 * Vector search pipeline — runs against the `projects` collection.
 *
 * Uses $vectorSearch on projects_vector_index (1024-dim Voyage 4 Large),
 * then $lookup to join synced posts. Returns the best post per project.
 *
 * numCandidates is set to 20x the limit per MongoDB best practice
 * for high ANN/ENN recall overlap.
 */
export function vectorSearchProjectsPipeline(
	queryVector: number[],
	limit: number,
): Document[] {
	const numCandidates = Math.min(limit * 20, 400);
	return [
		{
			$vectorSearch: {
				index: "projects_vector_index",
				path: "embedding",
				queryVector,
				numCandidates,
				limit,
			},
		},
		{
			$lookup: {
				from: "posts",
				localField: "url",
				foreignField: "project.url",
				pipeline: [
					{ $match: { portSyncStatus: "synced" } },
					{ $sort: { rankScore: -1 } },
					{ $limit: 1 },
				],
				as: "posts",
			},
		},
		{ $match: { posts: { $ne: [] } } },
		{ $replaceRoot: { newRoot: { $first: "$posts" } } },
	];
}

/**
 * Text search pipeline — runs against the `posts` collection.
 *
 * Uses Atlas Search ($search with BM25) on posts_search_index.
 * Deduplicates per project URL so mergeHybridResults doesn't inflate
 * scores for projects with multiple matching posts.
 *
 * Note: compound.filter uses the Atlas Search `text` operator (not MQL
 * $eq) because $search compound clauses require Atlas Search operators.
 * Using `text` in `filter` zeroes its score contribution, which is correct
 * for a pure filter. A follow-up $match stage enforces exact MQL equality
 * because the Atlas Search text operator does token-based matching, so
 * "not_synced" would also match "synced".
 */
export function textSearchPostsPipeline(
	queryText: string,
	limit: number,
): Document[] {
	return [
		{
			$search: {
				index: "posts_search_index",
				compound: {
					must: [
						{
							text: {
								query: queryText,
								path: ["project.title", "body", "agentHandle"],
							},
						},
					],
					filter: [{ text: { query: "synced", path: "portSyncStatus" } }],
				},
			},
		},
		// Exact MQL $match after $search — the Atlas Search text operator does
		// token-based matching, so "not_synced" would also match "synced".
		{ $match: { portSyncStatus: "synced" } },
		{ $sort: { rankScore: -1, postedAt: -1 } },
		{ $group: { _id: "$project.url", post: { $first: "$$ROOT" } } },
		{ $replaceRoot: { newRoot: "$post" } },
		// Sort by rankScore descending after dedup — $group order is undefined.
		{ $sort: { rankScore: -1, postedAt: -1 } },
		{ $limit: limit },
	];
}

/**
 * Merge vector and text search results using Reciprocal Rank Fusion.
 *
 * RRF score = sum(weight / (k + rank)) for each list the result appears in.
 * Results in both lists get higher fused scores. Results in only one list
 * still appear, weighted by that list's contribution.
 *
 * Pattern borrowed from Hybrid-Search-RAG — $rankFusion uses RRF internally,
 * but MongoDB $rankFusion can't span two collections, so we merge in TS.
 *
 * @param vectorResults - posts from vector search (already deduped per project)
 * @param textResults - posts from text search (already deduped per project)
 * @param vectorWeight - weight for the vector leg (default 0.6)
 * @param textWeight - weight for the text leg (default 0.4)
 * @param k - RRF constant (default 60, standard value)
 */
export function mergeHybridResults<T extends { project: { url: string } }>(
	vectorResults: T[],
	textResults: T[],
	vectorWeight = 0.6,
	textWeight = 0.4,
	k = 60,
): T[] {
	const scores = new Map<string, { score: number; post: T }>();

	for (let i = 0; i < vectorResults.length; i++) {
		const post = vectorResults[i];
		const key = post.project.url;
		const rank = i + 1;
		const score = vectorWeight / (k + rank);
		scores.set(key, { score, post });
	}

	for (let i = 0; i < textResults.length; i++) {
		const post = textResults[i];
		const key = post.project.url;
		const rank = i + 1;
		const score = textWeight / (k + rank);
		const existing = scores.get(key);
		if (existing) {
			existing.score += score;
		} else {
			scores.set(key, { score, post });
		}
	}

	return [...scores.values()]
		.sort((a, b) => b.score - a.score)
		.map((entry) => entry.post);
}

/**
 * Text-only search fallback when the Voyage API is unavailable.
 *
 * Uses $search (Atlas Search, BM25) without the vector leg.
 * Ensures the feed search never breaks entirely.
 *
 * A follow-up $match stage enforces exact MQL equality on portSyncStatus
 * because the Atlas Search text operator does token-based matching, so
 * "not_synced" would also match "synced".
 */
export function textOnlySearchPipeline(
	queryText: string,
	limit: number,
): Document[] {
	return [
		{
			$search: {
				index: "posts_search_index",
				compound: {
					must: [
						{
							text: {
								query: queryText,
								path: ["project.title", "body", "agentHandle"],
							},
						},
					],
					filter: [{ text: { query: "synced", path: "portSyncStatus" } }],
				},
			},
		},
		// Exact MQL $match after $search — the Atlas Search text operator does
		// token-based matching, so "not_synced" would also match "synced".
		{ $match: { portSyncStatus: "synced" } },
		{ $sort: { rankScore: -1, postedAt: -1 } },
		{ $group: { _id: "$project.url", post: { $first: "$$ROOT" } } },
		{ $replaceRoot: { newRoot: "$post" } },
		// Sort by rankScore descending after dedup — $group order is undefined.
		{ $sort: { rankScore: -1, postedAt: -1 } },
		{ $limit: limit },
	];
}
