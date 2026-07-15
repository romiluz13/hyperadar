import assert from "node:assert/strict";
import test from "node:test";

import { ObjectId } from "mongodb";

import {
	PUBLIC_POST_FILTER,
	publishedPostFilter,
	publishedSignalFilter,
} from "./publication.ts";
import * as publication from "./publication.ts";

test("posts stay private until their Port twin has synchronized", () => {
	assert.deepEqual(PUBLIC_POST_FILTER, {
		portSyncStatus: "synced",
		evidenceContractVersion: 2,
		legacyDuplicateOf: { $exists: false },
	});
});

test("digests stay private unless their Port-backed publication is synchronized", () => {
	assert.deepEqual(
		(publication as { PUBLIC_DIGEST_FILTER?: unknown }).PUBLIC_DIGEST_FILTER,
		{ publicationSyncStatus: "synced", evidenceContractVersion: 2 },
	);
});

test("related-project discovery excludes internal editorial wrappers", () => {
	const sourceUrls = (
		publication as {
			publicSourceProjectUrls?: (urls: string[]) => string[];
		}
	).publicSourceProjectUrls;
	assert.equal(typeof sourceUrls, "function");
	assert.deepEqual(
		sourceUrls?.([
			"https://github.com/example/project",
			"hyperadar://digest/2026-W28",
			"javascript:alert(1)",
		]),
		["https://github.com/example/project"],
	);
});

test("publication state composes with an exact post lookup", () => {
	assert.deepEqual(publishedPostFilter({ _id: "post-id" }), {
		_id: "post-id",
		portSyncStatus: "synced",
		evidenceContractVersion: 2,
		legacyDuplicateOf: { $exists: false },
	});
});

test("post-linked signals are readable only after their post is published", () => {
	const canonicalSignalId = new ObjectId();
	const verifiedSignalId = new ObjectId();
	assert.deepEqual(
		publishedSignalFilter(
			"https://example.com/project",
			[canonicalSignalId],
			[verifiedSignalId],
		),
		{
			projectId: "https://example.com/project",
			_id: { $in: [canonicalSignalId, verifiedSignalId] },
		},
	);
});
