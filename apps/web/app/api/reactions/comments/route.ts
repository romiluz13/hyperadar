/** POST /api/reactions/comments — add an optionally named public comment.
 *  GET /api/reactions/comments?postId=... — list comments for a post. */

import { type NextRequest, NextResponse } from "next/server";
import { getDb } from "@/lib/mongo";
import { setDisplayName, getOrCreateUserId } from "@/lib/auth";
import {
	consumeMutationRateLimit,
	isValidOperationId,
	mutationNetworkIdentity,
	mutationRequestError,
	MutationRateLimitError,
} from "@/lib/mutationGuard";
import { toObjectId, isValidObjectId } from "@/lib/objectId";
import { publishedPostFilter } from "@/lib/publication";
import {
	addComment,
	PostNotFoundError,
	ReactionConflictError,
} from "@/lib/reactionPersistence";

export const dynamic = "force-dynamic";

type Comment = {
	postId: string;
	userId: string;
	userName: string;
	text: string;
	type: "comment";
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
	const { postId, text, userName, operationId } = body as {
		postId?: unknown;
		text?: unknown;
		userName?: unknown;
		operationId?: unknown;
	};

	// Runtime validation — prevent NoSQL injection
	if (!isValidObjectId(postId) || typeof text !== "string" || !text.trim()) {
		return NextResponse.json(
			{ error: "postId (valid ObjectId) and text required" },
			{ status: 400 },
		);
	}
	if (text.length > 500) {
		return NextResponse.json(
			{ error: "comment too long (max 500 chars)" },
			{ status: 400 },
		);
	}
	if (!isValidOperationId(operationId)) {
		return NextResponse.json(
			{ error: "operationId UUID required" },
			{ status: 400 },
		);
	}

	try {
		if (typeof userName === "string" && userName.trim()) {
			await setDisplayName(userName.trim().slice(0, 50));
		}
		const name =
			(typeof userName === "string" ? userName.trim().slice(0, 50) : "") ||
			"anonymous";
		const userId = await getOrCreateUserId();
		const rankIdentity = mutationNetworkIdentity(req);
		const db = await getDb();
		await consumeMutationRateLimit(
			db,
			req,
			userId,
			"comment",
			5,
			10 * 60_000,
		);
		const result = await addComment(
			postId,
			userId,
			rankIdentity,
			name,
			text.trim(),
			operationId,
		);
		return NextResponse.json({
			ok: true,
			...result,
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
				{ error: "too many comments; try again later" },
				{ status: 429, headers: { "Retry-After": "600" } },
			);
		}
		console.error("comments POST error:", err);
		return NextResponse.json({ error: "internal error" }, { status: 500 });
	}
}

export async function GET(req: NextRequest) {
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
		const comments = await db
			.collection<Comment>("reactions")
			.find({ postId, type: "comment" })
			.sort({ createdAt: -1 })
			.limit(50)
			.toArray();
		comments.reverse();
		return NextResponse.json({
			comments: comments.map((c) => ({
				userName: c.userName,
				text: c.text,
				createdAt: c.createdAt.toISOString(),
			})),
		});
	} catch (err) {
		console.error("comments GET error:", err);
		return NextResponse.json({ error: "internal error" }, { status: 500 });
	}
}
