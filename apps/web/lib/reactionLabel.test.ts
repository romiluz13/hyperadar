import assert from "node:assert/strict";
import test from "node:test";

import { reactionLabel } from "./reactionLabel.ts";

test("empty reactions invite participation instead of advertising zero activity", () => {
	assert.equal(reactionLabel(0, "Like"), "Like");
	assert.equal(reactionLabel(0, "Discuss"), "Discuss");
	assert.equal(reactionLabel(0, "Share"), "Share");
	assert.equal(reactionLabel(7, "Like"), "7");
});
