import assert from "node:assert/strict";
import test from "node:test";

import { commentFailureMessage } from "./commentResponse.ts";

test("comment failures give recovery guidance that matches the response", () => {
	assert.equal(
		commentFailureMessage(429, "600"),
		"Too many comments from this network. Try again in 10 minutes.",
	);
	assert.equal(
		commentFailureMessage(409, null),
		"This comment changed while it was being retried. Review it and post again.",
	);
	assert.equal(
		commentFailureMessage(500, null),
		"Comments are unavailable right now. Try again later.",
	);
});
