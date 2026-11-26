/** GET /api/daily-digest — returns the latest daily digest for RomBot. */

import { NextResponse } from "next/server";

import { getDb } from "@/lib/mongo";
import { dailyDigestResponse } from "@/lib/dailyDigest";

export const dynamic = "force-dynamic";

export async function GET() {
	const { body, init } = await dailyDigestResponse(getDb);
	return NextResponse.json(body, init);
}
