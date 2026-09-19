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
	stars: number | null;
	velocity: number | null;
	contributorCount: number | null;
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
	generatedAt: string | null;
	sourceHealth: SourceHealth[];
	degraded: boolean;
};

export const SOURCE_AGENT_HANDLES = AGENT_CATALOG.filter(
	(agent) => agent.source_type !== "aggregator",
).map((agent) => agent.handle);

/** A digest older than this is stale evidence, not today's signal. */
const DIGEST_STALE_AFTER_MS = 48 * 60 * 60 * 1000;

function toIso(value: unknown): string | null {
	if (value instanceof Date) return value.toISOString();
	if (typeof value === "string") return value;
	return null;
}

/**
 * Latest run-health per source agent from the agent_runs collection the
 * runner writes on every cycle (healthy, unhealthy, or crashed). A source
 * that has never run reports ok=false — no evidence of health is a finding.
 */
async function getSourceHealth(db: Db): Promise<SourceHealth[]> {
	const runs = db.collection("agent_runs");
	const health: SourceHealth[] = [];
	for (const agentHandle of SOURCE_AGENT_HANDLES) {
		const [lastRun, lastOk] = await Promise.all([
			runs.findOne({ agentHandle }, { sort: { finishedAt: -1 } }),
			runs.findOne({ agentHandle, ok: true }, { sort: { finishedAt: -1 } }),
		]);
		health.push({
			agentHandle,
			lastRunAt: toIso(lastRun?.finishedAt),
			lastOkAt: toIso(lastOk?.finishedAt),
			ok: lastRun?.ok === true,
		});
	}
	return health;
}

/**
 * Query MongoDB for the latest daily digest plus source run health.
 * Returns { date: null, items: [] } when no digest exists
 * (RomBot treats empty as "stay silent"). `degraded` tells RomBot when a
 * source is broken or the evidence is stale, so breakage surfaces to the
 * reader instead of silently serving last week's digest.
 */
export async function getLatestDailyDigest(db: Db): Promise<DailyDigest> {
	const digest = await db
		.collection("digests")
		.findOne(
			{ digestType: "daily", ...PUBLIC_DIGEST_FILTER },
			{ sort: { createdAt: -1 } },
		);
	const sourceHealth = await getSourceHealth(db);
	const generatedAt = toIso(digest?.createdAt);
	const digestIsStale =
		generatedAt === null ||
		Date.now() - new Date(generatedAt).getTime() > DIGEST_STALE_AFTER_MS;
	const degraded =
		digestIsStale || sourceHealth.some((source) => !source.ok);
	return {
		date: digest?.date ?? null,
		items: (digest?.items ?? []) as DailyDigestItem[],
		generatedAt,
		sourceHealth,
		degraded,
	};
}
