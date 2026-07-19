import assert from "node:assert/strict";
import test from "node:test";

import {
	distinctProjectPostsPipeline,
	recentPostsMatch,
	searchPostsPipeline,
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

test("search pipeline filters to synced posts and searches across fields", () => {
	const pipeline = searchPostsPipeline("agent", 20);

	const matchIndex = pipeline.findIndex((stage) => "$match" in stage);
	const groupIndex = pipeline.findIndex((stage) => "$group" in stage);
	const limitIndex = pipeline.findIndex((stage) => "$limit" in stage);

	assert.ok(matchIndex >= 0, "pipeline must include $match");
	assert.ok(groupIndex > matchIndex, "$group must come after $match");
	assert.ok(limitIndex > groupIndex, "$limit must come after $group");

	const match = (pipeline[matchIndex] as { $match: Record<string, unknown> }).$match;
	assert.equal(match.portSyncStatus, "synced");
	assert.ok(match.$or, "pipeline must search across multiple fields");
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
