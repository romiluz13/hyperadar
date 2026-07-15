import assert from "node:assert/strict";
import test from "node:test";

import {
	comparableSignalSeries,
	evidenceLocator,
} from "./signalSeries.ts";

test("a chart never connects observations with different sources or units", () => {
	const signals = [
		{ source: "hacker_news", metric: "hn_points", value: 298 },
		{ source: "github", metric: "github_stars", value: 47 },
		{ source: "github", metric: "github_stars", value: 53 },
	];

	assert.deepEqual(
		comparableSignalSeries(signals).map((signal) => signal.value),
		[47, 53],
	);
});

test("a broad Reddit community URL is not labeled as the observed source", () => {
	assert.equal(
		evidenceLocator(
			{ source: "reddit", metric: "search_visibility_proxy", value: 70 },
			"https://www.reddit.com/r/LocalLLaMA/",
		),
		undefined,
	);
	assert.equal(
		evidenceLocator(
			{ source: "reddit", metric: "search_visibility_proxy", value: 70 },
			"https://www.reddit.com/r/LocalLLaMA/comments/abc123/thread/",
		),
		"https://www.reddit.com/r/LocalLLaMA/comments/abc123/thread/",
	);
	assert.equal(
		evidenceLocator(
			{
				source: "reddit",
				metric: "search_visibility_proxy",
				value: 70,
				evidenceUrl: "https://www.google.com/search?q=site%3Areddit.com",
			},
			"https://www.reddit.com/r/LocalLLaMA/",
		),
		"https://www.google.com/search?q=site%3Areddit.com",
	);
});
