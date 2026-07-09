/** POST /api/reactions/comments — add a comment (requires display name).
 *  GET /api/reactions/comments?postId=... — list comments for a post. */

import { type NextRequest, NextResponse } from "next/server";
import { getDb } from "@/lib/mongo";
import { setDisplayName } from "@/lib/auth";

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
	const { postId, text, userName } = (await req.json()) as {
		postId?: string;
		text?: string;
		userName?: string;
	};
	if (!postId || !text?.trim()) {
		return NextResponse.json(
			{ error: "postId and text required" },
			{ status: 400 },
		);
	}
	if (text.length > 500) {
		return NextResponse.json(
			{ error: "comment too long (max 500 chars)" },
			{ status: 400 },
		);
	}

	const db = await getDb();
	// Set the display name in a cookie for future comments
	if (userName?.trim()) {
		await setDisplayName(userName.trim().slice(0, 50));
	}
	const name = userName?.trim().slice(0, 50) || "anonymous";

	// Anonymous user id (for spam-dedup if needed)
	const { getOrCreateUserId } = await import("@/lib/auth");
	const userId = await getOrCreateUserId();

	const comment: Comment = {
		postId,
		userId,
		userName: name,
		text: text.trim(),
		type: "comment",
		createdAt: new Date(),
	};
	await db.collection<Comment>("reactions").insertOne(comment);
	await db
		.collection("posts")
		.updateOne(
			{ _id: asObjectId(postId) },
			{ $inc: { "reactionCounts.comments": 1 } },
		);
	return NextResponse.json({ ok: true, comment });
}

export async function GET(req: NextRequest) {
	const postId = req.nextUrl.searchParams.get("postId");
	if (!postId)
		return NextResponse.json({ error: "postId required" }, { status: 400 });
	const db = await getDb();
	const comments = await db
		.collection<Comment>("reactions")
		.find({ postId, type: "comment" })
		.sort({ createdAt: 1 })
		.limit(50)
		.toArray();
	return NextResponse.json({
		comments: comments.map((c) => ({
			userName: c.userName,
			text: c.text,
			createdAt: c.createdAt.toISOString(),
		})),
	});
}

function asObjectId(id: string) {
	const { ObjectId } = require("mongodb");
	try {
		return new ObjectId(id);
	} catch {
		return id;
	}
}
