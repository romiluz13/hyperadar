import assert from "node:assert/strict";
import test from "node:test";

import { operationForShare } from "./shareOperation.ts";

test("a response-loss retry reuses the logical share operation", () => {
	let created = 0;
	const createId = () => `share-${++created}`;
	const first = operationForShare(null, createId);
	const retry = operationForShare(first, createId);

	assert.equal(first, "share-1");
	assert.equal(retry, first);
});
