/** POST /api/reactions — like or share a post (anonymous, cookie-dedup).
 *  GET /api/reactions?postId=... — get a post's reaction counts. */

import { type NextRequest, NextResponse } from "next/server";
import { getDb } from "@/lib/mongo";
import { getOrCreateUserId } from "@/lib/auth";

export const dynamic = "force-dynamic";

type Reaction = {
	postId: string;
	userId: string;
	type: "like" | "share";
	createdAt: Date;
};

export async function POST(req: NextRequest) {
	const body = await req.json();
	const { postId, type } = body as { postId?: string; type?: "like" | "share" };
	if (!postId || !type || !["like", "share"].includes(type)) {
		return NextResponse.json(
			{ error: "postId and type (like|share) required" },
			{ status: 400 },
		);
	}

	const userId = await getOrCreateUserId();
	const db = await getDb();

	if (type === "like") {
		// One like per user per post (unique index enforces). Idempotent: if already liked, unlike.
		const existing = await db
			.collection<Reaction>("reactions")
			.findOne({ postId, userId, type: "like" });
		if (existing) {
			// Unlike
			await db
				.collection<Reaction>("reactions")
				.deleteOne({ _id: existing._id });
			await db
				.collection("posts")
				.updateOne(
					{ _id: asObjectId(postId) },
					{ $inc: { "reactionCounts.likes": -1 } },
				);
			return NextResponse.json({
				liked: false,
				counts: await getCounts(db, postId),
			});
		}
		await db
			.collection<Reaction>("reactions")
			.insertOne({ postId, userId, type: "like", createdAt: new Date() });
		await db
			.collection("posts")
			.updateOne(
				{ _id: asObjectId(postId) },
				{ $inc: { "reactionCounts.likes": 1 } },
			);
		// Recompute rankScore (reactionVelocity now factors in)
		await recomputeRank(db, postId);
		return NextResponse.json({
			liked: true,
			counts: await getCounts(db, postId),
		});
	}

	// share — no dedup (sharing multiple times is fine)
	await db
		.collection<Reaction>("reactions")
		.insertOne({ postId, userId, type: "share", createdAt: new Date() });
	await db
		.collection("posts")
		.updateOne(
			{ _id: asObjectId(postId) },
			{ $inc: { "reactionCounts.shares": 1 } },
		);
	await recomputeRank(db, postId);
	return NextResponse.json({ counts: await getCounts(db, postId) });
}

export async function GET(req: NextRequest) {
	const postId = req.nextUrl.searchParams.get("postId");
	if (!postId)
		return NextResponse.json({ error: "postId required" }, { status: 400 });
	const db = await getDb();
	const userId = await getOrCreateUserId();
	const liked = !!(await db
		.collection("reactions")
		.findOne({ postId, userId, type: "like" }));
	return NextResponse.json({ liked, counts: await getCounts(db, postId) });
}

async function getCounts(
	db: Awaited<ReturnType<typeof getDb>>,
	postId: string,
) {
	const post = await db
		.collection("posts")
		.findOne(
			{ _id: asObjectId(postId) },
			{ projection: { reactionCounts: 1 } },
		);
	return post?.reactionCounts ?? { likes: 0, comments: 0, shares: 0 };
}

/** rankScore = 0.6 × momentumScore + 0.25 × reactionVelocity + 0.15 × recency.
 *  reactionVelocity = normalized reactions in the last 24h. recency = age-based 0-1. */
async function recomputeRank(
	db: Awaited<ReturnType<typeof getDb>>,
	postId: string,
) {
	const post = await db
		.collection("posts")
		.findOne({ _id: asObjectId(postId) });
	if (!post) return;
	const momentum = post.project?.momentumScore ?? 0;

	// reactionVelocity: count reactions in last 24h, normalize (10 reactions = 100)
	const since = new Date(Date.now() - 24 * 60 * 60 * 1000);
	const recent = await db
		.collection("reactions")
		.countDocuments({ postId, createdAt: { $gte: since } });
	const reactionVelocity = Math.min(recent / 10, 1) * 100;

	// recency: 1.0 when fresh, 0.0 after 7 days
	const ageDays =
		(Date.now() - new Date(post.postedAt).getTime()) / (1000 * 60 * 60 * 24);
	const recency = Math.max(0, 1 - ageDays / 7) * 100;

	const rankScore =
		Math.round(
			(0.6 * momentum + 0.25 * reactionVelocity + 0.15 * recency) * 10,
		) / 10;
	await db
		.collection("posts")
		.updateOne({ _id: post._id }, { $set: { rankScore } });
}

function asObjectId(id: string) {
	const { ObjectId } = require("mongodb");
	try {
		return new ObjectId(id);
	} catch {
		return id; // fallback if not a valid ObjectId
	}
}
