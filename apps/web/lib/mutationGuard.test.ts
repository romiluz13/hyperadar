import assert from "node:assert/strict";
import test from "node:test";

import {
	clientAddress,
	isValidOperationId,
	mutationIdentityKey,
	mutationRequestError,
	reactionRetryAfter,
} from "./mutationGuard.ts";

function request(headers: HeadersInit) {
	return new Request("https://hyperadar.example/api/reactions", {
		method: "POST",
		headers,
	});
}

test("social mutations require same-origin JSON requests", () => {
	assert.equal(
		mutationRequestError(
			request({
				origin: "https://hyperadar.example",
				"content-type": "application/json; charset=utf-8",
			}),
		),
		null,
	);
	assert.match(
		mutationRequestError(
			request({
				origin: "https://attacker.example",
				"content-type": "application/json",
			}),
		) ?? "",
		/origin/i,
	);
	assert.match(
		mutationRequestError(
			request({ origin: "https://hyperadar.example" }),
		) ?? "",
		/JSON/i,
	);
	assert.match(
		mutationRequestError(
			request({
				origin: "https://attacker.example",
				"content-type": "application/json",
				"x-forwarded-host": "attacker.example",
			}),
		) ?? "",
		/origin/i,
	);
});

test("replay identities are UUIDs", () => {
	assert.equal(isValidOperationId("d9428888-122b-11e1-b85c-61cd3cbb3210"), true);
	assert.equal(isValidOperationId("share-again"), false);
});

test("rate limiting ignores a spoofable first forwarded address", () => {
	const forwarded = request({
		"x-forwarded-for": "198.51.100.7, 203.0.113.42",
	});
	assert.equal(clientAddress(forwarded), "203.0.113.42");
	assert.equal(reactionRetryAfter("like"), 60);
	assert.equal(reactionRetryAfter("share"), 600);
});

test("Vercel deployments use the platform-owned forwarded address", () => {
	const previous = process.env.VERCEL;
	process.env.VERCEL = "1";
	try {
		assert.equal(
			clientAddress(
				request({
					"x-forwarded-for": "198.51.100.7",
					"x-vercel-forwarded-for": "203.0.113.42",
				}),
			),
			"203.0.113.42",
		);
	} finally {
		if (previous === undefined) delete process.env.VERCEL;
		else process.env.VERCEL = previous;
	}
});

test("stored network identities require a secret and resist IPv4 enumeration", () => {
	const address = "203.0.113.42";
	assert.notEqual(
		mutationIdentityKey(address, "first-secret"),
		mutationIdentityKey(address, "second-secret"),
	);
	assert.equal(mutationIdentityKey(address, "first-secret").length, 32);
});
