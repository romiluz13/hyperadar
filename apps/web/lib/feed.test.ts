import assert from "node:assert/strict";
import test from "node:test";

import {
	feedEvidenceLabel,
	sourceFamily,
	uniqueProjects,
	uniqueProjectsExcluding,
} from "./feed.ts";

test("the discovery feed shows one strongest post per project", () => {
	const posts = [
		{ id: "top-a", project: { url: "https://example.com/a" } },
		{ id: "duplicate-a", project: { url: "https://example.com/a" } },
		{ id: "top-b", project: { url: "https://example.com/b" } },
	];

	assert.deepEqual(
		uniqueProjects(posts, 20).map((post) => post.id),
		["top-a", "top-b"],
	);
});

test("digest sections never repeat a project used by an earlier section", () => {
	const posts = [
		{ id: "shared", project: { url: "https://example.com/shared" } },
		{ id: "new", project: { url: "https://example.com/new" } },
	];

	assert.deepEqual(
		uniqueProjectsExcluding(
			posts,
			new Set(["https://example.com/shared"]),
			3,
		).map((post) => post.id),
		["new"],
	);
});

test("feed evidence labels lifetime averages without a growth arrow", () => {
	assert.equal(
		feedEvidenceLabel(
			"GitHub stars=700; avg since creation=350/wk; 6-week sustained=not proven",
		),
		"avg 350★/wk since creation",
	);
	assert.equal(feedEvidenceLabel("stars=700, +350/wk"), null);
	assert.equal(
		feedEvidenceLabel("HN points=298; HN comments=42"),
		"298 HN points",
	);
	assert.equal(
		feedEvidenceLabel("Google SERP rank=3; visibility proxy=70/100"),
		"Google rank 3 · Reddit visibility proxy 70/100",
	);
	assert.equal(
		feedEvidenceLabel(
			"Historical Reddit engagement snapshot; exact count not re-verified",
		),
		"Reddit search snapshot · exact count unverified",
	);
});

test("source aliases collapse to one independent source family", () => {
	assert.equal(sourceFamily("reddit"), "reddit");
	assert.equal(sourceFamily("reddit_search"), "reddit");
	assert.equal(sourceFamily("reddit-serp"), "reddit");
	assert.equal(sourceFamily("hn"), "hacker_news");
	assert.equal(sourceFamily("hacker_news"), "hacker_news");
});
