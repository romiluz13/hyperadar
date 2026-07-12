import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";

test("MongoDB configuration is checked on use, not while pages are imported", () => {
	const result = spawnSync(
		process.execPath,
		[
			"--no-warnings",
			"--experimental-strip-types",
			"--input-type=module",
			"--eval",
			'delete process.env.MONGODB_URI; await import("./lib/mongo.ts");',
		],
		{
			cwd: process.cwd(),
			encoding: "utf8",
			env: { ...process.env, MONGODB_URI: undefined },
		},
	);

	assert.equal(result.status, 0, result.stderr);
});
