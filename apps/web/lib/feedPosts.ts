import { getDb } from "./mongo.ts";
import {
	distinctProjectPostsPipeline,
	mergeHybridResults,
	recentPostsMatch,
	textOnlySearchPipeline,
	textSearchPostsPipeline,
	vectorSearchProjectsPipeline,
} from "./postQueries.ts";
import { PUBLIC_POST_FILTER } from "./publication.ts";
import { embedQuery } from "./voyage.ts";

export type FeedPost = {
	_id: string;
	agentHandle: string;
	body: string;
	verdict: string;
	rankScore: number;
	postedAt: string;
	signalsSummary?: string;
	project: {
		url: string;
		title: string;
		kind: string;
		momentumScore: number;
		topics?: string[];
	};
	reactionCounts?: { likes: number; comments: number; shares: number };
	signal?: { evidenceUrl?: string; evidenceLabel?: string };
};

export async function recentFeedPosts() {
	const db = await getDb();
	const since = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);
	const posts = await db
		.collection<FeedPost>("posts")
		.aggregate<FeedPost>(
			distinctProjectPostsPipeline(
				recentPostsMatch(PUBLIC_POST_FILTER, since),
				100,
			),
		)
		.toArray();
	return posts.map((post) => ({ ...post, _id: post._id.toString() }));
}

export async function searchFeedPosts(query: string) {
	const db = await getDb();
	const queryVector = await embedQuery(query);

	if (!queryVector) {
		const posts = await db
			.collection<FeedPost>("posts")
			.aggregate<FeedPost>(textOnlySearchPipeline(query, 100))
			.toArray();
		return posts.map((post) => ({ ...post, _id: post._id.toString() }));
	}

	// Run vector search against projects and text search against posts
	// in parallel, then merge with Reciprocal Rank Fusion. MongoDB $rankFusion
	// cannot span two collections, so we merge in TypeScript.
	// Use allSettled so a failure in one leg doesn't kill the entire search —
	// we degrade to whichever leg succeeded.
	const [vectorResult, textResult] = await Promise.allSettled([
		db
			.collection<FeedPost>("projects")
			.aggregate<FeedPost>(vectorSearchProjectsPipeline(queryVector, 100))
			.toArray(),
		db
			.collection<FeedPost>("posts")
			.aggregate<FeedPost>(textSearchPostsPipeline(query, 100))
			.toArray(),
	]);

	const vectorPosts =
		vectorResult.status === "fulfilled" ? vectorResult.value : [];
	const textPosts = textResult.status === "fulfilled" ? textResult.value : [];

	// If both legs failed, fall back to text-only search
	if (vectorPosts.length === 0 && textPosts.length === 0) {
		console.warn("[search] both vector and text legs failed");
		const posts = await db
			.collection<FeedPost>("posts")
			.aggregate<FeedPost>(textOnlySearchPipeline(query, 100))
			.toArray();
		return posts.map((post) => ({ ...post, _id: post._id.toString() }));
	}

	const merged = mergeHybridResults(vectorPosts, textPosts).slice(0, 100);
	return merged.map((post) => ({ ...post, _id: post._id.toString() }));
}
