import assert from "node:assert/strict";
import test from "node:test";

import {
	distinctProjectPostsPipeline,
	mergeHybridResults,
	recentPostsMatch,
	textOnlySearchPipeline,
	textSearchPostsPipeline,
	vectorSearchProjectsPipeline,
} from "./postQueries.ts";
import * as postQueries from "./postQueries.ts";

test("project deduplication happens before the result limit", () => {
	const pipeline = distinctProjectPostsPipeline(
		{ portSyncStatus: "synced" },
		20,
	);
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

test("vector search pipeline uses $vectorSearch with the projects index", () => {
	const fakeVector = Array.from({ length: 1024 }, () => 0.1);
	const pipeline = vectorSearchProjectsPipeline(fakeVector, 20);

	const vectorSearchStage = pipeline.find((s) => "$vectorSearch" in s);
	assert.ok(vectorSearchStage, "pipeline must include $vectorSearch");
	const vectorSearch = (
		vectorSearchStage as { $vectorSearch: Record<string, unknown> }
	).$vectorSearch;
	assert.equal(vectorSearch.index, "projects_vector_index");
	assert.equal(vectorSearch.queryVector, fakeVector);
});

test("vector search pipeline uses 20x numCandidates per MongoDB best practice", () => {
	const fakeVector = Array.from({ length: 1024 }, () => 0.1);
	const pipeline = vectorSearchProjectsPipeline(fakeVector, 20);
	const vectorSearch = (
		pipeline.find((s) => "$vectorSearch" in s) as {
			$vectorSearch: { numCandidates: number; limit: number };
		}
	).$vectorSearch;

	// numCandidates should be at least 10x the limit (MongoDB recommends 20x)
	const ratio = vectorSearch.numCandidates / vectorSearch.limit;
	assert.ok(ratio >= 10, `numCandidates ratio ${ratio} should be >= 10`);
});

test("vector search pipeline joins posts via $lookup", () => {
	const fakeVector = Array.from({ length: 1024 }, () => 0.1);
	const pipeline = vectorSearchProjectsPipeline(fakeVector, 20);

	const lookupStage = pipeline.find((s) => "$lookup" in s);
	assert.ok(lookupStage, "pipeline must include $lookup to posts");
	const lookup = (lookupStage as { $lookup: Record<string, unknown> }).$lookup;
	assert.equal(lookup.from, "posts");
});

test("text search pipeline uses $search with the posts index", () => {
	const pipeline = textSearchPostsPipeline("agent security", 20);

	const searchStage = pipeline.find((s) => "$search" in s);
	assert.ok(searchStage, "pipeline must include $search");
	const search = (searchStage as { $search: Record<string, unknown> }).$search;
	assert.equal(search.index, "posts_search_index");
});

test("text search pipeline uses Atlas Search text operator for filter, not MQL $eq", () => {
	const pipeline = textSearchPostsPipeline("agent security", 20);
	const searchStage = pipeline.find((s) => "$search" in s) as {
		$search: { compound: { filter: unknown[] } };
	};
	const filterClauses = searchStage.$search.compound.filter;

	// Each filter clause must use Atlas Search operators (text, equals, range)
	// NOT MQL operators like $eq
	for (const clause of filterClauses) {
		const keys = Object.keys(clause as Record<string, unknown>);
		assert.ok(
			keys.includes("text") ||
				keys.includes("equals") ||
				keys.includes("range"),
			`filter clause must use Atlas Search operator, got keys: ${keys.join(",")}`,
		);
	}
});

test("text search pipeline deduplicates by project URL", () => {
	const pipeline = textSearchPostsPipeline("test", 20);
	const hasGroup = pipeline.some((s) => "$group" in s);
	assert.ok(hasGroup, "text search pipeline must deduplicate via $group");
});

test("text-only fallback pipeline uses $search without $vectorSearch", () => {
	const pipeline = textOnlySearchPipeline("test query", 20);

	const searchIndex = pipeline.findIndex((stage) => "$search" in stage);
	const vectorIndex = pipeline.findIndex((stage) => "$vectorSearch" in stage);

	assert.ok(searchIndex >= 0, "text-only pipeline must include $search");
	assert.equal(
		vectorIndex,
		-1,
		"text-only pipeline must NOT include $vectorSearch",
	);
});

test("text-only fallback uses Atlas Search text operator for filter", () => {
	const pipeline = textOnlySearchPipeline("test", 20);
	const searchStage = pipeline.find((s) => "$search" in s) as {
		$search: { compound: { filter: unknown[] } };
	};
	const filterClauses = searchStage.$search.compound.filter;

	for (const clause of filterClauses) {
		const keys = Object.keys(clause as Record<string, unknown>);
		assert.ok(
			keys.includes("text") ||
				keys.includes("equals") ||
				keys.includes("range"),
			`filter clause must use Atlas Search operator, got keys: ${keys.join(",")}`,
		);
	}
});

test("mergeHybridResults fuses vector and text results by RRF score", () => {
	const vectorResults = [
		{ project: { url: "https://a.com" }, body: "a" },
		{ project: { url: "https://b.com" }, body: "b" },
		{ project: { url: "https://c.com" }, body: "c" },
	];
	const textResults = [
		{ project: { url: "https://b.com" }, body: "b" },
		{ project: { url: "https://d.com" }, body: "d" },
	];

	const merged = mergeHybridResults(vectorResults, textResults);
	const urls = merged.map((p) => p.project.url);

	// b appears in both lists — should rank first
	assert.equal(urls[0], "https://b.com");
	// all 4 unique URLs should be present
	assert.equal(urls.length, 4);
	assert.ok(urls.includes("https://a.com"));
	assert.ok(urls.includes("https://c.com"));
	assert.ok(urls.includes("https://d.com"));
});

test("mergeHybridResults handles empty input lists", () => {
	assert.deepEqual(mergeHybridResults([], []), []);
	assert.deepEqual(
		mergeHybridResults([], [{ project: { url: "https://a.com" }, body: "a" }]),
		[{ project: { url: "https://a.com" }, body: "a" }],
	);
});

test("mergeHybridResults deduplicates by project URL", () => {
	const vectorResults = [
		{ project: { url: "https://a.com" }, body: "from-vector" },
	];
	const textResults = [
		{ project: { url: "https://a.com" }, body: "from-text" },
	];

	const merged = mergeHybridResults(vectorResults, textResults);
	// Should have exactly 1 result (deduped by URL)
	assert.equal(merged.length, 1);
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
