import assert from "node:assert/strict";
import test from "node:test";

import {
	distinctProjectPostsPipeline,
	recentPostsMatch,
} from "./postQueries.ts";
import * as postQueries from "./postQueries.ts";

test("project deduplication happens before the result limit", () => {
	const pipeline = distinctProjectPostsPipeline({ portSyncStatus: "synced" }, 20);
	const groupIndex = pipeline.findIndex((stage) => "$group" in stage);
	const limitIndex = pipeline.findIndex((stage) => "$limit" in stage);

	assert.ok(groupIndex >= 0);
	assert.ok(limitIndex > groupIndex);
});

test("current feed queries require a recent publication timestamp", () => {
	const since = new Date("2026-07-06T00:00:00.000Z");
	assert.deepEqual(recentPostsMatch({ portSyncStatus: "synced" }, since), {
		portSyncStatus: "synced",
		postedAt: { $gte: since },
	});
});

test("weekly evidence queries stay inside the digest measurement window", () => {
	const publicationWindowMatch = (
		postQueries as {
			publicationWindowMatch?: (
				match: Record<string, unknown>,
				since: Date,
				until: Date,
			) => Record<string, unknown>;
		}
	).publicationWindowMatch;
	const since = new Date("2026-07-06T12:00:00.000Z");
	const until = new Date("2026-07-13T12:00:00.000Z");
	assert.equal(typeof publicationWindowMatch, "function");
	assert.deepEqual(
		publicationWindowMatch?.({ portSyncStatus: "synced" }, since, until),
		{
			portSyncStatus: "synced",
			postedAt: { $gte: since, $lte: until },
		},
	);
});
