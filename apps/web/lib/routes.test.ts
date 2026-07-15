import assert from "node:assert/strict";
import test from "node:test";

import { projectHref } from "./routes.ts";
import { urlToSlug } from "./slug.ts";

test("project links route digests to digest pages and sources to unique dossiers", () => {
	assert.equal(
		projectHref({ url: "hyperadar://digest/2026-W27" }),
		"/digest/2026-W27",
	);
	assert.equal(
		projectHref({ url: "https://www.youtube.com/watch?v=rp5EwOogWEw" }),
		`/project/${urlToSlug("https://www.youtube.com/watch?v=rp5EwOogWEw")}`,
	);
	assert.equal(
		projectHref(
			{ url: "https://www.reddit.com/r/LocalLLaMA/" },
			"6a52bc70ddc3a66fa488c2ad",
		),
		`/project/${urlToSlug("https://www.reddit.com/r/LocalLLaMA/")}?post=6a52bc70ddc3a66fa488c2ad`,
	);
	assert.equal(
		projectHref({ url: "https://github.com/modiqo/skillspec" }),
		`/project/${urlToSlug("https://github.com/modiqo/skillspec")}`,
	);
});
