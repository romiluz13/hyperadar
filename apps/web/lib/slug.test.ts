import assert from "node:assert/strict";
import test from "node:test";

import {
	legacySlugCandidates,
	legacyUrlPatterns,
	legacyUrlToSlug,
	urlToSlug,
} from "./slug.ts";

test("project URLs keep enough source identity to avoid route collisions", () => {
	assert.match(
		urlToSlug("https://github.com/modiqo/skillspec"),
		/^modiqo-skillspec-[0-9a-f]{16}$/,
	);
	assert.match(
		urlToSlug("https://github.com/acme/foo_bar.js"),
		/^acme-foo-bar-js-[0-9a-f]{16}$/,
	);
	assert.match(
		urlToSlug("https://www.youtube.com/watch?v=rp5EwOogWEw"),
		/^youtube-rp5ewoogwew-[0-9a-f]{16}$/,
	);
	assert.match(
		urlToSlug("https://youtu.be/ElYxdpYi4U0"),
		/^youtube-elyxdpyi4u0-[0-9a-f]{16}$/,
	);
	assert.match(
		urlToSlug(
			"https://www.reddit.com/r/LocalLLaMA/comments/abc123/a_real_thread/",
		),
		/^reddit-localllama-abc123-[0-9a-f]{16}$/,
	);
	assert.match(
		urlToSlug("hyperadar://digest/2026-W27"),
		/^digest-2026-w27-[0-9a-f]{16}$/,
	);
	assert.equal(
		legacyUrlToSlug("https://github.com/modiqo/skillspec"),
		"modiqo-skillspec",
	);
});

test("project route identities cannot collide when path boundaries move", () => {
	const first = urlToSlug("https://github.com/foo-bar/baz");
	const second = urlToSlug("https://github.com/foo/bar-baz");

	assert.notEqual(first, second);
	assert.match(first, /^foo-bar-baz-[0-9a-f]{16}$/);
	assert.match(second, /^foo-bar-baz-[0-9a-f]{16}$/);
});

test("new routes can locate projects stored with legacy slugs", () => {
	assert.ok(
		legacySlugCandidates("reddit-com-r-localllama-1234abcd").includes(
			"reddit-com-r-localllama",
		),
	);
	assert.ok(
		legacySlugCandidates("example-com-guides-agent-tools").includes(
			"agent-tools",
		),
	);
	assert.ok(
		legacyUrlPatterns("youtube-rp5ewoogwew-1234abcd").some((pattern) =>
			pattern.test("https://youtu.be/rp5EwOogWEw"),
		),
	);
	assert.ok(
		legacyUrlPatterns("reddit-localllama-abc123-1234abcd").some((pattern) =>
			pattern.test(
				"https://reddit.com/r/LocalLLaMA/comments/abc123/a_real_thread/",
			),
		),
	);
});
