/** POST /api/reactions — like or share a post (anonymous, cookie-dedup).
 *  GET /api/reactions?postId=... — get a post's reaction counts + liked state. */

import { type NextRequest, NextResponse } from "next/server";
import { getDb } from "@/lib/mongo";
import { getOrCreateUserId } from "@/lib/auth";
import {
	consumeMutationRateLimit,
	isValidOperationId,
	mutationNetworkIdentity,
	mutationRequestError,
	MutationRateLimitError,
	reactionRetryAfter,
} from "@/lib/mutationGuard";
import { toObjectId, isValidObjectId } from "@/lib/objectId";
import { publishedPostFilter } from "@/lib/publication";
import {
	addShare,
	getReactionCounts,
	PostNotFoundError,
	ReactionConflictError,
	setLike,
} from "@/lib/reactionPersistence";

export const dynamic = "force-dynamic";

type Reaction = {
	postId: string;
	userId: string;
	rankIdentity?: string;
	type: "like" | "share";
	createdAt: Date;
};

export async function POST(req: NextRequest) {
	const requestError = mutationRequestError(req);
	if (requestError) {
		return NextResponse.json(
			{ error: requestError },
			{ status: requestError.includes("JSON") ? 415 : 403 },
		);
	}
	let body: unknown;
	try {
		body = await req.json();
	} catch {
		return NextResponse.json({ error: "valid JSON required" }, { status: 400 });
	}
	if (!body || typeof body !== "object" || Array.isArray(body)) {
		return NextResponse.json({ error: "JSON object required" }, { status: 400 });
	}
	const { postId, type, liked, operationId } = body as {
		postId?: unknown;
		type?: unknown;
		liked?: unknown;
		operationId?: unknown;
	};

	// Runtime validation — prevent NoSQL injection (objects like {$ne:""} must not pass)
	if (
		!isValidObjectId(postId) ||
		typeof type !== "string" ||
		!["like", "share"].includes(type)
	) {
		return NextResponse.json(
			{ error: "postId (valid ObjectId) and type (like|share) required" },
			{ status: 400 },
		);
	}
	if (type === "like" && typeof liked !== "boolean") {
		return NextResponse.json(
			{ error: "liked boolean required for a like" },
			{ status: 400 },
		);
	}
	if (type === "share" && !isValidOperationId(operationId)) {
		return NextResponse.json(
			{ error: "operationId UUID required for a share" },
			{ status: 400 },
		);
	}

	const userId = await getOrCreateUserId();
	const rankIdentity = mutationNetworkIdentity(req);

	try {
		const db = await getDb();
		await consumeMutationRateLimit(
			db,
			req,
			userId,
			type,
			type === "share" ? 8 : 30,
			type === "share" ? 10 * 60_000 : 60_000,
		);
		if (type === "like") {
			return NextResponse.json(
				await setLike(postId, userId, rankIdentity, liked as boolean),
			);
		}

		return NextResponse.json({
			counts: await addShare(
				postId,
				userId,
				rankIdentity,
				operationId as string,
			),
		});
	} catch (err) {
		if (err instanceof PostNotFoundError) {
			return NextResponse.json({ error: "post not found" }, { status: 404 });
		}
		if (err instanceof ReactionConflictError) {
			return NextResponse.json({ error: "operation conflict" }, { status: 409 });
		}
		if (err instanceof MutationRateLimitError) {
			return NextResponse.json(
				{ error: "too many reactions; try again shortly" },
				{
					status: 429,
					headers: { "Retry-After": String(reactionRetryAfter(type)) },
				},
			);
		}
		console.error("reactions POST error:", err);
		return NextResponse.json({ error: "internal error" }, { status: 500 });
	}
}

export async function GET(req: NextRequest) {
	const postIds = [
		...new Set(
			(req.nextUrl.searchParams.get("postIds") ?? "")
				.split(",")
				.filter(Boolean),
		),
	];
	if (postIds.length > 0) {
		if (postIds.length > 20 || postIds.some((postId) => !isValidObjectId(postId))) {
			return NextResponse.json(
				{ error: "postIds must contain at most 20 valid ObjectIds" },
				{ status: 400 },
			);
		}
		try {
			const db = await getDb();
			const publishedPosts = await db
				.collection("posts")
				.find(
					publishedPostFilter({ _id: { $in: postIds.map(toObjectId) } }),
					{ projection: { _id: 1 } },
				)
				.toArray();
			const publishedPostIds = publishedPosts.map((post) => post._id.toString());
			if (publishedPostIds.length === 0) {
				return NextResponse.json({ likedPostIds: [] });
			}
			const userId = await getOrCreateUserId();
			const rankIdentity = mutationNetworkIdentity(req);
			const reactions = await db
				.collection<Reaction>("reactions")
				.find(
					{
						postId: { $in: publishedPostIds },
						type: "like",
						$or: [{ userId }, { rankIdentity }],
					},
					{ projection: { _id: 0, postId: 1 } },
				)
				.toArray();
			return NextResponse.json({
				likedPostIds: reactions.map((reaction) => reaction.postId),
			});
		} catch (err) {
			console.error("reactions batch GET error:", err);
			return NextResponse.json({ error: "internal error" }, { status: 500 });
		}
	}

	const postId = req.nextUrl.searchParams.get("postId");
	if (!postId || !isValidObjectId(postId)) {
		return NextResponse.json(
			{ error: "valid postId required" },
			{ status: 400 },
		);
	}
	try {
		const db = await getDb();
		const publishedPost = await db.collection("posts").findOne(
			publishedPostFilter({ _id: toObjectId(postId) }),
			{ projection: { _id: 1 } },
		);
		if (!publishedPost) {
			return NextResponse.json({ error: "post not found" }, { status: 404 });
		}
		const userId = await getOrCreateUserId();
		const rankIdentity = mutationNetworkIdentity(req);
		const liked = Boolean(
			await db
				.collection("reactions")
				.findOne({ postId, type: "like", $or: [{ userId }, { rankIdentity }] }),
		);
		return NextResponse.json({
			liked,
			counts: await getReactionCounts(postId),
		});
	} catch (err) {
		console.error("reactions GET error:", err);
		return NextResponse.json({ error: "internal error" }, { status: 500 });
	}
}
