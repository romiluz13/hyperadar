import assert from "node:assert/strict";
import test from "node:test";

import { ObjectId } from "mongodb";

import { closeMongoConnection, getDb } from "./mongo.ts";
import {
	consumeMutationRateLimit,
	MutationRateLimitError,
} from "./mutationGuard.ts";
import {
	PUBLIC_POST_FILTER,
	publishedSignalFilter,
} from "./publication.ts";
import {
	addComment,
	addShare,
	setLike,
} from "./reactionPersistence.ts";

const testDbName = process.env.MONGODB_TEST_DB ?? "";
const hasSafeAtlasTestDb =
	Boolean(process.env.MONGODB_URI) &&
	(testDbName.startsWith("test_") || testDbName.endsWith("_test"));

test(
	"anonymous mutation budgets persist across requests without storing raw addresses",
	{ skip: !hasSafeAtlasTestDb },
	async () => {
		process.env.MONGODB_DB = testDbName;
		const db = await getDb();
		const scope = `rate-test-${crypto.randomUUID()}`;
		const request = new Request("https://hyperadar.example/api/reactions", {
			headers: { "x-forwarded-for": "203.0.113.42" },
		});
		try {
			await consumeMutationRateLimit(
				db,
				request,
				"anonymous-test-user",
				scope,
				1,
				60_000,
			);
			await assert.rejects(
				consumeMutationRateLimit(
					db,
					request,
					"anonymous-test-user",
					scope,
					1,
					60_000,
				),
				MutationRateLimitError,
			);
			const documents = await db
				.collection<{ _id: string }>("reaction_rate_limits")
				.find({ _id: { $regex: `^${scope}:` } })
				.toArray();
			assert.equal(documents.length, 2);
			assert.equal(JSON.stringify(documents).includes("203.0.113.42"), false);
		} finally {
			await db
				.collection<{ _id: string }>("reaction_rate_limits")
				.deleteMany({ _id: { $regex: `^${scope}:` } });
			await closeMongoConnection();
		}
	},
);

test(
	"reaction events and denormalized counters remain atomic under concurrency",
	{ skip: !hasSafeAtlasTestDb },
	async () => {
		process.env.MONGODB_DB = testDbName;
		const db = await getDb();
		await db.collection("reactions").createIndex(
			{ operationId: 1 },
			{
				unique: true,
				partialFilterExpression: { operationId: { $type: "string" } },
				name: "one_reaction_per_operation",
			},
		);
		await db.collection("reactions").createIndex(
			{ postId: 1, rankIdentity: 1, type: 1 },
			{
				unique: true,
				partialFilterExpression: {
					type: "like",
					rankIdentity: { $type: "string" },
				},
				name: "one_like_per_network",
			},
		);
		const postId = new ObjectId();
		const postIdText = postId.toString();
		const missingPostId = new ObjectId().toString();
		const shareOperation = crypto.randomUUID();
		const commentOperation = crypto.randomUUID();
		await db.collection("posts").insertOne({
			_id: postId,
			agentHandle: "@reaction-test",
			body: "Atomic social write proof",
			postedAt: new Date(),
			portSyncStatus: "synced",
			evidenceContractVersion: 2,
			project: { momentumScore: 50 },
			reactionCounts: { likes: 99, comments: 99, shares: 99 },
		});

		try {
			await Promise.all([
				setLike(postIdText, "same-user", "same-network", true),
				setLike(postIdText, "same-user", "same-network", true),
			]);
			await Promise.all([
				addShare(postIdText, "share-user", "share-network", shareOperation),
				addShare(postIdText, "share-user", "share-network", shareOperation),
				addComment(
					postIdText,
					"comment-user",
					"comment-network",
					"Reader",
					"First",
					commentOperation,
				),
				addComment(
					postIdText,
					"comment-user",
					"comment-network",
					"Reader",
					"First",
					commentOperation,
				),
			]);

			const post = await db.collection("posts").findOne({ _id: postId });
			const [likes, shares, comments] = await Promise.all([
				db
					.collection("reactions")
					.countDocuments({ postId: postIdText, type: "like" }),
				db
					.collection("reactions")
					.countDocuments({ postId: postIdText, type: "share" }),
				db
					.collection("reactions")
					.countDocuments({ postId: postIdText, type: "comment" }),
			]);

			assert.equal(post?.reactionCounts.likes, likes);
			assert.equal(post?.reactionCounts.shares, shares);
			assert.equal(post?.reactionCounts.comments, comments);
			assert.equal(likes, 1);
			assert.equal(shares, 1);
			assert.equal(comments, 1);
			assert.ok(
				(post?.rankScore ?? 0) >= 50,
				"a human reaction must not demote the source momentum score",
			);
			assert.equal(post?.humanRankBonus, 6);
			await setLike(postIdText, "same-user", "moved-network", true);
			assert.equal(
				await db
					.collection("reactions")
					.countDocuments({ postId: postIdText, type: "like" }),
				1,
				"one user moving networks must not create an extra like",
			);
			await setLike(postIdText, "fresh-cookie", "same-network", true);
			assert.equal(
				await db
					.collection("reactions")
					.countDocuments({ postId: postIdText, type: "like" }),
				1,
				"new cookies on one network must not create extra likes",
			);

			const unliked = await setLike(
				postIdText,
				"same-user",
				"third-network",
				false,
			);
			assert.equal(unliked.liked, false);
			assert.equal(unliked.counts.likes, 0);
			assert.equal(
				await db
					.collection("reactions")
					.countDocuments({ postId: postIdText, type: "like" }),
				0,
			);

			await assert.rejects(
				addShare(
					missingPostId,
					"orphan-proof",
					"orphan-network",
					crypto.randomUUID(),
				),
				/published post/i,
			);
			assert.equal(
				await db
					.collection("reactions")
					.countDocuments({ postId: missingPostId }),
				0,
			);
		} finally {
			await db
				.collection("reactions")
				.deleteMany({ postId: { $in: [postIdText, missingPostId] } });
			await db.collection("posts").deleteOne({ _id: postId });
			await closeMongoConnection();
		}
	},
);

test(
	"public queries hide unpublished posts and their linked signals",
	{ skip: !hasSafeAtlasTestDb },
	async () => {
		process.env.MONGODB_DB = testDbName;
		const db = await getDb();
		const projectUrl = `https://example.com/public-filter-${crypto.randomUUID()}`;
		const syncedPostId = new ObjectId();
		const pendingPostId = new ObjectId();
		await db.collection("posts").insertMany([
			{
				_id: syncedPostId,
				agentHandle: "@publication-test",
				body: "public",
				postedAt: new Date(),
				project: { url: projectUrl },
				portSyncStatus: "synced",
				evidenceContractVersion: 2,
			},
			{
				_id: pendingPostId,
				agentHandle: "@publication-test",
				body: "private",
				postedAt: new Date(),
				project: { url: projectUrl },
				portSyncStatus: "pending",
				evidenceContractVersion: 2,
			},
		]);
		const signalInsert = await db.collection("signals").insertMany([
			{
				projectId: projectUrl,
				capturedAt: new Date(),
				metric: "verified-legacy",
				value: 1,
			},
			{
				projectId: projectUrl,
				capturedAt: new Date(),
				metric: "unverified-legacy",
				value: 0,
			},
			{
				projectId: projectUrl,
				capturedAt: new Date(),
				postId: syncedPostId.toString(),
				metric: "published",
				value: 2,
			},
			{
				projectId: projectUrl,
				capturedAt: new Date(),
				postId: pendingPostId.toString(),
				metric: "pending",
				value: 3,
			},
		]);
		const verifiedLegacySignalId = signalInsert.insertedIds[0];
		await db.collection("legacy_signal_verifications").insertOne({
			_id: verifiedLegacySignalId.toString(),
			signalId: verifiedLegacySignalId,
			projectId: projectUrl,
			postId: syncedPostId.toString(),
			basis: "synced-project-post",
			signalOverride: {
				source: "verified-source",
				metric: "verified-legacy",
				value: 1,
				delta: 0,
			},
			verifiedAt: new Date(),
		});

		try {
			const [posts, signals] = await Promise.all([
				db
					.collection("posts")
					.find({ ...PUBLIC_POST_FILTER, "project.url": projectUrl })
					.toArray(),
					db
						.collection("signals")
						.find(
							publishedSignalFilter(
								projectUrl,
								[signalInsert.insertedIds[2]],
								[verifiedLegacySignalId],
							),
						)
					.toArray(),
			]);

			assert.deepEqual(
				posts.map((post) => post.body),
				["public"],
			);
			assert.deepEqual(
				signals.map((signal) => signal.metric).sort(),
				["published", "verified-legacy"],
			);
		} finally {
			await db.collection("posts").deleteMany({ "project.url": projectUrl });
			await db.collection("signals").deleteMany({ projectId: projectUrl });
			await db
				.collection("legacy_signal_verifications")
				.deleteMany({ projectId: projectUrl });
			await closeMongoConnection();
		}
	},
);
