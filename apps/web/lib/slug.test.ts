import assert from "node:assert/strict";
import test from "node:test";

import {
	legacySlugCandidates,
	legacyUrlPatterns,
	urlToSlug,
} from "./slug.ts";

test("project URLs keep enough source identity to avoid route collisions", () => {
	assert.equal(
		urlToSlug("https://github.com/modiqo/skillspec"),
		"modiqo-skillspec",
	);
	assert.equal(
		urlToSlug("https://github.com/acme/foo_bar.js"),
		"acme-foo-bar-js",
	);
	assert.equal(
		urlToSlug("https://www.youtube.com/watch?v=rp5EwOogWEw"),
		"youtube-rp5ewoogwew",
	);
	assert.equal(
		urlToSlug("https://youtu.be/ElYxdpYi4U0"),
		"youtube-elyxdpyi4u0",
	);
	assert.equal(
		urlToSlug(
			"https://www.reddit.com/r/LocalLLaMA/comments/abc123/a_real_thread/",
		),
		"reddit-localllama-abc123",
	);
	assert.equal(
		urlToSlug("hyperadar://digest/2026-W27"),
		"digest-2026-w27",
	);
});

test("new routes can locate projects stored with legacy slugs", () => {
	assert.ok(
		legacySlugCandidates("reddit-com-r-localllama").includes("localllama"),
	);
	assert.ok(
		legacySlugCandidates("example-com-guides-agent-tools").includes(
			"agent-tools",
		),
	);
	assert.ok(
		legacyUrlPatterns("youtube-rp5ewoogwew").some((pattern) =>
			pattern.test("https://youtu.be/rp5EwOogWEw"),
		),
	);
	assert.ok(
		legacyUrlPatterns("reddit-localllama-abc123").some((pattern) =>
			pattern.test(
				"https://reddit.com/r/LocalLLaMA/comments/abc123/a_real_thread/",
			),
		),
	);
});
