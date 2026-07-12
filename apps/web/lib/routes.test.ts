import assert from "node:assert/strict";
import test from "node:test";

import { projectHref } from "./routes.ts";

test("project links route digests to digest pages and sources to unique dossiers", () => {
	assert.equal(
		projectHref({ url: "hyperadar://digest/2026-W27" }),
		"/digest/2026-W27",
	);
	assert.equal(
		projectHref({ url: "https://www.youtube.com/watch?v=rp5EwOogWEw" }),
		"/project/youtube-rp5ewoogwew",
	);
	assert.equal(
		projectHref(
			{ url: "https://www.reddit.com/r/LocalLLaMA/" },
			"6a52bc70ddc3a66fa488c2ad",
		),
		"/project/reddit-com-r-localllama?post=6a52bc70ddc3a66fa488c2ad",
	);
	assert.equal(
		projectHref({ url: "https://github.com/modiqo/skillspec" }),
		"/project/modiqo-skillspec",
	);
});
