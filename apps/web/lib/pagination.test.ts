import assert from "node:assert/strict";
import test from "node:test";

import { archiveWindow } from "./pagination.ts";

test("archive pagination rejects invalid input and clamps past the final page", () => {
	assert.deepEqual(archiveWindow("not-a-page", 45), {
		page: 1,
		totalPages: 3,
		skip: 0,
		start: 1,
		end: 20,
	});
	assert.deepEqual(archiveWindow("99", 45), {
		page: 3,
		totalPages: 3,
		skip: 40,
		start: 41,
		end: 45,
	});
	assert.deepEqual(archiveWindow("2", 0), {
		page: 1,
		totalPages: 1,
		skip: 0,
		start: 0,
		end: 0,
	});
});
