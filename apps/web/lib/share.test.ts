import assert from "node:assert/strict";
import test from "node:test";

import { absoluteShareUrl } from "./share.ts";

test("sharing a feed item copies its dossier instead of the feed", () => {
	assert.equal(
		absoluteShareUrl("/project/youtube-rp5ewoogwew", "https://hyperadar.dev"),
		"https://hyperadar.dev/project/youtube-rp5ewoogwew",
	);
});
