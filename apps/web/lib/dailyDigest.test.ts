import assert from "node:assert/strict";
import test from "node:test";

import { getLatestDailyDigest } from "./dailyDigest.ts";

/** Minimal mock of a MongoDB collection — only implements findOne. */
function mockCollection(findOneResult: unknown | null) {
	return {
		async findOne(
			_filter: unknown,
			_opts?: { sort?: Record<string, unknown> },
		): Promise<unknown | null> {
			void _filter;
			void _opts;
			return findOneResult;
		},
	};
}

function mockDb(findOneResult: unknown | null) {
	return {
		collection: () => mockCollection(findOneResult),
	};
}

test("getLatestDailyDigest returns the latest daily digest when one exists", async () => {
	const digestDoc = {
		date: "2026-07-23",
		digestType: "daily",
		items: [
			{
				rank: 1,
				agentHandle: "@github-radar",
				title: "awesome-repo",
				url: "https://github.com/owner/awesome-repo",
				kind: "repo",
				blurb: "Stars exploding",
				score: 78,
				stars: 1250,
				velocity: 45,
				contributorCount: 12,
			},
		],
		publicationSyncStatus: "synced",
		evidenceContractVersion: 2,
		createdAt: new Date("2026-07-23T10:00:00Z"),
	};

	const result = await getLatestDailyDigest(mockDb(digestDoc) as never);

	assert.equal(result.date, "2026-07-23");
	assert.equal(result.items.length, 1);
	assert.equal(result.items[0].title, "awesome-repo");
	assert.equal(result.items[0].blurb, "Stars exploding");
});

test("getLatestDailyDigest returns empty items and null date when no digest exists", async () => {
	const result = await getLatestDailyDigest(mockDb(null) as never);

	assert.equal(result.date, null);
	assert.deepEqual(result.items, []);
});

test("getLatestDailyDigest throws on database error (route returns 500)", async () => {
	const errorDb = {
		collection: () => ({
			async findOne(): Promise<never> {
				throw new Error("connection refused");
			},
		}),
	};

	await assert.rejects(
		getLatestDailyDigest(errorDb as never),
		/connection refused/,
	);
});
