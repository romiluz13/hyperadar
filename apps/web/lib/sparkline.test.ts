import assert from "node:assert/strict";
import test from "node:test";

import { sparklinePoints } from "./sparkline.ts";

test("sparkline geometry keeps strokes inset from every viewport edge", () => {
	const points = sparklinePoints([10, 30, 20], 200, 40, 3);

	assert.deepEqual(points, [
		[3, 37],
		[100, 3],
		[197, 20],
	]);
});

test("flat series remain centered inside the drawing area", () => {
	assert.deepEqual(sparklinePoints([5, 5], 100, 20, 2), [
		[2, 10],
		[98, 10],
	]);
});
