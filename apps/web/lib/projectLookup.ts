import type { Collection } from "mongodb";

import { legacySlugCandidates, legacyUrlPatterns, urlToSlug } from "./slug.ts";

export type ProjectRecord = {
	url: string;
	slug: string;
	legacySlugs?: string[];
	title: string;
	description: string;
	topics: string[];
	kind?: string;
	momentumScore: number;
	hypeVerdict: string;
	firstSeenAt: string | Date;
};

export type ProjectReader = Pick<Collection<ProjectRecord>, "find" | "findOne">;

export const PROJECT_PROJECTION = {
	url: 1,
	slug: 1,
	legacySlugs: 1,
	title: 1,
	description: 1,
	topics: 1,
	kind: 1,
	momentumScore: 1,
	hypeVerdict: 1,
	firstSeenAt: 1,
} as const;

export const PROJECT_POST_PROJECTION = {
	_id: 1,
	agentHandle: 1,
	body: 1,
	verdict: 1,
	postedAt: 1,
	"project.url": 1,
	"project.title": 1,
	"project.description": 1,
	"project.kind": 1,
	"project.momentumScore": 1,
	"project.topics": 1,
	reactionCounts: 1,
} as const;

export async function findProject(
	projects: ProjectReader,
	slug: string,
): Promise<ProjectRecord | null> {
	const direct = await projects
		.find(
			{ $or: [{ slug }, { legacySlugs: slug }] },
			{ projection: PROJECT_PROJECTION },
		)
		.limit(2)
		.toArray();
	if (direct.length === 1) return direct[0];
	if (direct.length > 1) return null;

	const urlPatterns = legacyUrlPatterns(slug);
	if (urlPatterns.length > 0) {
		const urlMatch = await projects.findOne(
			{ url: { $in: urlPatterns } },
			{ projection: PROJECT_PROJECTION },
		);
		if (urlMatch) return urlMatch;
	}

	const legacySlugs = legacySlugCandidates(slug);
	if (legacySlugs.length === 0) return null;
	const candidates = await projects
		.find({ slug: { $in: legacySlugs } }, { projection: PROJECT_PROJECTION })
		.limit(25)
		.toArray();
	return candidates.find((item) => urlToSlug(item.url) === slug) ?? null;
}
