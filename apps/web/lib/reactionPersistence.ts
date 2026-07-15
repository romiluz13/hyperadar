import type { ClientSession, Db, ObjectId } from "mongodb";

import { withMongoTransaction, getDb } from "./mongo.ts";
import { toObjectId } from "./objectId.ts";
import { publishedPostFilter } from "./publication.ts";
import { rankWithHumanSignal } from "./ranking.ts";

export type ReactionCounts = {
	likes: number;
	comments: number;
	shares: number;
};

type Reaction = {
	_id?: ObjectId;
	postId: string;
	userId: string;
	rankIdentity?: string;
	type: "like" | "share" | "comment";
	createdAt: Date;
	operationId?: string;
	userName?: string;
	text?: string;
};

export class PostNotFoundError extends Error {
	constructor() {
		super("Published post not found");
		this.name = "PostNotFoundError";
	}
}

export class ReactionConflictError extends Error {
	constructor() {
		super("Reaction operation conflicts with an existing event");
		this.name = "ReactionConflictError";
	}
}

async function reconcileCounts(
	db: Db,
	session: ClientSession,
	postId: string,
	): Promise<ReactionCounts> {
	const reactions = db.collection<Reaction>("reactions");
	const counts = {
		likes: await reactions.countDocuments({ postId, type: "like" }, { session }),
		comments: await reactions.countDocuments(
			{ postId, type: "comment" },
			{ session },
		),
		shares: await reactions.countDocuments({ postId, type: "share" }, { session }),
	};
	const result = await db.collection("posts").updateOne(
		publishedPostFilter({ _id: toObjectId(postId) }),
		{ $set: { reactionCounts: counts } },
		{ session },
	);
	if (result.matchedCount !== 1) {
		throw new PostNotFoundError();
	}
	return counts;
}

async function recomputeRank(
	db: Db,
	session: ClientSession,
	postId: string,
) {
	const post = await db.collection("posts").findOne(
		publishedPostFilter({ _id: toObjectId(postId) }),
		{ session },
	);
	if (!post) throw new PostNotFoundError();

	const participants = await db
		.collection("reactions")
		.aggregate(
			[
				{ $match: { postId } },
				{ $group: { _id: { $ifNull: ["$rankIdentity", "$userId"] } } },
				{ $limit: 5 },
			],
			{ session },
		)
		.toArray();
	const momentum = post.project?.momentumScore ?? post.baseRankScore ?? 0;
	const humanRankBonus = Math.min(participants.length * 2, 10);
	const rankScore = rankWithHumanSignal(momentum, participants.length);
	await db
		.collection("posts")
		.updateOne(
			publishedPostFilter({ _id: post._id }),
			{ $set: { humanRankBonus, rankScore } },
			{ session },
		);
}

export async function setLike(
	postId: string,
	userId: string,
	rankIdentity: string,
	liked: boolean,
) {
	return withMongoTransaction(async (db, session) => {
		const reactions = db.collection<Reaction>("reactions");
		if (liked) {
			await reactions.updateOne(
				{
					postId,
					type: "like",
					$or: [{ userId }, { rankIdentity }],
				},
				{
					$setOnInsert: {
						postId,
						userId,
						rankIdentity,
						type: "like",
						createdAt: new Date(),
					},
				},
				{ upsert: true, session },
			);
		} else {
			await reactions.deleteOne(
				{
					postId,
					type: "like",
					$or: [{ userId }, { rankIdentity }],
				},
				{ session },
			);
		}
		const counts = await reconcileCounts(db, session, postId);
		await recomputeRank(db, session, postId);
		return { liked, counts };
	});
}

export async function addShare(
	postId: string,
	userId: string,
	rankIdentity: string,
	operationId: string,
) {
	return withMongoTransaction(async (db, session) => {
		const reactions = db.collection<Reaction>("reactions");
		await reactions.updateOne(
			{ operationId },
			{
				$setOnInsert: {
					postId,
					userId,
					rankIdentity,
					operationId,
					type: "share",
					createdAt: new Date(),
				},
			},
			{ upsert: true, session },
		);
		const stored = await reactions.findOne({ operationId }, { session });
		if (
			!stored ||
			stored.postId !== postId ||
			stored.userId !== userId ||
			stored.type !== "share"
		) {
			throw new ReactionConflictError();
		}
		const counts = await reconcileCounts(db, session, postId);
		await recomputeRank(db, session, postId);
		return counts;
	});
}

export async function addComment(
	postId: string,
	userId: string,
	rankIdentity: string,
	userName: string,
	text: string,
	operationId: string,
) {
	return withMongoTransaction(async (db, session) => {
		const reactions = db.collection<Reaction>("reactions");
		await reactions.updateOne(
			{ operationId },
			{
				$setOnInsert: {
					postId,
					userId,
					rankIdentity,
					userName,
					text,
					operationId,
					type: "comment",
					createdAt: new Date(),
				},
			},
			{ upsert: true, session },
		);
		const stored = await reactions.findOne({ operationId }, { session });
		if (
			!stored ||
			stored.postId !== postId ||
			stored.userId !== userId ||
			stored.type !== "comment" ||
			stored.userName !== userName ||
			stored.text !== text
		) {
			throw new ReactionConflictError();
		}
		const counts = await reconcileCounts(db, session, postId);
		await recomputeRank(db, session, postId);
		return {
			comment: {
				operationId,
				userName,
				text,
				createdAt: stored.createdAt,
			},
			counts,
		};
	});
}

export async function getReactionCounts(
	postId: string,
): Promise<ReactionCounts> {
	const db = await getDb();
	const post = await db.collection("posts").findOne(
		publishedPostFilter({ _id: toObjectId(postId) }),
		{ projection: { reactionCounts: 1 } },
	);
	if (!post) throw new PostNotFoundError();
	return post.reactionCounts ?? { likes: 0, comments: 0, shares: 0 };
}
