import type { Db } from "mongodb";

import { PUBLIC_DIGEST_FILTER } from "./publication.ts";

export type DailyDigestItem = {
	rank: number;
	agentHandle: string;
	title: string;
	url: string;
	kind: string;
	blurb: string;
	score: number;
	stars: number | null;
	velocity: number | null;
	contributorCount: number | null;
};

export type DailyDigest = {
	date: string | null;
	items: DailyDigestItem[];
};

/**
 * Query MongoDB for the latest daily digest.
 * Returns { date: null, items: [] } when no digest exists
 * (RomBot treats empty as "stay silent").
 */
export async function getLatestDailyDigest(db: Db): Promise<DailyDigest> {
	const digest = await db
		.collection("digests")
		.findOne(
			{ digestType: "daily", ...PUBLIC_DIGEST_FILTER },
			{ sort: { createdAt: -1 } },
		);
	if (!digest) {
		return { date: null, items: [] };
	}
	return {
		date: digest.date ?? null,
		items: (digest.items ?? []) as DailyDigestItem[],
	};
}
