/** GET /api/daily-digest — returns the latest daily digest for RomBot. */

import { NextResponse } from "next/server";

import { getDb } from "@/lib/mongo";
import { getLatestDailyDigest } from "@/lib/dailyDigest";

export const dynamic = "force-dynamic";

export async function GET() {
	try {
		const db = await getDb();
		const result = await getLatestDailyDigest(db);
		return NextResponse.json(result, { status: 200 });
	} catch (err) {
		console.error("daily-digest GET error:", err);
		return NextResponse.json({ error: "internal error" }, { status: 500 });
	}
}
