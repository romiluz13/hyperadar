import assert from "node:assert/strict";
import test from "node:test";

import { getLatestDailyDigest } from "./dailyDigest.ts";
import { SOURCE_AGENT_HANDLES } from "./dailyDigest.ts";

type RunDoc = {
	agentHandle: string;
	finishedAt: Date;
	ok: boolean;
};

function healthyRuns(): RunDoc[] {
	const now = new Date();
	return SOURCE_AGENT_HANDLES.map((agentHandle) => ({
		agentHandle,
		finishedAt: now,
		ok: true,
	}));
}

/** Minimal mock of a MongoDB collection — only implements findOne. */
function mockDb(digestResult: unknown | null, runs: RunDoc[] = healthyRuns()) {
	return {
		collection(name: string) {
			if (name === "agent_runs") {
				return {
					async findOne(filter: {
						agentHandle?: string;
						ok?: boolean;
					}): Promise<RunDoc | null> {
						const matching = runs
							.filter((run) => run.agentHandle === filter.agentHandle)
							.filter((run) =>
								filter.ok === undefined ? true : run.ok === filter.ok,
							)
							.sort(
								(a, b) => b.finishedAt.getTime() - a.finishedAt.getTime(),
							);
						return matching[0] ?? null;
					},
				};
			}
			return {
				async findOne(): Promise<unknown | null> {
					return digestResult;
				},
			};
		},
	};
}

test("getLatestDailyDigest returns the latest daily digest when one exists", async () => {
	const digestDoc = {
		date: "2026-07-23",
		digestType: "daily",
		items: [
			{
				rank: 1,
				agentHandle: "@github-radar",
				title: "awesome-repo",
				url: "https://github.com/owner/awesome-repo",
				kind: "repo",
				blurb: "Stars exploding",
				score: 78,
				signalSource: "github",
				signalMetric: "stars",
				signalValue: 1250,
				signalDelta: 45,
				stars: 1250,
				velocity: 45,
				contributorCount: 12,
			},
		],
		publicationSyncStatus: "synced",
		evidenceContractVersion: 2,
		createdAt: new Date(),
	};

	const result = await getLatestDailyDigest(mockDb(digestDoc) as never);

	assert.equal(result.date, "2026-07-23");
	assert.equal(result.items.length, 1);
	assert.equal(result.items[0].title, "awesome-repo");
	assert.equal(result.items[0].blurb, "Stars exploding");
	assert.equal(result.items[0].signalSource, "github");
	assert.equal(result.items[0].signalMetric, "stars");
	assert.equal(result.items[0].signalValue, 1250);
	assert.equal(result.items[0].signalDelta, 45);
	assert.ok(result.generatedAt);
	assert.equal(result.degraded, false);
	assert.equal(
		result.sourceHealth.some(
			(source) => source.agentHandle === "@github-radar" && source.ok,
		),
		true,
	);
});

test("a failed source run marks the digest degraded", async () => {
	const digestDoc = {
		date: "2026-07-23",
		digestType: "daily",
		items: [],
		publicationSyncStatus: "synced",
		evidenceContractVersion: 2,
		createdAt: new Date(),
	};
	const runs = [
		...healthyRuns().map((run) => ({
			...run,
			finishedAt: new Date(Date.now() - 60 * 60 * 1000),
		})),
		{
			agentHandle: "@community-radar",
			finishedAt: new Date(),
			ok: false,
		},
	];

	const result = await getLatestDailyDigest(mockDb(digestDoc, runs) as never);

	assert.equal(result.degraded, true);
	const community = result.sourceHealth.find(
		(source) => source.agentHandle === "@community-radar",
	);
	assert.ok(community);
	assert.equal(community.ok, false);
	assert.ok(community.lastOkAt); // an earlier healthy run is still reported
});

test("a stale digest marks the payload degraded even when sources are healthy", async () => {
	const digestDoc = {
		date: "2026-07-01",
		digestType: "daily",
		items: [],
		publicationSyncStatus: "synced",
		evidenceContractVersion: 2,
		createdAt: new Date(Date.now() - 72 * 60 * 60 * 1000),
	};

	const result = await getLatestDailyDigest(mockDb(digestDoc) as never);

	assert.equal(result.degraded, true);
});

test("getLatestDailyDigest returns empty items and null date when no digest exists", async () => {
	const result = await getLatestDailyDigest(mockDb(null) as never);

	assert.equal(result.date, null);
	assert.deepEqual(result.items, []);
	assert.equal(result.generatedAt, null);
	// No digest at all is itself degraded evidence — RomBot must not stay silent.
	assert.equal(result.degraded, true);
});

test("getLatestDailyDigest throws on database error (route returns 500)", async () => {
	const errorDb = {
		collection: () => ({
			async findOne(): Promise<never> {
				throw new Error("connection refused");
			},
		}),
	};

	await assert.rejects(
		getLatestDailyDigest(errorDb as never),
		/connection refused/,
	);
});
