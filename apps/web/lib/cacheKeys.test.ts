import assert from "node:assert/strict";
import test from "node:test";

import {
	CACHE_KEY_MAX_PAGE,
	CACHE_KEY_MAX_PARAM_CHARS,
	cacheKeyPage,
	cacheKeyParam,
	cacheKeyPostId,
} from "./cacheKeys.ts";

test("a page becomes a positive integer no larger than the sane maximum", () => {
	assert.equal(cacheKeyPage(undefined), 1);
	assert.equal(cacheKeyPage("3"), 3);
	assert.equal(cacheKeyPage("3abc"), 3);
	assert.equal(cacheKeyPage("not-a-page"), 1);
	assert.equal(cacheKeyPage("0"), 1);
	assert.equal(cacheKeyPage("-4"), 1);
	assert.equal(cacheKeyPage("9".repeat(400)), CACHE_KEY_MAX_PAGE);
	assert.equal(cacheKeyPage(String(CACHE_KEY_MAX_PAGE + 1)), CACHE_KEY_MAX_PAGE);
});

test("a post is kept only as a 24-hex ObjectId", () => {
	assert.equal(
		cacheKeyPostId("6a52bc70ddc3a66fa488c2ad"),
		"6a52bc70ddc3a66fa488c2ad",
	);
	assert.equal(cacheKeyPostId(undefined), undefined);
	assert.equal(cacheKeyPostId(""), undefined);
	assert.equal(cacheKeyPostId("abcdefghijkl"), undefined);
	assert.equal(cacheKeyPostId("6a52bc70ddc3a66fa488c2adX"), undefined);
	assert.equal(cacheKeyPostId("x".repeat(10_000)), undefined);
});

test("a handle or slug is capped at 200 characters", () => {
	assert.equal(CACHE_KEY_MAX_PARAM_CHARS, 200);
	assert.equal(cacheKeyParam("@github-radar"), "@github-radar");
	assert.equal(cacheKeyParam("s".repeat(10_000)), "s".repeat(200));
});
