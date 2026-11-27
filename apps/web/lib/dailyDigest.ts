import type { Db } from "mongodb";

import { AGENT_CATALOG } from "./agentCatalog.ts";
import { PUBLIC_DIGEST_FILTER } from "./publication.ts";

export type DailyDigestItem = {
	rank: number;
	agentHandle: string;
	title: string;
	url: string;
	kind: string;
	blurb: string;
	score: number;
	signalSource: string | null;
	signalMetric: string | null;
	signalValue: number | null;
	signalDelta: number | null;
	/** Always null: nothing writes these three, so do not read them. */
	stars: number | null;
	velocity: number | null;
	contributorCount: number | null;
	/** Optional: digests stored before these fields existed lack them. */
	what?: string | null;
	/** Topic emoji chosen by code from a fixed map. */
	emoji?: string;
	verdict?: string | null;
	starsThisWeek?: number | null;
	starsToday?: number | null;
	/** Rank on that day's all-languages trending page named by trendingSince. */
	trendingRank?: number | null;
	trendingSince?: "daily" | "weekly" | null;
	upvotes?: number | null;
	comments?: number | null;
	/** Stars this week, with weeklyGainWindowDays 7. */
	weeklyGain?: number | null;
	weeklyGainWindowDays?: number | null;
	/** Recent Hacker News story about the repo, when one exists (null: none found). */
	hnPoints?: number | null;
	hnComments?: number | null;
};

export type SourceHealth = {
	agentHandle: string;
	lastRunAt: string | null;
	lastOkAt: string | null;
	ok: boolean;
};

export type DailyDigest = {
	date: string | null;
	items: DailyDigestItem[];
	/** The digest had fewer than 2 items past their floors and was topped up. */
	belowFloor: boolean;
	generatedAt: string | null;
	sourceHealth: SourceHealth[];
	degraded: boolean;
};

export const SOURCE_AGENT_HANDLES = AGENT_CATALOG.filter(
	(agent) => agent.source_type !== "aggregator",
).map((agent) => agent.handle);

const GITHUB_SOURCE_HANDLE = "@github-radar";

/** RomBot's staleness check needs every read fresh, so nothing caches it. */
const DAILY_DIGEST_HEADERS = { "Cache-Control": "no-store" };

/** A digest older than this is stale evidence, not today's signal. */
const DIGEST_STALE_AFTER_MS = 48 * 60 * 60 * 1000;

function toIso(value: unknown): string | null {
	if (value instanceof Date) return value.toISOString();
	if (typeof value === "string") return value;
	return null;
}

type RunHealthRow = {
	_id: string;
	lastRunAt?: unknown;
	lastRunOk?: unknown;
	lastOkAt?: unknown;
};

/**
 * Latest run-health per source agent from the agent_runs collection the
 * runner writes on every cycle (healthy, unhealthy, or crashed), in one
 * aggregation. A source that has never run reports ok=false — no evidence of
 * health is a finding.
 */
async function getSourceHealth(db: Db): Promise<SourceHealth[]> {
	const rows = await db
		.collection("agent_runs")
		.aggregate<RunHealthRow>([
			{ $match: { agentHandle: { $in: SOURCE_AGENT_HANDLES } } },
			{ $project: { _id: 0, agentHandle: 1, finishedAt: 1, ok: 1 } },
			{ $sort: { agentHandle: 1, finishedAt: -1 } },
			{
				$group: {
					_id: "$agentHandle",
					lastRunAt: { $first: "$finishedAt" },
					lastRunOk: { $first: "$ok" },
					lastOkAt: {
						$max: { $cond: [{ $eq: ["$ok", true] }, "$finishedAt", null] },
					},
				},
			},
		])
		.toArray();
	const byHandle = new Map(rows.map((row) => [row._id, row]));
	return SOURCE_AGENT_HANDLES.map((agentHandle) => {
		const row = byHandle.get(agentHandle);
		return {
			agentHandle,
			lastRunAt: toIso(row?.lastRunAt),
			lastOkAt: toIso(row?.lastOkAt),
			ok: row?.lastRunOk === true,
		};
	});
}

/**
 * Query MongoDB for the latest daily digest plus source run health.
 * Returns { date: null, items: [] } when no digest exists
 * (RomBot treats empty as "stay silent"). `degraded` tells RomBot when a
 * source is broken or the evidence is stale, so breakage surfaces to the
 * reader instead of silently serving last week's digest. A digest with no
 * repo item reports GitHub not ok, whatever its last run said.
 */
export async function getLatestDailyDigest(db: Db): Promise<DailyDigest> {
	const [digest, runHealth] = await Promise.all([
		db.collection("digests").findOne(
			{ digestType: "daily", ...PUBLIC_DIGEST_FILTER },
			{
				sort: { createdAt: -1 },
				projection: { _id: 0, date: 1, items: 1, createdAt: 1, belowFloor: 1 },
			},
		),
		getSourceHealth(db),
	]);
	const items = (digest?.items ?? []) as DailyDigestItem[];
	const hasRepo = items.some((item) => item.kind === "repo");
	const sourceHealth = runHealth.map((source) =>
		source.agentHandle === GITHUB_SOURCE_HANDLE && !hasRepo
			? { ...source, ok: false }
			: source,
	);
	const generatedAt = toIso(digest?.createdAt);
	const digestIsStale =
		generatedAt === null ||
		Date.now() - new Date(generatedAt).getTime() > DIGEST_STALE_AFTER_MS;
	const degraded =
		digestIsStale || sourceHealth.some((source) => !source.ok);
	return {
		date: digest?.date ?? null,
		items,
		belowFloor: digest?.belowFloor === true,
		generatedAt,
		sourceHealth,
		degraded,
	};
}

/** The body and init GET /api/daily-digest sends; a failure is a 500. */
export async function dailyDigestResponse(getDb: () => Promise<Db>): Promise<{
	body: DailyDigest | { error: string };
	init: { status: number; headers: Record<string, string> };
}> {
	try {
		const db = await getDb();
		return {
			body: await getLatestDailyDigest(db),
			init: { status: 200, headers: DAILY_DIGEST_HEADERS },
		};
	} catch (err) {
		console.error("daily-digest GET error:", err);
		return {
			body: { error: "internal error" },
			init: { status: 500, headers: DAILY_DIGEST_HEADERS },
		};
	}
}
