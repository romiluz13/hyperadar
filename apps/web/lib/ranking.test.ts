import assert from "node:assert/strict";
import test from "node:test";

import { rankWithHumanSignal } from "./ranking.ts";

test("the first human reaction cannot demote a source score", () => {
	assert.equal(rankWithHumanSignal(80, 0), 80);
	assert.equal(rankWithHumanSignal(80, 1), 82);
	assert.equal(rankWithHumanSignal(98, 20), 100);
});
