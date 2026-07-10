/** POST /api/reactions/comments — add a comment (requires display name).
 *  GET /api/reactions/comments?postId=... — list comments for a post. */

import { type NextRequest, NextResponse } from "next/server";
import { getDb } from "@/lib/mongo";
import { setDisplayName, getOrCreateUserId } from "@/lib/auth";
import { toObjectId, isValidObjectId } from "@/lib/objectId";

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
	const body = await req.json();
	const { postId, text, userName } = body as {
		postId?: unknown;
		text?: unknown;
		userName?: unknown;
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

	try {
		const db = await getDb();
		if (typeof userName === "string" && userName.trim()) {
			await setDisplayName(userName.trim().slice(0, 50));
		}
		const name =
			(typeof userName === "string" ? userName.trim().slice(0, 50) : "") ||
			"anonymous";
		const userId = await getOrCreateUserId();

		await db.collection<Comment>("reactions").insertOne({
			postId,
			userId,
			userName: name,
			text: text.trim(),
			type: "comment",
			createdAt: new Date(),
		});
		await db
			.collection("posts")
			.updateOne(
				{ _id: toObjectId(postId) },
				{ $inc: { "reactionCounts.comments": 1 } },
			);
		return NextResponse.json({
			ok: true,
			comment: { userName: name, text: text.trim() },
		});
	} catch (err) {
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
	} catch (err) {
		console.error("comments GET error:", err);
		return NextResponse.json({ error: "internal error" }, { status: 500 });
	}
}
