import assert from "node:assert/strict";
import test from "node:test";

import {
	dailyDigestResponse,
	getLatestDailyDigest,
	SOURCE_AGENT_HANDLES,
} from "./dailyDigest.ts";

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

type Reads = {
	digestOptions: Array<{ projection?: Record<string, unknown> }>;
	runPipelines: Array<Array<Record<string, unknown>>>;
};

/**
 * A MongoDB stand-in: `digests.findOne` returns the given digest, and
 * `agent_runs.aggregate` answers the per-agent latest run and latest ok run
 * the route's pipeline asks for. Every read is recorded in `reads`.
 */
function mockDb(
	digestResult: unknown | null,
	runs: RunDoc[] = healthyRuns(),
	reads: Reads = { digestOptions: [], runPipelines: [] },
) {
	return {
		collection(name: string) {
			if (name === "agent_runs") {
				return {
					aggregate(pipeline: Array<Record<string, unknown>>) {
						reads.runPipelines.push(pipeline);
						const handles = [...new Set(runs.map((run) => run.agentHandle))];
						const latest = (list: RunDoc[]) =>
							[...list].sort(
								(a, b) => b.finishedAt.getTime() - a.finishedAt.getTime(),
							)[0];
						const rows = handles.map((handle) => {
							const own = runs.filter((run) => run.agentHandle === handle);
							const lastRun = latest(own);
							return {
								_id: handle,
								lastRunAt: lastRun.finishedAt,
								lastRunOk: lastRun.ok,
								lastOkAt: latest(own.filter((run) => run.ok))?.finishedAt ?? null,
							};
						});
						return { toArray: async () => rows };
					},
				};
			}
			return {
				async findOne(
					_filter: unknown,
					options: { projection?: Record<string, unknown> } = {},
				): Promise<unknown | null> {
					reads.digestOptions.push(options);
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

test("the item text, stat, weekly gain and HN fields reach the payload unchanged, and older digests without them still load", async () => {
	const base = {
		rank: 1,
		agentHandle: "@github-radar",
		title: "awesome-repo",
		url: "https://github.com/owner/awesome-repo",
		kind: "repo",
		blurb: "Stars exploding",
		score: 78,
		signalSource: "github",
		signalMetric: "github_stars",
		signalValue: 1250,
		signalDelta: 0,
		stars: null,
		velocity: null,
		contributorCount: null,
	};
	const digestDoc = {
		date: "2026-07-23",
		digestType: "daily",
		items: [
			{
				...base,
				what: "A git hook that stops unexplained pushes.",
				emoji: "🛠️",
				verdict: "emerging",
				starsThisWeek: 240,
				starsToday: 31,
				trendingRank: 4,
				trendingSince: "daily",
				upvotes: null,
				comments: null,
				weeklyGain: 240,
				weeklyGainWindowDays: 7,
				hnPoints: 292,
				hnComments: 139,
			},
			{
				...base,
				rank: 2,
				what: null,
				verdict: null,
				weeklyGain: null,
				weeklyGainWindowDays: null,
			},
			{ ...base, rank: 3 },
			{
				...base,
				rank: 4,
				kind: "thread",
				signalMetric: "upvotes",
				upvotes: 900,
				comments: 64,
			},
		],
		publicationSyncStatus: "synced",
		evidenceContractVersion: 2,
		createdAt: new Date(),
	};

	const result = await getLatestDailyDigest(mockDb(digestDoc) as never);

	assert.equal(result.items[0].what, "A git hook that stops unexplained pushes.");
	assert.equal(result.items[0].verdict, "emerging");
	assert.equal(result.items[0].weeklyGain, 240);
	assert.equal(result.items[0].weeklyGainWindowDays, 7);
	assert.equal(result.items[0].hnPoints, 292);
	assert.equal(result.items[0].hnComments, 139);
	assert.equal(result.items[0].emoji, "🛠️");
	assert.equal(result.items[0].starsThisWeek, 240);
	assert.equal(result.items[0].starsToday, 31);
	assert.equal(result.items[0].trendingRank, 4);
	assert.equal(result.items[0].trendingSince, "daily");
	assert.equal(result.items[3].upvotes, 900);
	assert.equal(result.items[3].comments, 64);
	assert.equal(result.items[1].what, null);
	assert.equal(result.items[1].verdict, null);
	assert.equal(result.items[1].weeklyGain, null);
	assert.equal(result.items[1].weeklyGainWindowDays, null);
	assert.equal(result.items[2].what, undefined);
	assert.equal(result.items[2].weeklyGain, undefined);
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
			aggregate() {
				return {
					toArray: async (): Promise<never> => {
						throw new Error("connection refused");
					},
				};
			},
		}),
	};

	await assert.rejects(
		getLatestDailyDigest(errorDb as never),
		/connection refused/,
	);
});

function repoDigest(items: Array<Record<string, unknown>>) {
	return {
		date: "2026-07-23",
		digestType: "daily",
		items,
		belowFloor: false,
		publicationSyncStatus: "synced",
		evidenceContractVersion: 2,
		createdAt: new Date(),
	};
}

test("one projected digest read and one projected agent_runs aggregation serve the payload", async () => {
	const reads: Reads = { digestOptions: [], runPipelines: [] };

	await getLatestDailyDigest(
		mockDb(repoDigest([{ kind: "repo" }]), healthyRuns(), reads) as never,
	);

	assert.equal(reads.digestOptions.length, 1);
	const projection = reads.digestOptions[0].projection;
	assert.ok(projection);
	for (const field of ["date", "items", "createdAt", "belowFloor"]) {
		assert.equal(projection[field], 1);
	}
	assert.equal(reads.runPipelines.length, 1);
	const [pipeline] = reads.runPipelines;
	assert.deepEqual(pipeline[0], {
		$match: { agentHandle: { $in: SOURCE_AGENT_HANDLES } },
	});
	assert.ok(pipeline.some((stage) => "$project" in stage));
});

test("the latest run decides ok, and the latest ok run is still reported", async () => {
	const earlier = new Date(Date.now() - 60 * 60 * 1000);
	const runs = [
		...healthyRuns().map((run) => ({ ...run, finishedAt: earlier })),
		{ agentHandle: "@reddit-pulse", finishedAt: new Date(), ok: false },
	];

	const result = await getLatestDailyDigest(
		mockDb(repoDigest([{ kind: "repo" }]), runs) as never,
	);

	const reddit = result.sourceHealth.find(
		(source) => source.agentHandle === "@reddit-pulse",
	);
	assert.ok(reddit);
	assert.equal(reddit.ok, false);
	assert.equal(reddit.lastOkAt, earlier.toISOString());
	assert.equal(result.degraded, true);
});

test("on a day the digest holds no repo, GitHub's source health is not ok", async () => {
	const result = await getLatestDailyDigest(
		mockDb(repoDigest([{ kind: "thread" }, { kind: "video" }])) as never,
	);

	const github = result.sourceHealth.find(
		(source) => source.agentHandle === "@github-radar",
	);
	assert.ok(github);
	assert.equal(github.ok, false);
	assert.ok(github.lastOkAt);
	assert.equal(result.degraded, true);
	assert.equal(
		result.sourceHealth
			.filter((source) => source.agentHandle !== "@github-radar")
			.every((source) => source.ok),
		true,
	);
});

test("belowFloor reaches the payload", async () => {
	const digest = { ...repoDigest([{ kind: "thread" }]), belowFloor: true };

	const result = await getLatestDailyDigest(mockDb(digest) as never);

	assert.equal(result.belowFloor, true);
});

test("the response is never cached, on success or on error", async () => {
	const ok = await dailyDigestResponse(
		async () => mockDb(repoDigest([{ kind: "repo" }])) as never,
	);
	const failed = await dailyDigestResponse(async () => {
		throw new Error("connection refused");
	});

	assert.equal(ok.init.status, 200);
	assert.equal(ok.init.headers["Cache-Control"], "no-store");
	assert.equal(failed.init.status, 500);
	assert.deepEqual(failed.body, { error: "internal error" });
	assert.equal(failed.init.headers["Cache-Control"], "no-store");
});
