import assert from "node:assert/strict";
import test from "node:test";

import { operationForComment } from "./commentOperation.ts";

test("a response-loss retry reuses the logical comment operation", () => {
	let sequence = 0;
	const createId = () => `operation-${++sequence}`;
	const first = operationForComment(null, "Evidence matters", "Ada", createId);
	const retry = operationForComment(first, "Evidence matters", "Ada", createId);
	const edited = operationForComment(first, "Different evidence", "Ada", createId);

	assert.equal(retry.operationId, first.operationId);
	assert.notEqual(edited.operationId, first.operationId);
});
